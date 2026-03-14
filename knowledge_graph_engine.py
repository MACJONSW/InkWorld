"""
知识图谱增强引擎
从文本中自动抽取结构化关系和事件，构建可查询的知识图谱
"""
import uuid
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class KnowledgeGraphEngine:
    def __init__(self, db):
        self.db = db
        self._relation_extractor = None
        self._event_extractor = None

    def set_relation_extractor(self, callback):
        """签名: callback(text, known_entities) -> list[{source, target, relation_type, relation_detail, evidence_text, confidence}]"""
        self._relation_extractor = callback

    def set_event_extractor(self, callback):
        """签名: callback(text, known_entities) -> list[{actor, target, action, location, story_time, participants, consequences, significance}]"""
        self._event_extractor = callback

    # ------------------------------------------------------------------
    #  Known entities helper
    # ------------------------------------------------------------------

    def _get_known_entities(self, book_id):
        """从 lorebook 获取已知实体"""
        conn = self.db._conn()
        rows = conn.execute('SELECT * FROM lorebook WHERE book_id=? AND enabled=1', (book_id,)).fetchall()
        conn.close()
        entities = []
        for row in rows:
            d = dict(row)
            entities.append({
                'name': d.get('name', ''),
                'type': d.get('category', 'character'),
                'aliases': [a.strip() for a in (d.get('aliases', '') or '').split(',') if a.strip()]
            })
        return entities

    # ------------------------------------------------------------------
    #  Node management
    # ------------------------------------------------------------------

    def _find_or_create_node(self, book_id, entity_name, entity_type, conn):
        """查找或创建知识图谱节点"""
        row = conn.execute(
            'SELECT * FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, entity_name)
        ).fetchone()
        if row:
            node = dict(row)
            conn.execute(
                'UPDATE knowledge_nodes SET mention_count=mention_count+1, updated_at=? WHERE id=?',
                (datetime.now().isoformat(), node['id'])
            )
            return node['id']

        # 尝试查找 lorebook 关联
        lorebook_id = None
        lb_row = conn.execute(
            "SELECT id FROM lorebook WHERE book_id=? AND name=? AND enabled=1",
            (book_id, entity_name)
        ).fetchone()
        if lb_row:
            lorebook_id = lb_row['id']

        nid = str(uuid.uuid4())[:12]
        now = datetime.now().isoformat()
        conn.execute(
            '''INSERT INTO knowledge_nodes (id, book_id, entity_name, entity_type,
               linked_lorebook_id, mention_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)''',
            (nid, book_id, entity_name, entity_type, lorebook_id, now, now)
        )
        return nid

    # ------------------------------------------------------------------
    #  Extract
    # ------------------------------------------------------------------

    def extract_from_chapter(self, book_id, node_id, text):
        """从章节文本抽取关系+事件"""
        if not text or not text.strip():
            return {'relations': [], 'events': []}

        known = self._get_known_entities(book_id)
        new_relations = []
        new_events = []
        conn = self.db._conn()

        # 抽取关系
        if self._relation_extractor:
            try:
                rels = self._relation_extractor(text, known)
                if isinstance(rels, list):
                    for rel in rels:
                        source_name = rel.get('source', '').strip()
                        target_name = rel.get('target', '').strip()
                        if not source_name or not target_name:
                            continue

                        source_id = self._find_or_create_node(book_id, source_name, 'character', conn)
                        target_id = self._find_or_create_node(book_id, target_name, 'character', conn)

                        # 检查是否已存在相同关系
                        existing = conn.execute(
                            '''SELECT id FROM knowledge_edges WHERE book_id=?
                               AND source_node_id=? AND target_node_id=? AND relation_type=?''',
                            (book_id, source_id, target_id, rel.get('relation_type', ''))
                        ).fetchone()
                        if existing:
                            continue

                        eid = str(uuid.uuid4())[:12]
                        now = datetime.now().isoformat()
                        conn.execute(
                            '''INSERT INTO knowledge_edges (id, book_id, source_node_id, target_node_id,
                               relation_type, relation_detail, evidence_text, evidence_node_id,
                               confidence, status, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto', ?)''',
                            (eid, book_id, source_id, target_id,
                             rel.get('relation_type', ''),
                             rel.get('relation_detail', ''),
                             rel.get('evidence_text', '')[:500],
                             node_id,
                             rel.get('confidence', 0.5),
                             now)
                        )
                        new_relations.append({
                            'id': eid,
                            'source': source_name,
                            'target': target_name,
                            'relation_type': rel.get('relation_type', ''),
                            'relation_detail': rel.get('relation_detail', '')
                        })
            except Exception as e:
                logger.error("Relation extraction failed: %s", e)

        # 抽取事件
        if self._event_extractor:
            try:
                events = self._event_extractor(text, known)
                if isinstance(events, list):
                    for evt in events:
                        actor_name = evt.get('actor', '').strip()
                        if not actor_name:
                            continue

                        actor_id = self._find_or_create_node(book_id, actor_name, 'character', conn)
                        target_id = None
                        target_name = evt.get('target', '').strip()
                        if target_name:
                            target_id = self._find_or_create_node(book_id, target_name, 'character', conn)

                        location_id = None
                        location_name = evt.get('location', '').strip()
                        if location_name:
                            location_id = self._find_or_create_node(book_id, location_name, 'location', conn)

                        # 获取章节序号
                        node_row = conn.execute(
                            'SELECT sort_order FROM nodes WHERE id=?', (node_id,)
                        ).fetchone()
                        chapter_index = node_row['sort_order'] if node_row else 0

                        event_id = str(uuid.uuid4())[:12]
                        now = datetime.now().isoformat()
                        conn.execute(
                            '''INSERT INTO story_events (id, book_id, node_id, actor_node_id,
                               action, target_node_id, location_node_id, story_time,
                               significance, consequences, participants, evidence_text,
                               chapter_index, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (event_id, book_id, node_id, actor_id,
                             evt.get('action', ''),
                             target_id, location_id,
                             evt.get('story_time', ''),
                             evt.get('significance', 3),
                             json.dumps(evt.get('consequences', []), ensure_ascii=False),
                             json.dumps(evt.get('participants', []), ensure_ascii=False),
                             evt.get('evidence_text', '')[:500] if evt.get('evidence_text') else '',
                             chapter_index, now)
                        )
                        new_events.append({
                            'id': event_id,
                            'actor': actor_name,
                            'action': evt.get('action', ''),
                            'target': target_name,
                            'location': location_name
                        })
            except Exception as e:
                logger.error("Event extraction failed: %s", e)

        conn.commit()
        conn.close()
        return {'relations': new_relations, 'events': new_events}

    def extract_from_summary(self, book_id, node_id, summary_text):
        """从摘要文本抽取"""
        return self.extract_from_chapter(book_id, node_id, summary_text)

    # ------------------------------------------------------------------
    #  Query
    # ------------------------------------------------------------------

    def query_entity(self, book_id, entity_name):
        """查询实体全部关系和事件"""
        conn = self.db._conn()
        node = conn.execute(
            'SELECT * FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, entity_name)
        ).fetchone()
        if not node:
            conn.close()
            return None

        node_d = dict(node)
        node_id = node_d['id']

        # 获取所有关系
        outgoing = conn.execute(
            '''SELECT e.*, kn.entity_name as target_name FROM knowledge_edges e
               JOIN knowledge_nodes kn ON e.target_node_id=kn.id
               WHERE e.book_id=? AND e.source_node_id=? AND e.status != 'dismissed' ''',
            (book_id, node_id)
        ).fetchall()
        incoming = conn.execute(
            '''SELECT e.*, kn.entity_name as source_name FROM knowledge_edges e
               JOIN knowledge_nodes kn ON e.source_node_id=kn.id
               WHERE e.book_id=? AND e.target_node_id=? AND e.status != 'dismissed' ''',
            (book_id, node_id)
        ).fetchall()

        # 获取事件
        events = conn.execute(
            '''SELECT * FROM story_events WHERE book_id=? AND (actor_node_id=? OR target_node_id=?)
               ORDER BY chapter_index''',
            (book_id, node_id, node_id)
        ).fetchall()
        conn.close()

        return {
            'node': node_d,
            'outgoing_relations': [dict(r) for r in outgoing],
            'incoming_relations': [dict(r) for r in incoming],
            'events': [dict(e) for e in events]
        }

    def query_relation_evolution(self, book_id, entity_a, entity_b):
        """查询两实体间关系演变"""
        conn = self.db._conn()
        node_a = conn.execute(
            'SELECT id FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, entity_a)
        ).fetchone()
        node_b = conn.execute(
            'SELECT id FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, entity_b)
        ).fetchone()
        if not node_a or not node_b:
            conn.close()
            return []

        edges = conn.execute(
            '''SELECT * FROM knowledge_edges WHERE book_id=?
               AND ((source_node_id=? AND target_node_id=?) OR (source_node_id=? AND target_node_id=?))
               ORDER BY created_at''',
            (book_id, node_a['id'], node_b['id'], node_b['id'], node_a['id'])
        ).fetchall()
        conn.close()
        return [dict(e) for e in edges]

    def query_location_events(self, book_id, location_name):
        """查询某地点发生的所有事件"""
        conn = self.db._conn()
        loc_node = conn.execute(
            'SELECT id FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, location_name)
        ).fetchone()
        if not loc_node:
            conn.close()
            return []

        events = conn.execute(
            'SELECT * FROM story_events WHERE book_id=? AND location_node_id=? ORDER BY chapter_index',
            (book_id, loc_node['id'])
        ).fetchall()
        conn.close()
        return [dict(e) for e in events]

    # ------------------------------------------------------------------
    #  Graph data for visualization
    # ------------------------------------------------------------------

    def get_graph_data(self, book_id, center_entity=None, depth=2):
        """获取力导向图渲染数据"""
        conn = self.db._conn()

        if center_entity:
            # 以某实体为中心，获取 depth 层子图
            center = conn.execute(
                'SELECT id FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
                (book_id, center_entity)
            ).fetchone()
            if not center:
                conn.close()
                return {'nodes': [], 'edges': []}

            node_ids = {center['id']}
            for _ in range(depth):
                if not node_ids:
                    break
                placeholders = ','.join('?' * len(node_ids))
                edges = conn.execute(
                    f'''SELECT source_node_id, target_node_id FROM knowledge_edges
                        WHERE book_id=? AND status != 'dismissed'
                        AND (source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders}))''',
                    [book_id] + list(node_ids) + list(node_ids)
                ).fetchall()
                for e in edges:
                    node_ids.add(e['source_node_id'])
                    node_ids.add(e['target_node_id'])

            placeholders = ','.join('?' * len(node_ids))
            nodes = conn.execute(
                f'SELECT * FROM knowledge_nodes WHERE id IN ({placeholders})',
                list(node_ids)
            ).fetchall()
            edges = conn.execute(
                f'''SELECT * FROM knowledge_edges WHERE book_id=? AND status != 'dismissed'
                    AND source_node_id IN ({placeholders}) AND target_node_id IN ({placeholders})''',
                [book_id] + list(node_ids) + list(node_ids)
            ).fetchall()
        else:
            nodes = conn.execute(
                'SELECT * FROM knowledge_nodes WHERE book_id=?', (book_id,)
            ).fetchall()
            edges = conn.execute(
                "SELECT * FROM knowledge_edges WHERE book_id=? AND status != 'dismissed'",
                (book_id,)
            ).fetchall()

        conn.close()

        type_colors = {
            'character': '#4e79a7', 'location': '#59a14f', 'faction': '#e15759',
            'item': '#f28e2b', 'concept': '#b07aa1'
        }

        graph_nodes = []
        for n in nodes:
            nd = dict(n)
            graph_nodes.append({
                'id': nd['id'],
                'label': nd['entity_name'],
                'type': nd['entity_type'],
                'color': type_colors.get(nd['entity_type'], '#76b7b2'),
                'size': min(30, 10 + nd.get('mention_count', 0)),
                'linked_lorebook_id': nd.get('linked_lorebook_id')
            })

        graph_edges = []
        for e in edges:
            ed = dict(e)
            graph_edges.append({
                'id': ed['id'],
                'from': ed['source_node_id'],
                'to': ed['target_node_id'],
                'label': ed.get('relation_type', ''),
                'detail': ed.get('relation_detail', ''),
                'confidence': ed.get('confidence', 0.5),
                'dashes': ed.get('confidence', 0.5) < 0.5,
                'color': '#ff9800' if ed.get('confidence', 0.5) < 0.5 else '#666'
            })

        return {'nodes': graph_nodes, 'edges': graph_edges}

    # ------------------------------------------------------------------
    #  Node management
    # ------------------------------------------------------------------

    def merge_duplicate_nodes(self, book_id, node_ids, primary_id):
        """合并重复节点"""
        if primary_id not in node_ids:
            return False

        secondary_ids = [nid for nid in node_ids if nid != primary_id]
        if not secondary_ids:
            return False

        conn = self.db._conn()
        for sid in secondary_ids:
            # 将边的引用更新到 primary
            conn.execute(
                'UPDATE knowledge_edges SET source_node_id=? WHERE source_node_id=? AND book_id=?',
                (primary_id, sid, book_id)
            )
            conn.execute(
                'UPDATE knowledge_edges SET target_node_id=? WHERE target_node_id=? AND book_id=?',
                (primary_id, sid, book_id)
            )
            conn.execute(
                'UPDATE story_events SET actor_node_id=? WHERE actor_node_id=? AND book_id=?',
                (primary_id, sid, book_id)
            )
            conn.execute(
                'UPDATE story_events SET target_node_id=? WHERE target_node_id=? AND book_id=?',
                (primary_id, sid, book_id)
            )
            # 累加 mention_count
            sec_row = conn.execute('SELECT mention_count FROM knowledge_nodes WHERE id=?', (sid,)).fetchone()
            if sec_row:
                conn.execute(
                    'UPDATE knowledge_nodes SET mention_count=mention_count+? WHERE id=?',
                    (sec_row['mention_count'], primary_id)
                )
            conn.execute('DELETE FROM knowledge_nodes WHERE id=?', (sid,))

        # 删除自环
        conn.execute(
            'DELETE FROM knowledge_edges WHERE source_node_id=target_node_id AND book_id=?',
            (book_id,)
        )
        conn.commit()
        conn.close()
        return True

    def update_edge_status(self, edge_id, status):
        """更新边状态"""
        conn = self.db._conn()
        conn.execute('UPDATE knowledge_edges SET status=? WHERE id=?', (status, edge_id))
        conn.commit()
        conn.close()

    def get_events(self, book_id, node_id=None, actor_name=None):
        """查询事件列表"""
        conn = self.db._conn()
        query = 'SELECT * FROM story_events WHERE book_id=?'
        params = [book_id]
        if node_id:
            query += ' AND node_id=?'
            params.append(node_id)
        if actor_name:
            actor_node = conn.execute(
                'SELECT id FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
                (book_id, actor_name)
            ).fetchone()
            if actor_node:
                query += ' AND actor_node_id=?'
                params.append(actor_node['id'])
        query += ' ORDER BY chapter_index, created_at'
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
