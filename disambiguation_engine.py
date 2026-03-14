"""
角色消歧与共指解析引擎
将多个称呼/代词归一到同一个 character_id
三层消歧：规则匹配 → 上下文打分 → LLM 推断
"""
import uuid
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class DisambiguationEngine:
    def __init__(self, db):
        self.db = db
        self._llm_resolver = None

    def set_llm_resolver(self, callback):
        """注入 LLM 共指消解回调
        签名: callback(mentions, context, candidates) -> list[{mention_text, resolved_to, confidence}]
        """
        self._llm_resolver = callback

    # ------------------------------------------------------------------
    #  Alias table
    # ------------------------------------------------------------------

    def build_alias_table(self, book_id):
        """从 lorebook + 用户反馈构建完整别名表"""
        alias_map = {}  # alias_lower -> [{character_id, character_name, priority, source}]

        conn = self.db._conn()
        # Lorebook 来源
        rows = conn.execute(
            "SELECT * FROM lorebook WHERE book_id=? AND enabled=1 AND category='character'",
            (book_id,)
        ).fetchall()
        for row in rows:
            entry = dict(row)
            char_id = entry['id']
            char_name = entry.get('name', '').strip()
            if not char_name:
                continue

            all_names = [char_name]
            for field in ['aliases', 'keywords']:
                raw = entry.get(field, '') or ''
                all_names.extend([a.strip() for a in raw.split(',') if a.strip()])

            for name in all_names:
                key = name.lower()
                if key not in alias_map:
                    alias_map[key] = []
                alias_map[key].append({
                    'character_id': char_id,
                    'character_name': char_name,
                    'priority': 10 if name == char_name else 5,
                    'source': 'lorebook'
                })

        # 用户反馈来源
        feedbacks = conn.execute(
            'SELECT * FROM disambiguation_feedback WHERE book_id=? ORDER BY created_at DESC',
            (book_id,)
        ).fetchall()
        for fb in feedbacks:
            fb_d = dict(fb)
            key = fb_d['mention_text'].lower()
            if key not in alias_map:
                alias_map[key] = []
            # 用户反馈优先级最高
            alias_map[key].insert(0, {
                'character_id': fb_d['resolved_character_id'],
                'character_name': fb_d['mention_text'],
                'priority': 20,
                'source': 'user_feedback'
            })

        conn.close()
        return alias_map

    # ------------------------------------------------------------------
    #  Resolve
    # ------------------------------------------------------------------

    def resolve_mentions(self, book_id, node_id, entities):
        """三层消歧流程"""
        alias_table = self.build_alias_table(book_id)
        unresolved = []
        results = []

        # 第1层：规则匹配
        for ent in entities:
            mention_text = ent.get('mention_text', ent.get('text', ent.get('entity_text', ''))).strip()
            key = mention_text.lower()

            if key in alias_table:
                candidates = alias_table[key]
                if len(candidates) == 1:
                    # 无歧义，直接绑定
                    results.append({
                        'mention_text': mention_text,
                        'entity': ent,
                        'resolved_character_id': candidates[0]['character_id'],
                        'resolved_character_name': candidates[0]['character_name'],
                        'confidence': 0.95,
                        'method': 'rule'
                    })
                    continue
                else:
                    # 优先选 priority 最高的
                    best = max(candidates, key=lambda c: c['priority'])
                    if best['priority'] >= 20:
                        results.append({
                            'mention_text': mention_text,
                            'entity': ent,
                            'resolved_character_id': best['character_id'],
                            'resolved_character_name': best['character_name'],
                            'confidence': 0.9,
                            'method': 'rule_priority'
                        })
                        continue
                    unresolved.append(ent)
            else:
                unresolved.append(ent)

        # 第2层：上下文共现打分（对 unresolved 做简单启发式）
        still_unresolved = []
        for ent in unresolved:
            mention_text = ent.get('mention_text', ent.get('text', ent.get('entity_text', ''))).strip()
            context = ent.get('context_snippet', '')
            if not context:
                still_unresolved.append(ent)
                continue

            # 遍历所有角色，看谁在上下文中出现最多
            best_id = None
            best_name = None
            best_score = 0
            context_lower = context.lower()
            for alias, candidates in alias_table.items():
                if alias == mention_text.lower():
                    continue
                count = context_lower.count(alias)
                if count > 0:
                    for c in candidates:
                        score = count * c['priority']
                        if score > best_score:
                            best_score = score
                            best_id = c['character_id']
                            best_name = c['character_name']

            if best_id and best_score > 5:
                results.append({
                    'mention_text': mention_text,
                    'entity': ent,
                    'resolved_character_id': best_id,
                    'resolved_character_name': best_name,
                    'confidence': min(0.85, 0.3 + best_score * 0.05),
                    'method': 'context'
                })
            else:
                still_unresolved.append(ent)

        # 第3层：LLM 消解
        if still_unresolved and self._llm_resolver:
            try:
                # 收集候选角色列表
                characters = []
                seen = set()
                for alias, candidates in alias_table.items():
                    for c in candidates:
                        if c['character_id'] not in seen:
                            seen.add(c['character_id'])
                            characters.append({
                                'id': c['character_id'],
                                'name': c['character_name']
                            })

                mentions_for_llm = []
                for ent in still_unresolved:
                    mentions_for_llm.append({
                        'text': ent.get('mention_text', ent.get('text', ent.get('entity_text', ''))),
                        'context': ent.get('context_snippet', '')
                    })

                # 获取章节文本作为额外上下文
                conn = self.db._conn()
                content_row = conn.execute(
                    'SELECT content FROM node_contents WHERE node_id=?', (node_id,)
                ).fetchone()
                chapter_context = content_row['content'][:3000] if content_row else ''
                conn.close()

                llm_results = self._llm_resolver(mentions_for_llm, chapter_context, characters)
                if isinstance(llm_results, list):
                    for lr in llm_results:
                        resolved_name = lr.get('resolved_to', '')
                        # 查找对应 character_id
                        char_id = None
                        for c in characters:
                            if c['name'] == resolved_name:
                                char_id = c['id']
                                break
                        if char_id:
                            results.append({
                                'mention_text': lr.get('mention_text', ''),
                                'entity': {},
                                'resolved_character_id': char_id,
                                'resolved_character_name': resolved_name,
                                'confidence': lr.get('confidence', 0.6),
                                'method': 'llm'
                            })
            except Exception as e:
                logger.error("LLM coreference resolution failed: %s", e)

        # 存入 coreference_links 表
        now = datetime.now().isoformat()
        for r in results:
            link = {
                'id': str(uuid.uuid4())[:12],
                'book_id': book_id,
                'node_id': node_id,
                'mention_id': r.get('entity', {}).get('id', ''),
                'resolved_character_id': r['resolved_character_id'],
                'confidence': r['confidence'],
                'resolution_method': r['method'],
                'created_at': now
            }
            self.db.add_coreference_link(link)

        return results

    # ------------------------------------------------------------------
    #  User feedback
    # ------------------------------------------------------------------

    def add_user_feedback(self, book_id, mention_text, character_id, scope='book', scope_node_id=None):
        """用户纠错"""
        record = {
            'id': str(uuid.uuid4())[:12],
            'book_id': book_id,
            'mention_text': mention_text,
            'resolved_character_id': character_id,
            'scope': scope,
            'scope_node_id': scope_node_id,
            'created_at': datetime.now().isoformat()
        }
        self.db.add_disambiguation_feedback(record)
        return record

    def get_feedbacks(self, book_id):
        """获取已有反馈"""
        return self.db.get_disambiguation_feedbacks(book_id)

    def delete_feedback(self, feedback_id):
        """删除反馈"""
        self.db.delete_disambiguation_feedback(feedback_id)

    def get_disambiguation_stats(self, book_id):
        """消歧统计"""
        conn = self.db._conn()
        total = conn.execute(
            'SELECT COUNT(*) as cnt FROM coreference_links WHERE book_id=?', (book_id,)
        ).fetchone()['cnt']
        by_method = conn.execute(
            'SELECT resolution_method, COUNT(*) as cnt FROM coreference_links WHERE book_id=? GROUP BY resolution_method',
            (book_id,)
        ).fetchall()
        high_conf = conn.execute(
            'SELECT COUNT(*) as cnt FROM coreference_links WHERE book_id=? AND confidence >= 0.8',
            (book_id,)
        ).fetchone()['cnt']
        low_conf = conn.execute(
            'SELECT COUNT(*) as cnt FROM coreference_links WHERE book_id=? AND confidence < 0.5',
            (book_id,)
        ).fetchone()['cnt']
        conn.close()

        methods = {dict(r)['resolution_method']: dict(r)['cnt'] for r in by_method}
        return {
            'total_mentions': total,
            'high_confidence': high_conf,
            'low_confidence': low_conf,
            'by_method': methods
        }
