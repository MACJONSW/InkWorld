"""
导出引擎 - Markdown, TXT, EPUB, JSON 导出与导入
"""
import json
import os
import tempfile
from datetime import datetime


class ExportEngine:
    def __init__(self, db):
        self.db = db

    def _get_ordered_contents(self, book_id):
        """获取按文档树顺序排列的所有内容"""
        tree = self.db.get_document_tree(book_id)
        result = []
        self._flatten_tree(tree, result, 0)
        return result

    def _flatten_tree(self, nodes, result, depth):
        for node in nodes:
            content_data = self.db.get_node_content(node['id'])
            result.append({
                'title': node['title'],
                'type': node['type'],
                'depth': depth,
                'content': content_data.get('content', ''),
                'status': node.get('status', 'draft')
            })
            if node.get('children'):
                self._flatten_tree(node['children'], result, depth + 1)

    def to_markdown(self, book_id, user_id=None):
        book = self.db.get_book(book_id, user_id=user_id)
        title = book['title'] if book else '未命名'
        contents = self._get_ordered_contents(book_id)

        lines = [f"# {title}\n"]
        if book and book.get('author'):
            lines.append(f"**作者**: {book['author']}\n")
        if book and book.get('description'):
            lines.append(f"> {book['description']}\n")
        lines.append("---\n")

        for item in contents:
            heading = '#' * min(item['depth'] + 2, 6)
            lines.append(f"\n{heading} {item['title']}\n")
            if item['content']:
                lines.append(f"\n{item['content']}\n")

        md_text = "\n".join(lines)
        filename = f"{title}.md"
        return md_text, filename

    def to_txt(self, book_id, user_id=None):
        book = self.db.get_book(book_id, user_id=user_id)
        title = book['title'] if book else '未命名'
        contents = self._get_ordered_contents(book_id)

        lines = [f"{title}\n{'=' * 40}\n"]

        for item in contents:
            indent = '  ' * item['depth']
            lines.append(f"\n{indent}【{item['title']}】\n")
            if item['content']:
                lines.append(f"\n{item['content']}\n")

        txt = "\n".join(lines)
        filename = f"{title}.txt"
        return txt, filename

    def to_epub(self, book_id, user_id=None):
        try:
            from ebooklib import epub
        except ImportError:
            # Fallback: 导出为简单HTML文件
            return self._to_html_fallback(book_id, user_id=user_id)

        book_data = self.db.get_book(book_id, user_id=user_id)
        title = book_data['title'] if book_data else '未命名'
        contents = self._get_ordered_contents(book_id)

        book = epub.EpubBook()
        book.set_identifier(f'novel-{book_id}')
        book.set_title(title)
        book.set_language('zh')
        if book_data and book_data.get('author'):
            book.add_author(book_data['author'])

        # 样式
        style = epub.EpubItem(uid="style", file_name="style/default.css",
                              media_type="text/css",
                              content=b'''body { font-family: serif; line-height: 1.8; }
                              h1, h2, h3 { color: #333; } p { text-indent: 2em; }''')
        book.add_item(style)

        chapters = []
        spine = ['nav']

        for i, item in enumerate(contents):
            ch = epub.EpubHtml(title=item['title'], file_name=f'ch{i}.xhtml', lang='zh')
            heading = f"h{min(item['depth'] + 1, 6)}"
            paragraphs = item['content'].split('\n') if item['content'] else []
            html_content = f"<{heading}>{item['title']}</{heading}>\n"
            for p in paragraphs:
                if p.strip():
                    html_content += f"<p>{p.strip()}</p>\n"
            ch.content = html_content.encode('utf-8')
            ch.add_item(style)
            book.add_item(ch)
            chapters.append(ch)
            spine.append(ch)

        book.toc = chapters
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine

        filepath = os.path.join(tempfile.gettempdir(), f"{title}.epub")
        epub.write_epub(filepath, book)
        filename = f"{title}.epub"
        return filepath, filename

    def _to_html_fallback(self, book_id, user_id=None):
        """EPUB不可用时回退为HTML"""
        book = self.db.get_book(book_id, user_id=user_id)
        title = book['title'] if book else '未命名'
        contents = self._get_ordered_contents(book_id)

        html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
        <title>{title}</title><style>body{{font-family:serif;max-width:800px;margin:0 auto;padding:20px;line-height:1.8;}}
        h1,h2,h3{{color:#333;}} p{{text-indent:2em;}}</style></head><body><h1>{title}</h1>"""

        for item in contents:
            h = min(item['depth'] + 2, 6)
            html += f"<h{h}>{item['title']}</h{h}>"
            if item['content']:
                for p in item['content'].split('\n'):
                    if p.strip():
                        html += f"<p>{p.strip()}</p>"
        html += "</body></html>"

        filepath = os.path.join(tempfile.gettempdir(), f"{title}.html")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        return filepath, f"{title}.html"

    def to_json_workspace(self, book_id, user_id=None):
        """打包整个工作空间为JSON"""
        data = self.db.export_all_book_data(book_id, user_id=user_id)
        if not data:
            return '{}', 'export.json'
        title = data['book']['title']
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return content, f"{title}_workspace.json"

    def import_json_workspace(self, data, user_id):
        """从JSON导入工作空间"""
        book_data = data.get('book', {})
        book_id = self.db.create_book(book_data, user_id=user_id)

        # 导入节点
        id_map = {}
        for node in data.get('nodes', []):
            old_id = node['id']
            node['book_id'] = book_id
            if node.get('parent_id') and node['parent_id'] in id_map:
                node['parent_id'] = id_map[node['parent_id']]
            elif node.get('parent_id'):
                node['parent_id'] = None
            new_id = self.db.create_node(node)
            id_map[old_id] = new_id

        # 导入内容
        for old_id, content_data in data.get('contents', {}).items():
            if old_id in id_map:
                self.db.save_node_content(id_map[old_id], content_data)

        # 导入版本分支
        active_versions = []
        for ver in data.get('versions', []):
            old_node_id = ver.get('node_id')
            new_node_id = id_map.get(old_node_id)
            if not new_node_id:
                continue
            new_ver_id = self.db.create_version({
                'node_id': new_node_id,
                'label': ver.get('label', 'A'),
                'content': ver.get('content', ''),
                'is_active': 1 if ver.get('is_active') else 0
            })
            if ver.get('is_active'):
                active_versions.append((new_node_id, new_ver_id))

        # 恢复激活分支
        for node_id, ver_id in active_versions:
            self.db.activate_version(node_id, ver_id)

        # 导入 Lorebook
        for entry in data.get('lorebook', []):
            entry['book_id'] = book_id
            self.db.add_lorebook_entry(entry)

        # 导入实体图谱
        if data.get('entity_graph'):
            self.db.update_entity_graph(book_id, {'relations': [
                {'source': r['source_entity'], 'target': r['target_entity'],
                 'type': r['relation_type'], 'value': r['relation_value']}
                for r in data['entity_graph']
            ]})

        # 导入章节摘要
        for summary in data.get('summaries', []):
            self.db.save_chapter_summary({
                'book_id': book_id,
                'node_id': id_map.get(summary.get('node_id'), ''),
                'chapter_title': summary.get('chapter_title', ''),
                'summary': summary.get('summary', ''),
                'key_events': summary.get('key_events', '')
            })

        # 导入大纲
        for outline in data.get('outlines', []):
            self.db.save_outline({
                'book_id': book_id,
                'content': outline.get('content', ''),
                'outline_type': outline.get('outline_type', 'volume')
            })

        # 导入伏笔池
        for fs in data.get('foreshadowing', []):
            new_fs_id = self.db.add_foreshadowing({
                'book_id': book_id,
                'node_id': id_map.get(fs.get('node_id'), ''),
                'text': fs.get('text', ''),
                'label': fs.get('label', ''),
                'description': fs.get('description', ''),
                'status': fs.get('status', 'unresolved'),
                'created_chapter': fs.get('created_chapter', '')
            })
            if fs.get('status') == 'resolved':
                self.db.update_foreshadowing(new_fs_id, {
                    'status': 'resolved',
                    'resolved_chapter': fs.get('resolved_chapter', ''),
                    'resolved_node_id': id_map.get(fs.get('resolved_node_id'), ''),
                    'resolved_text': fs.get('resolved_text', '')
                })

        # 导入世界状态
        for ws in data.get('world_state', []):
            self.db.upsert_world_state({
                'book_id': book_id,
                'entity_name': ws.get('entity_name', ''),
                'state_type': ws.get('state_type', ''),
                'state_value': ws.get('state_value', ''),
                'scene_context': ws.get('scene_context', ''),
                'last_updated_node': id_map.get(ws.get('last_updated_node'), '')
            })

        # 导入角色心理档案
        for p in data.get('psychology', []):
            self.db.upsert_character_psychology({
                'book_id': book_id,
                'character_name': p.get('character_name', ''),
                'drives': p.get('drives', ''),
                'fears': p.get('fears', ''),
                'defense_mechanisms': p.get('defense_mechanisms', ''),
                'subtext_style': p.get('subtext_style', ''),
                'core_contradiction': p.get('core_contradiction', '')
            })

        # 导入角色历史档案
        for item in data.get('character_history', []):
            self.db.add_character_history({
                'book_id': book_id,
                'character_name': item.get('character_name', ''),
                'entry_type': item.get('entry_type', 'event'),
                'summary': item.get('summary', ''),
                'details': item.get('details', ''),
                'source_node_id': id_map.get(item.get('source_node_id'), ''),
                'chapter_title': item.get('chapter_title', ''),
                'source_excerpt': item.get('source_excerpt', ''),
                'foreshadow_refs': item.get('foreshadow_refs', ''),
                'is_manual': item.get('is_manual', False)
            })

        return book_id
