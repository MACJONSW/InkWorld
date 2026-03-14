"""
时间线与事件账本引擎
"""
import json
import re


class TimelineEngine:
    def __init__(self, db):
        self.db = db
        self._event_extractor = None

    def set_event_extractor(self, callback):
        """设置事件提取回调（由 agent 提供 LLM 调用）
        签名: callback(book_id, node_id, text) -> list[dict]
        每个 dict: {entity_name, event_type, description, location}
        """
        self._event_extractor = callback

    def extract_events_from_text(self, book_id, node_id, text, chapter_index=0):
        """从章节文本中提取事件并写入时间线"""
        if not self._event_extractor:
            return []

        # 删除该节点之前自动提取的事件
        self.db.delete_timeline_events_for_node(book_id, node_id)

        events_data = self._event_extractor(book_id, node_id, text)
        if not events_data or not isinstance(events_data, list):
            return []

        created = []
        for i, ev in enumerate(events_data):
            eid = self.db.add_timeline_event({
                'book_id': book_id,
                'node_id': node_id,
                'event_type': ev.get('event_type', 'action'),
                'entity_name': ev.get('entity_name', ''),
                'description': ev.get('description', ''),
                'location': ev.get('location', ''),
                'chapter_index': chapter_index,
                'event_order': i,
                'source': 'auto'
            })
            created.append(eid)

            # 如果事件包含状态变更，记录状态变迁
            if ev.get('state_change'):
                sc = ev['state_change']
                self.db.add_entity_state_transition({
                    'book_id': book_id,
                    'entity_name': ev.get('entity_name', ''),
                    'state_type': sc.get('state_type', 'location'),
                    'old_value': sc.get('old_value', ''),
                    'new_value': sc.get('new_value', ''),
                    'cause_event_id': eid,
                    'start_node_id': node_id
                })

        return created

    def get_timeline(self, book_id, entity_name=None, node_id=None, event_type=None):
        """获取筛选后的时间线"""
        return self.db.get_timeline_events(book_id, entity_name=entity_name,
                                           node_id=node_id, event_type=event_type)

    def get_entity_history(self, book_id, entity_name):
        """获取某实体的完整历史（事件+状态变迁）"""
        events = self.db.get_timeline_events(book_id, entity_name=entity_name)
        transitions = self.db.get_entity_state_transitions(book_id, entity_name=entity_name)
        return {
            'entity_name': entity_name,
            'events': events,
            'state_transitions': transitions
        }

    def detect_conflicts(self, book_id):
        """检测时间线冲突"""
        conflicts = []
        events = self.db.get_timeline_events(book_id)
        transitions = self.db.get_entity_state_transitions(book_id)

        # 1. 检测时间顺序冲突：同一实体的事件是否存在逻辑矛盾
        entity_events = {}
        for ev in events:
            name = ev.get('entity_name', '')
            if name not in entity_events:
                entity_events[name] = []
            entity_events[name].append(ev)

        # 2. 检测状态冲突：同一实体在同一时间是否有矛盾状态
        entity_transitions = {}
        for t in transitions:
            name = t.get('entity_name', '')
            if name not in entity_transitions:
                entity_transitions[name] = []
            entity_transitions[name].append(t)

        for entity, trans_list in entity_transitions.items():
            # 按类型分组检查
            by_type = {}
            for t in trans_list:
                st = t.get('state_type', '')
                if st not in by_type:
                    by_type[st] = []
                by_type[st].append(t)

            for state_type, type_trans in by_type.items():
                for i in range(len(type_trans) - 1):
                    t1 = type_trans[i]
                    t2 = type_trans[i + 1]
                    # 如果后一个状态的 old_value 不等于前一个状态的 new_value，则存在不连续
                    if (t1.get('new_value') and t2.get('old_value') and
                            t1['new_value'] != t2['old_value']):
                        conflicts.append({
                            'type': 'state_discontinuity',
                            'severity': 'medium',
                            'entity': entity,
                            'state_type': state_type,
                            'description': (f'{entity}的{state_type}状态不连续: '
                                          f'"{t1["new_value"]}" -> "{t2["old_value"]}"'),
                            'transition_ids': [t1['id'], t2['id']]
                        })

        return conflicts

    def build_timeline_context(self, book_id, node_id=None, max_events=20):
        """构建时间线上下文文本，用于注入 LLM prompt"""
        events = self.db.get_timeline_events(book_id)
        if not events:
            return ''

        # 如果指定了 node_id，只取该节点之前的事件
        if node_id:
            node = self.db.get_node(node_id)
            if node:
                node_order = node.get('sort_order', 0)
                events = [e for e in events if e.get('chapter_index', 0) <= node_order]

        events = events[-max_events:]

        parts = ['=== 时间线事件 ===']
        for ev in events:
            loc = f'@{ev["location"]}' if ev.get('location') else ''
            parts.append(f'- [{ev.get("entity_name", "")}] {ev.get("description", "")} {loc}')

        return '\n'.join(parts)
