"""
NER 实体识别引擎 - 基于 LLM 的中文小说实体抽取
支持人物、地点、组织、物品、概念的识别，并与 Lorebook 条目关联
"""
import uuid
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class NEREngine:
    def __init__(self, db):
        self.db = db
        self._llm_extractor = None

    def set_llm_extractor(self, callback):
        """注入 LLM 实体抽取回调
        签名: callback(text, known_entities) -> list[{text, type, start, end, confidence}]
        """
        self._llm_extractor = callback

    # ------------------------------------------------------------------
    #  Known entities
    # ------------------------------------------------------------------

    def _build_known_entities(self, book_id):
        """从 lorebook 构建已知实体字典"""
        conn = self.db._conn()
        rows = conn.execute('SELECT * FROM lorebook WHERE book_id=? AND enabled=1', (book_id,)).fetchall()
        conn.close()
        known = {}
        for row in rows:
            entry = dict(row)
            name = entry.get('name', '').strip()
            if not name:
                continue
            aliases_raw = entry.get('aliases', '') or ''
            keywords_raw = entry.get('keywords', '') or ''
            aliases = [a.strip() for a in aliases_raw.split(',') if a.strip()]
            kw = [k.strip() for k in keywords_raw.split(',') if k.strip()]
            all_names = [name] + aliases + kw
            known[name] = {
                'id': entry['id'],
                'type': entry.get('category', 'character'),
                'aliases': all_names,
                'description': entry.get('description', '')
            }
        return known

    def _link_to_known(self, entities, known):
        """将抽取结果与已知实体匹配"""
        known_flat = {}  # alias -> {lorebook_id, entity_name, type}
        for name, info in known.items():
            for alias in info['aliases']:
                alias_lower = alias.lower()
                known_flat[alias_lower] = {
                    'lorebook_id': info['id'],
                    'entity_name': name,
                    'type': info['type']
                }

        for entity in entities:
            entity_text = entity.get('text', '').strip().lower()
            # 精确匹配
            if entity_text in known_flat:
                match = known_flat[entity_text]
                entity['linked_lorebook_id'] = match['lorebook_id']
                entity['link_confidence'] = 1.0
                continue
            # 子串匹配
            best_match = None
            best_score = 0
            for alias, info in known_flat.items():
                if alias in entity_text or entity_text in alias:
                    score = min(len(alias), len(entity_text)) / max(len(alias), len(entity_text))
                    if score > best_score and score > 0.5:
                        best_score = score
                        best_match = info
            if best_match:
                entity['linked_lorebook_id'] = best_match['lorebook_id']
                entity['link_confidence'] = round(best_score, 2)

        return entities

    # ------------------------------------------------------------------
    #  Extract
    # ------------------------------------------------------------------

    def extract_entities(self, book_id, node_id, text):
        """完整 NER 流程"""
        if not text or not text.strip():
            return []

        known = self._build_known_entities(book_id)

        # 先删除旧结果
        self.db.delete_entities_for_node(book_id, node_id)

        entities = []
        if self._llm_extractor:
            try:
                known_list = []
                for name, info in known.items():
                    known_list.append({
                        'name': name,
                        'type': info['type'],
                        'aliases': info['aliases']
                    })
                raw = self._llm_extractor(text, known_list)
                if isinstance(raw, list):
                    entities = raw
            except Exception as e:
                logger.error("NER LLM extraction failed: %s", e)

        # 链接到已知实体
        entities = self._link_to_known(entities, known)

        # 存入数据库
        now = datetime.now().isoformat()
        saved = []
        for ent in entities:
            eid = str(uuid.uuid4())[:12]
            record = {
                'id': eid,
                'book_id': book_id,
                'node_id': node_id,
                'entity_text': ent.get('text', ''),
                'entity_type': ent.get('type', 'character'),
                'start_pos': ent.get('start'),
                'end_pos': ent.get('end'),
                'confidence': ent.get('confidence', 0.5),
                'source_type': 'auto',
                'status': 'pending',
                'linked_lorebook_id': ent.get('linked_lorebook_id'),
                'link_confidence': ent.get('link_confidence', 0.0),
                'created_at': now
            }
            self.db.add_extracted_entity(record)
            saved.append(record)

            # 记录提及
            mention = {
                'id': str(uuid.uuid4())[:12],
                'book_id': book_id,
                'entity_id': eid,
                'node_id': node_id,
                'mention_text': ent.get('text', ''),
                'start_pos': ent.get('start'),
                'end_pos': ent.get('end'),
                'context_snippet': self._extract_context(text, ent.get('start'), ent.get('end')),
                'mention_type': 'name',
                'created_at': now
            }
            self.db.add_entity_mention(mention)

        return saved

    def extract_entities_batch(self, book_id, node_ids):
        """批量 NER"""
        results = {}
        conn = self.db._conn()
        for nid in node_ids:
            row = conn.execute('SELECT content FROM node_contents WHERE node_id=?', (nid,)).fetchone()
            if row and row['content']:
                results[nid] = self.extract_entities(book_id, nid, row['content'])
        conn.close()
        return results

    def _extract_context(self, text, start, end, window=50):
        """提取上下文片段"""
        if start is None or end is None:
            return ''
        s = max(0, start - window)
        e = min(len(text), end + window)
        return text[s:e]

    # ------------------------------------------------------------------
    #  Entity management
    # ------------------------------------------------------------------

    def link_entity(self, entity_id, lorebook_id):
        """用户手动绑定实体到 lorebook"""
        self.db.link_entity_to_lorebook(entity_id, lorebook_id)

    def confirm_entity(self, entity_id):
        """确认实体"""
        self.db.update_entity_status(entity_id, 'confirmed')

    def dismiss_entity(self, entity_id):
        """忽略实体"""
        self.db.update_entity_status(entity_id, 'dismissed')

    def get_entities(self, book_id, node_id=None, entity_type=None, status=None):
        """获取实体列表"""
        return self.db.get_extracted_entities(book_id, node_id=node_id,
                                              entity_type=entity_type, status=status)

    def get_unlinked_entities(self, book_id):
        """获取未关联实体"""
        return self.db.get_unlinked_entities(book_id)

    def suggest_lorebook_entry(self, book_id, entity_id):
        """为未关联实体生成 lorebook 条目建议"""
        entities = self.db.get_extracted_entities(book_id)
        target = None
        for e in entities:
            if e['id'] == entity_id:
                target = e
                break
        if not target:
            return None

        # 收集该实体的所有提及上下文
        mentions = self.db.get_entity_mentions(book_id, entity_id=entity_id)
        contexts = [m.get('context_snippet', '') for m in mentions if m.get('context_snippet')]

        return {
            'name': target['entity_text'],
            'category': target['entity_type'],
            'description': '',
            'keywords': target['entity_text'],
            'content': '\n'.join(contexts[:5])
        }
