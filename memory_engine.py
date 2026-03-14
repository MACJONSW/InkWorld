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
import logging
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 可选 FAISS 支持
try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

# 可选 jieba 中文分词
try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

# 可选 BM25 检索
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False

logger = logging.getLogger(__name__)


class MemoryEngine:
    """三层记忆引擎"""

    def __init__(self, db):
        self.db = db
        self._faiss_indices = {}  # book_id -> faiss.IndexFlatIP
        self._faiss_chunks = {}   # book_id -> list of chunk dicts
        self._bm25_indices = {}   # book_id -> BM25Okapi
        self._faiss_dim = 512     # TF-IDF 降维维度（从 128 提升到 512）
        self._vectorizer = None
        self._chunk_size = 500    # 向量切片字符数
        self._chunk_overlap = 100 # 切片重叠
        self._summarizer_callback = None  # (book_id, node_id, chapter_title, text) -> summary_text
        self._jieba_user_dict_loaded = {}  # book_id -> bool
        self._embedding_engine = None
        self._disambiguation_engine = None

    def set_summarizer_callback(self, callback):
        """注入摘要生成回调，签名: callback(book_id, node_id, chapter_title, text) -> summary_text"""
        self._summarizer_callback = callback

    def set_embedding_engine(self, engine):
        """注入 embedding 引擎"""
        self._embedding_engine = engine

    def set_disambiguation_engine(self, engine):
        """注入消歧引擎"""
        self._disambiguation_engine = engine

    # ------ jieba 动态词典 ------

    def _ensure_jieba_dict(self, book_id):
        """从 Lorebook 动态构建 jieba 自定义词典"""
        if not HAS_JIEBA or self._jieba_user_dict_loaded.get(book_id):
            return
        for entry in self._character_entries(book_id):
            for term in self._character_terms(entry):
                if len(term) >= 2:
                    jieba.add_word(term, freq=99999, tag='nr')
        self._jieba_user_dict_loaded[book_id] = True

    def _tokenize(self, text):
        """使用 jieba 分词（如可用），否则回退到字符级切分"""
        if HAS_JIEBA:
            return list(jieba.cut(text))
        return list(text)

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
        """使用 TF-IDF 为文本列表构建向量（支持 jieba 分词）"""
        if not texts:
            return np.array([])
        if HAS_JIEBA:
            # jieba 分词预处理，空格拼接后由 TfidfVectorizer 按空格 tokenize
            tokenized = [' '.join(jieba.cut(t)) for t in texts]
            vectorizer = TfidfVectorizer(
                token_pattern=r'(?u)\b\w+\b',
                max_features=self._faiss_dim,
                sublinear_tf=True
            )
            tfidf_matrix = vectorizer.fit_transform(tokenized)
        else:
            vectorizer = TfidfVectorizer(
                token_pattern=r'(?u)\b\w+\b',
                max_features=self._faiss_dim,
                sublinear_tf=True
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
        self._vectorizer = vectorizer
        return tfidf_matrix.toarray().astype('float32')

    def vectorize_book(self, book_id):
        """为整本书建立向量索引（Tier 3 构建 + BM25）"""
        self._ensure_jieba_dict(book_id)

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

        # BM25 索引
        if HAS_BM25:
            tokenized_corpus = [self._tokenize(t) for t in texts]
            self._bm25_indices[book_id] = BM25Okapi(tokenized_corpus)

        return {'status': 'ok', 'chunk_count': len(all_chunks), 'has_faiss': HAS_FAISS, 'has_bm25': HAS_BM25}

    def incremental_update_index(self, book_id, node_id):
        """Update the vector index for a single node without full rebuild"""
        if book_id not in self._faiss_chunks:
            # No existing index, do full build
            return self.vectorize_book(book_id)

        # Remove old chunks for this node
        chunks = self._faiss_chunks.get(book_id, [])
        chunks = [c for c in chunks if c.get('source_id') != node_id]

        # Add new chunks for this node
        content_data = self.db.get_node_content(node_id)
        content = content_data.get('content', '') if content_data else ''
        node = self.db.get_node(node_id)
        if content:
            for chunk in self._chunk_text(content):
                chunks.append({
                    'text': chunk,
                    'source': 'content',
                    'source_id': node_id,
                    'name': node.get('title', '') if node else '',
                    'category': 'chapter'
                })

        self._faiss_chunks[book_id] = chunks

        # Rebuild vectors
        if chunks:
            texts = [c['text'] for c in chunks]
            vectors = self._build_tfidf_vectors(texts)
            if HAS_FAISS and len(vectors) > 0:
                dim = vectors.shape[1]
                index = faiss.IndexFlatIP(dim)
                faiss.normalize_L2(vectors)
                index.add(vectors)
                self._faiss_indices[book_id] = index
            else:
                self._faiss_indices[book_id] = vectors
            if HAS_BM25:
                tokenized_corpus = [self._tokenize(t) for t in texts]
                self._bm25_indices[book_id] = BM25Okapi(tokenized_corpus)

        return {'status': 'ok', 'chunk_count': len(chunks)}

    def vector_retrieve(self, book_id, query, top_k=5, user_id=None):
        """混合检索：优先 embedding，降级到 TF-IDF/FAISS + BM25（RRF 融合排序），失败时回退到关键词匹配"""
        # 优先使用 embedding 检索
        if self._embedding_engine and user_id and self._embedding_engine.has_index(book_id):
            try:
                results = self._embedding_engine.retrieve(book_id, user_id, query, top_k)
                if results:
                    return results
            except Exception as e:
                logger.warning("Embedding retrieval failed, falling back to TF-IDF: %s", e)

        return self._tfidf_retrieve(book_id, query, top_k)

    def _tfidf_retrieve(self, book_id, query, top_k=5):
        """TF-IDF + FAISS + BM25 混合检索"""
        if book_id not in self._faiss_chunks or not self._faiss_chunks[book_id]:
            self.vectorize_book(book_id)

        chunks = self._faiss_chunks.get(book_id, [])
        if not chunks:
            return []

        try:
            # ---- TF-IDF 向量检索 ----
            texts = [c['text'] for c in chunks]
            texts.append(query)
            vectors = self._build_tfidf_vectors(texts)

            tfidf_ranking = {}  # idx -> rank (0-based)
            if len(vectors) > 0:
                query_vec = vectors[-1:].astype('float32')
                doc_vecs = vectors[:-1].astype('float32')

                if HAS_FAISS and book_id in self._faiss_indices and isinstance(self._faiss_indices[book_id], faiss.Index):
                    index = self._faiss_indices[book_id]
                    faiss.normalize_L2(query_vec)
                    n = min(top_k * 3, len(chunks))
                    scores, indices = index.search(query_vec, n)
                    for rank, (score, idx) in enumerate(zip(scores[0], indices[0])):
                        if 0 <= idx < len(chunks) and score > 0.01:
                            tfidf_ranking[int(idx)] = rank
                else:
                    from sklearn.metrics.pairwise import cosine_similarity as cos_sim
                    sims = cos_sim(query_vec, doc_vecs)[0]
                    sorted_indices = np.argsort(sims)[::-1][:top_k * 3]
                    for rank, idx in enumerate(sorted_indices):
                        if sims[idx] > 0.05:
                            tfidf_ranking[int(idx)] = rank

            # ---- BM25 检索 ----
            bm25_ranking = {}  # idx -> rank
            if HAS_BM25 and book_id in self._bm25_indices:
                query_tokens = self._tokenize(query)
                bm25_scores = self._bm25_indices[book_id].get_scores(query_tokens)
                sorted_bm25 = np.argsort(bm25_scores)[::-1][:top_k * 3]
                for rank, idx in enumerate(sorted_bm25):
                    if bm25_scores[idx] > 0:
                        bm25_ranking[int(idx)] = rank

            # ---- RRF 融合 ----
            k = 60  # RRF 常数
            all_idx = set(tfidf_ranking.keys()) | set(bm25_ranking.keys())
            rrf_scores = {}
            for idx in all_idx:
                score = 0.0
                if idx in tfidf_ranking:
                    score += 1.0 / (k + tfidf_ranking[idx])
                if idx in bm25_ranking:
                    score += 1.0 / (k + bm25_ranking[idx])
                rrf_scores[idx] = score

            sorted_results = sorted(rrf_scores.items(), key=lambda x: -x[1])[:top_k]
            results = []
            for idx, score in sorted_results:
                r = dict(chunks[idx])
                r['score'] = round(score, 4)
                results.append(r)
            return results

        except Exception as e:
            logger.warning("Vector retrieval failed, falling back to keyword matching: %s", e)
            return self._keyword_fallback_retrieve(chunks, query, top_k)

    def _keyword_fallback_retrieve(self, chunks, query, top_k=5):
        """Fallback keyword matching when vector retrieval fails"""
        query_lower = query.lower()
        # Extract keywords from query (split on whitespace and punctuation)
        query_terms = [t.strip() for t in re.split(r'[\s，。、！？,.!?\-;；：:]+', query_lower) if t.strip() and len(t.strip()) >= 2]
        if not query_terms:
            return []

        scored = []
        for idx, chunk in enumerate(chunks):
            chunk_lower = chunk['text'].lower()
            match_count = 0
            for term in query_terms:
                match_count += chunk_lower.count(term)
            if match_count > 0:
                scored.append((idx, match_count))

        scored.sort(key=lambda x: -x[1])
        results = []
        for idx, match_count in scored[:top_k]:
            r = dict(chunks[idx])
            r['score'] = round(match_count / max(len(query_terms), 1), 4)
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
        result = self._build_context_impl(book_id, current_node_id, max_chars=max_chars, track=False)
        return result['context_text']

    def build_context_window_with_log(self, book_id, current_node_id, max_tokens=6000):
        """Three-tier memory fusion with structured injection log"""
        return self._build_context_impl(book_id, current_node_id, max_chars=max_tokens, track=True)

    def _build_context_impl(self, book_id, current_node_id, max_chars=6000, track=False):
        """Internal implementation for context window building with optional injection tracking."""
        context_parts = []
        injected_items = []
        candidate_items = []

        # Tier 0: 全局设定（常驻）
        entries = self.db.get_lorebook_entries(book_id)
        setting_text = ""
        for e in entries:
            if e.get('enabled'):
                entry_text = f"【{e['category']}-{e['name']}】{e['content']}\n"
                setting_text += entry_text
                if track:
                    injected_items.append({
                        'type': 'lorebook',
                        'source': e.get('id', ''),
                        'content_preview': entry_text[:200],
                        'reason': 'match_keyword',
                        'source_chapter': '',
                        'tier': 0
                    })
        if setting_text:
            context_parts.append(f"=== 世界观设定 ===\n{setting_text[:2000]}")

        # Tier 2: 滚动摘要
        summaries = self.get_rolling_summaries(book_id, limit=10)
        if summaries:
            summary_text = "\n".join([
                f"[{s['chapter_title']}] {s['summary']}" for s in summaries
            ])
            context_parts.append(f"=== 前情摘要(Tier2) ===\n{summary_text[:2000]}")
            if track:
                for s in summaries:
                    item_text = f"[{s['chapter_title']}] {s['summary']}"
                    injected_items.append({
                        'type': 'summary',
                        'source': s.get('id', ''),
                        'content_preview': item_text[:200],
                        'reason': 'rolling_summary',
                        'source_chapter': s.get('chapter_title', ''),
                        'tier': 2
                    })

        # Tier 1: 工作记忆
        working = self.get_working_memory(current_node_id, max_chars=3000)
        if working:
            context_parts.append(f"=== 工作记忆(Tier1) ===\n{working}")
            if track:
                injected_items.append({
                    'type': 'working',
                    'source': current_node_id or '',
                    'content_preview': working[:200],
                    'reason': 'current_editing_context',
                    'source_chapter': '',
                    'tier': 1
                })

            reminder_text = self.build_character_reminder_context(
                book_id,
                text=working[-1500:],
                node_id=current_node_id,
                max_characters=4
            )
            if reminder_text:
                context_parts.append(reminder_text)
                if track:
                    injected_items.append({
                        'type': 'character_reminder',
                        'source': current_node_id or '',
                        'content_preview': reminder_text[:200],
                        'reason': 'character_mention_in_working_memory',
                        'source_chapter': '',
                        'tier': 1
                    })

            # Tier 3: 向量RAG检索（基于工作记忆的相关片段）
            rag_results = self.vector_retrieve(book_id, working[-500:], top_k=3)
            if rag_results:
                rag_text = "\n".join([
                    f"[{r['source']}/{r['name']}](相关度{r['score']}): {r['text'][:200]}"
                    for r in rag_results
                ])
                context_parts.append(f"=== 向量检索(Tier3) ===\n{rag_text}")
                if track:
                    for r in rag_results:
                        injected_items.append({
                            'type': 'vector',
                            'source': r.get('source_id', ''),
                            'content_preview': r['text'][:200],
                            'reason': 'vector_similarity',
                            'source_chapter': r.get('name', ''),
                            'tier': 3
                        })

            # Track candidate items from vector search that were not included
            if track:
                all_rag = self.vector_retrieve(book_id, working[-500:], top_k=10)
                included_sources = {r.get('source_id', '') for r in rag_results}
                for r in all_rag:
                    if r.get('source_id', '') not in included_sources:
                        candidate_items.append({
                            'type': 'vector',
                            'source': r.get('source_id', ''),
                            'content_preview': r['text'][:200],
                            'reason': 'vector_similarity_below_cutoff',
                            'source_chapter': r.get('name', ''),
                            'tier': 3,
                            'score': r.get('score', 0)
                        })

        return {
            'context_text': "\n\n".join(context_parts),
            'injected_items': injected_items,
            'candidate_items': candidate_items
        }

    def _character_entries(self, book_id):
        entries = self.db.get_lorebook_entries(book_id)
        return [e for e in entries if e.get('category') == 'character' and e.get('enabled', True)]

    def get_pinned_memories(self, book_id):
        """Get all pinned/excluded memory directives"""
        return self.db.get_pinned_memories(book_id)

    def _apply_pin_exclude(self, book_id, items, memory_type):
        """Filter items based on pin/exclude directives.
        Pinned items are always included, excluded items are removed."""
        pins = self.get_pinned_memories(book_id)
        pin_refs = {p['memory_ref'] for p in pins if p['action'] == 'pin' and p['memory_type'] == memory_type}
        exclude_refs = {p['memory_ref'] for p in pins if p['action'] == 'exclude' and p['memory_type'] == memory_type}

        result = []
        pinned = []
        for item in items:
            ref = item.get('id', '') or item.get('name', '')
            if ref in exclude_refs:
                continue
            if ref in pin_refs:
                pinned.append(item)
            else:
                result.append(item)
        return pinned + result

    def _character_terms(self, entry):
        terms = []
        name = (entry.get('name') or '').strip()
        if name:
            terms.append(name)
        # Add aliases
        aliases = (entry.get('aliases') or '').strip()
        if aliases:
            for alias in aliases.split(','):
                alias = alias.strip()
                if alias and alias not in terms:
                    terms.append(alias)
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

        self._ensure_jieba_dict(book_id)

        # jieba 分词匹配：精确词边界
        if HAS_JIEBA:
            tokens = list(jieba.cut(text))
            tokens_lower = [t.lower() for t in tokens]
            token_set = set(tokens_lower)

        lowered = text.lower()
        mentions = []
        for entry in self._character_entries(book_id):
            terms = self._character_terms(entry)

            # Parse keyword_weights for confidence calculation
            keyword_weights = {}
            kw_weights_raw = entry.get('keyword_weights', '') or ''
            if kw_weights_raw:
                try:
                    keyword_weights = json.loads(kw_weights_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            count = 0
            first_index = None
            matched_terms = []
            weighted_score = 0.0
            for term in terms:
                if not term:
                    continue
                term_lower = term.lower()
                if HAS_JIEBA:
                    # jieba 级别精确匹配（分词结果中的完整 token）
                    hit_count = tokens_lower.count(term_lower)
                    if hit_count <= 0:
                        # 回退：子串匹配（多字词可能被分词器拆开）
                        hit_count = lowered.count(term_lower)
                else:
                    hit_count = lowered.count(term_lower)
                if hit_count <= 0:
                    continue
                count += hit_count
                weight = keyword_weights.get(term, 1.0)
                weighted_score += hit_count * weight
                idx = lowered.find(term_lower)
                if first_index is None or idx < first_index:
                    first_index = idx
                matched_terms.append(term)

            if count > 0:
                # Calculate confidence based on weighted score and co-occurrence
                # Co-occurrence bonus: matching multiple distinct terms increases confidence
                co_occurrence_bonus = min(len(matched_terms) / max(len(terms), 1), 1.0)
                # Normalize weighted_score: cap at a reasonable level
                raw_confidence = weighted_score / max(len(terms), 1)
                confidence = min(raw_confidence * (1.0 + co_occurrence_bonus * 0.5), 1.0)

                mentions.append({
                    'name': entry.get('name', ''),
                    'entry': entry,
                    'count': count,
                    'first_index': first_index if first_index is not None else len(text),
                    'matched_terms': matched_terms,
                    'confidence': round(confidence, 4)
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

    def _build_history_details(self, chapter_title, summary_payload, source_excerpt, foreshadow_labels, character_name=''):
        details = []
        summary_text = summary_payload.get('summary', '')
        if summary_text:
            details.append(summary_text)
        key_events = summary_payload.get('key_events', '')
        if isinstance(key_events, list):
            key_events = '；'.join([str(item) for item in key_events if item])
        if key_events:
            details.append(f"关键事件：{self._short_text(str(key_events), 180)}")

        # P2: 结构化角色状态解析
        character_states = summary_payload.get('character_states', '')
        if isinstance(character_states, list) and character_name:
            # 从结构化数组中提取当前角色的状态
            for cs in character_states:
                if not isinstance(cs, dict):
                    continue
                cs_name = (cs.get('name') or '').strip()
                if cs_name.lower() != character_name.lower():
                    continue
                state_parts = []
                if cs.get('emotion'):
                    state_parts.append(f"情绪：{cs['emotion']}")
                if cs.get('goal'):
                    state_parts.append(f"目标：{cs['goal']}")
                rel_changes = cs.get('relationship_changes', [])
                if isinstance(rel_changes, list):
                    for rc in rel_changes[:3]:
                        if isinstance(rc, dict) and rc.get('target'):
                            state_parts.append(
                                f"与{rc['target']}的{rc.get('relation', '关系')}：{rc.get('change', '变化')}"
                            )
                if state_parts:
                    details.append(f"角色状态：{'；'.join(state_parts)}")
                break
        elif character_states:
            # 旧格式回退
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

        # P0: 无摘要时自动触发摘要生成
        summary_payload = self._parse_summary_payload(summary)
        if not summary_payload.get('summary') and self._summarizer_callback and len(content) >= 200:
            try:
                auto_summary = self._summarizer_callback(book_id, node_id, chapter_title, content)
                if auto_summary:
                    summary_payload = self._parse_summary_payload(auto_summary)
                    logger.info("Auto-generated summary for node %s", node_id)
            except Exception as e:
                logger.warning("Auto-summary failed for node %s: %s", node_id, e)

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

            details = self._build_history_details(chapter_title, summary_payload, source_excerpt, foreshadow_labels[:3], character_name=name)
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

    def save_injection_log(self, book_id, node_id, agent_role, injected_items, candidate_items=None):
        """Save injection log to database for explainability panel"""
        import json as _json
        self.db.add_memory_injection_log({
            'book_id': book_id,
            'node_id': node_id,
            'agent_role': agent_role,
            'injected_items': _json.dumps(injected_items, ensure_ascii=False),
            'candidate_items': _json.dumps(candidate_items or [], ensure_ascii=False)
        })
