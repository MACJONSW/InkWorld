"""
数据库层 - SQLite 持久化存储
"""
import sqlite3
import json
import uuid
import os
import time
import re
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
        # 启动时修正权限为 owner-only
        try:
            current_mode = os.stat(KEY_PATH).st_mode & 0o777
            if current_mode != 0o600:
                os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass
        with open(KEY_PATH, 'rb') as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
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
        self._mutation_listeners = []
        self._fts5_enabled = False
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

    def register_mutation_listener(self, listener):
        if callable(listener):
            self._mutation_listeners.append(listener)

    def _emit_mutation(self, payload):
        for listener in list(self._mutation_listeners):
            try:
                listener(dict(payload))
            except Exception:
                continue

    def _table_exists(self, conn, table_name):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
            (table_name,)
        ).fetchone()
        return bool(row)

    def search_index_enabled(self):
        return self._fts5_enabled

    def _setup_search_index(self, conn):
        try:
            conn.execute(
                '''CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    book_id UNINDEXED,
                    source_type UNINDEXED,
                    source_id UNINDEXED,
                    title,
                    body,
                    meta UNINDEXED,
                    tokenize='unicode61'
                )'''
            )
            self._fts5_enabled = True
        except sqlite3.OperationalError:
            self._fts5_enabled = False

    def _bootstrap_search_index(self):
        if not self._fts5_enabled:
            return
        conn = self._conn()
        try:
            if not self._table_exists(conn, 'search_index'):
                return
            row = conn.execute('SELECT COUNT(*) AS cnt FROM search_index').fetchone()
            if row and row['cnt'] > 0:
                return
            book_rows = conn.execute('SELECT id FROM books').fetchall()
            conn.close()
            for row in book_rows:
                self.rebuild_search_index_for_book(row['id'])
        finally:
            try:
                conn.close()
            except Exception:
                pass

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

        # ========== 新增表：写作规则中心 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS writing_rule_sets (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS writing_rules (
            id TEXT PRIMARY KEY,
            rule_set_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'style',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            scope_type TEXT DEFAULT 'book',
            scope_node_id TEXT,
            enabled INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rule_set_id) REFERENCES writing_rule_sets(id) ON DELETE CASCADE,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：时间线与事件账本 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS timeline_events (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            event_type TEXT NOT NULL DEFAULT 'action',
            entity_name TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT DEFAULT '',
            chapter_index INTEGER DEFAULT 0,
            event_order INTEGER DEFAULT 0,
            source TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS entity_state_transitions (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            state_type TEXT NOT NULL DEFAULT 'location',
            old_value TEXT DEFAULT '',
            new_value TEXT NOT NULL,
            cause_event_id TEXT,
            start_node_id TEXT,
            end_node_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：快照与回收站 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            snapshot_type TEXT NOT NULL DEFAULT 'manual',
            label TEXT DEFAULT '',
            content_data TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS recycle_bin (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_data TEXT NOT NULL,
            deleted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：异步任务中心 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS async_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            book_id TEXT,
            job_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            total_steps INTEGER DEFAULT 0,
            current_step INTEGER DEFAULT 0,
            result_summary TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            level TEXT DEFAULT 'info',
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES async_jobs(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：记忆注入日志 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS memory_injection_logs (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            agent_role TEXT NOT NULL,
            injected_items TEXT NOT NULL DEFAULT '[]',
            candidate_items TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pinned_memories (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            memory_ref TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'pin',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：一致性报告 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS consistency_reports (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            status TEXT DEFAULT 'running',
            issue_count INTEGER DEFAULT 0,
            high_count INTEGER DEFAULT 0,
            medium_count INTEGER DEFAULT 0,
            low_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS consistency_issues (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence TEXT DEFAULT '[]',
            related_node_ids TEXT DEFAULT '[]',
            related_entities TEXT DEFAULT '[]',
            resolution TEXT DEFAULT 'open',
            resolution_note TEXT DEFAULT '',
            FOREIGN KEY (report_id) REFERENCES consistency_reports(id) ON DELETE CASCADE,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：章节工作流 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS workflow_templates (
            id TEXT PRIMARY KEY,
            book_id TEXT,
            user_id TEXT,
            name TEXT NOT NULL,
            steps TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            template_id TEXT,
            status TEXT DEFAULT 'running',
            current_step INTEGER DEFAULT 0,
            step_results TEXT DEFAULT '[]',
            goals TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：增强统计 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS enhanced_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            book_id TEXT,
            agent_role TEXT NOT NULL,
            first_token_latency_ms INTEGER,
            total_duration_ms INTEGER,
            success INTEGER DEFAULT 1,
            retried INTEGER DEFAULT 0,
            adopted INTEGER DEFAULT 0,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')

        # ========== 新增表：Embedding 索引 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS embedding_chunks (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            chunk_index INTEGER DEFAULT 0,
            chunk_text TEXT NOT NULL,
            embedding BLOB,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS embedding_index_meta (
            book_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            dim INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            model_id TEXT NOT NULL,
            last_built_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：NER 实体识别 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS extracted_entities (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            entity_text TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            start_pos INTEGER,
            end_pos INTEGER,
            confidence REAL DEFAULT 0.5,
            source_type TEXT DEFAULT 'auto',
            status TEXT DEFAULT 'pending',
            linked_lorebook_id TEXT,
            link_confidence REAL DEFAULT 0.0,
            created_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS entity_mentions (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            mention_text TEXT NOT NULL,
            start_pos INTEGER,
            end_pos INTEGER,
            context_snippet TEXT DEFAULT '',
            mention_type TEXT DEFAULT 'name',
            created_at TEXT,
            FOREIGN KEY (entity_id) REFERENCES extracted_entities(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：消歧与共指 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS disambiguation_feedback (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            mention_text TEXT NOT NULL,
            resolved_character_id TEXT NOT NULL,
            scope TEXT DEFAULT 'book',
            scope_node_id TEXT,
            created_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS coreference_links (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            mention_id TEXT NOT NULL,
            resolved_character_id TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            resolution_method TEXT DEFAULT 'rule',
            created_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：知识图谱 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            linked_lorebook_id TEXT,
            properties TEXT DEFAULT '{}',
            first_seen_node TEXT,
            last_seen_node TEXT,
            mention_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_edges (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            relation_detail TEXT DEFAULT '',
            evidence_text TEXT DEFAULT '',
            evidence_node_id TEXT,
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'auto',
            valid_from_chapter INTEGER,
            valid_until_chapter INTEGER,
            created_at TEXT,
            FOREIGN KEY (source_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (target_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS story_events (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT,
            actor_node_id TEXT,
            action TEXT NOT NULL,
            target_node_id TEXT,
            location_node_id TEXT,
            story_time TEXT DEFAULT '',
            significance INTEGER DEFAULT 3,
            consequences TEXT DEFAULT '[]',
            participants TEXT DEFAULT '[]',
            evidence_text TEXT DEFAULT '',
            chapter_index INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：伏笔回填 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS foreshadow_payoff_links (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            foreshadow_id TEXT NOT NULL,
            payoff_node_id TEXT NOT NULL,
            payoff_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_text TEXT DEFAULT '',
            auto_detected INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY (foreshadow_id) REFERENCES foreshadowing(id) ON DELETE CASCADE
        )''')

        # ========== 新增表：叙事弧光分析 ==========
        c.execute('''CREATE TABLE IF NOT EXISTS narrative_analysis (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            chapter_index INTEGER DEFAULT 0,
            chapter_title TEXT DEFAULT '',
            tension INTEGER DEFAULT 50,
            conflict_level INTEGER DEFAULT 50,
            pacing TEXT DEFAULT 'moderate',
            emotions TEXT DEFAULT '{}',
            character_focus TEXT DEFAULT '[]',
            overall_role TEXT DEFAULT '',
            key_tension_point TEXT DEFAULT '',
            analyzed_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        )''')

        # 兼容历史库的字段迁移
        self._ensure_column(conn, 'models', 'user_id', 'TEXT')
        self._ensure_column(conn, 'books', 'user_id', 'TEXT')
        self._ensure_column(conn, 'token_stats', 'user_id', 'TEXT')

        # 版本分支增强
        self._ensure_column(conn, 'versions', 'label_name', 'TEXT DEFAULT ""')
        self._ensure_column(conn, 'versions', 'description', 'TEXT DEFAULT ""')
        self._ensure_column(conn, 'versions', 'source_type', 'TEXT DEFAULT "manual"')
        self._ensure_column(conn, 'versions', 'is_candidate', 'INTEGER DEFAULT 0')

        # 人物提醒增强
        self._ensure_column(conn, 'lorebook', 'aliases', 'TEXT DEFAULT ""')
        self._ensure_column(conn, 'lorebook', 'keyword_weights', 'TEXT DEFAULT "{}"')

        # 世界状态增强
        self._ensure_column(conn, 'world_state', 'source_node_id', 'TEXT DEFAULT ""')
        self._ensure_column(conn, 'world_state', 'source_type', 'TEXT DEFAULT "manual"')
        self._ensure_column(conn, 'world_state', 'superseded_by', 'TEXT')
        self._ensure_column(conn, 'world_state', 'valid_from_node', 'TEXT DEFAULT ""')
        self._ensure_column(conn, 'world_state', 'valid_until_node', 'TEXT')

        # 伏笔表增强：回填字段
        self._ensure_column(conn, 'foreshadowing', 'payoff_type', 'TEXT')
        self._ensure_column(conn, 'foreshadowing', 'payoff_evidence', 'TEXT DEFAULT ""')

        # 检索索引（FTS5）
        self._setup_search_index(conn)

        # 初始化默认生成参数
        c.execute('INSERT OR IGNORE INTO generation_params (id) VALUES (1)')

        conn.commit()
        conn.close()
        self._bootstrap_search_index()

    # ============== 检索索引（FTS5） ==============

    def _json_meta(self, payload):
        return json.dumps(payload or {}, ensure_ascii=False)

    def _loads_meta(self, value):
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    def _normalize_search_document(self, book_id, source_type, source_id, title='', body='', meta=None):
        return {
            'book_id': book_id,
            'source_type': source_type,
            'source_id': source_id,
            'title': title or '',
            'body': body or '',
            'meta': dict(meta or {}),
        }

    def upsert_search_document(self, book_id, source_type, source_id, title='', body='', meta=None, conn=None):
        if not self._fts5_enabled or not book_id or not source_id:
            return
        payload = self._normalize_search_document(book_id, source_type, source_id, title, body, meta)
        own_conn = conn is None
        conn = conn or self._conn()
        conn.execute(
            'DELETE FROM search_index WHERE book_id=? AND source_type=? AND source_id=?',
            (book_id, source_type, source_id)
        )
        if payload['title'].strip() or payload['body'].strip():
            conn.execute(
                '''INSERT INTO search_index (book_id, source_type, source_id, title, body, meta)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    payload['book_id'],
                    payload['source_type'],
                    payload['source_id'],
                    payload['title'],
                    payload['body'],
                    self._json_meta(payload['meta']),
                )
            )
        if own_conn:
            conn.commit()
            conn.close()

    def delete_search_documents(self, book_id, source_type=None, source_id=None, conn=None):
        if not self._fts5_enabled or not book_id:
            return
        own_conn = conn is None
        conn = conn or self._conn()
        if source_id:
            conn.execute(
                'DELETE FROM search_index WHERE book_id=? AND source_type=? AND source_id=?',
                (book_id, source_type, source_id)
            )
        elif source_type:
            conn.execute(
                'DELETE FROM search_index WHERE book_id=? AND source_type=?',
                (book_id, source_type)
            )
        else:
            conn.execute('DELETE FROM search_index WHERE book_id=?', (book_id,))
        if own_conn:
            conn.commit()
            conn.close()

    def _prepare_fts_query(self, query):
        tokens = [token.strip() for token in re.split(r'\s+', (query or '').strip()) if token.strip()]
        if not tokens:
            return ''
        return ' AND '.join([f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens])

    def search_documents(self, book_id, query, scope=None, limit=50):
        if not self._fts5_enabled or not book_id or not (query or '').strip():
            return []
        match_query = self._prepare_fts_query(query)
        if not match_query:
            return []
        source_types = None
        if scope:
            source_types = [scope]
        sql = '''
            SELECT source_type, source_id, title, meta,
                   snippet(search_index, 4, '[', ']', '...', 12) AS excerpt
            FROM search_index
            WHERE book_id=? AND search_index MATCH ?
        '''
        params = [book_id, match_query]
        if source_types:
            placeholders = ','.join(['?'] * len(source_types))
            sql += f' AND source_type IN ({placeholders})'
            params.extend(source_types)
        sql += ' ORDER BY rank LIMIT ?'
        params.append(int(limit))
        conn = self._conn()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return []
        conn.close()
        docs = []
        for row in rows:
            item = dict(row)
            item['meta'] = self._loads_meta(item.get('meta'))
            docs.append(item)
        return docs

    def _get_search_content_document(self, source_id):
        conn = self._conn()
        row = conn.execute(
            '''SELECT n.book_id, n.id, n.title, n.type, nc.content
               FROM nodes n
               LEFT JOIN node_contents nc ON nc.node_id = n.id
               WHERE n.id=?''',
            (source_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        return self._normalize_search_document(
            item['book_id'],
            'content',
            item['id'],
            title=item.get('title', ''),
            body=decrypt(item.get('content', '') or ''),
            meta={'node_type': item.get('type', 'chapter')}
        )

    def _get_search_summary_document(self, source_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM chapter_summaries WHERE id=?', (source_id,)).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        chapter_title = decrypt(item.get('chapter_title', ''))
        summary = decrypt(item.get('summary', ''))
        key_events = decrypt(item.get('key_events', ''))
        return self._normalize_search_document(
            item['book_id'],
            'summary',
            item['id'],
            title=chapter_title,
            body='\n'.join([part for part in [summary, key_events] if part]),
            meta={'node_id': item.get('node_id', ''), 'chapter_title': chapter_title}
        )

    def _get_search_lorebook_document(self, source_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM lorebook WHERE id=?', (source_id,)).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        description = decrypt(item.get('description', ''))
        keywords = decrypt(item.get('keywords', ''))
        content = decrypt(item.get('content', ''))
        body = '\n'.join([part for part in [description, content, keywords] if part])
        return self._normalize_search_document(
            item['book_id'],
            'lorebook',
            item['id'],
            title=item.get('name', ''),
            body=body,
            meta={'category': item.get('category', ''), 'keywords': keywords}
        )

    def _get_search_character_history_document(self, source_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM character_history WHERE id=?', (source_id,)).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        summary = decrypt(item.get('summary', ''))
        details = decrypt(item.get('details', ''))
        chapter_title = decrypt(item.get('chapter_title', ''))
        source_excerpt = decrypt(item.get('source_excerpt', ''))
        return self._normalize_search_document(
            item['book_id'],
            'character_history',
            item['id'],
            title=item.get('character_name', ''),
            body='\n'.join([part for part in [summary, details, source_excerpt] if part]),
            meta={
                'character_name': item.get('character_name', ''),
                'chapter_title': chapter_title,
                'entry_type': item.get('entry_type', 'event'),
            }
        )

    def _get_search_world_state_document(self, source_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM world_state WHERE id=?', (source_id,)).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        state_value = decrypt(item.get('state_value', ''))
        scene_context = decrypt(item.get('scene_context', ''))
        body = '\n'.join([
            part for part in [item.get('entity_name', ''), state_value, scene_context] if part
        ])
        return self._normalize_search_document(
            item['book_id'],
            'world_state',
            item['id'],
            title=item.get('entity_name', ''),
            body=body,
            meta={'state_type': item.get('state_type', ''), 'state_value': state_value}
        )

    def _get_search_foreshadowing_document(self, source_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM foreshadowing WHERE id=?', (source_id,)).fetchone()
        conn.close()
        if not row:
            return None
        item = dict(row)
        label = decrypt(item.get('label', ''))
        text = decrypt(item.get('text', ''))
        description = decrypt(item.get('description', ''))
        resolved_text = decrypt(item.get('resolved_text', ''))
        return self._normalize_search_document(
            item['book_id'],
            'foreshadowing',
            item['id'],
            title=label,
            body='\n'.join([part for part in [text, description, resolved_text] if part]),
            meta={'status': item.get('status', 'unresolved'), 'node_id': item.get('node_id', '')}
        )

    def get_search_document(self, source_type, source_id):
        getters = {
            'content': self._get_search_content_document,
            'summary': self._get_search_summary_document,
            'lorebook': self._get_search_lorebook_document,
            'character_history': self._get_search_character_history_document,
            'world_state': self._get_search_world_state_document,
            'foreshadowing': self._get_search_foreshadowing_document,
        }
        getter = getters.get(source_type)
        if not getter or not source_id:
            return None
        return getter(source_id)

    def sync_search_document(self, source_type, source_id, book_id=None):
        if not self._fts5_enabled:
            return False
        doc = self.get_search_document(source_type, source_id)
        if not doc:
            if book_id:
                self.delete_search_documents(book_id, source_type=source_type, source_id=source_id)
            return False
        self.upsert_search_document(
            doc['book_id'],
            doc['source_type'],
            doc['source_id'],
            title=doc.get('title', ''),
            body=doc.get('body', ''),
            meta=doc.get('meta', {}),
        )
        return True

    def rebuild_search_index_for_book(self, book_id):
        if not self._fts5_enabled or not book_id:
            return
        conn = self._conn()
        self.delete_search_documents(book_id, conn=conn)
        for item in self.get_all_node_contents(book_id):
            self.upsert_search_document(
                book_id, 'content', item['id'],
                title=item.get('title', ''),
                body=item.get('content', ''),
                meta={'node_type': item.get('type', 'chapter')},
                conn=conn,
            )
        for item in self.get_chapter_summaries(book_id):
            self.upsert_search_document(
                book_id, 'summary', item['id'],
                title=item.get('chapter_title', ''),
                body='\n'.join([part for part in [item.get('summary', ''), item.get('key_events', '')] if part]),
                meta={'node_id': item.get('node_id', ''), 'chapter_title': item.get('chapter_title', '')},
                conn=conn,
            )
        for item in self.get_lorebook_entries(book_id):
            self.upsert_search_document(
                book_id, 'lorebook', item['id'],
                title=item.get('name', ''),
                body='\n'.join([part for part in [item.get('description', ''), item.get('content', ''), item.get('keywords', '')] if part]),
                meta={'category': item.get('category', ''), 'keywords': item.get('keywords', '')},
                conn=conn,
            )
        for item in self.get_character_history(book_id):
            self.upsert_search_document(
                book_id, 'character_history', item['id'],
                title=item.get('character_name', ''),
                body='\n'.join([part for part in [item.get('summary', ''), item.get('details', ''), item.get('source_excerpt', '')] if part]),
                meta={
                    'character_name': item.get('character_name', ''),
                    'chapter_title': item.get('chapter_title', ''),
                    'entry_type': item.get('entry_type', 'event'),
                },
                conn=conn,
            )
        for item in self.get_world_state(book_id):
            self.upsert_search_document(
                book_id, 'world_state', item['id'],
                title=item.get('entity_name', ''),
                body='\n'.join([part for part in [item.get('entity_name', ''), item.get('state_value', ''), item.get('scene_context', '')] if part]),
                meta={'state_type': item.get('state_type', ''), 'state_value': item.get('state_value', '')},
                conn=conn,
            )
        for item in self.get_foreshadowing(book_id):
            self.upsert_search_document(
                book_id, 'foreshadowing', item['id'],
                title=item.get('label', ''),
                body='\n'.join([part for part in [item.get('text', ''), item.get('description', ''), item.get('resolved_text', '')] if part]),
                meta={'status': item.get('status', 'unresolved'), 'node_id': item.get('node_id', '')},
                conn=conn,
            )
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
            full_key = decrypt(d.pop('api_key_enc', ''))
            # 只返回掩码形式，不返回完整 key
            if full_key and len(full_key) > 8:
                d['api_key_display'] = full_key[:4] + '****' + full_key[-4:]
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
        if self._fts5_enabled:
            self.delete_search_documents(book_id, conn=conn)
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

    def get_book_owner(self, book_id):
        conn = self._conn()
        row = conn.execute('SELECT user_id FROM books WHERE id=?', (book_id,)).fetchone()
        conn.close()
        return row['user_id'] if row else None

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
        book_id = self.get_node_book_id(node_id)
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
        if book_id and any(key in data for key in ['title', 'type']):
            self.sync_search_document('content', node_id, book_id=book_id)
            self._emit_mutation({
                'book_id': book_id,
                'source_type': 'content',
                'source_id': node_id,
                'reason': 'node_meta_updated',
            })

    def delete_node(self, node_id):
        book_id = self.get_node_book_id(node_id)
        conn = self._conn()
        if book_id and self._fts5_enabled:
            self.delete_search_documents(book_id, source_type='content', source_id=node_id, conn=conn)
        conn.execute('DELETE FROM nodes WHERE id=?', (node_id,))
        conn.commit()
        conn.close()
        if book_id:
            self._emit_mutation({
                'book_id': book_id,
                'source_type': 'content',
                'source_id': node_id,
                'deleted': True,
            })

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
        row = conn.execute('SELECT book_id, title, type FROM nodes WHERE id=?', (node_id,)).fetchone()
        conn.execute('''INSERT OR REPLACE INTO node_contents (node_id, content, word_count, updated_at)
                        VALUES (?, ?, ?, ?)''', (node_id, encrypt(content), word_count, now))
        conn.execute('UPDATE nodes SET updated_at=? WHERE id=?', (now, node_id))
        if row and self._fts5_enabled:
            self.upsert_search_document(
                row['book_id'],
                'content',
                node_id,
                title=row['title'],
                body=content,
                meta={'node_type': row['type']},
                conn=conn,
            )
        conn.commit()
        conn.close()
        if row:
            self._emit_mutation({
                'book_id': row['book_id'],
                'source_type': 'content',
                'source_id': node_id,
            })

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
        node_row = conn.execute('SELECT book_id, title, type FROM nodes WHERE id=?', (node_id,)).fetchone()
        if row:
            now = datetime.now().isoformat()
            plain = decrypt(row['content'])
            conn.execute('''INSERT OR REPLACE INTO node_contents (node_id, content, word_count, updated_at)
                            VALUES (?, ?, ?, ?)''', (node_id, row['content'], len(plain), now))
            conn.execute('UPDATE nodes SET updated_at=? WHERE id=?', (now, node_id))
            if node_row and self._fts5_enabled:
                self.upsert_search_document(
                    node_row['book_id'],
                    'content',
                    node_id,
                    title=node_row['title'],
                    body=plain,
                    meta={'node_type': node_row['type']},
                    conn=conn,
                )
        conn.commit()
        conn.close()
        if node_row:
            self._emit_mutation({
                'book_id': node_row['book_id'],
                'source_type': 'content',
                'source_id': node_id,
                'reason': 'version_activated',
            })

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
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'lorebook',
                eid,
                title=data.get('name', ''),
                body='\n'.join([part for part in [data.get('description', ''), data.get('content', ''), data.get('keywords', '')] if part]),
                meta={'category': data.get('category', 'character'), 'keywords': data.get('keywords', '')},
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'lorebook', 'source_id': eid})
        return eid

    def update_lorebook_entry(self, entry_id, data):
        existing = self.get_search_document('lorebook', entry_id)
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
        if existing:
            self.sync_search_document('lorebook', entry_id, book_id=existing['book_id'])
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'lorebook', 'source_id': entry_id})

    def delete_lorebook_entry(self, entry_id):
        existing = self.get_search_document('lorebook', entry_id)
        conn = self._conn()
        if existing and self._fts5_enabled:
            self.delete_search_documents(existing['book_id'], source_type='lorebook', source_id=entry_id, conn=conn)
        conn.execute('DELETE FROM lorebook WHERE id=?', (entry_id,))
        conn.commit()
        conn.close()
        if existing:
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'lorebook', 'source_id': entry_id, 'deleted': True})

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
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'summary',
                sid,
                title=data.get('chapter_title', ''),
                body='\n'.join([part for part in [data.get('summary', ''), data.get('key_events', '')] if part]),
                meta={'node_id': data.get('node_id', ''), 'chapter_title': data.get('chapter_title', '')},
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'summary', 'source_id': sid})
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
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'foreshadowing',
                fid,
                title=data.get('label', ''),
                body='\n'.join([part for part in [data.get('text', ''), data.get('description', '')] if part]),
                meta={'status': data.get('status', 'unresolved'), 'node_id': data.get('node_id', '')},
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'foreshadowing', 'source_id': fid})
        return fid

    def update_foreshadowing(self, fs_id, data):
        existing = self.get_search_document('foreshadowing', fs_id)
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
        if existing:
            self.sync_search_document('foreshadowing', fs_id, book_id=existing['book_id'])
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'foreshadowing', 'source_id': fs_id})

    def delete_foreshadowing(self, fs_id):
        existing = self.get_search_document('foreshadowing', fs_id)
        conn = self._conn()
        if existing and self._fts5_enabled:
            self.delete_search_documents(existing['book_id'], source_type='foreshadowing', source_id=fs_id, conn=conn)
        conn.execute('DELETE FROM foreshadowing WHERE id=?', (fs_id,))
        conn.commit()
        conn.close()
        if existing:
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'foreshadowing', 'source_id': fs_id, 'deleted': True})

    def resolve_foreshadowing(self, fs_id, data):
        existing = self.get_search_document('foreshadowing', fs_id)
        conn = self._conn()
        conn.execute('''UPDATE foreshadowing SET status='resolved', resolved_chapter=?,
                        resolved_node_id=?, resolved_text=? WHERE id=?''',
                     (encrypt(data.get('resolved_chapter', '')), data.get('resolved_node_id', ''),
                      encrypt(data.get('resolved_text', '')), fs_id))
        conn.commit()
        conn.close()
        if existing:
            self.sync_search_document('foreshadowing', fs_id, book_id=existing['book_id'])
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'foreshadowing', 'source_id': fs_id})

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
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'world_state',
                sid,
                title=data.get('entity_name', ''),
                body='\n'.join([part for part in [data.get('entity_name', ''), data.get('state_value', ''), data.get('scene_context', '')] if part]),
                meta={'state_type': data.get('state_type', ''), 'state_value': data.get('state_value', '')},
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'world_state', 'source_id': sid})
        return sid

    def delete_world_state(self, ws_id):
        existing = self.get_search_document('world_state', ws_id)
        conn = self._conn()
        if existing and self._fts5_enabled:
            self.delete_search_documents(existing['book_id'], source_type='world_state', source_id=ws_id, conn=conn)
        conn.execute('DELETE FROM world_state WHERE id=?', (ws_id,))
        conn.commit()
        conn.close()
        if existing:
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'world_state', 'source_id': ws_id, 'deleted': True})

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
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'character_history',
                hid,
                title=data.get('character_name', ''),
                body='\n'.join([part for part in [data.get('summary', ''), data.get('details', ''), data.get('source_excerpt', '')] if part]),
                meta={
                    'character_name': data.get('character_name', ''),
                    'chapter_title': data.get('chapter_title', ''),
                    'entry_type': data.get('entry_type', 'event'),
                },
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'character_history', 'source_id': hid})
        return hid

    def update_character_history(self, history_id, data):
        existing = self.get_search_document('character_history', history_id)
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
        if existing:
            self.sync_search_document('character_history', history_id, book_id=existing['book_id'])
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'character_history', 'source_id': history_id})

    def delete_character_history(self, history_id):
        existing = self.get_search_document('character_history', history_id)
        conn = self._conn()
        if existing and self._fts5_enabled:
            self.delete_search_documents(existing['book_id'], source_type='character_history', source_id=history_id, conn=conn)
        conn.execute('DELETE FROM character_history WHERE id=?', (history_id,))
        conn.commit()
        conn.close()
        if existing:
            self._emit_mutation({'book_id': existing['book_id'], 'source_type': 'character_history', 'source_id': history_id, 'deleted': True})

    def delete_generated_character_history(self, book_id, character_name=None, source_node_id=None):
        existing_entries = self.get_character_history(book_id, character_name=character_name)
        if source_node_id:
            existing_entries = [item for item in existing_entries if item.get('source_node_id') == source_node_id]
        existing_entries = [item for item in existing_entries if not item.get('is_manual')]
        conn = self._conn()
        query = 'DELETE FROM character_history WHERE book_id=? AND is_manual=0'
        vals = [book_id]
        if character_name:
            query += ' AND character_name=?'
            vals.append(character_name)
        if source_node_id:
            query += ' AND source_node_id=?'
            vals.append(source_node_id)
        if self._fts5_enabled:
            for item in existing_entries:
                self.delete_search_documents(book_id, source_type='character_history', source_id=item['id'], conn=conn)
        conn.execute(query, tuple(vals))
        conn.commit()
        conn.close()
        for item in existing_entries:
            self._emit_mutation({'book_id': book_id, 'source_type': 'character_history', 'source_id': item['id'], 'deleted': True})

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

    # ============== 写作规则中心 ==============

    def get_writing_rule_sets(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM writing_rule_sets WHERE book_id=? ORDER BY priority DESC, created_at',
                            (book_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_writing_rule_set(self, data):
        sid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute('''INSERT INTO writing_rule_sets (id, book_id, name, description, enabled, priority, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (sid, data['book_id'], data.get('name', ''), data.get('description', ''),
                      data.get('enabled', 1), data.get('priority', 0), now, now))
        conn.commit()
        conn.close()
        return sid

    def update_writing_rule_set(self, set_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['name', 'description', 'enabled', 'priority']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        fields.append('updated_at=?')
        vals.append(datetime.now().isoformat())
        vals.append(set_id)
        conn.execute(f'UPDATE writing_rule_sets SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_writing_rule_set(self, set_id):
        conn = self._conn()
        conn.execute('DELETE FROM writing_rule_sets WHERE id=?', (set_id,))
        conn.commit()
        conn.close()

    def get_writing_rules(self, book_id, rule_set_id=None, category=None):
        conn = self._conn()
        query = 'SELECT * FROM writing_rules WHERE book_id=?'
        vals = [book_id]
        if rule_set_id:
            query += ' AND rule_set_id=?'
            vals.append(rule_set_id)
        if category:
            query += ' AND category=?'
            vals.append(category)
        query += ' ORDER BY priority DESC, created_at'
        rows = conn.execute(query, tuple(vals)).fetchall()
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
        conn = self._conn()
        conn.execute('''INSERT INTO writing_rules (id, rule_set_id, book_id, category, title, content,
                        scope_type, scope_node_id, enabled, priority, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (rid, data['rule_set_id'], data['book_id'], data.get('category', 'style'),
                      encrypt(data.get('title', '')), encrypt(data.get('content', '')),
                      data.get('scope_type', 'book'), data.get('scope_node_id'),
                      data.get('enabled', 1), data.get('priority', 0),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return rid

    def update_writing_rule(self, rule_id, data):
        conn = self._conn()
        fields, vals = [], []
        encrypted_fields = {'title', 'content'}
        for k in ['category', 'title', 'content', 'scope_type', 'scope_node_id', 'enabled', 'priority']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        vals.append(rule_id)
        conn.execute(f'UPDATE writing_rules SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_writing_rule(self, rule_id):
        conn = self._conn()
        conn.execute('DELETE FROM writing_rules WHERE id=?', (rule_id,))
        conn.commit()
        conn.close()

    # ============== 时间线与事件账本 ==============

    def get_timeline_events(self, book_id, entity_name=None, node_id=None, event_type=None):
        conn = self._conn()
        query = 'SELECT * FROM timeline_events WHERE book_id=?'
        vals = [book_id]
        if entity_name:
            query += ' AND entity_name=?'
            vals.append(entity_name)
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        if event_type:
            query += ' AND event_type=?'
            vals.append(event_type)
        query += ' ORDER BY chapter_index, event_order'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['description', 'location'])
            result.append(d)
        return result

    def add_timeline_event(self, data):
        eid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO timeline_events (id, book_id, node_id, event_type, entity_name,
                        description, location, chapter_index, event_order, source, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (eid, data['book_id'], data.get('node_id'), data.get('event_type', 'action'),
                      data['entity_name'], encrypt(data.get('description', '')),
                      encrypt(data.get('location', '')),
                      data.get('chapter_index', 0), data.get('event_order', 0),
                      data.get('source', 'auto'), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return eid

    def update_timeline_event(self, event_id, data):
        conn = self._conn()
        fields, vals = [], []
        encrypted_fields = {'description', 'location'}
        for k in ['event_type', 'entity_name', 'description', 'location', 'chapter_index', 'event_order', 'source']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(encrypt(data[k]) if k in encrypted_fields else data[k])
        vals.append(event_id)
        conn.execute(f'UPDATE timeline_events SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def delete_timeline_event(self, event_id):
        conn = self._conn()
        conn.execute('DELETE FROM timeline_events WHERE id=?', (event_id,))
        conn.commit()
        conn.close()

    def delete_timeline_events_for_node(self, book_id, node_id):
        conn = self._conn()
        conn.execute('DELETE FROM timeline_events WHERE book_id=? AND node_id=? AND source=?',
                     (book_id, node_id, 'auto'))
        conn.commit()
        conn.close()

    def get_entity_state_transitions(self, book_id, entity_name=None):
        conn = self._conn()
        query = 'SELECT * FROM entity_state_transitions WHERE book_id=?'
        vals = [book_id]
        if entity_name:
            query += ' AND entity_name=?'
            vals.append(entity_name)
        query += ' ORDER BY created_at'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['old_value', 'new_value'])
            result.append(d)
        return result

    def add_entity_state_transition(self, data):
        tid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO entity_state_transitions (id, book_id, entity_name, state_type,
                        old_value, new_value, cause_event_id, start_node_id, end_node_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (tid, data['book_id'], data['entity_name'], data.get('state_type', 'location'),
                      encrypt(data.get('old_value', '')), encrypt(data.get('new_value', '')),
                      data.get('cause_event_id'), data.get('start_node_id'),
                      data.get('end_node_id'), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return tid

    # ============== 快照与回收站 ==============

    def get_snapshots(self, book_id, node_id=None, limit=50):
        conn = self._conn()
        query = 'SELECT id, book_id, node_id, snapshot_type, label, created_at FROM snapshots WHERE book_id=?'
        vals = [book_id]
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        query += ' ORDER BY created_at DESC LIMIT ?'
        vals.append(limit)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_snapshot(self, snapshot_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        d['content_data'] = decrypt(d.get('content_data', ''))
        return d

    def create_snapshot(self, data):
        sid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO snapshots (id, book_id, node_id, snapshot_type, label, content_data, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (sid, data['book_id'], data.get('node_id'), data.get('snapshot_type', 'manual'),
                      data.get('label', ''), encrypt(json.dumps(data.get('content_data', {}), ensure_ascii=False)),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return sid

    def delete_snapshot(self, snapshot_id):
        conn = self._conn()
        conn.execute('DELETE FROM snapshots WHERE id=?', (snapshot_id,))
        conn.commit()
        conn.close()

    def cleanup_snapshots(self, book_id, keep_count=50):
        conn = self._conn()
        conn.execute('''DELETE FROM snapshots WHERE id IN (
            SELECT id FROM snapshots WHERE book_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?
        )''', (book_id, keep_count))
        conn.commit()
        conn.close()

    def get_recycle_bin(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT id, book_id, item_type, item_id, deleted_at FROM recycle_bin WHERE book_id=? ORDER BY deleted_at DESC',
                            (book_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_to_recycle_bin(self, data):
        rid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO recycle_bin (id, book_id, item_type, item_id, item_data, deleted_at)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (rid, data['book_id'], data['item_type'], data['item_id'],
                      encrypt(json.dumps(data.get('item_data', {}), ensure_ascii=False)),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return rid

    def get_recycle_bin_item(self, recycle_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM recycle_bin WHERE id=?', (recycle_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        d['item_data'] = decrypt(d.get('item_data', ''))
        return d

    def delete_recycle_bin_item(self, recycle_id):
        conn = self._conn()
        conn.execute('DELETE FROM recycle_bin WHERE id=?', (recycle_id,))
        conn.commit()
        conn.close()

    # ============== 异步任务中心 ==============

    def get_async_jobs(self, user_id, status=None, limit=50):
        conn = self._conn()
        query = 'SELECT * FROM async_jobs WHERE user_id=?'
        vals = [user_id]
        if status:
            query += ' AND status=?'
            vals.append(status)
        query += ' ORDER BY created_at DESC LIMIT ?'
        vals.append(limit)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_async_job(self, job_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM async_jobs WHERE id=?', (job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_async_job(self, data):
        jid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO async_jobs (id, user_id, book_id, job_type, status,
                        total_steps, created_at)
                        VALUES (?, ?, ?, ?, 'pending', ?, ?)''',
                     (jid, data['user_id'], data.get('book_id'), data['job_type'],
                      data.get('total_steps', 0), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jid

    def update_async_job(self, job_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['status', 'progress', 'current_step', 'total_steps', 'result_summary',
                   'error_message', 'started_at', 'completed_at']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        vals.append(job_id)
        conn.execute(f'UPDATE async_jobs SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def add_job_log(self, job_id, level, message):
        conn = self._conn()
        conn.execute('''INSERT INTO job_logs (job_id, level, message, created_at)
                        VALUES (?, ?, ?, ?)''', (job_id, level, message, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_job_logs(self, job_id, limit=100):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM job_logs WHERE job_id=? ORDER BY created_at DESC LIMIT ?',
                            (job_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ============== 记忆注入日志 ==============

    def save_memory_injection_log(self, data):
        lid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO memory_injection_logs (id, book_id, node_id, agent_role,
                        injected_items, candidate_items, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (lid, data['book_id'], data.get('node_id'), data['agent_role'],
                      json.dumps(data.get('injected_items', []), ensure_ascii=False),
                      json.dumps(data.get('candidate_items', []), ensure_ascii=False),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return lid

    def get_memory_injection_logs(self, book_id, node_id=None, limit=10):
        conn = self._conn()
        query = 'SELECT * FROM memory_injection_logs WHERE book_id=?'
        vals = [book_id]
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        query += ' ORDER BY created_at DESC LIMIT ?'
        vals.append(limit)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_pinned_memories(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM pinned_memories WHERE book_id=? ORDER BY created_at',
                            (book_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_pinned_memory(self, data):
        pid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO pinned_memories (id, book_id, memory_type, memory_ref, action, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (pid, data['book_id'], data['memory_type'], data['memory_ref'],
                      data.get('action', 'pin'), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return pid

    def delete_pinned_memory(self, pin_id):
        conn = self._conn()
        conn.execute('DELETE FROM pinned_memories WHERE id=?', (pin_id,))
        conn.commit()
        conn.close()

    # ============== 一致性报告 ==============

    def create_consistency_report(self, data):
        rid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO consistency_reports (id, book_id, status, created_at)
                        VALUES (?, ?, 'running', ?)''',
                     (rid, data['book_id'], datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return rid

    def update_consistency_report(self, report_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['status', 'issue_count', 'high_count', 'medium_count', 'low_count', 'completed_at']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        vals.append(report_id)
        conn.execute(f'UPDATE consistency_reports SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    def get_consistency_reports(self, book_id):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM consistency_reports WHERE book_id=? ORDER BY created_at DESC',
                            (book_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_consistency_report(self, report_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM consistency_reports WHERE id=?', (report_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def add_consistency_issue(self, data):
        iid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO consistency_issues (id, report_id, book_id, issue_type, severity,
                        title, description, evidence, related_node_ids, related_entities)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (iid, data['report_id'], data['book_id'], data['issue_type'],
                      data.get('severity', 'medium'), encrypt(data.get('title', '')),
                      encrypt(data.get('description', '')),
                      json.dumps(data.get('evidence', []), ensure_ascii=False),
                      json.dumps(data.get('related_node_ids', []), ensure_ascii=False),
                      json.dumps(data.get('related_entities', []), ensure_ascii=False)))
        conn.commit()
        conn.close()
        return iid

    def get_consistency_issues(self, report_id=None, book_id=None, resolution=None):
        conn = self._conn()
        query = 'SELECT * FROM consistency_issues WHERE 1=1'
        vals = []
        if report_id:
            query += ' AND report_id=?'
            vals.append(report_id)
        if book_id:
            query += ' AND book_id=?'
            vals.append(book_id)
        if resolution:
            query += ' AND resolution=?'
            vals.append(resolution)
        query += ' ORDER BY CASE severity WHEN "high" THEN 0 WHEN "medium" THEN 1 ELSE 2 END'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['title', 'description'])
            result.append(d)
        return result

    def update_consistency_issue(self, issue_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['resolution', 'resolution_note']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        vals.append(issue_id)
        conn.execute(f'UPDATE consistency_issues SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    # ============== 章节工作流 ==============

    def get_workflow_templates(self, user_id=None, book_id=None):
        conn = self._conn()
        query = 'SELECT * FROM workflow_templates WHERE 1=1'
        vals = []
        if user_id:
            query += ' AND (user_id=? OR user_id IS NULL)'
            vals.append(user_id)
        if book_id:
            query += ' AND (book_id=? OR book_id IS NULL)'
            vals.append(book_id)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_workflow_template(self, data):
        tid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO workflow_templates (id, book_id, user_id, name, steps, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (tid, data.get('book_id'), data.get('user_id'), data['name'],
                      json.dumps(data.get('steps', []), ensure_ascii=False),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return tid

    def create_workflow_run(self, data):
        rid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO workflow_runs (id, book_id, node_id, template_id, status,
                        goals, created_at)
                        VALUES (?, ?, ?, ?, 'running', ?, ?)''',
                     (rid, data['book_id'], data['node_id'], data.get('template_id'),
                      data.get('goals', ''), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return rid

    def get_workflow_run(self, run_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM workflow_runs WHERE id=?', (run_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_workflow_run(self, run_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['status', 'current_step', 'step_results', 'completed_at']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        vals.append(run_id)
        conn.execute(f'UPDATE workflow_runs SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    # ============== 增强统计 ==============

    def record_enhanced_stat(self, data):
        conn = self._conn()
        conn.execute('''INSERT INTO enhanced_stats (user_id, book_id, agent_role,
                        first_token_latency_ms, total_duration_ms, success, retried, adopted,
                        prompt_tokens, completion_tokens, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (data['user_id'], data.get('book_id'), data['agent_role'],
                      data.get('first_token_latency_ms'), data.get('total_duration_ms'),
                      data.get('success', 1), data.get('retried', 0), data.get('adopted', 0),
                      data.get('prompt_tokens', 0), data.get('completion_tokens', 0),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_enhanced_stats(self, user_id, book_id=None):
        conn = self._conn()
        query = '''SELECT agent_role,
                   COUNT(*) as call_count,
                   SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as success_count,
                   SUM(CASE WHEN retried=1 THEN 1 ELSE 0 END) as retry_count,
                   SUM(CASE WHEN adopted=1 THEN 1 ELSE 0 END) as adopt_count,
                   AVG(first_token_latency_ms) as avg_first_token_ms,
                   AVG(total_duration_ms) as avg_duration_ms,
                   SUM(prompt_tokens) as total_prompt_tokens,
                   SUM(completion_tokens) as total_completion_tokens
                   FROM enhanced_stats WHERE user_id=?'''
        vals = [user_id]
        if book_id:
            query += ' AND book_id=?'
            vals.append(book_id)
        query += ' GROUP BY agent_role ORDER BY call_count DESC'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_stat_adopted(self, user_id, agent_role, book_id=None):
        conn = self._conn()
        query = '''UPDATE enhanced_stats SET adopted=1 WHERE id=(
                   SELECT id FROM enhanced_stats WHERE user_id=? AND agent_role=?'''
        vals = [user_id, agent_role]
        if book_id:
            query += ' AND book_id=?'
            vals.append(book_id)
        query += ' ORDER BY created_at DESC LIMIT 1)'
        conn.execute(query, tuple(vals))
        conn.commit()
        conn.close()

    # ============== 世界状态增强 ==============

    def get_world_state_history(self, book_id, entity_name=None):
        conn = self._conn()
        query = 'SELECT * FROM world_state WHERE book_id=? ORDER BY updated_at DESC'
        vals = [book_id]
        if entity_name:
            query = 'SELECT * FROM world_state WHERE book_id=? AND entity_name=? ORDER BY updated_at DESC'
            vals.append(entity_name)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['state_value', 'scene_context'])
            result.append(d)
        return result

    def get_current_world_state(self, book_id, entity_name=None):
        conn = self._conn()
        query = 'SELECT * FROM world_state WHERE book_id=? AND superseded_by IS NULL'
        vals = [book_id]
        if entity_name:
            query += ' AND entity_name=?'
            vals.append(entity_name)
        query += ' ORDER BY updated_at DESC'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            self._decrypt_fields(d, ['state_value', 'scene_context'])
            result.append(d)
        return result

    def upsert_world_state_v2(self, data):
        conn = self._conn()
        row = conn.execute('''SELECT id FROM world_state WHERE book_id=? AND entity_name=?
                             AND state_type=? AND superseded_by IS NULL''',
                           (data['book_id'], data['entity_name'], data['state_type'])).fetchone()
        now = datetime.now().isoformat()
        new_id = str(uuid.uuid4())[:8]
        if row:
            conn.execute('UPDATE world_state SET superseded_by=? WHERE id=?', (new_id, row['id']))
        conn.execute('''INSERT INTO world_state (id, book_id, entity_name, state_type, state_value,
                       scene_context, last_updated_node, source_node_id, source_type,
                       valid_from_node, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (new_id, data['book_id'], data['entity_name'], data['state_type'],
                      encrypt(data.get('state_value', '')), encrypt(data.get('scene_context', '')),
                      data.get('last_updated_node', ''), data.get('source_node_id', ''),
                      data.get('source_type', 'manual'), data.get('valid_from_node', ''), now))
        if self._fts5_enabled:
            self.upsert_search_document(
                data['book_id'],
                'world_state',
                new_id,
                title=data.get('entity_name', ''),
                body='\n'.join([part for part in [data.get('entity_name', ''), data.get('state_value', ''), data.get('scene_context', '')] if part]),
                meta={'state_type': data.get('state_type', ''), 'state_value': data.get('state_value', '')},
                conn=conn,
            )
        conn.commit()
        conn.close()
        self._emit_mutation({'book_id': data['book_id'], 'source_type': 'world_state', 'source_id': new_id})
        return new_id

    # ============== 版本分支增强 ==============

    def create_version_v2(self, data):
        vid = str(uuid.uuid4())[:8]
        conn = self._conn()
        conn.execute('''INSERT INTO versions (id, node_id, label, content, is_active, label_name,
                        description, source_type, is_candidate, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (vid, data['node_id'], data.get('label', 'A'),
                      encrypt(data.get('content', '')), data.get('is_active', 0),
                      data.get('label_name', ''), data.get('description', ''),
                      data.get('source_type', 'manual'), data.get('is_candidate', 0),
                      datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return vid

    def update_version(self, ver_id, data):
        conn = self._conn()
        fields, vals = [], []
        for k in ['label_name', 'description', 'source_type', 'is_candidate']:
            if k in data:
                fields.append(f'{k}=?')
                vals.append(data[k])
        if 'content' in data:
            fields.append('content=?')
            vals.append(encrypt(data['content']))
        vals.append(ver_id)
        conn.execute(f'UPDATE versions SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        conn.close()

    # ============== 全书节点遍历（供搜索使用）==============

    def get_all_node_contents(self, book_id):
        conn = self._conn()
        rows = conn.execute('''SELECT n.id, n.title, n.type, n.sort_order, nc.content
                               FROM nodes n LEFT JOIN node_contents nc ON n.id = nc.node_id
                               WHERE n.book_id=? ORDER BY n.sort_order''',
                            (book_id,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d['content'] = decrypt(d.get('content', '') or '')
            result.append(d)
        return result

    # ============== Embedding 索引 ==============

    def save_embedding_chunks(self, *args):
        if len(args) == 1:
            chunks = args[0]
        elif len(args) == 2:
            _, chunks = args
        else:
            raise TypeError('save_embedding_chunks expects chunks or (book_id, chunks)')
        conn = self._conn()
        for ch in chunks:
            chunk_text = ch.get('chunk_text', ch.get('text', ''))
            conn.execute(
                '''INSERT OR REPLACE INTO embedding_chunks
                   (id, book_id, source_type, source_id, chunk_index, chunk_text, embedding, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (ch['id'], ch['book_id'], ch['source_type'], ch['source_id'],
                 ch.get('chunk_index', 0), chunk_text,
                 ch.get('embedding'), json.dumps(ch.get('metadata', {}), ensure_ascii=False),
                 ch.get('created_at', datetime.now().isoformat()))
            )
        conn.commit()
        conn.close()

    def get_embedding_chunks(self, book_id, source_type=None):
        conn = self._conn()
        if source_type:
            rows = conn.execute(
                'SELECT * FROM embedding_chunks WHERE book_id=? AND source_type=? ORDER BY chunk_index',
                (book_id, source_type)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM embedding_chunks WHERE book_id=? ORDER BY source_type, chunk_index',
                (book_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_embedding_chunks_by_source(self, book_id, source_type=None, source_id=None):
        conn = self._conn()
        if source_id:
            conn.execute('DELETE FROM embedding_chunks WHERE book_id=? AND source_id=?', (book_id, source_id))
        elif source_type:
            conn.execute('DELETE FROM embedding_chunks WHERE book_id=? AND source_type=?', (book_id, source_type))
        else:
            conn.execute('DELETE FROM embedding_chunks WHERE book_id=?', (book_id,))
        conn.commit()
        conn.close()

    def save_index_meta(self, meta):
        conn = self._conn()
        conn.execute(
            '''INSERT OR REPLACE INTO embedding_index_meta
               (book_id, user_id, dim, chunk_count, model_id, last_built_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (meta['book_id'], meta['user_id'], meta['dim'],
             meta['chunk_count'], meta['model_id'],
             meta.get('last_built_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_index_meta(self, book_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM embedding_index_meta WHERE book_id=?', (book_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def delete_embedding_chunks(self, book_id):
        self.delete_embedding_chunks_by_source(book_id)

    def save_embedding_index_meta(self, book_id, user_id, dim, chunk_count, model_id, last_built_at=None):
        self.save_index_meta({
            'book_id': book_id,
            'user_id': user_id,
            'dim': dim,
            'chunk_count': chunk_count,
            'model_id': model_id,
            'last_built_at': last_built_at,
        })

    def get_embedding_index_meta(self, book_id):
        return self.get_index_meta(book_id)

    # ============== NER 实体识别 ==============

    def add_extracted_entity(self, entity):
        conn = self._conn()
        conn.execute(
            '''INSERT OR REPLACE INTO extracted_entities
               (id, book_id, node_id, entity_text, entity_type, start_pos, end_pos,
                confidence, source_type, status, linked_lorebook_id, link_confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (entity['id'], entity['book_id'], entity.get('node_id'),
             entity['entity_text'], entity['entity_type'],
             entity.get('start_pos'), entity.get('end_pos'),
             entity.get('confidence', 0.5), entity.get('source_type', 'auto'),
             entity.get('status', 'pending'), entity.get('linked_lorebook_id'),
             entity.get('link_confidence', 0.0),
             entity.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_extracted_entities(self, book_id, node_id=None, entity_type=None, status=None):
        conn = self._conn()
        query = 'SELECT * FROM extracted_entities WHERE book_id=?'
        vals = [book_id]
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        if entity_type:
            query += ' AND entity_type=?'
            vals.append(entity_type)
        if status:
            query += ' AND status=?'
            vals.append(status)
        query += ' ORDER BY created_at DESC'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_entity_status(self, entity_id, status):
        conn = self._conn()
        conn.execute('UPDATE extracted_entities SET status=? WHERE id=?', (status, entity_id))
        conn.commit()
        conn.close()

    def link_entity_to_lorebook(self, entity_id, lorebook_id, confidence=0.9):
        conn = self._conn()
        conn.execute(
            'UPDATE extracted_entities SET linked_lorebook_id=?, link_confidence=?, status=? WHERE id=?',
            (lorebook_id, confidence, 'confirmed', entity_id)
        )
        conn.commit()
        conn.close()

    def delete_entities_for_node(self, book_id, node_id):
        conn = self._conn()
        conn.execute('DELETE FROM extracted_entities WHERE book_id=? AND node_id=?', (book_id, node_id))
        conn.execute('DELETE FROM entity_mentions WHERE book_id=? AND node_id=?', (book_id, node_id))
        conn.commit()
        conn.close()

    def get_unlinked_entities(self, book_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM extracted_entities WHERE book_id=? AND linked_lorebook_id IS NULL AND status != 'dismissed' ORDER BY confidence DESC",
            (book_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_entity_mention(self, mention):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO entity_mentions
               (id, book_id, entity_id, node_id, mention_text, start_pos, end_pos,
                context_snippet, mention_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (mention['id'], mention['book_id'], mention['entity_id'],
             mention['node_id'], mention['mention_text'],
             mention.get('start_pos'), mention.get('end_pos'),
             mention.get('context_snippet', ''), mention.get('mention_type', 'name'),
             mention.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_entity_mentions(self, book_id, entity_id=None, node_id=None):
        conn = self._conn()
        query = 'SELECT * FROM entity_mentions WHERE book_id=?'
        vals = [book_id]
        if entity_id:
            query += ' AND entity_id=?'
            vals.append(entity_id)
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ============== 消歧与共指 ==============

    def add_disambiguation_feedback(self, record):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO disambiguation_feedback
               (id, book_id, mention_text, resolved_character_id, scope, scope_node_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (record['id'], record['book_id'], record['mention_text'],
             record['resolved_character_id'], record.get('scope', 'book'),
             record.get('scope_node_id'),
             record.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_disambiguation_feedbacks(self, book_id):
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM disambiguation_feedback WHERE book_id=? ORDER BY created_at DESC',
            (book_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_disambiguation_feedback(self, feedback_id):
        conn = self._conn()
        conn.execute('DELETE FROM disambiguation_feedback WHERE id=?', (feedback_id,))
        conn.commit()
        conn.close()

    def add_coreference_link(self, link):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO coreference_links
               (id, book_id, node_id, mention_id, resolved_character_id, confidence, resolution_method, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (link['id'], link['book_id'], link['node_id'], link.get('mention_id', ''),
             link['resolved_character_id'], link.get('confidence', 0.5),
             link.get('resolution_method', 'rule'),
             link.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    # ============== 知识图谱 ==============

    def add_knowledge_node(self, node):
        conn = self._conn()
        now = datetime.now().isoformat()
        conn.execute(
            '''INSERT OR REPLACE INTO knowledge_nodes
               (id, book_id, entity_name, entity_type, description, linked_lorebook_id,
                properties, first_seen_node, last_seen_node, mention_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (node['id'], node['book_id'], node['entity_name'], node['entity_type'],
             node.get('description', ''), node.get('linked_lorebook_id'),
             json.dumps(node.get('properties', {}), ensure_ascii=False),
             node.get('first_seen_node'), node.get('last_seen_node'),
             node.get('mention_count', 0),
             node.get('created_at', now), now)
        )
        conn.commit()
        conn.close()

    def get_knowledge_nodes(self, book_id, entity_type=None):
        conn = self._conn()
        if entity_type:
            rows = conn.execute(
                'SELECT * FROM knowledge_nodes WHERE book_id=? AND entity_type=? ORDER BY mention_count DESC',
                (book_id, entity_type)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM knowledge_nodes WHERE book_id=? ORDER BY mention_count DESC',
                (book_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_knowledge_node_by_name(self, book_id, entity_name):
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM knowledge_nodes WHERE book_id=? AND entity_name=?',
            (book_id, entity_name)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def add_knowledge_edge(self, edge):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO knowledge_edges
               (id, book_id, source_node_id, target_node_id, relation_type, relation_detail,
                evidence_text, evidence_node_id, confidence, status, valid_from_chapter, valid_until_chapter, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (edge['id'], edge['book_id'], edge['source_node_id'], edge['target_node_id'],
             edge['relation_type'], edge.get('relation_detail', ''),
             edge.get('evidence_text', ''), edge.get('evidence_node_id'),
             edge.get('confidence', 0.5), edge.get('status', 'auto'),
             edge.get('valid_from_chapter'), edge.get('valid_until_chapter'),
             edge.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_knowledge_edges(self, book_id, node_id=None, status=None):
        conn = self._conn()
        query = 'SELECT * FROM knowledge_edges WHERE book_id=?'
        vals = [book_id]
        if node_id:
            query += ' AND (source_node_id=? OR target_node_id=?)'
            vals.extend([node_id, node_id])
        if status:
            query += ' AND status=?'
            vals.append(status)
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_knowledge_edge_status(self, edge_id, status):
        conn = self._conn()
        conn.execute('UPDATE knowledge_edges SET status=? WHERE id=?', (status, edge_id))
        conn.commit()
        conn.close()

    def delete_knowledge_nodes(self, node_ids):
        conn = self._conn()
        for nid in node_ids:
            conn.execute('DELETE FROM knowledge_edges WHERE source_node_id=? OR target_node_id=?', (nid, nid))
            conn.execute('DELETE FROM knowledge_nodes WHERE id=?', (nid,))
        conn.commit()
        conn.close()

    def add_story_event(self, event):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO story_events
               (id, book_id, node_id, actor_node_id, action, target_node_id, location_node_id,
                story_time, significance, consequences, participants, evidence_text, chapter_index, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (event['id'], event['book_id'], event.get('node_id'),
             event.get('actor_node_id'), event['action'],
             event.get('target_node_id'), event.get('location_node_id'),
             event.get('story_time', ''), event.get('significance', 3),
             json.dumps(event.get('consequences', []), ensure_ascii=False),
             json.dumps(event.get('participants', []), ensure_ascii=False),
             event.get('evidence_text', ''), event.get('chapter_index', 0),
             event.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_story_events(self, book_id, node_id=None, actor_node_id=None):
        conn = self._conn()
        query = 'SELECT * FROM story_events WHERE book_id=?'
        vals = [book_id]
        if node_id:
            query += ' AND node_id=?'
            vals.append(node_id)
        if actor_node_id:
            query += ' AND actor_node_id=?'
            vals.append(actor_node_id)
        query += ' ORDER BY chapter_index'
        rows = conn.execute(query, tuple(vals)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ============== 伏笔回填 ==============

    def add_foreshadow_payoff_link(self, link):
        conn = self._conn()
        conn.execute(
            '''INSERT INTO foreshadow_payoff_links
               (id, book_id, foreshadow_id, payoff_node_id, payoff_type, confidence,
                evidence_text, auto_detected, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (link['id'], link['book_id'], link['foreshadow_id'],
             link['payoff_node_id'], link['payoff_type'],
             link.get('confidence', 0.5), link.get('evidence_text', ''),
             link.get('auto_detected', 1),
             link.get('created_at', datetime.now().isoformat()))
        )
        conn.commit()
        conn.close()

    def get_foreshadow_payoff_links(self, book_id, foreshadow_id=None):
        conn = self._conn()
        if foreshadow_id:
            rows = conn.execute(
                'SELECT * FROM foreshadow_payoff_links WHERE book_id=? AND foreshadow_id=? ORDER BY created_at DESC',
                (book_id, foreshadow_id)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM foreshadow_payoff_links WHERE book_id=? ORDER BY created_at DESC',
                (book_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_foreshadow_payoff_link(self, link_id):
        conn = self._conn()
        conn.execute('DELETE FROM foreshadow_payoff_links WHERE id=?', (link_id,))
        conn.commit()
        conn.close()

    def update_foreshadowing_payoff(self, foreshadow_id, payoff_type, evidence, resolved_node_id='', resolved_chapter=''):
        conn = self._conn()
        conn.execute(
            '''UPDATE foreshadowing SET status=?, payoff_type=?, payoff_evidence=?,
               resolved_node_id=?, resolved_chapter=? WHERE id=?''',
            ('resolved' if payoff_type == 'resolved' else 'partial',
             payoff_type, evidence, resolved_node_id, resolved_chapter, foreshadow_id)
        )
        conn.commit()
        conn.close()

    def undo_foreshadowing_payoff(self, foreshadow_id):
        conn = self._conn()
        conn.execute(
            "UPDATE foreshadowing SET status='unresolved', payoff_type=NULL, payoff_evidence='', resolved_node_id='', resolved_chapter='' WHERE id=?",
            (foreshadow_id,)
        )
        conn.commit()
        conn.close()

    # ============== 叙事弧光分析 ==============

    def get_narrative_analysis(self, book_id, node_id=None):
        conn = self._conn()
        if node_id:
            rows = conn.execute(
                'SELECT * FROM narrative_analysis WHERE book_id=? AND node_id=?',
                (book_id, node_id)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM narrative_analysis WHERE book_id=? ORDER BY chapter_index',
                (book_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
