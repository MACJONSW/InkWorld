"""
数据库层 - SQLite 持久化存储
"""
import sqlite3
import json
import uuid
import os
import time
from datetime import datetime
from cryptography.fernet import Fernet

try:
    from sqlalchemy import create_engine, event
    HAS_SQLALCHEMY = True
except Exception:
    HAS_SQLALCHEMY = False

DB_PATH = os.path.join(os.path.dirname(__file__), 'novel_platform.db')
KEY_PATH = os.path.join(os.path.dirname(__file__), '.encryption_key')


def get_cipher():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, 'rb') as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_PATH, 'wb') as f:
            f.write(key)
    return Fernet(key)


cipher = get_cipher()


def encrypt(text):
    if not text:
        return text
    return cipher.encrypt(text.encode('utf-8')).decode('utf-8')


def decrypt(text):
    if not text:
        return text
    try:
        return cipher.decrypt(text.encode('utf-8')).decode('utf-8')
    except Exception:
        return text


class Database:
    def __init__(self):
        self.db_path = DB_PATH
        self._engine = None
        if HAS_SQLALCHEMY:
            db_url = f"sqlite:///{self.db_path}"
            self._engine = create_engine(
                db_url,
                future=True,
                connect_args={'check_same_thread': False}
            )

            @event.listens_for(self._engine, 'connect')
            def _set_sqlite_pragma(dbapi_connection, _connection_record):
                cur = dbapi_connection.cursor()
                cur.execute('PRAGMA journal_mode=WAL')
                cur.execute('PRAGMA foreign_keys=ON')
                cur.close()

    def _decrypt_fields(self, row_dict, fields):
        for field in fields:
            if field in row_dict:
                row_dict[field] = decrypt(row_dict[field])
        return row_dict

    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(self, conn, table, column, definition):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
        if column not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def init_db(self):
        conn = self._conn()
        c = conn.cursor()

        # 用户账号表
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        # 模型配置表
        c.execute('''CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openai',
            base_url TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            model_id TEXT NOT NULL,
            max_context INTEGER DEFAULT 8192,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 任务路由表
        c.execute('''CREATE TABLE IF NOT EXISTS routing (
            role TEXT PRIMARY KEY,
            model_id TEXT,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL
        )''')

        # 生成参数表
        c.execute('''CREATE TABLE IF NOT EXISTS generation_params (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            temperature REAL DEFAULT 0.7,
            top_p REAL DEFAULT 0.9,
            presence_penalty REAL DEFAULT 0.0,
            frequency_penalty REAL DEFAULT 0.0,
            max_tokens INTEGER DEFAULT 2000
        )''')

        # Token 统计
        c.execute('''CREATE TABLE IF NOT EXISTS token_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT,
            role TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        # 书籍表
        c.execute('''CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            author TEXT DEFAULT '',
            genre TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 文档节点表 (书 > 卷 > 章 > 场景)
        c.execute('''CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            parent_id TEXT,
            type TEXT NOT NULL DEFAULT 'chapter',
            title TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 节点内容表 (当前主线内容)
        c.execute('''CREATE TABLE IF NOT EXISTS node_contents (
            node_id TEXT PRIMARY KEY,
            content TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
        )''')

        # 版本 / 分支表
        c.execute('''CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            label TEXT DEFAULT 'A',
            content TEXT DEFAULT '',
            is_active INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
        )''')

        # Lorebook 设定集
        c.execute('''CREATE TABLE IF NOT EXISTS lorebook (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            category TEXT DEFAULT 'character',
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            keywords TEXT DEFAULT '',
            content TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 实体关系图谱
        c.execute('''CREATE TABLE IF NOT EXISTS entity_graph (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            source_entity TEXT NOT NULL,
            target_entity TEXT NOT NULL,
            relation_type TEXT DEFAULT '',
            relation_value TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 章节摘要 (滚动记忆)
        c.execute('''CREATE TABLE IF NOT EXISTS chapter_summaries (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            chapter_title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            key_events TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 大纲表
        c.execute('''CREATE TABLE IF NOT EXISTS outlines (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            content TEXT DEFAULT '',
            outline_type TEXT DEFAULT 'volume',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 伏笔追踪表
        c.execute('''CREATE TABLE IF NOT EXISTS foreshadowing (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            text TEXT DEFAULT '',
            label TEXT DEFAULT '',
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'unresolved',
            created_chapter TEXT DEFAULT '',
            resolved_chapter TEXT DEFAULT '',
            resolved_node_id TEXT,
            resolved_text TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 世界状态表
        c.execute('''CREATE TABLE IF NOT EXISTS world_state (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            state_type TEXT DEFAULT 'location',
            state_value TEXT DEFAULT '',
            scene_context TEXT DEFAULT '',
            last_updated_node TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 角色心理档案表
        c.execute('''CREATE TABLE IF NOT EXISTS character_psychology (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            drives TEXT DEFAULT '',
            fears TEXT DEFAULT '',
            defense_mechanisms TEXT DEFAULT '',
            subtext_style TEXT DEFAULT '',
            core_contradiction TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 角色历史档案表
        c.execute('''CREATE TABLE IF NOT EXISTS character_history (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            entry_type TEXT DEFAULT 'event',
            summary TEXT DEFAULT '',
            details TEXT DEFAULT '',
            source_node_id TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            source_excerpt TEXT DEFAULT '',
            foreshadow_refs TEXT DEFAULT '',
            is_manual INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 用户级任务路由
        c.execute('''CREATE TABLE IF NOT EXISTS user_routing (
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            model_id TEXT,
            PRIMARY KEY (user_id, role),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL
        )''')

        # 用户级生成参数
        c.execute('''CREATE TABLE IF NOT EXISTS user_generation_params (
            user_id TEXT PRIMARY KEY,
            temperature REAL DEFAULT 0.7,
            top_p REAL DEFAULT 0.9,
            presence_penalty REAL DEFAULT 0.0,
            frequency_penalty REAL DEFAULT 0.0,
            max_tokens INTEGER DEFAULT 2000,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 时间线与事件账本
        c.execute('''CREATE TABLE IF NOT EXISTS timeline_events (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            chapter_number INTEGER DEFAULT 0,
            event_type TEXT DEFAULT 'event',
            entity_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            state_before TEXT DEFAULT '',
            state_after TEXT DEFAULT '',
            effective_from TEXT DEFAULT '',
            effective_to TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 内容快照（自动备份）
        c.execute('''CREATE TABLE IF NOT EXISTS content_snapshots (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            content TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            label TEXT DEFAULT '',
            trigger_type TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 回收站（软删除节点）
        c.execute('''CREATE TABLE IF NOT EXISTS deleted_nodes (
            id TEXT PRIMARY KEY,
            original_node_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            parent_id TEXT DEFAULT '',
            node_type TEXT DEFAULT 'chapter',
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            deleted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            restore_data TEXT DEFAULT ''
        )''')

        # 创作规则中心 / 风格圣经
        c.execute('''CREATE TABLE IF NOT EXISTS writing_rules (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            rule_type TEXT DEFAULT 'style',
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 异步任务中心
        c.execute('''CREATE TABLE IF NOT EXISTS async_tasks (
            id TEXT PRIMARY KEY,
            book_id TEXT DEFAULT '',
            user_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            result TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 兼容历史库的字段迁移
        self._ensure_column(conn, 'models', 'user_id', 'TEXT')
        self._ensure_column(conn, 'books', 'user_id', 'TEXT')
        self._ensure_column(conn, 'token_stats', 'user_id', 'TEXT')

        # 初始化默认生成参数
        c.execute('INSERT OR IGNORE INTO generation_params (id) VALUES (1)')

        conn.commit()
        conn.close()

    # ============== 模型管理 ==============

    def create_user(self, email, password_hash):
        uid = str(uuid.uuid4())[:12]
        conn = self._conn()
        conn.execute('INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)',
                     (uid, email.strip().lower(), password_hash))
        conn.commit()
        conn.close()
        return uid

    def get_user_by_email(self, email):
        conn = self._conn()
        row = conn.execute('SELECT * FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_id(self, user_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_models(self, user_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM models WHERE user_id=? ORDER BY created_at DESC', (user_id,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d['api_key'] = decrypt(d.pop('api_key_enc', ''))
            # 只显示部分 key
            if d['api_key'] and len(d['api_key']) > 8:
                d['api_key_display'] = d['api_key'][:4] + '****' + d['api_key'][-4:]
            else:
                d['api_key_display'] = '****'
            result.append(d)
        return result

    def get_model(self, model_id, user_id=None):
        conn = self._conn()
        if user_id:
            row = conn.execute('SELECT * FROM models WHERE id=? AND user_id=?', (model_id, user_id)).fetchone()
        else:
            row = conn.execute('SELECT * FROM models WHERE id=?', (model_id,)).fetchone()
        conn.close()
        if row:
            d = dict(row)
            d['api_key'] = decrypt(d.pop('api_key_enc', ''))
            return d
        return None

    def add_model(self, data, user_id):
        mid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO models (id, user_id, name, provider, base_url, api_key_enc, model_id, max_context)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (mid, user_id, data.get('name', ''), data.get('provider', 'openai'),
                      data.get('base_url', ''), encrypt(data.get('api_key', '')),
                      data.get('model_id', ''), data.get('max_context', 8192)))
        conn.commit()
        conn.close()
        return mid

    def update_model(self, model_id, data, user_id):
        conn = self._conn()
        fields = []
        vals = []
        for k in ['name', 'provider', 'base_url', 'model_id', 'max_context']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        if 'api_key' in data:
            fields.append('api_key_enc=?')
            vals.append(encrypt(data['api_key']))
        vals.extend([model_id, user_id])
        conn.execute(f'UPDATE models SET {",".join(fields)} WHERE id=? AND user_id=?', vals)
        conn.commit()
        conn.close()

    def delete_model(self, model_id, user_id):
        conn = self._conn()
        conn.execute('DELETE FROM models WHERE id=? AND user_id=?', (model_id, user_id))
        conn.execute('DELETE FROM user_routing WHERE user_id=? AND model_id=?', (user_id, model_id))
        conn.commit()
        conn.close()

    # ============== 路由 ==============

    def get_routing(self, user_id):
        conn = self._conn()
        rows = conn.execute('SELECT role, model_id FROM user_routing WHERE user_id=?', (user_id,)).fetchall()
        conn.close()
        return {r['role']: r['model_id'] for r in rows}

    def set_routing(self, data, user_id):
        conn = self._conn()
        conn.execute('DELETE FROM user_routing WHERE user_id=?', (user_id,))
        for role, model_id in data.items():
            conn.execute('INSERT OR REPLACE INTO user_routing (user_id, role, model_id) VALUES (?, ?, ?)',
                        (user_id, role, model_id))
        conn.commit()
        conn.close()

    def get_model_for_role(self, role, user_id):
        conn = self._conn()
        row = conn.execute('SELECT model_id FROM user_routing WHERE user_id=? AND role=?', (user_id, role)).fetchone()
        if row and row['model_id']:
            model = self.get_model(row['model_id'], user_id=user_id)
            conn.close()
            return model
        conn.close()
        # Fallback: return first model for current user
        models = self.get_all_models(user_id)
        return models[0] if models else None

    # ============== 生成参数 ==============

    def get_generation_params(self, user_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM user_generation_params WHERE user_id=?', (user_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
        return {
            'temperature': 0.7,
            'top_p': 0.9,
            'presence_penalty': 0.0,
            'frequency_penalty': 0.0,
            'max_tokens': 2000
        }

    def set_generation_params(self, data, user_id):
        conn = self._conn()
        conn.execute('''INSERT OR REPLACE INTO user_generation_params
                        (user_id, temperature, top_p, presence_penalty, frequency_penalty, max_tokens)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (user_id, data.get('temperature', 0.7), data.get('top_p', 0.9),
                      data.get('presence_penalty', 0.0), data.get('frequency_penalty', 0.0),
                      data.get('max_tokens', 2000)))
        conn.commit()
        conn.close()

    def get_token_stats(self, user_id):
        conn = self._conn()
        rows = conn.execute('''SELECT model_id, role,
                               SUM(prompt_tokens) as total_prompt,
                               SUM(completion_tokens) as total_completion,
                               SUM(total_tokens) as grand_total
                               FROM token_stats WHERE user_id=?
                               GROUP BY model_id, role''', (user_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def record_tokens(self, model_id, role, prompt_tokens, completion_tokens, user_id):
        conn = self._conn()
        conn.execute('''INSERT INTO token_stats (model_id, role, prompt_tokens, completion_tokens, total_tokens, user_id)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (model_id, role, prompt_tokens, completion_tokens, prompt_tokens + completion_tokens, user_id))
        conn.commit()
        conn.close()

    # ============== 书籍管理 ==============

    def get_books(self, user_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM books WHERE user_id=? ORDER BY updated_at DESC', (user_id,)).fetchall()
        conn.close()
        books = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['description', 'author', 'genre'])
            books.append(d)
        return books

    def create_book(self, data, user_id):
        bid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO books (id, user_id, title, description, author, genre, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (bid, user_id, data.get('title', '未命名'), encrypt(data.get('description', '')),
                      encrypt(data.get('author', '')), encrypt(data.get('genre', '')), now, now))
        conn.commit()
        conn.close()
        return bid

    def update_book(self, book_id, data, user_id):
        conn = self._conn()
        fields = []
        vals = []
        encrypted_fields = {'description', 'author', 'genre'}
        for k in ['title', 'description', 'author', 'genre']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        fields.append('updated_at=?')
        vals.append(datetime.now().isoformat())
        vals.extend([book_id, user_id])
        conn.execute(f'UPDATE books SET {",".join(fields)} WHERE id=? AND user_id=?', vals)
        conn.commit()
        conn.close()

    def delete_book(self, book_id, user_id):
        conn = self._conn()
        conn.execute('DELETE FROM books WHERE id=? AND user_id=?', (book_id, user_id))
        conn.commit()
        conn.close()

    def get_book(self, book_id, user_id=None):
        conn = self._conn()
        if user_id:
            row = conn.execute('SELECT * FROM books WHERE id=? AND user_id=?', (book_id, user_id)).fetchone()
        else:
            row = conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        return self._decrypt_fields(d, ['description', 'author', 'genre'])

    def book_belongs_to_user(self, book_id, user_id):
        conn = self._conn()
        row = conn.execute('SELECT id FROM books WHERE id=? AND user_id=?', (book_id, user_id)).fetchone()
        conn.close()
        return bool(row)

    def node_belongs_to_user(self, node_id, user_id):
        conn = self._conn()
        row = conn.execute('''SELECT n.id FROM nodes n
                              JOIN books b ON b.id = n.book_id
                              WHERE n.id=? AND b.user_id=?''', (node_id, user_id)).fetchone()
        conn.close()
        return bool(row)

    def get_node_book_id(self, node_id):
        conn = self._conn()
        row = conn.execute('SELECT book_id FROM nodes WHERE id=?', (node_id,)).fetchone()
        conn.close()
        return row['book_id'] if row else None

    # ============== 文档树 ==============

    def get_document_tree(self, book_id):
        conn = self._conn()
        rows = conn.execute('''SELECT n.*, COALESCE(nc.word_count, 0) as word_count
                               FROM nodes n LEFT JOIN node_contents nc ON n.id = nc.node_id
                               WHERE n.book_id=? ORDER BY n.sort_order''',
                            (book_id,)).fetchall()
        conn.close()
        nodes = [dict(r) for r in rows]
        return self._build_tree(nodes)

    def _build_tree(self, nodes):
        node_map = {n['id']: {**n, 'children': []} for n in nodes}
        tree = []
        for n in nodes:
            if n['parent_id'] and n['parent_id'] in node_map:
                node_map[n['parent_id']]['children'].append(node_map[n['id']])
            else:
                tree.append(node_map[n['id']])
        return tree

    def create_node(self, data):
        nid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        # Get max sort_order
        max_order = conn.execute(
            'SELECT COALESCE(MAX(sort_order), -1) FROM nodes WHERE book_id=? AND parent_id IS ?',
            (data['book_id'], data.get('parent_id'))).fetchone()[0]
        conn.execute('''INSERT INTO nodes (id, book_id, parent_id, type, title, sort_order, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (nid, data['book_id'], data.get('parent_id'),
                      data.get('type', 'chapter'), data.get('title', '未命名'),
                      max_order + 1, data.get('status', 'draft'), now, now))
        conn.execute('INSERT INTO node_contents (node_id, content, word_count, updated_at) VALUES (?, ?, 0, ?)',
                     (nid, '', now))
        conn.commit()
        conn.close()
        return nid

    def get_node(self, node_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_node(self, node_id, data):
        conn = self._conn()
        fields = []
        vals = []
        for k in ['title', 'type', 'parent_id', 'sort_order', 'status']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        fields.append('updated_at=?')
        vals.append(datetime.now().isoformat())
        vals.append(node_id)
        conn.execute(f'UPDATE nodes SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_node(self, node_id):
        conn = self._conn()
        conn.execute('DELETE FROM nodes WHERE id=?', (node_id,))
        conn.commit()
        conn.close()

    def reorder_nodes(self, data):
        conn = self._conn()
        for item in data.get('items', []):
            conn.execute('UPDATE nodes SET sort_order=?, parent_id=? WHERE id=?',
                        (item['sort_order'], item.get('parent_id'), item['id']))
        conn.commit()
        conn.close()

    def get_node_content(self, node_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM node_contents WHERE node_id=?', (node_id,)).fetchone()
        conn.close()
        if not row:
            return {'node_id': node_id, 'content': '', 'word_count': 0}
        d = dict(row)
        d['content'] = decrypt(d.get('content', ''))
        return d

    def save_node_content(self, node_id, data):
        content = data.get('content', '')
        word_count = len(content)
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT OR REPLACE INTO node_contents (node_id, content, word_count, updated_at)
                        VALUES (?, ?, ?, ?)''', (node_id, encrypt(content), word_count, now))
        conn.execute('UPDATE nodes SET updated_at=? WHERE id=?', (now, node_id))
        conn.commit()
        conn.close()

    # ============== 版本 ==============

    def get_versions(self, node_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM versions WHERE node_id=? ORDER BY created_at', (node_id,)).fetchall()
        conn.close()
        versions = []
        for r in rows:
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            versions.append(d)
        return versions

    def create_version(self, data):
        vid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO versions (id, node_id, label, content, is_active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (vid, data['node_id'], data.get('label', 'A'),
                      encrypt(data.get('content', '')), data.get('is_active', 0),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return vid

    def activate_version(self, node_id, ver_id):
        conn = self._conn()
        conn.execute('UPDATE versions SET is_active=0 WHERE node_id=?', (node_id,))
        conn.execute('UPDATE versions SET is_active=1 WHERE id=?', (ver_id,))
        # Also update main content
        row = conn.execute('SELECT content FROM versions WHERE id=?', (ver_id,)).fetchone()
        if row:
            now = datetime.now().isoformat()
            plain = decrypt(row['content'])
            conn.execute('''INSERT OR REPLACE INTO node_contents (node_id, content, word_count, updated_at)
                            VALUES (?, ?, ?, ?)''', (node_id, row['content'], len(plain), now))
            conn.execute('UPDATE nodes SET updated_at=? WHERE id=?', (now, node_id))
        conn.commit()
        conn.close()

    # ============== Lorebook ==============

    def get_lorebook_entries(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM lorebook WHERE book_id=? ORDER BY sort_order, category',
                            (book_id,)).fetchall()
        conn.close()
        entries = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['description', 'keywords', 'content'])
            entries.append(d)
        return entries

    def add_lorebook_entry(self, data):
        eid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO lorebook (id, book_id, category, name, description, keywords, content, enabled, sort_order)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (eid, data['book_id'], data.get('category', 'character'),
                      data.get('name', ''), encrypt(data.get('description', '')),
                      encrypt(data.get('keywords', '')), encrypt(data.get('content', '')),
                      data.get('enabled', 1), data.get('sort_order', 0)))
        conn.commit()
        conn.close()
        return eid

    def update_lorebook_entry(self, entry_id, data):
        conn = self._conn()
        fields = []
        vals = []
        encrypted_fields = {'description', 'keywords', 'content'}
        for k in ['category', 'name', 'description', 'keywords', 'content', 'enabled', 'sort_order']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        vals.append(entry_id)
        conn.execute(f'UPDATE lorebook SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_lorebook_entry(self, entry_id):
        conn = self._conn()
        conn.execute('DELETE FROM lorebook WHERE id=?', (entry_id,))
        conn.commit()
        conn.close()

    # ============== 实体图谱 ==============

    def get_entity_graph(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM entity_graph WHERE book_id=? ORDER BY updated_at DESC',
                            (book_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_entity_graph(self, book_id, data):
        conn = self._conn()
        relations = data.get('relations', [])
        # Clear and re-insert
        conn.execute('DELETE FROM entity_graph WHERE book_id=?', (book_id,))
        for rel in relations:
            rid = str(uuid.uuid4())[:8]
            conn.execute('''INSERT INTO entity_graph (id, book_id, source_entity, target_entity, relation_type, relation_value)
                            VALUES (?, ?, ?, ?, ?, ?)''',
                         (rid, book_id, rel.get('source', ''), rel.get('target', ''),
                          rel.get('type', ''), rel.get('value', '')))
        conn.commit()
        conn.close()

    # ============== 章节摘要 ==============

    def get_chapter_summaries(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM chapter_summaries WHERE book_id=? ORDER BY created_at',
                            (book_id,)).fetchall()
        conn.close()
        summaries = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['chapter_title', 'summary', 'key_events'])
            summaries.append(d)
        return summaries

    def save_chapter_summary(self, data):
        sid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO chapter_summaries (id, book_id, node_id, chapter_title, summary, key_events)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (sid, data['book_id'], data.get('node_id', ''),
                      encrypt(data.get('chapter_title', '')), encrypt(data.get('summary', '')),
                      encrypt(data.get('key_events', ''))))
        conn.commit()
        conn.close()
        return sid

    # ============== 大纲 ==============

    def get_outlines(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM outlines WHERE book_id=? ORDER BY created_at DESC',
                            (book_id,)).fetchall()
        conn.close()
        outlines = []
        for r in rows:
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            outlines.append(d)
        return outlines

    def save_outline(self, data):
        oid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO outlines (id, book_id, content, outline_type)
                        VALUES (?, ?, ?, ?)''',
                     (oid, data['book_id'], encrypt(data.get('content', '')),
                      data.get('outline_type', 'volume')))
        conn.commit()
        conn.close()
        return oid

    # ============== 伏笔追踪 ==============

    def get_foreshadowing(self, book_id, status=None):
        conn = self._conn()
        if status:
            rows = conn.execute('SELECT * FROM foreshadowing WHERE book_id=? AND status=? ORDER BY created_at DESC',
                                (book_id, status)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM foreshadowing WHERE book_id=? ORDER BY created_at DESC',
                                (book_id,)).fetchall()
        conn.close()
        items = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['text', 'label', 'description', 'created_chapter', 'resolved_chapter', 'resolved_text'])
            items.append(d)
        return items

    def add_foreshadowing(self, data):
        fid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO foreshadowing (id, book_id, node_id, text, label, description, status,
                        created_chapter, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (fid, data['book_id'], data.get('node_id', ''), encrypt(data.get('text', '')),
                      encrypt(data.get('label', '')), encrypt(data.get('description', '')),
                      data.get('status', 'unresolved'), encrypt(data.get('created_chapter', '')),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return fid

    def update_foreshadowing(self, fs_id, data):
        conn = self._conn()
        fields = []
        vals = []
        encrypted_fields = {'text', 'label', 'description', 'resolved_chapter', 'resolved_text'}
        for k in ['text', 'label', 'description', 'status', 'resolved_chapter',
                   'resolved_node_id', 'resolved_text']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        vals.append(fs_id)
        conn.execute(f'UPDATE foreshadowing SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_foreshadowing(self, fs_id):
        conn = self._conn()
        conn.execute('DELETE FROM foreshadowing WHERE id=?', (fs_id,))
        conn.commit()
        conn.close()

    def resolve_foreshadowing(self, fs_id, data):
        conn = self._conn()
        conn.execute('''UPDATE foreshadowing SET status='resolved', resolved_chapter=?,
                        resolved_node_id=?, resolved_text=? WHERE id=?''',
                     (encrypt(data.get('resolved_chapter', '')), data.get('resolved_node_id', ''),
                      encrypt(data.get('resolved_text', '')), fs_id))
        conn.commit()
        conn.close()

    # ============== 世界状态 ==============

    def get_world_state(self, book_id, entity_name=None, state_type=None):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM world_state WHERE book_id=? ORDER BY updated_at DESC', (book_id,)).fetchall()
        conn.close()
        states = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['state_value', 'scene_context'])
            states.append(d)
        if entity_name:
            states = [s for s in states if (s.get('entity_name') or '') == entity_name]
        if state_type:
            states = [s for s in states if (s.get('state_type') or '') == state_type]
        return states

    def upsert_world_state(self, data):
        conn = self._conn()
        # Check if exists
        row = conn.execute('SELECT id FROM world_state WHERE book_id=? AND entity_name=? AND state_type=?',
                           (data['book_id'], data['entity_name'], data['state_type'])).fetchone()
        now = datetime.now().isoformat()
        if row:
            conn.execute('''UPDATE world_state SET state_value=?, scene_context=?,
                           last_updated_node=?, updated_at=? WHERE id=?''',
                         (encrypt(data.get('state_value', '')), encrypt(data.get('scene_context', '')),
                          data.get('last_updated_node', ''), now, row['id']))
            sid = row['id']
        else:
            sid = str(uuid.uuid4())[:8]
            conn.execute('''INSERT INTO world_state (id, book_id, entity_name, state_type, state_value,
                           scene_context, last_updated_node, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                         (sid, data['book_id'], data['entity_name'], data['state_type'],
                          encrypt(data.get('state_value', '')), encrypt(data.get('scene_context', '')),
                          data.get('last_updated_node', ''), now))
        conn.commit()
        conn.close()
        return sid

    def delete_world_state(self, ws_id):
        conn = self._conn()
        conn.execute('DELETE FROM world_state WHERE id=?', (ws_id,))
        conn.commit()
        conn.close()

    # ============== 角色心理档案 ==============

    def get_character_psychology(self, book_id, character_name=None):
        conn = self._conn()
        if character_name:
            rows = conn.execute('SELECT * FROM character_psychology WHERE book_id=? AND character_name=?',
                                (book_id, character_name)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM character_psychology WHERE book_id=? ORDER BY updated_at DESC',
                                (book_id,)).fetchall()
        conn.close()
        profiles = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['drives', 'fears', 'defense_mechanisms', 'subtext_style', 'core_contradiction'])
            profiles.append(d)
        return profiles

    def upsert_character_psychology(self, data):
        conn = self._conn()
        row = conn.execute('SELECT id FROM character_psychology WHERE book_id=? AND character_name=?',
                           (data['book_id'], data['character_name'])).fetchone()
        now = datetime.now().isoformat()
        if row:
            conn.execute('''UPDATE character_psychology SET drives=?, fears=?, defense_mechanisms=?,
                           subtext_style=?, core_contradiction=?, updated_at=? WHERE id=?''',
                         (encrypt(data.get('drives', '')), encrypt(data.get('fears', '')),
                          encrypt(data.get('defense_mechanisms', '')), encrypt(data.get('subtext_style', '')),
                          encrypt(data.get('core_contradiction', '')), now, row['id']))
            pid = row['id']
        else:
            pid = str(uuid.uuid4())[:8]
            conn.execute('''INSERT INTO character_psychology (id, book_id, character_name, drives, fears,
                           defense_mechanisms, subtext_style, core_contradiction, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (pid, data['book_id'], data['character_name'],
                          encrypt(data.get('drives', '')), encrypt(data.get('fears', '')),
                          encrypt(data.get('defense_mechanisms', '')), encrypt(data.get('subtext_style', '')),
                          encrypt(data.get('core_contradiction', '')), now))
        conn.commit()
        conn.close()
        return pid

    def delete_character_psychology(self, cp_id):
        conn = self._conn()
        conn.execute('DELETE FROM character_psychology WHERE id=?', (cp_id,))
        conn.commit()
        conn.close()

    # ============== 角色历史档案 ==============

    def get_character_history(self, book_id, character_name=None, entry_type=None, limit=None):
        conn = self._conn()
        query = 'SELECT * FROM character_history WHERE book_id=?'
        vals = [book_id]
        if character_name:
            query += ' AND character_name=?'
            vals.append(character_name)
        if entry_type:
            query += ' AND entry_type=?'
            vals.append(entry_type)
        query += ' ORDER BY updated_at DESC, created_at DESC'
        if limit:
            query += ' LIMIT ?'
            vals.append(int(limit))
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        entries = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['summary', 'details', 'chapter_title', 'source_excerpt', 'foreshadow_refs'])
            d['is_manual'] = bool(d.get('is_manual'))
            entries.append(d)
        return entries

    def add_character_history(self, data):
        hid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO character_history (
                        id, book_id, character_name, entry_type, summary, details,
                        source_node_id, chapter_title, source_excerpt, foreshadow_refs,
                        is_manual, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (hid, data['book_id'], data.get('character_name', ''), data.get('entry_type', 'event'),
                      encrypt(data.get('summary', '')), encrypt(data.get('details', '')),
                      data.get('source_node_id', ''), encrypt(data.get('chapter_title', '')),
                      encrypt(data.get('source_excerpt', '')), encrypt(data.get('foreshadow_refs', '')),
                      1 if data.get('is_manual') else 0, now, now))
        conn.commit()
        conn.close()
        return hid

    def update_character_history(self, history_id, data):
        conn = self._conn()
        fields = []
        vals = []
        encrypted_fields = {'summary', 'details', 'chapter_title', 'source_excerpt', 'foreshadow_refs'}
        for key in ['character_name', 'entry_type', 'summary', 'details', 'source_node_id',
                    'chapter_title', 'source_excerpt', 'foreshadow_refs', 'is_manual']:
            if key in data:
                fields.append(f'{key}=?')
                value = data[key]
                if key in encrypted_fields:
                    vals.append(encrypt(value))
                elif key == 'is_manual':
                    vals.append(1 if value else 0)
                else:
                    vals.append(value)
        fields.append('updated_at=?')
        vals.append(datetime.now().isoformat())
        vals.append(history_id)
        conn.execute(f'UPDATE character_history SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_character_history(self, history_id):
        conn = self._conn()
        conn.execute('DELETE FROM character_history WHERE id=?', (history_id,))
        conn.commit()
        conn.close()

    def delete_generated_character_history(self, book_id, character_name=None, source_node_id=None):
        conn = self._conn()
        query = 'DELETE FROM character_history WHERE book_id=? AND is_manual=0'
        vals = [book_id]
        if character_name:
            query += ' AND character_name=?'
            vals.append(character_name)
        if source_node_id:
            query += ' AND source_node_id=?'
            vals.append(source_node_id)
        conn.execute(query, tuple(vals))
        conn.commit()
        conn.close()

    def replace_generated_character_history(self, book_id, character_name, entries, source_node_id=None):
        self.delete_generated_character_history(book_id, character_name=character_name, source_node_id=source_node_id)
        created_ids = []
        for entry in entries:
            payload = dict(entry)
            payload['book_id'] = book_id
            payload['character_name'] = character_name
            payload['is_manual'] = False
            if source_node_id and not payload.get('source_node_id'):
                payload['source_node_id'] = source_node_id
            created_ids.append(self.add_character_history(payload))
        return created_ids

    # ============== 时间线与事件账本 ==============

    def get_timeline_events(self, book_id, entity_name=None, event_type=None, node_id=None):
        conn = self._conn()
        query = 'SELECT * FROM timeline_events WHERE book_id=?'
        vals = [book_id]
        if entity_name:
            query += ' AND entity_name=?'
            vals.append(entity_name)
        if event_type:
            query += ' AND event_type=?'
            vals.append(event_type)
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        query += ' ORDER BY chapter_number ASC, created_at ASC'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        events = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['description', 'state_before', 'state_after', 'chapter_title', 'tags'])
            events.append(d)
        return events

    def add_timeline_event(self, data):
        eid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO timeline_events
            (id, book_id, node_id, chapter_title, chapter_number, event_type, entity_name,
             description, state_before, state_after, effective_from, effective_to, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (eid, data['book_id'], data.get('node_id', ''),
             encrypt(data.get('chapter_title', '')),
             data.get('chapter_number', 0),
             data.get('event_type', 'event'),
             data.get('entity_name', ''),
             encrypt(data.get('description', '')),
             encrypt(data.get('state_before', '')),
             encrypt(data.get('state_after', '')),
             data.get('effective_from', ''),
             data.get('effective_to', ''),
             encrypt(data.get('tags', '')),
             now))
        conn.commit()
        conn.close()
        return eid

    def update_timeline_event(self, event_id, data):
        conn = self._conn()
        fields = []
        vals = []
        encrypted_fields = {'description', 'state_before', 'state_after', 'chapter_title', 'tags'}
        for k in ['chapter_title', 'chapter_number', 'event_type', 'entity_name', 'description',
                  'state_before', 'state_after', 'effective_from', 'effective_to', 'tags', 'node_id']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        if not fields:
            conn.close()
            return
        vals.append(event_id)
        conn.execute(f'UPDATE timeline_events SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_timeline_event(self, event_id):
        conn = self._conn()
        conn.execute('DELETE FROM timeline_events WHERE id=?', (event_id,))
        conn.commit()
        conn.close()

    # ============== 内容快照 ==============

    def get_snapshots(self, node_id, limit=20):
        conn = self._conn()
        rows = conn.execute(
            'SELECT id, node_id, book_id, word_count, label, trigger_type, created_at '
            'FROM content_snapshots WHERE node_id=? ORDER BY created_at DESC LIMIT ?',
            (node_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_snapshot(self, node_id, book_id, content, label='', trigger_type='auto'):
        sid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO content_snapshots
            (id, node_id, book_id, content, word_count, label, trigger_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (sid, node_id, book_id, encrypt(content), len(content), label, trigger_type, now))
        # Keep only the last 30 snapshots per node
        conn.execute('''DELETE FROM content_snapshots WHERE node_id=? AND id NOT IN
            (SELECT id FROM content_snapshots WHERE node_id=? ORDER BY created_at DESC LIMIT 30)''',
            (node_id, node_id))
        conn.commit()
        conn.close()
        return sid

    def get_snapshot_content(self, snapshot_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM content_snapshots WHERE id=?', (snapshot_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        d['content'] = decrypt(d.get('content', ''))
        return d

    # ============== 回收站 ==============

    def soft_delete_node(self, node_id):
        """将节点及其所有子节点移入回收站，返回被删除的节点ID列表"""
        conn = self._conn()
        # Collect all descendant nodes
        all_ids = []
        queue = [node_id]
        while queue:
            current = queue.pop()
            row = conn.execute('SELECT * FROM nodes WHERE id=?', (current,)).fetchone()
            if not row:
                continue
            node = dict(row)
            content_row = conn.execute('SELECT content, word_count FROM node_contents WHERE node_id=?', (current,)).fetchone()
            content = ''
            word_count = 0
            if content_row:
                content = decrypt(content_row['content'] or '')
                word_count = content_row['word_count'] or 0
            did = str(uuid.uuid4())[:8]
            restore_data = json.dumps({
                'original_node_id': current,
                'parent_id': node.get('parent_id', ''),
                'book_id': node['book_id'],
                'sort_order': node['sort_order'],
                'status': node.get('status', 'draft'),
                'type': node.get('type', 'chapter'),
            })
            conn.execute('''INSERT INTO deleted_nodes
                (id, original_node_id, book_id, parent_id, node_type, title, content, word_count, sort_order, deleted_at, restore_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (did, current, node['book_id'], node.get('parent_id', '') or '',
                 node.get('type', 'chapter'), node.get('title', ''),
                 encrypt(content), word_count, node.get('sort_order', 0),
                 datetime.now().isoformat(), restore_data))
            all_ids.append(current)
            # Queue children
            children = conn.execute('SELECT id FROM nodes WHERE parent_id=?', (current,)).fetchall()
            for child in children:
                queue.append(child['id'])
        # Now delete from main tables
        for nid in all_ids:
            conn.execute('DELETE FROM nodes WHERE id=?', (nid,))
        conn.commit()
        conn.close()
        return all_ids

    def get_deleted_nodes(self, book_id):
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM deleted_nodes WHERE book_id=? ORDER BY deleted_at DESC',
            (book_id,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            result.append(d)
        return result

    def restore_deleted_node(self, deleted_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM deleted_nodes WHERE id=?', (deleted_id,)).fetchone()
        if not row:
            conn.close()
            return False
        d = dict(row)
        restore_data = json.loads(d.get('restore_data', '{}'))
        now = datetime.now().isoformat()
        nid = d['original_node_id']
        # Check if node_id still exists (conflict)
        existing = conn.execute('SELECT id FROM nodes WHERE id=?', (nid,)).fetchone()
        if existing:
            nid = str(uuid.uuid4())[:8]
        conn.execute('''INSERT INTO nodes (id, book_id, parent_id, type, title, sort_order, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (nid, d['book_id'], restore_data.get('parent_id') or None,
             d.get('node_type', 'chapter'), d['title'],
             d.get('sort_order', 0), restore_data.get('status', 'draft'), now, now))
        content = d.get('content', '')
        conn.execute('''INSERT OR REPLACE INTO node_contents (node_id, content, word_count, updated_at)
            VALUES (?, ?, ?, ?)''', (nid, encrypt(content), len(content), now))
        conn.execute('DELETE FROM deleted_nodes WHERE id=?', (deleted_id,))
        conn.commit()
        conn.close()
        return nid

    def purge_deleted_node(self, deleted_id):
        conn = self._conn()
        conn.execute('DELETE FROM deleted_nodes WHERE id=?', (deleted_id,))
        conn.commit()
        conn.close()

    # ============== 创作规则中心 ==============

    def get_writing_rules(self, book_id, rule_type=None):
        conn = self._conn()
        if rule_type:
            rows = conn.execute(
                'SELECT * FROM writing_rules WHERE book_id=? AND rule_type=? ORDER BY sort_order, created_at',
                (book_id, rule_type)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM writing_rules WHERE book_id=? ORDER BY sort_order, rule_type, created_at',
                (book_id,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            d['title'] = decrypt(d.get('title', ''))
            result.append(d)
        return result

    def add_writing_rule(self, data):
        rid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO writing_rules
            (id, book_id, rule_type, title, content, is_active, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (rid, data['book_id'], data.get('rule_type', 'style'),
             encrypt(data.get('title', '')), encrypt(data.get('content', '')),
             1 if data.get('is_active', True) else 0,
             data.get('sort_order', 0), now, now))
        conn.commit()
        conn.close()
        return rid

    def update_writing_rule(self, rule_id, data):
        conn = self._conn()
        fields = []
        vals = []
        for k in ['rule_type', 'is_active', 'sort_order']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(1 if (k == 'is_active' and data[k]) else (0 if k == 'is_active' else data[k]))
        for k in ['title', 'content']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]))
        fields.append('updated_at=?')
        vals.append(datetime.now().isoformat())
        vals.append(rule_id)
        conn.execute(f'UPDATE writing_rules SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_writing_rule(self, rule_id):
        conn = self._conn()
        conn.execute('DELETE FROM writing_rules WHERE id=?', (rule_id,))
        conn.commit()
        conn.close()

    def get_active_writing_rules_text(self, book_id):
        """返回所有激活规则的文本，供注入 prompt 使用"""
        rules = self.get_writing_rules(book_id)
        active = [r for r in rules if r.get('is_active')]
        if not active:
            return ''
        lines = ['=== 创作规则 ===']
        type_labels = {
            'style': '文风', 'pov': '视角', 'forbidden': '禁用词',
            'character_voice': '角色语气', 'format': '格式规范', 'other': '其他规则'
        }
        by_type = {}
        for r in active:
            t = r.get('rule_type', 'other')
            by_type.setdefault(t, []).append(r)
        for rtype, items in by_type.items():
            label = type_labels.get(rtype, rtype)
            lines.append(f'[{label}]')
            for item in items:
                lines.append(f'- {item["title"]}: {item["content"][:200]}')
        return '\n'.join(lines)

    # ============== 异步任务中心 ==============

    def create_async_task(self, user_id, task_type, book_id=''):
        tid = str(uuid.uuid4())[:12]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO async_tasks
            (id, book_id, user_id, task_type, status, progress, total, result, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', 0, 0, '', '', ?, ?)''',
            (tid, book_id, user_id, task_type, now, now))
        conn.commit()
        conn.close()
        return tid

    def update_async_task(self, task_id, status=None, progress=None, total=None, result=None, error=None):
        conn = self._conn()
        fields = ['updated_at=?']
        vals = [datetime.now().isoformat()]
        if status is not None:
            fields.append('status=?')
            vals.append(status)
        if progress is not None:
            fields.append('progress=?')
            vals.append(progress)
        if total is not None:
            fields.append('total=?')
            vals.append(total)
        if result is not None:
            fields.append('result=?')
            vals.append(result[:4000] if isinstance(result, str) else str(result))
        if error is not None:
            fields.append('error=?')
            vals.append(str(error)[:1000])
        vals.append(task_id)
        conn.execute(f'UPDATE async_tasks SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def get_async_task(self, task_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM async_tasks WHERE id=?', (task_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_async_tasks(self, user_id, book_id=None, status=None, limit=50):
        conn = self._conn()
        query = 'SELECT * FROM async_tasks WHERE user_id=?'
        vals = [user_id]
        if book_id:
            query += ' AND book_id=?'
            vals.append(book_id)
        if status:
            query += ' AND status=?'
            vals.append(status)
        query += ' ORDER BY created_at DESC LIMIT ?'
        vals.append(limit)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def cancel_async_task(self, task_id, user_id):
        conn = self._conn()
        conn.execute(
            "UPDATE async_tasks SET status='cancelled', updated_at=? WHERE id=? AND user_id=? AND status IN ('pending','running')",
            (datetime.now().isoformat(), task_id, user_id))
        conn.commit()
        conn.close()

    # ============== 全量数据导出 ==============

    def export_all_book_data(self, book_id, user_id=None):
        book = self.get_book(book_id, user_id=user_id)
        if not book:
            return None
        conn = self._conn()
        nodes = [dict(r) for r in conn.execute('SELECT * FROM nodes WHERE book_id=? ORDER BY sort_order', (book_id,)).fetchall()]
        contents = {}
        for n in nodes:
            row = conn.execute('SELECT * FROM node_contents WHERE node_id=?', (n['id'],)).fetchone()
            if row:
                d = dict(row)
                d['content'] = decrypt(d.get('content', ''))
                contents[n['id']] = d
        versions = []
        for r in conn.execute('SELECT v.* FROM versions v JOIN nodes n ON v.node_id=n.id WHERE n.book_id=?', (book_id,)).fetchall():
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            versions.append(d)
        lorebook = []
        for r in conn.execute('SELECT * FROM lorebook WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['description', 'keywords', 'content'])
            lorebook.append(d)
        graph = [dict(r) for r in conn.execute('SELECT * FROM entity_graph WHERE book_id=?', (book_id,)).fetchall()]
        summaries = []
        for r in conn.execute('SELECT * FROM chapter_summaries WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['chapter_title', 'summary', 'key_events'])
            summaries.append(d)
        outlines = []
        for r in conn.execute('SELECT * FROM outlines WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            d['content'] = decrypt(d.get('content', ''))
            outlines.append(d)
        foreshadowing = []
        for r in conn.execute('SELECT * FROM foreshadowing WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['text', 'label', 'description', 'created_chapter', 'resolved_chapter', 'resolved_text'])
            foreshadowing.append(d)
        world_state = []
        for r in conn.execute('SELECT * FROM world_state WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['state_value', 'scene_context'])
            world_state.append(d)
        psychology = []
        for r in conn.execute('SELECT * FROM character_psychology WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['drives', 'fears', 'defense_mechanisms', 'subtext_style', 'core_contradiction'])
            psychology.append(d)
        character_history = []
        for r in conn.execute('SELECT * FROM character_history WHERE book_id=?', (book_id,)).fetchall():
            d = dict(r)
            self._decrypt_fields(d, ['summary', 'details', 'chapter_title', 'source_excerpt', 'foreshadow_refs'])
            d['is_manual'] = bool(d.get('is_manual'))
            character_history.append(d)
        conn.close()
        return {
            'book': book,
            'nodes': nodes,
            'contents': contents,
            'versions': versions,
            'lorebook': lorebook,
            'entity_graph': graph,
            'summaries': summaries,
            'outlines': outlines,
            'foreshadowing': foreshadowing,
            'world_state': world_state,
            'psychology': psychology,
            'character_history': character_history,
        }
