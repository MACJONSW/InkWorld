"""
全局搜索与引用追踪引擎
"""
import re
from collections import defaultdict


class SearchEngine:
    def __init__(self, db):
        self.db = db

    def search(self, book_id, query, scope=None, entity_type=None):
        """跨章节全文搜索，结果按类别分组。
        scope: 'content'|'summary'|'lorebook'|'character_history'|'world_state'|None(all)
        """
        results = {
            'content': [],
            'summary': [],
            'lorebook': [],
            'character_history': [],
            'world_state': []
        }
        if not query:
            return results

        query_lower = query.lower()

        # 搜索正文
        if not scope or scope == 'content':
            nodes = self.db.get_all_node_contents(book_id)
            for n in nodes:
                content = n.get('content', '')
                if query_lower in content.lower():
                    positions = self._find_positions(content, query)
                    results['content'].append({
                        'node_id': n['id'],
                        'title': n.get('title', ''),
                        'match_count': len(positions),
                        'excerpts': [self._excerpt(content, p, query) for p in positions[:3]]
                    })

        # 搜索摘要
        if not scope or scope == 'summary':
            summaries = self.db.get_chapter_summaries(book_id)
            for s in summaries:
                text = (s.get('summary', '') + ' ' + s.get('key_events', '')).lower()
                if query_lower in text:
                    results['summary'].append({
                        'node_id': s.get('node_id', ''),
                        'chapter_title': s.get('chapter_title', ''),
                        'excerpt': self._short(s.get('summary', ''), 200)
                    })

        # 搜索设定
        if not scope or scope == 'lorebook':
            entries = self.db.get_lorebook_entries(book_id)
            for e in entries:
                text = (e.get('name', '') + ' ' + e.get('description', '') + ' ' +
                        e.get('content', '') + ' ' + e.get('keywords', '')).lower()
                if entity_type and e.get('category') != entity_type:
                    continue
                if query_lower in text:
                    results['lorebook'].append({
                        'id': e['id'],
                        'name': e.get('name', ''),
                        'category': e.get('category', ''),
                        'excerpt': self._short(e.get('content', ''), 200)
                    })

        # 搜索角色历史
        if not scope or scope == 'character_history':
            history = self.db.get_character_history(book_id)
            for h in history:
                text = (h.get('summary', '') + ' ' + h.get('details', '')).lower()
                if query_lower in text:
                    results['character_history'].append({
                        'id': h['id'],
                        'character_name': h.get('character_name', ''),
                        'chapter_title': h.get('chapter_title', ''),
                        'excerpt': self._short(h.get('summary', ''), 200)
                    })

        # 搜索世界状态
        if not scope or scope == 'world_state':
            states = self.db.get_world_state(book_id)
            for ws in states:
                text = (ws.get('entity_name', '') + ' ' + ws.get('state_value', '') +
                        ' ' + ws.get('scene_context', '')).lower()
                if query_lower in text:
                    results['world_state'].append({
                        'id': ws['id'],
                        'entity_name': ws.get('entity_name', ''),
                        'state_type': ws.get('state_type', ''),
                        'state_value': ws.get('state_value', '')
                    })

        return results

    def replace_all(self, book_id, search_text, replace_text, preview_only=True):
        """全书替换。preview_only=True 时只返回影响范围预览。"""
        nodes = self.db.get_all_node_contents(book_id)
        affected = []

        for n in nodes:
            content = n.get('content', '')
            if not content:
                continue
            count = content.count(search_text)
            if count > 0:
                affected.append({
                    'node_id': n['id'],
                    'title': n.get('title', ''),
                    'match_count': count,
                    'excerpt': self._excerpt(content, content.find(search_text), search_text)
                })

        preview = {
            'search_text': search_text,
            'replace_text': replace_text,
            'affected_chapters': len(affected),
            'total_replacements': sum(a['match_count'] for a in affected),
            'chapters': affected
        }

        if preview_only:
            return preview

        # 执行替换
        for a in affected:
            content_data = self.db.get_node_content(a['node_id'])
            old_content = content_data.get('content', '')
            new_content = old_content.replace(search_text, replace_text)
            self.db.save_node_content(a['node_id'], {'content': new_content})

        return preview

    def find_references(self, book_id, entity_name):
        """查看某实体被哪些章节引用"""
        nodes = self.db.get_all_node_contents(book_id)
        refs = []
        for n in nodes:
            content = n.get('content', '')
            if entity_name in content:
                count = content.count(entity_name)
                first_pos = content.find(entity_name)
                refs.append({
                    'node_id': n['id'],
                    'title': n.get('title', ''),
                    'mention_count': count,
                    'first_excerpt': self._excerpt(content, first_pos, entity_name)
                })
        return {
            'entity_name': entity_name,
            'total_chapters': len(refs),
            'total_mentions': sum(r['mention_count'] for r in refs),
            'chapters': refs
        }

    def find_chapter_references(self, book_id, node_id):
        """查看某章节涉及了哪些设定资产"""
        content_data = self.db.get_node_content(node_id)
        content = content_data.get('content', '')
        if not content:
            return {'entities': [], 'foreshadowing': [], 'world_states': []}

        # 匹配 lorebook
        entries = self.db.get_lorebook_entries(book_id)
        matched_entries = []
        for e in entries:
            if e.get('name', '') and e['name'] in content:
                matched_entries.append({
                    'id': e['id'], 'name': e['name'], 'category': e.get('category', '')
                })

        # 匹配伏笔
        foreshadowing = self.db.get_foreshadowing(book_id)
        matched_fs = []
        for f in foreshadowing:
            label = f.get('label', '')
            text = f.get('text', '')
            if (label and label in content) or (text and text[:20] in content):
                matched_fs.append({
                    'id': f['id'], 'label': label, 'status': f.get('status', '')
                })

        # 匹配世界状态
        states = self.db.get_world_state(book_id)
        matched_ws = []
        for ws in states:
            if ws.get('entity_name', '') and ws['entity_name'] in content:
                matched_ws.append({
                    'id': ws['id'], 'entity_name': ws['entity_name'],
                    'state_type': ws.get('state_type', '')
                })

        return {
            'entities': matched_entries,
            'foreshadowing': matched_fs,
            'world_states': matched_ws
        }

    def find_first_last_occurrence(self, book_id, text):
        """查找某文本在全书中的首次和最近出现"""
        nodes = self.db.get_all_node_contents(book_id)
        first = None
        last = None
        for n in nodes:
            content = n.get('content', '')
            if text in content:
                hit = {
                    'node_id': n['id'],
                    'title': n.get('title', ''),
                    'excerpt': self._excerpt(content, content.find(text), text)
                }
                if first is None:
                    first = hit
                last = hit
        return {'first': first, 'last': last}

    def _find_positions(self, text, query):
        """Find all positions of query in text (case-insensitive)."""
        positions = []
        text_lower = text.lower()
        query_lower = query.lower()
        start = 0
        while True:
            pos = text_lower.find(query_lower, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + 1
        return positions

    def _excerpt(self, text, pos, query, radius=60):
        """Extract an excerpt around a match position."""
        if pos < 0:
            return ''
        start = max(0, pos - radius)
        end = min(len(text), pos + len(query) + radius)
        prefix = '...' if start > 0 else ''
        suffix = '...' if end < len(text) else ''
        return prefix + text[start:end] + suffix

    def _short(self, text, limit=200):
        if len(text) <= limit:
            return text
        return text[:limit] + '...'
