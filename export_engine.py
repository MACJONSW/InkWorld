"""
导出引擎 - Markdown, TXT, EPUB, JSON 导出与导入
"""
import json
import os
import re
import html as _html
import tempfile
from datetime import datetime


def _safe_filename(name):
    """清洗文件名，去除路径分隔符和特殊字符"""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', name or 'untitled')
    name = name.strip('. ')
    return name[:100] or 'untitled'


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
        filename = f"{_safe_filename(title)}.md"
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
        filename = f"{_safe_filename(title)}.txt"
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
            html_content = f"<{heading}>{_html.escape(item['title'])}</{heading}>\n"
            for p in paragraphs:
                if p.strip():
                    html_content += f"<p>{_html.escape(p.strip())}</p>\n"
            ch.content = html_content.encode('utf-8')
            ch.add_item(style)
            book.add_item(ch)
            chapters.append(ch)
            spine.append(ch)

        book.toc = chapters
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine

        filepath = os.path.join(tempfile.gettempdir(), f"{_safe_filename(title)}.epub")
        epub.write_epub(filepath, book)
        filename = f"{_safe_filename(title)}.epub"
        return filepath, filename

    def _to_html_fallback(self, book_id, user_id=None):
        """EPUB不可用时回退为HTML"""
        book = self.db.get_book(book_id, user_id=user_id)
        title = book['title'] if book else '未命名'
        contents = self._get_ordered_contents(book_id)

        html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
        <title>{_html.escape(title)}</title><style>body{{font-family:serif;max-width:800px;margin:0 auto;padding:20px;line-height:1.8;}}
        h1,h2,h3{{color:#333;}} p{{text-indent:2em;}}</style></head><body><h1>{_html.escape(title)}</h1>"""

        for item in contents:
            h = min(item['depth'] + 2, 6)
            html += f"<h{h}>{_html.escape(item['title'])}</h{h}>"
            if item['content']:
                for p in item['content'].split('\n'):
                    if p.strip():
                        html += f"<p>{_html.escape(p.strip())}</p>"
        html += "</body></html>"

        filepath = os.path.join(tempfile.gettempdir(), f"{_safe_filename(title)}.html")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        return filepath, f"{_safe_filename(title)}.html"

    def to_json_workspace(self, book_id, user_id=None):
        """打包整个工作空间为JSON"""
        data = self.db.export_all_book_data(book_id, user_id=user_id)
        if not data:
            return '{}', 'export.json'
        title = data['book']['title']
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return content, f"{_safe_filename(title)}_workspace.json"

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

    # ── Scope-based export ──────────────────────────────────────────────

    def to_markdown_scoped(self, book_id, scope='book', node_id=None, include=None, user_id=None):
        """Export with scope selection.
        scope: 'book' | 'volume' | 'chapter'
        node_id: required for volume/chapter scope
        include: list of content types to include: ['content', 'summary', 'settings', 'timeline', 'history', 'versions']
        """
        if include is None:
            include = ['content']

        book = self.db.get_book(book_id, user_id=user_id)
        title = book['title'] if book else '未命名'
        lines = [f"# {title}\n"]

        if scope == 'chapter' and node_id:
            # Export single chapter
            node = self.db.get_node(node_id)
            content_data = self.db.get_node_content(node_id)
            if node:
                lines.append(f"\n## {node.get('title', '')}\n")
            if 'content' in include and content_data:
                lines.append(f"\n{content_data.get('content', '')}\n")
            if 'summary' in include:
                summaries = self.db.get_chapter_summaries(book_id)
                for s in summaries:
                    if s.get('node_id') == node_id:
                        lines.append(f"\n### 章节摘要\n{s.get('summary', '')}\n")
            if 'versions' in include:
                versions = self.db.get_versions(node_id)
                for v in versions:
                    lines.append(f"\n### 版本: {v.get('label', '')}\n{v.get('content', '')[:500]}\n")
        elif scope == 'volume' and node_id:
            # Export volume and its children
            tree = self.db.get_document_tree(book_id)
            volume_nodes = self._find_subtree(tree, node_id)
            if volume_nodes:
                result = []
                self._flatten_tree(volume_nodes, result, 0)
                for item in result:
                    heading = '#' * min(item['depth'] + 2, 6)
                    lines.append(f"\n{heading} {item['title']}\n")
                    if 'content' in include and item['content']:
                        lines.append(f"\n{item['content']}\n")
        else:
            # Full book export (existing logic)
            contents = self._get_ordered_contents(book_id)
            for item in contents:
                heading = '#' * min(item['depth'] + 2, 6)
                lines.append(f"\n{heading} {item['title']}\n")
                if 'content' in include and item['content']:
                    lines.append(f"\n{item['content']}\n")

        # Append settings if requested
        if 'settings' in include:
            entries = self.db.get_lorebook_entries(book_id)
            if entries:
                lines.append(f"\n---\n\n# 设定集\n")
                for e in entries:
                    lines.append(f"\n## {e.get('name', '')} ({e.get('category', '')})\n")
                    lines.append(f"{e.get('content', '')}\n")

        if 'timeline' in include:
            try:
                events = self.db.get_timeline_events(book_id)
                if events:
                    lines.append(f"\n---\n\n# 时间线\n")
                    for ev in events:
                        lines.append(f"- [{ev.get('entity_name', '')}] {ev.get('description', '')}\n")
            except Exception:
                pass

        if 'history' in include:
            try:
                history = self.db.get_character_history(book_id)
                if history:
                    lines.append(f"\n---\n\n# 角色历史\n")
                    for h in history:
                        lines.append(f"- [{h.get('character_name', '')}] {h.get('summary', '')}\n")
            except Exception:
                pass

        md_text = "\n".join(lines)
        filename = f"{_safe_filename(title)}.md"
        return md_text, filename

    def _find_subtree(self, tree, target_id):
        """Find a node and return it as a list with its children"""
        for node in tree:
            if node['id'] == target_id:
                return [node]
            if node.get('children'):
                result = self._find_subtree(node['children'], target_id)
                if result:
                    return result
        return []

    # ── Markdown import ─────────────────────────────────────────────────

    def import_markdown(self, md_text, book_title, user_id):
        """Import a Markdown document. Parse # headings as structure."""
        chapters = self._parse_markdown_to_tree(md_text)

        book_id = self.db.create_book({
            'title': book_title,
            'description': f'从Markdown导入',
        }, user_id=user_id)

        for i, chapter in enumerate(chapters):
            node_id = self.db.create_node({
                'book_id': book_id,
                'title': chapter['title'],
                'type': 'volume' if chapter['depth'] == 0 else 'chapter',
                'parent_id': chapter.get('parent_node_id'),
                'sort_order': i
            })
            chapter['node_id'] = node_id
            if chapter.get('content'):
                self.db.save_node_content(node_id, {'content': chapter['content']})
            # Update children's parent_id
            for child in chapters:
                if child.get('parent_index') == chapters.index(chapter):
                    child['parent_node_id'] = node_id

        return book_id

    def _parse_markdown_to_tree(self, md_text):
        """Parse markdown into flat list with depth info"""
        lines = md_text.split('\n')
        chapters = []
        current_content = []
        current_title = None
        current_depth = 0
        parent_stack = []  # [(depth, index)]

        for line in lines:
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                # Save previous chapter
                if current_title is not None:
                    chapters.append({
                        'title': current_title,
                        'depth': current_depth,
                        'content': '\n'.join(current_content).strip(),
                        'parent_index': parent_stack[-1][1] if parent_stack else None,
                        'parent_node_id': None
                    })

                depth = len(heading_match.group(1)) - 1
                current_title = heading_match.group(2).strip()
                current_depth = depth
                current_content = []

                # Update parent stack
                while parent_stack and parent_stack[-1][0] >= depth:
                    parent_stack.pop()
                if depth > 0 and parent_stack:
                    pass  # parent is top of stack
                if current_title:
                    parent_stack.append((depth, len(chapters)))
            else:
                current_content.append(line)

        # Save last chapter
        if current_title is not None:
            chapters.append({
                'title': current_title,
                'depth': current_depth,
                'content': '\n'.join(current_content).strip(),
                'parent_index': parent_stack[-2][1] if len(parent_stack) > 1 else None,
                'parent_node_id': None
            })

        return chapters

    # ── TXT import ──────────────────────────────────────────────────────

    def import_txt(self, txt_text, book_title, user_id):
        """Import a TXT document. Split by 第X章 patterns or double newlines."""
        chapters = []

        # Try to split by chapter patterns like 第一章, 第1章, Chapter 1, etc.
        pattern = r'(第[一二三四五六七八九十百千\d]+[章节卷部回][\s\S]*?)(?=第[一二三四五六七八九十百千\d]+[章节卷部回]|$)'
        matches = re.findall(pattern, txt_text)

        if matches and len(matches) > 1:
            for i, match in enumerate(matches):
                lines = match.strip().split('\n', 1)
                title = lines[0].strip()[:50]
                content = lines[1].strip() if len(lines) > 1 else ''
                chapters.append({'title': title, 'content': content})
        else:
            # Fallback: split by double newlines into paragraphs, group into ~2000 char chapters
            paragraphs = re.split(r'\n\s*\n', txt_text)
            current_content = []
            current_len = 0
            chapter_num = 1
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                current_content.append(para)
                current_len += len(para)
                if current_len >= 2000:
                    chapters.append({
                        'title': f'第{chapter_num}节',
                        'content': '\n\n'.join(current_content)
                    })
                    current_content = []
                    current_len = 0
                    chapter_num += 1
            if current_content:
                chapters.append({
                    'title': f'第{chapter_num}节',
                    'content': '\n\n'.join(current_content)
                })

        book_id = self.db.create_book({
            'title': book_title,
            'description': '从TXT导入',
        }, user_id=user_id)

        for i, chapter in enumerate(chapters):
            node_id = self.db.create_node({
                'book_id': book_id,
                'title': chapter['title'],
                'type': 'chapter',
                'sort_order': i
            })
            if chapter.get('content'):
                self.db.save_node_content(node_id, {'content': chapter['content']})

        return book_id

    # ── DOCX import ─────────────────────────────────────────────────────

    def import_docx(self, file_path, book_title, user_id):
        """Import a DOCX document. Parse headings as structure."""
        try:
            from docx import Document
        except ImportError:
            raise ImportError("python-docx 未安装，请运行: pip install python-docx")

        doc = Document(file_path)
        chapters = []
        current_content = []
        current_title = book_title

        for para in doc.paragraphs:
            if para.style.name.startswith('Heading'):
                # Save previous
                if current_content:
                    chapters.append({
                        'title': current_title,
                        'content': '\n'.join(current_content)
                    })
                    current_content = []
                current_title = para.text.strip() or '未命名章节'
            else:
                if para.text.strip():
                    current_content.append(para.text)

        if current_content:
            chapters.append({
                'title': current_title,
                'content': '\n'.join(current_content)
            })

        book_id = self.db.create_book({
            'title': book_title,
            'description': '从DOCX导入',
        }, user_id=user_id)

        for i, chapter in enumerate(chapters):
            node_id = self.db.create_node({
                'book_id': book_id,
                'title': chapter['title'],
                'type': 'chapter',
                'sort_order': i
            })
            if chapter.get('content'):
                self.db.save_node_content(node_id, {'content': chapter['content']})

        return book_id
