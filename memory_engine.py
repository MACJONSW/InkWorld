"""
记忆引擎 - 三层记忆架构 + 动态上下文注入 + 向量RAG检索
Tier 1: 工作记忆 (Working Memory) - 当前编辑区原文 ~3000 tokens
Tier 2: 滚动摘要 (Rolling Summary) - 自动章节摘要 + 实体图谱更新
Tier 3: 向量长记忆 (Vector RAG) - FAISS索引 + Top-K检索
"""
import re
import json
import math
import os
import hashlib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 可选 FAISS 支持
try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False


class MemoryEngine:
    """三层记忆引擎"""

    def __init__(self, db):
        self.db = db
        self._faiss_indices = {}  # book_id -> faiss.IndexFlatIP
        self._faiss_chunks = {}   # book_id -> list of chunk dicts
        self._faiss_dim = 128     # TF-IDF降维维度
        self._vectorizer = None
        self._chunk_size = 500    # 向量切片字符数
        self._chunk_overlap = 100 # 切片重叠

    # =================================================================
    #  Tier 1: 工作记忆 (Working Memory)
    # =================================================================

    def get_working_memory(self, node_id, max_chars=3000):
        """获取工作记忆：当前编辑区最新原文快照"""
        if not node_id:
            return ""
        content_data = self.db.get_node_content(node_id)
        content = content_data.get('content', '') if content_data else ''
        if not content:
            return ""
        # 截取最近 max_chars
        return content[-max_chars:] if len(content) > max_chars else content

    # =================================================================
    #  Tier 2: 滚动摘要 (Rolling Summary)
    # =================================================================

    def get_rolling_summaries(self, book_id, limit=10):
        """获取最近的滚动摘要"""
        summaries = self.db.get_chapter_summaries(book_id)
        return summaries[-limit:] if summaries else []

    def auto_summarize_check(self, book_id, node_id, threshold=2000):
        """检查当前章节是否需要自动总结（字数超过阈值且无摘要时提示）"""
        if not node_id or not book_id:
            return False
        content_data = self.db.get_node_content(node_id)
        content = content_data.get('content', '') if content_data else ''
        if len(content) < threshold:
            return False
        # 检查是否已有摘要
        summaries = self.db.get_chapter_summaries(book_id)
        existing = [s for s in summaries if s.get('node_id') == node_id]
        return len(existing) == 0

    # =================================================================
    #  Tier 3: 向量长记忆 (Vector RAG)
    # =================================================================

    def _chunk_text(self, text, chunk_size=None, overlap=None):
        """将文本切片为重叠块"""
        chunk_size = chunk_size or self._chunk_size
        overlap = overlap or self._chunk_overlap
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += chunk_size - overlap
        return chunks

    def _build_tfidf_vectors(self, texts):
        """使用TF-IDF为文本列表构建向量"""
        if not texts:
            return np.array([])
        vectorizer = TfidfVectorizer(
            token_pattern=r'(?u)\b\w+\b',
            max_features=self._faiss_dim,
            sublinear_tf=True
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        self._vectorizer = vectorizer
        return tfidf_matrix.toarray().astype('float32')

    def vectorize_book(self, book_id):
        """为整本书建立向量索引（Tier 3 构建）"""
        # 收集所有文本：lorebook + 摘要 + 节点内容
        all_chunks = []

        # Lorebook条目
        entries = self.db.get_lorebook_entries(book_id)
        for e in entries:
            text = f"{e['name']}({e['category']}): {e.get('content', '')}"
            for chunk in self._chunk_text(text):
                all_chunks.append({
                    'text': chunk,
                    'source': 'lorebook',
                    'source_id': e['id'],
                    'name': e['name'],
                    'category': e['category']
                })

        # 章节摘要
        summaries = self.db.get_chapter_summaries(book_id)
        for s in summaries:
            text = f"{s.get('chapter_title', '')}: {s.get('summary', '')}"
            for chunk in self._chunk_text(text):
                all_chunks.append({
                    'text': chunk,
                    'source': 'summary',
                    'source_id': s.get('id', ''),
                    'name': s.get('chapter_title', ''),
                    'category': 'summary'
                })

        # 节点内容
        tree = self.db.get_document_tree(book_id)
        for node in self._flatten_tree(tree):
            content_data = self.db.get_node_content(node['id'])
            content = content_data.get('content', '') if content_data else ''
            if content:
                for chunk in self._chunk_text(content):
                    all_chunks.append({
                        'text': chunk,
                        'source': 'content',
                        'source_id': node['id'],
                        'name': node.get('title', ''),
                        'category': 'chapter'
                    })

        if not all_chunks:
            return {'status': 'empty', 'chunk_count': 0}

        # 构建向量
        texts = [c['text'] for c in all_chunks]
        vectors = self._build_tfidf_vectors(texts)

        if HAS_FAISS and len(vectors) > 0:
            # 使用FAISS建立索引
            dim = vectors.shape[1]
            index = faiss.IndexFlatIP(dim)
            # L2归一化 -> 内积 = 余弦相似度
            faiss.normalize_L2(vectors)
            index.add(vectors)
            self._faiss_indices[book_id] = index
            self._faiss_chunks[book_id] = all_chunks
            self._faiss_dim = dim
        else:
            # Fallback: numpy存储
            self._faiss_indices[book_id] = vectors
            self._faiss_chunks[book_id] = all_chunks

        return {'status': 'ok', 'chunk_count': len(all_chunks), 'has_faiss': HAS_FAISS}

    def vector_retrieve(self, book_id, query, top_k=5):
        """向量检索：返回与query最相关的top_k个文本块"""
        if book_id not in self._faiss_chunks or not self._faiss_chunks[book_id]:
            # 尝试自动建立索引
            self.vectorize_book(book_id)

        chunks = self._faiss_chunks.get(book_id, [])
        if not chunks:
            return []

        # 构建查询向量
        texts = [c['text'] for c in chunks]
        texts.append(query)
        vectors = self._build_tfidf_vectors(texts)
        if len(vectors) == 0:
            return []

        query_vec = vectors[-1:].astype('float32')
        doc_vecs = vectors[:-1].astype('float32')

        if HAS_FAISS and book_id in self._faiss_indices and isinstance(self._faiss_indices[book_id], faiss.Index):
            index = self._faiss_indices[book_id]
            faiss.normalize_L2(query_vec)
            scores, indices = index.search(query_vec, min(top_k, len(chunks)))
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and idx < len(chunks):
                    r = dict(chunks[idx])
                    r['score'] = round(float(score), 4)
                    results.append(r)
            return results
        else:
            # Numpy fallback
            from sklearn.metrics.pairwise import cosine_similarity as cos_sim
            sims = cos_sim(query_vec, doc_vecs)[0]
            top_indices = np.argsort(sims)[::-1][:top_k]
            results = []
            for idx in top_indices:
                if sims[idx] > 0.05:
                    r = dict(chunks[idx])
                    r['score'] = round(float(sims[idx]), 4)
                    results.append(r)
            return results

    def _flatten_tree(self, tree):
        """将树形结构扁平化"""
        nodes = []
        if not tree:
            return nodes
        for node in tree:
            nodes.append(node)
            if 'children' in node and node['children']:
                nodes.extend(self._flatten_tree(node['children']))
        return nodes

    # =================================================================
    #  综合记忆构建（三层融合）
    # =================================================================

    def build_context_window(self, book_id, current_node_id, max_chars=6000):
        """
        三层记忆融合上下文窗口：
        Tier 1: 工作记忆 (当前编辑区原文 ~3000字符)
        Tier 2: 滚动摘要 (最近章节摘要)
        Tier 3: 向量RAG (相关设定检索) -- 自动触发
        + 全局设定注入
        """
        context_parts = []

        # Tier 0: 全局设定（常驻）
        entries = self.db.get_lorebook_entries(book_id)
        setting_text = ""
        for e in entries:
            if e.get('enabled'):
                setting_text += f"【{e['category']}-{e['name']}】{e['content']}\n"
        if setting_text:
            context_parts.append(f"=== 世界观设定 ===\n{setting_text[:2000]}")

        # Tier 2: 滚动摘要
        summaries = self.get_rolling_summaries(book_id, limit=10)
        if summaries:
            summary_text = "\n".join([
                f"[{s['chapter_title']}] {s['summary']}" for s in summaries
            ])
            context_parts.append(f"=== 前情摘要(Tier2) ===\n{summary_text[:2000]}")

        # Tier 1: 工作记忆
        working = self.get_working_memory(current_node_id, max_chars=3000)
        if working:
            context_parts.append(f"=== 工作记忆(Tier1) ===\n{working}")

            reminder_text = self.build_character_reminder_context(
                book_id,
                text=working[-1500:],
                node_id=current_node_id,
                max_characters=4
            )
            if reminder_text:
                context_parts.append(reminder_text)

            # Tier 3: 向量RAG检索（基于工作记忆的相关片段）
            rag_results = self.vector_retrieve(book_id, working[-500:], top_k=3)
            if rag_results:
                rag_text = "\n".join([
                    f"[{r['source']}/{r['name']}](相关度{r['score']}): {r['text'][:200]}"
                    for r in rag_results
                ])
                context_parts.append(f"=== 向量检索(Tier3) ===\n{rag_text}")

        return "\n\n".join(context_parts)

    def _character_entries(self, book_id):
        entries = self.db.get_lorebook_entries(book_id)
        return [e for e in entries if e.get('category') == 'character' and e.get('enabled', True)]

    def _character_terms(self, entry):
        terms = []
        name = (entry.get('name') or '').strip()
        if name:
            terms.append(name)
        keywords = entry.get('keywords', '') or ''
        for keyword in keywords.split(','):
            cleaned = keyword.strip()
            if cleaned and cleaned not in terms:
                terms.append(cleaned)
        return terms

    def extract_character_mentions(self, book_id, text, max_characters=5):
        text = text or ''
        if not book_id or not text.strip():
            return []

        lowered = text.lower()
        mentions = []
        for entry in self._character_entries(book_id):
            terms = self._character_terms(entry)
            count = 0
            first_index = None
            matched_terms = []
            for term in terms:
                if not term:
                    continue
                term_lower = term.lower()
                hit_count = lowered.count(term_lower)
                if hit_count <= 0:
                    continue
                count += hit_count
                idx = lowered.find(term_lower)
                if first_index is None or idx < first_index:
                    first_index = idx
                matched_terms.append(term)
            if count > 0:
                mentions.append({
                    'name': entry.get('name', ''),
                    'entry': entry,
                    'count': count,
                    'first_index': first_index if first_index is not None else len(text),
                    'matched_terms': matched_terms
                })

        mentions.sort(key=lambda item: (-item['count'], item['first_index'], item['name']))
        return mentions[:max_characters]

    def _parse_summary_payload(self, summary):
        if not summary:
            return {}
        if isinstance(summary, dict):
            return summary
        if isinstance(summary, str):
            try:
                match = re.search(r'\{[\s\S]*\}', summary)
                if match:
                    return json.loads(match.group())
            except Exception:
                return {'summary': summary.strip()}
            return {'summary': summary.strip()}
        return {}

    def _short_text(self, text, limit=160):
        text = (text or '').strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '...'

    def _excerpt_around_term(self, text, term, radius=70):
        text = text or ''
        term = term or ''
        if not text.strip() or not term:
            return self._short_text(text, 140)
        idx = text.lower().find(term.lower())
        if idx < 0:
            return self._short_text(text, 140)
        start = max(0, idx - radius)
        end = min(len(text), idx + len(term) + radius)
        excerpt = text[start:end].strip()
        if start > 0:
            excerpt = '...' + excerpt
        if end < len(text):
            excerpt += '...'
        return excerpt

    def _character_foreshadowing(self, book_id, character_name, limit=3):
        items = []
        for item in self.db.get_foreshadowing(book_id, status='unresolved'):
            haystack = ' '.join([
                item.get('text', ''),
                item.get('label', ''),
                item.get('description', '')
            ]).lower()
            if character_name.lower() in haystack:
                items.append(item)
        return items[:limit]

    def build_character_reminders(self, book_id, text='', node_id=None,
                                  max_characters=5, history_limit=3, foreshadow_limit=3):
        working_text = text or ''
        if node_id and book_id and self.db.get_node_book_id(node_id) != book_id:
            node_id = None
        if not working_text and node_id:
            working_text = self.get_working_memory(node_id, max_chars=4000)

        mentions = self.extract_character_mentions(book_id, working_text, max_characters=max_characters)
        if not mentions and node_id:
            node = self.db.get_node(node_id)
            if node:
                history = self.db.get_character_history(book_id, limit=max_characters)
                by_name = []
                seen = set()
                for item in history:
                    name = item.get('character_name', '')
                    if name and name not in seen:
                        by_name.append(name)
                        seen.add(name)
                character_map = {entry.get('name'): entry for entry in self._character_entries(book_id)}
                mentions = [
                    {'name': name, 'entry': character_map.get(name, {'name': name}), 'count': 1, 'first_index': i, 'matched_terms': [name]}
                    for i, name in enumerate(by_name[:max_characters])
                ]

        reminders = []
        for mention in mentions:
            name = mention['name']
            lore_entry = mention['entry']
            history_entries = self.db.get_character_history(book_id, character_name=name, limit=history_limit + 3)
            foreshadowing = self._character_foreshadowing(book_id, name, limit=foreshadow_limit)
            states = self.db.get_world_state(book_id, entity_name=name)[:3]
            psych_profiles = self.db.get_character_psychology(book_id, character_name=name)
            psych = psych_profiles[0] if psych_profiles else {}

            personality_bits = []
            if lore_entry.get('description'):
                personality_bits.append(lore_entry['description'])
            elif lore_entry.get('content'):
                personality_bits.append(self._short_text(lore_entry['content'], 180))
            if psych.get('drives'):
                personality_bits.append(f"驱动力：{self._short_text(psych['drives'], 80)}")
            if psych.get('fears'):
                personality_bits.append(f"恐惧：{self._short_text(psych['fears'], 80)}")
            if psych.get('core_contradiction'):
                personality_bits.append(f"核心矛盾：{self._short_text(psych['core_contradiction'], 80)}")

            recent_history = []
            last_seen_chapter = ''
            for entry in history_entries:
                if not last_seen_chapter and entry.get('chapter_title'):
                    last_seen_chapter = entry.get('chapter_title', '')
                recent_history.append({
                    'id': entry.get('id', ''),
                    'entry_type': entry.get('entry_type', 'event'),
                    'summary': entry.get('summary', ''),
                    'details': entry.get('details', ''),
                    'chapter_title': entry.get('chapter_title', ''),
                    'is_manual': bool(entry.get('is_manual')),
                    'source_excerpt': entry.get('source_excerpt', '')
                })

            reminders.append({
                'name': name,
                'matched_terms': mention.get('matched_terms', []),
                'personality': '；'.join([bit for bit in personality_bits if bit]),
                'recent_history': recent_history[:history_limit],
                'foreshadowing': [
                    {
                        'id': item.get('id', ''),
                        'label': item.get('label', ''),
                        'description': item.get('description', ''),
                        'text': item.get('text', ''),
                        'status': item.get('status', 'unresolved')
                    }
                    for item in foreshadowing
                ],
                'world_state': [
                    {
                        'id': state.get('id', ''),
                        'state_type': state.get('state_type', ''),
                        'state_value': state.get('state_value', ''),
                        'scene_context': state.get('scene_context', '')
                    }
                    for state in states
                ],
                'last_seen_chapter': last_seen_chapter,
                'lorebook_id': lore_entry.get('id', ''),
                'keywords': lore_entry.get('keywords', '')
            })

        return reminders

    def build_character_reminder_context(self, book_id, text='', node_id=None, max_characters=4):
        reminders = self.build_character_reminders(
            book_id,
            text=text,
            node_id=node_id,
            max_characters=max_characters,
            history_limit=3,
            foreshadow_limit=3
        )
        if not reminders:
            return ''

        parts = ['=== 人物提醒 ===']
        for reminder in reminders:
            parts.append(f"【{reminder['name']}】")
            if reminder.get('personality'):
                parts.append(f"性格/底色：{self._short_text(reminder['personality'], 220)}")
            if reminder.get('recent_history'):
                parts.append('历史事件：')
                for item in reminder['recent_history'][:3]:
                    chapter_prefix = f"[{item['chapter_title']}] " if item.get('chapter_title') else ''
                    parts.append(f"- {chapter_prefix}{self._short_text(item.get('summary', ''), 140)}")
            if reminder.get('foreshadowing'):
                parts.append('未回收伏笔：')
                for item in reminder['foreshadowing'][:2]:
                    label_prefix = f"{item['label']}：" if item.get('label') else ''
                    parts.append(f"- {label_prefix}{self._short_text(item.get('description') or item.get('text', ''), 120)}")
            if reminder.get('world_state'):
                parts.append('当前状态：')
                for state in reminder['world_state'][:2]:
                    parts.append(f"- {state.get('state_type', '')}: {self._short_text(state.get('state_value', ''), 80)}")
        return '\n'.join(parts)

    def _build_history_details(self, chapter_title, summary_payload, source_excerpt, foreshadow_labels):
        details = []
        summary_text = summary_payload.get('summary', '')
        if summary_text:
            details.append(summary_text)
        key_events = summary_payload.get('key_events', '')
        if isinstance(key_events, list):
            key_events = '；'.join([str(item) for item in key_events if item])
        if key_events:
            details.append(f"关键事件：{self._short_text(str(key_events), 180)}")
        character_states = summary_payload.get('character_states', '')
        if character_states:
            details.append(f"角色状态：{self._short_text(str(character_states), 180)}")
        if foreshadow_labels:
            details.append(f"相关伏笔：{'、'.join(foreshadow_labels)}")
        if source_excerpt:
            details.append(f"原文片段：{self._short_text(source_excerpt, 180)}")
        return '\n'.join(details)

    def refresh_character_history_for_node(self, book_id, node_id, chapter_title='', text='', summary=''):
        if not book_id or not node_id:
            return []
        if self.db.get_node_book_id(node_id) != book_id:
            return []

        content = text or ''
        if not content:
            content_data = self.db.get_node_content(node_id)
            content = content_data.get('content', '') if content_data else ''
        node = self.db.get_node(node_id)
        chapter_title = chapter_title or (node.get('title', '') if node else '')

        self.db.delete_generated_character_history(book_id, source_node_id=node_id)
        if not content.strip():
            return []

        mentions = self.extract_character_mentions(book_id, content, max_characters=20)
        if not mentions:
            return []

        summary_payload = self._parse_summary_payload(summary)
        unresolved = self.db.get_foreshadowing(book_id, status='unresolved')
        created = []

        for mention in mentions:
            name = mention['name']
            source_excerpt = self._excerpt_around_term(content, name)
            foreshadow_labels = []
            for item in unresolved:
                haystack = ' '.join([
                    item.get('text', ''),
                    item.get('label', ''),
                    item.get('description', '')
                ]).lower()
                if name.lower() in haystack:
                    label = item.get('label') or self._short_text(item.get('text', ''), 30)
                    foreshadow_labels.append(label)

            if summary_payload.get('summary'):
                summary_text = f"在《{chapter_title or '未命名章节'}》中：{self._short_text(summary_payload.get('summary', ''), 120)}"
            else:
                summary_text = f"在《{chapter_title or '未命名章节'}》中出场，相关片段：{self._short_text(source_excerpt, 120)}"

            details = self._build_history_details(chapter_title, summary_payload, source_excerpt, foreshadow_labels[:3])
            created.append({
                'book_id': book_id,
                'character_name': name,
                'entry_type': 'event',
                'summary': summary_text,
                'details': details,
                'source_node_id': node_id,
                'chapter_title': chapter_title,
                'source_excerpt': source_excerpt,
                'foreshadow_refs': '、'.join(foreshadow_labels[:5]),
                'is_manual': False
            })

        created_ids = []
        for entry in created:
            created_ids.append(self.db.add_character_history(entry))
        return created_ids

    def refresh_character_history_for_book(self, book_id):
        if not book_id:
            return {'refreshed_nodes': 0, 'created_entries': 0}

        tree = self.db.get_document_tree(book_id)
        nodes = self._flatten_tree(tree)
        refreshed_nodes = 0
        created_entries = 0
        summaries = {item.get('node_id'): item for item in self.db.get_chapter_summaries(book_id)}

        for node in nodes:
            content_data = self.db.get_node_content(node['id'])
            content = content_data.get('content', '') if content_data else ''
            if not content.strip():
                continue
            summary_item = summaries.get(node['id'], {})
            created_ids = self.refresh_character_history_for_node(
                book_id,
                node['id'],
                chapter_title=node.get('title', ''),
                text=content,
                summary=summary_item.get('summary', '')
            )
            refreshed_nodes += 1
            created_entries += len(created_ids)

        return {'refreshed_nodes': refreshed_nodes, 'created_entries': created_entries}

    # =================================================================
    #  原有功能保留
    # =================================================================

    def dynamic_inject(self, book_id, text):
        """基于正则匹配和向量检索，找出与当前文本相关的 Lorebook 条目"""
        entries = self.db.get_lorebook_entries(book_id)
        injected = []

        for entry in entries:
            if not entry.get('enabled', True):
                continue

            # 正则关键词匹配
            keywords = entry.get('keywords', '')
            if keywords:
                kw_list = [k.strip() for k in keywords.split(',') if k.strip()]
                for kw in kw_list:
                    try:
                        if re.search(kw, text, re.IGNORECASE):
                            injected.append({
                                'id': entry['id'],
                                'name': entry['name'],
                                'category': entry['category'],
                                'content': entry['content'],
                                'match_type': 'keyword'
                            })
                            break
                    except re.error:
                        if kw.lower() in text.lower():
                            injected.append({
                                'id': entry['id'],
                                'name': entry['name'],
                                'category': entry['category'],
                                'content': entry['content'],
                                'match_type': 'keyword'
                            })
                            break

        # 向量检索补充（TF-IDF）
        injected_ids = {e['id'] for e in injected}
        remaining = [e for e in entries if e['id'] not in injected_ids and e.get('enabled', True)]

        if remaining and text.strip():
            try:
                corpus = [e.get('content', '') + ' ' + e.get('name', '') for e in remaining]
                corpus.append(text)
                vectorizer = TfidfVectorizer(token_pattern=r'(?u)\b\w+\b')
                tfidf_matrix = vectorizer.fit_transform(corpus)
                similarities = cosine_similarity(tfidf_matrix[-1:], tfidf_matrix[:-1])[0]

                for i, sim in enumerate(similarities):
                    if sim > 0.15:
                        injected.append({
                            'id': remaining[i]['id'],
                            'name': remaining[i]['name'],
                            'category': remaining[i]['category'],
                            'content': remaining[i]['content'],
                            'match_type': 'vector',
                            'similarity': round(float(sim), 3)
                        })
            except Exception:
                pass

        return injected

    def lookup_entity(self, book_id, text):
        """划词查询 - 查找实体设定和关系"""
        entries = self.db.get_lorebook_entries(book_id)
        graph = self.db.get_entity_graph(book_id)
        states = self.db.get_world_state(book_id)

        results = {
            'entries': [],
            'relations': [],
            'world_states': []
        }

        text_lower = text.lower().strip()

        for entry in entries:
            if (text_lower in entry.get('name', '').lower() or
                text_lower in entry.get('keywords', '').lower() or
                entry.get('name', '').lower() in text_lower):
                results['entries'].append({
                    'id': entry['id'],
                    'name': entry['name'],
                    'category': entry['category'],
                    'description': entry.get('description', ''),
                    'content': entry.get('content', '')
                })

        for rel in graph:
            if (text_lower in rel.get('source_entity', '').lower() or
                text_lower in rel.get('target_entity', '').lower()):
                results['relations'].append(rel)

        for state in states:
            entity_name = state.get('entity_name', '')
            if (text_lower in entity_name.lower() or
                entity_name.lower() in text_lower):
                results['world_states'].append({
                    'id': state.get('id', ''),
                    'entity_name': entity_name,
                    'state_type': state.get('state_type', ''),
                    'state_value': state.get('state_value', ''),
                    'scene_context': state.get('scene_context', '')
                })

        return results

    def get_memory_status(self, book_id, node_id):
        """获取三层记忆状态摘要"""
        working = self.get_working_memory(node_id)
        summaries = self.get_rolling_summaries(book_id)
        chunk_count = len(self._faiss_chunks.get(book_id, []))
        has_index = book_id in self._faiss_indices

        return {
            'tier1_working': {
                'chars': len(working),
                'active': bool(working)
            },
            'tier2_rolling': {
                'summary_count': len(summaries),
                'active': len(summaries) > 0
            },
            'tier3_vector': {
                'chunk_count': chunk_count,
                'indexed': has_index,
                'has_faiss': HAS_FAISS
            }
        }
