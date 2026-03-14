"""
一致性报告引擎 - 全书体检
"""
import json
from datetime import datetime


class ConsistencyEngine:
    def __init__(self, db):
        self.db = db
        self._llm_checker = None

    def set_llm_checker(self, callback):
        """设置 LLM 检查回调
        签名: callback(check_type, context_text) -> list[dict]
        每个 dict: {issue_type, severity, title, description, evidence}
        """
        self._llm_checker = callback

    def run_full_scan(self, book_id, report_id=None):
        """执行全书一致性扫描，返回报告 ID"""
        if not report_id:
            report_id = self.db.create_consistency_report({'book_id': book_id})

        issues = []
        issues.extend(self._check_foreshadowing(book_id))
        issues.extend(self._check_world_state(book_id))
        issues.extend(self._check_timeline(book_id))
        issues.extend(self._check_character_behavior(book_id))

        high = medium = low = 0
        for issue in issues:
            issue['report_id'] = report_id
            issue['book_id'] = book_id
            self.db.add_consistency_issue(issue)
            sev = issue.get('severity', 'medium')
            if sev == 'high':
                high += 1
            elif sev == 'low':
                low += 1
            else:
                medium += 1

        self.db.update_consistency_report(report_id, {
            'status': 'completed',
            'issue_count': len(issues),
            'high_count': high,
            'medium_count': medium,
            'low_count': low,
            'completed_at': datetime.now().isoformat()
        })

        return report_id

    def _check_foreshadowing(self, book_id):
        """检查未回收伏笔"""
        issues = []
        foreshadowing = self.db.get_foreshadowing(book_id)
        unresolved = [f for f in foreshadowing if f.get('status') == 'unresolved']
        for f in unresolved:
            issues.append({
                'issue_type': 'foreshadowing',
                'severity': 'low',
                'title': f'未回收伏笔: {f.get("label", "未命名")}',
                'description': f'伏笔「{f.get("label", "")}」尚未回收: {f.get("text", "")[:100]}',
                'evidence': [{'source': 'foreshadowing', 'text': f.get('text', '')[:200]}],
                'related_node_ids': [f.get('node_id', '')] if f.get('node_id') else [],
                'related_entities': []
            })
        return issues

    def _check_world_state(self, book_id):
        """检查世界状态冲突"""
        issues = []
        states = self.db.get_world_state(book_id)

        # 按 entity+type 分组，检查最新状态中是否有矛盾
        entity_states = {}
        for s in states:
            key = (s.get('entity_name', ''), s.get('state_type', ''))
            if key not in entity_states:
                entity_states[key] = []
            entity_states[key].append(s)

        for (entity, state_type), state_list in entity_states.items():
            # 检查非 superseded 状态是否有多个
            active = [s for s in state_list if not s.get('superseded_by')]
            if len(active) > 1:
                values = [s.get('state_value', '') for s in active]
                issues.append({
                    'issue_type': 'world_state',
                    'severity': 'medium',
                    'title': f'{entity}的{state_type}存在多个当前值',
                    'description': f'{entity}的{state_type}有{len(active)}个有效状态: {", ".join(values[:3])}',
                    'evidence': [{'source': 'world_state', 'text': v} for v in values[:3]],
                    'related_entities': [entity]
                })

        return issues

    def _check_timeline(self, book_id):
        """检查时间线冲突"""
        issues = []
        transitions = self.db.get_entity_state_transitions(book_id)

        entity_trans = {}
        for t in transitions:
            name = t.get('entity_name', '')
            if name not in entity_trans:
                entity_trans[name] = []
            entity_trans[name].append(t)

        for entity, trans_list in entity_trans.items():
            by_type = {}
            for t in trans_list:
                st = t.get('state_type', '')
                if st not in by_type:
                    by_type[st] = []
                by_type[st].append(t)

            for state_type, typed_trans in by_type.items():
                for i in range(len(typed_trans) - 1):
                    t1 = typed_trans[i]
                    t2 = typed_trans[i + 1]
                    if (t1.get('new_value') and t2.get('old_value') and
                            t1['new_value'] != t2['old_value']):
                        issues.append({
                            'issue_type': 'timeline',
                            'severity': 'high',
                            'title': f'{entity}的{state_type}时间线断裂',
                            'description': (f'{entity}的{state_type}从"{t1["new_value"]}"'
                                          f'变为"{t2["old_value"]}"存在不连续'),
                            'evidence': [
                                {'source': 'transition', 'text': f'{t1["new_value"]} -> {t2["old_value"]}'}
                            ],
                            'related_entities': [entity]
                        })

        return issues

    def _check_character_behavior(self, book_id):
        """检查角色行为冲突（基础检查 - 心理档案与历史对比）"""
        issues = []
        psychology = self.db.get_character_psychology(book_id)

        for p in psychology:
            char_name = p.get('character_name', '')
            fears = p.get('fears', '')
            drives = p.get('drives', '')

            if not fears and not drives:
                continue

            history = self.db.get_character_history(book_id, character_name=char_name, limit=20)
            if not history:
                continue

            # 基础检测：如果角色有明确恐惧但历史中完全没有相关表现，标记为可能的动机断裂
            if fears and len(history) >= 5:
                fear_mentioned = any(fears[:10] in (h.get('summary', '') + h.get('details', ''))
                                    for h in history)
                if not fear_mentioned:
                    issues.append({
                        'issue_type': 'motivation_break',
                        'severity': 'low',
                        'title': f'{char_name}的恐惧设定未在行为中体现',
                        'description': f'{char_name}的恐惧"{fears[:50]}"在近期历史中完全未体现',
                        'evidence': [{'source': 'psychology', 'text': f'恐惧: {fears[:100]}'}],
                        'related_entities': [char_name]
                    })

        return issues

    def resolve_issue(self, issue_id, resolution, note=''):
        """标记问题处理方式: open/ignored/fixed/exception"""
        self.db.update_consistency_issue(issue_id, {
            'resolution': resolution,
            'resolution_note': note
        })
