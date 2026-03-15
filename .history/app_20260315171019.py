"""
AI 辅助长篇小说写作平台 - 主应用入口
"""
import os
import json
import time
import uuid
import hashlib
import sqlite3
import re
import math
import secrets
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import Flask, request, jsonify, render_template, Response, stream_with_context, send_file, g, abort
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

from database import Database
from agents import AgentOrchestrator
from memory_engine import MemoryEngine
from export_engine import ExportEngine
from rule_engine import RuleEngine
from timeline_engine import TimelineEngine
from snapshot_engine import SnapshotEngine
from search_engine import SearchEngine
from job_engine import JobEngine
from workflow_engine import WorkflowEngine
from consistency_engine import ConsistencyEngine
from stats_engine import StatsEngine
from embedding_engine import EmbeddingEngine
from ner_engine import NEREngine
from disambiguation_engine import DisambiguationEngine
from knowledge_graph_engine import KnowledgeGraphEngine
from foreshadow_engine import ForeshadowEngine
from narrative_engine import NarrativeEngine
from role_registry import get_frontend_role_registry, get_routing_role_ids


def _get_jwt_secret():
    """从环境变量或本地文件加载 JWT 密钥，不使用硬编码默认值"""
    env_key = os.environ.get('APP_SECRET_KEY')
    if env_key and len(env_key) >= 32:
        return env_key
    secret_path = os.path.join(os.path.dirname(__file__), '.jwt_secret')
    if os.path.exists(secret_path):
        with open(secret_path, 'r') as f:
            return f.read().strip()
    new_secret = secrets.token_hex(32)
    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(new_secret)
    return new_secret


app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = _get_jwt_secret()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'http://localhost:5000').split(',')
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins=ALLOWED_ORIGINS, async_mode='threading')

db = Database()
agent_orchestrator = AgentOrchestrator(db)
memory_engine = MemoryEngine(db)
export_engine = ExportEngine(db)
rule_engine = RuleEngine(db)
timeline_engine = TimelineEngine(db)
snapshot_engine = SnapshotEngine(db)
search_engine = SearchEngine(db)
job_engine = JobEngine(db)
workflow_engine = WorkflowEngine(db)
consistency_engine = ConsistencyEngine(db)
stats_engine = StatsEngine(db)
embedding_engine = EmbeddingEngine(db)
ner_engine = NEREngine(db)
disambiguation_engine = DisambiguationEngine(db)
knowledge_graph_engine = KnowledgeGraphEngine(db)
foreshadow_engine = ForeshadowEngine(db)
narrative_engine = NarrativeEngine(db)
db.init_db()


def _refresh_retrieval_artifacts(event):
    book_id = event.get('book_id')
    source_type = event.get('source_type')
    source_id = event.get('source_id')
    if not book_id or not source_type or not source_id:
        return

    try:
        memory_engine.refresh_retrieval_index(book_id, source_type=source_type, source_id=source_id)
    except Exception:
        pass

    try:
        if not db.get_embedding_index_meta(book_id):
            return
        user_id = db.get_book_owner(book_id)
        if not user_id:
            return
        embedding_engine.incremental_update(book_id, user_id, source_type, source_id)
    except Exception:
        pass


db.register_mutation_listener(_refresh_retrieval_artifacts)

# P0: 注入摘要回调，让 MemoryEngine 在无摘要时自动触发 LLM 摘要
def _summarizer_callback(book_id, node_id, chapter_title, text):
    result = agent_orchestrator.run_summarizer({
        'book_id': book_id,
        'node_id': node_id,
        'chapter_title': chapter_title,
        'text': text
    })
    return result.get('summary', '')

memory_engine.set_summarizer_callback(_summarizer_callback)
memory_engine.set_embedding_engine(embedding_engine)
memory_engine.set_disambiguation_engine(disambiguation_engine)
agent_orchestrator.set_rule_engine(rule_engine)
agent_orchestrator.set_stats_engine(stats_engine)

# NER LLM 回调注入
def _ner_extractor_callback(text, known_entities):
    result = agent_orchestrator.run_ner_extract({'text': text, 'known_entities': known_entities})
    return result.get('entities', result) if isinstance(result, dict) else result

ner_engine.set_llm_extractor(_ner_extractor_callback)

# 消歧 LLM 回调注入
def _coreference_resolver_callback(mentions, context, candidates):
    return agent_orchestrator.run_coreference_resolve({
        'mentions': mentions, 'context': context, 'candidates': candidates
    })

disambiguation_engine.set_llm_resolver(_coreference_resolver_callback)

# 知识图谱回调注入
def _relation_extractor_callback(text, known_entities):
    result = agent_orchestrator.run_relation_extract({'text': text, 'known_entities': known_entities})
    return result.get('relations', result) if isinstance(result, dict) else result

def _event_extractor_callback(text, known_entities):
    result = agent_orchestrator.run_event_extract({'text': text, 'known_entities': known_entities})
    return result.get('events', result) if isinstance(result, dict) else result

knowledge_graph_engine.set_relation_extractor(_relation_extractor_callback)
knowledge_graph_engine.set_event_extractor(_event_extractor_callback)

# 伏笔回填回调注入
def _payoff_judge_callback(chapter_text, foreshadow_item):
    return agent_orchestrator.run_payoff_judge({
        'chapter_text': chapter_text, 'foreshadow': foreshadow_item
    })

foreshadow_engine.set_embedding_engine(embedding_engine)
foreshadow_engine.set_llm_judge(_payoff_judge_callback)

# 叙事分析回调注入
def _narrative_analyzer_callback(summary_text, chapter_title):
    return agent_orchestrator.run_narrative_analysis({
        'summary': summary_text, 'chapter_title': chapter_title
    })

narrative_engine.set_llm_analyzer(_narrative_analyzer_callback)

JWT_ALGORITHM = 'HS256'
JWT_EXPIRE_DAYS = 30
AUTH_WHITELIST = {
    '/api/auth/register',
    '/api/auth/login',
}


def _make_token(user_id):
    payload = {
        'sub': user_id,
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm=JWT_ALGORITHM)


def _decode_token(token):
    return jwt.decode(token, app.config['SECRET_KEY'], algorithms=[JWT_ALGORITHM])


def _extract_bearer_token():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    return ''


@app.before_request
def authenticate_api():
    if not request.path.startswith('/api'):
        return
    if request.method == 'OPTIONS':
        return
    if request.path in AUTH_WHITELIST:
        return
    token = _extract_bearer_token()
    if not token:
        return jsonify({'error': 'missing_token'}), 401
    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'token_expired'}), 401
    except Exception:
        return jsonify({'error': 'invalid_token'}), 401
    user_id = payload.get('sub')
    user = db.get_user_by_id(user_id) if user_id else None
    if not user:
        return jsonify({'error': 'user_not_found'}), 401
    g.user_id = user['id']
    g.user_email = user['email']


def _require_book_access(book_id):
    if not db.book_belongs_to_user(book_id, g.user_id):
        abort(403)


def _require_node_access(node_id):
    if not db.node_belongs_to_user(node_id, g.user_id):
        abort(403)


def _require_node_in_book(node_id, book_id):
    if not node_id or not book_id:
        return
    node_book_id = db.get_node_book_id(node_id)
    if node_book_id != book_id:
        abort(400, description='node_book_mismatch')


def _json_body():
    return request.get_json(silent=True) or {}


# ---- 安全响应头 ----
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# ---- IDOR 权限检查辅助 ----
def _require_resource_owner(table, id_value, id_col='id', book_col='book_id'):
    """通用资源权限检查：resource → book_id → user_id"""
    conn = db._conn()
    row = conn.execute(f'SELECT {book_col} FROM {table} WHERE {id_col}=?', (id_value,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    book_id = row[book_col]
    _require_book_access(book_id)
    return book_id


def _require_job_owner(job_id):
    """检查 Job 归属"""
    conn = db._conn()
    row = conn.execute('SELECT user_id FROM async_jobs WHERE id=?', (job_id,)).fetchone()
    conn.close()
    if not row or row['user_id'] != g.user_id:
        abort(403)


# ---- SSRF 校验 ----
def _validate_base_url(url):
    """校验 base_url 不指向内网地址"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False, '仅支持 http/https 协议'
    host = (parsed.hostname or '').lower()
    blocked = ['localhost', '127.0.0.1', '0.0.0.0', '169.254.169.254', '[::1]', '::1']
    if host in blocked:
        return False, '不允许指向本地地址'
    # 拒绝常见内网网段
    if host.startswith('10.') or host.startswith('192.168.'):
        return False, '不允许指向内网地址'
    if host.startswith('172.'):
        parts = host.split('.')
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return False, '不允许指向内网地址'
            except ValueError:
                pass
    return True, ''


def _prepare_agent_data():
    data = _json_body()
    book_id = data.get('book_id')
    node_id = data.get('node_id')
    if book_id:
        _require_book_access(book_id)
    if node_id:
        _require_node_access(node_id)
        if not book_id:
            book_id = db.get_node_book_id(node_id)
            if book_id:
                _require_book_access(book_id)
                data['book_id'] = book_id
        else:
            _require_node_in_book(node_id, book_id)
    data['user_id'] = g.user_id
    agent_orchestrator.set_request_user(g.user_id)
    return data

# ===================== 页面路由 =====================

@app.route('/')
def index():
    return render_template('index.html', role_registry=get_frontend_role_registry())


@app.route('/api/meta/roles', methods=['GET'])
def get_role_registry():
    return jsonify(get_frontend_role_registry())


# ===================== 认证 API =====================

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = _json_body()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or '@' not in email:
        return jsonify({'error': 'invalid_email'}), 400
    if len(password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    if db.get_user_by_email(email):
        return jsonify({'error': 'email_exists'}), 409
    user_id = db.create_user(email, generate_password_hash(password))
    token = _make_token(user_id)
    return jsonify({'token': token, 'user': {'id': user_id, 'email': email}})


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = _json_body()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = db.get_user_by_email(email)
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'invalid_credentials'}), 401
    token = _make_token(user['id'])
    return jsonify({'token': token, 'user': {'id': user['id'], 'email': user['email']}})


@app.route('/api/auth/me', methods=['GET'])
def me():
    return jsonify({'id': g.user_id, 'email': g.user_email})

# ===================== 模块一：模型配置 API =====================

@app.route('/api/models', methods=['GET'])
def get_models():
    models = db.get_all_models(g.user_id)
    return jsonify(models)

@app.route('/api/models', methods=['POST'])
def add_model():
    data = _json_body()
    base_url = data.get('base_url', '')
    if base_url:
        valid, err = _validate_base_url(base_url)
        if not valid:
            return jsonify({'error': err}), 400
    model_id = db.add_model(data, g.user_id)
    return jsonify({'id': model_id, 'status': 'ok'})

@app.route('/api/models/<model_id>', methods=['PUT'])
def update_model(model_id):
    data = _json_body()
    base_url = data.get('base_url', '')
    if base_url:
        valid, err = _validate_base_url(base_url)
        if not valid:
            return jsonify({'error': err}), 400
    db.update_model(model_id, data, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/models/<model_id>', methods=['DELETE'])
def delete_model(model_id):
    db.delete_model(model_id, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/routing', methods=['GET'])
def get_routing():
    routing = db.get_routing(g.user_id)
    return jsonify(routing)

@app.route('/api/routing', methods=['POST'])
def set_routing():
    data = _json_body()
    allowed_roles = get_routing_role_ids()
    invalid_roles = sorted(set(data.keys()) - allowed_roles)
    if invalid_roles:
        return jsonify({'error': 'invalid_roles', 'roles': invalid_roles}), 400
    db.set_routing(data, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/generation-params', methods=['GET'])
def get_gen_params():
    params = db.get_generation_params(g.user_id)
    return jsonify(params)

@app.route('/api/generation-params', methods=['POST'])
def set_gen_params():
    data = _json_body()
    db.set_generation_params(data, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/token-stats', methods=['GET'])
def get_token_stats():
    stats = db.get_token_stats(g.user_id)
    return jsonify(stats)

# ===================== 模块二：世界观设定 API =====================

@app.route('/api/lorebook/<book_id>', methods=['GET'])
def get_lorebook(book_id):
    _require_book_access(book_id)
    entries = db.get_lorebook_entries(book_id)
    return jsonify(entries)

@app.route('/api/lorebook/<book_id>', methods=['POST'])
def add_lorebook_entry(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    entry_id = db.add_lorebook_entry(data)
    return jsonify({'id': entry_id, 'status': 'ok'})

@app.route('/api/lorebook/<book_id>/<entry_id>', methods=['PUT'])
def update_lorebook_entry(book_id, entry_id):
    _require_book_access(book_id)
    data = _json_body()
    db.update_lorebook_entry(entry_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/lorebook/<book_id>/<entry_id>', methods=['DELETE'])
def delete_lorebook_entry(book_id, entry_id):
    _require_book_access(book_id)
    db.delete_lorebook_entry(entry_id)
    return jsonify({'status': 'ok'})

@app.route('/api/entity-graph/<book_id>', methods=['GET'])
def get_entity_graph(book_id):
    _require_book_access(book_id)
    graph = db.get_entity_graph(book_id)
    return jsonify(graph)

@app.route('/api/entity-graph/<book_id>', methods=['POST'])
def update_entity_graph(book_id):
    _require_book_access(book_id)
    data = _json_body()
    db.update_entity_graph(book_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/memory/summary/<book_id>', methods=['GET'])
def get_summaries(book_id):
    _require_book_access(book_id)
    summaries = db.get_chapter_summaries(book_id)
    return jsonify(summaries)

@app.route('/api/memory/inject', methods=['POST'])
def inject_context():
    data = _json_body()
    text = data.get('text', '')
    book_id = data.get('book_id', '')
    if book_id:
        _require_book_access(book_id)
    injected = memory_engine.dynamic_inject(book_id, text)
    return jsonify({'injected_entries': injected})

# ===================== 模块三：多智能体 API =====================

@app.route('/api/agent/plan', methods=['POST'])
def agent_plan():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_planner(data)
    return jsonify(result)

@app.route('/api/agent/beats', methods=['POST'])
def agent_beats():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_beat_generator(data)
    return jsonify(result)

@app.route('/api/agent/draft', methods=['POST'])
def agent_draft_stream():
    data = _prepare_agent_data()
    def generate():
        for chunk in agent_orchestrator.run_drafter_stream(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/agent/validate', methods=['POST'])
def agent_validate():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_validator(data)
    return jsonify(result)

@app.route('/api/agent/polish', methods=['POST'])
def agent_polish():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_polisher(data)
    return jsonify(result)

@app.route('/api/agent/summarize', methods=['POST'])
def agent_summarize():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_summarizer(data)
    refreshed = []
    if data.get('book_id') and data.get('node_id'):
        refreshed = memory_engine.refresh_character_history_for_node(
            data.get('book_id'),
            data.get('node_id'),
            chapter_title=data.get('chapter_title', ''),
            text=data.get('text', ''),
            summary=result.get('summary', '')
        )
        result['character_history_updated'] = len(refreshed)
    return jsonify(result)

@app.route('/api/agent/stop', methods=['POST'])
def agent_stop():
    agent_orchestrator.set_request_user(g.user_id)
    agent_orchestrator.stop_generation(g.user_id)
    return jsonify({'status': 'stopped'})

# ===================== 新增Agent API: 续写/自动补全/冲突/联想 =====================

@app.route('/api/agent/continue', methods=['POST'])
def agent_continue_stream():
    """智能续写（批评-重试循环版）"""
    data = _prepare_agent_data()
    def generate():
        for chunk in agent_orchestrator.run_smart_continuation(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/agent/continue-fast', methods=['POST'])
def agent_continue_fast():
    """快速流式续写（无批评循环）"""
    data = _prepare_agent_data()
    def generate():
        for chunk in agent_orchestrator.run_smart_continuation_stream(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/agent/autocomplete', methods=['POST'])
def agent_autocomplete():
    """自动补全（ghost text预测）"""
    data = _prepare_agent_data()
    result = agent_orchestrator.run_autocomplete(data)
    return jsonify(result)

@app.route('/api/agent/conflict', methods=['POST'])
def agent_conflict():
    """冲突设计Agent"""
    data = _prepare_agent_data()
    result = agent_orchestrator.run_conflict_design(data)
    return jsonify(result)

@app.route('/api/agent/associate', methods=['POST'])
def agent_associate():
    """联想/头脑风暴Agent"""
    data = _prepare_agent_data()
    result = agent_orchestrator.run_association(data)
    return jsonify(result)

# ===================== 三层记忆API =====================

@app.route('/api/memory/vectorize/<book_id>', methods=['POST'])
def vectorize_book(book_id):
    """为整本书建立向量索引"""
    _require_book_access(book_id)
    result = memory_engine.vectorize_book(book_id)
    return jsonify(result)

@app.route('/api/memory/retrieve', methods=['POST'])
def vector_retrieve():
    """向量检索"""
    data = _json_body()
    book_id = data.get('book_id', '')
    if book_id:
        _require_book_access(book_id)
    query = data.get('query', '')
    top_k = min(int(data.get('top_k', 5)), 50)
    results = memory_engine.vector_retrieve(book_id, query, top_k, user_id=g.user_id)
    return jsonify({'results': results})

@app.route('/api/memory/status/<book_id>/<node_id>', methods=['GET'])
def memory_status(book_id, node_id):
    """获取三层记忆状态"""
    _require_book_access(book_id)
    _require_node_access(node_id)
    status = memory_engine.get_memory_status(book_id, node_id)
    return jsonify(status)

@app.route('/api/character-reminders', methods=['POST'])
def character_reminders():
    data = _json_body()
    book_id = data.get('book_id', '')
    node_id = data.get('node_id', '')
    text = data.get('text', '')
    if book_id:
        _require_book_access(book_id)
    if node_id:
        _require_node_access(node_id)
        _require_node_in_book(node_id, book_id)
    reminders = memory_engine.build_character_reminders(book_id, text=text, node_id=node_id)
    context = memory_engine.build_character_reminder_context(book_id, text=text, node_id=node_id)
    return jsonify({'characters': reminders, 'context': context})

@app.route('/api/character-history/<book_id>', methods=['GET'])
def get_character_history(book_id):
    _require_book_access(book_id)
    character_name = request.args.get('character')
    entry_type = request.args.get('type')
    limit = request.args.get('limit', type=int)
    items = db.get_character_history(book_id, character_name=character_name, entry_type=entry_type, limit=limit)
    return jsonify(items)

@app.route('/api/character-history/<book_id>', methods=['POST'])
def add_character_history(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    history_id = db.add_character_history(data)
    return jsonify({'id': history_id, 'status': 'ok'})

@app.route('/api/character-history/<book_id>/refresh', methods=['POST'])
def refresh_character_history(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    if node_id:
        _require_node_access(node_id)
        _require_node_in_book(node_id, book_id)
        created_ids = memory_engine.refresh_character_history_for_node(
            book_id,
            node_id,
            chapter_title=data.get('chapter_title', ''),
            text=data.get('text', ''),
            summary=data.get('summary', '')
        )
        return jsonify({'status': 'ok', 'created_entries': len(created_ids), 'mode': 'node'})

    result = memory_engine.refresh_character_history_for_book(book_id)
    result['status'] = 'ok'
    result['mode'] = 'book'
    return jsonify(result)

@app.route('/api/character-history/<book_id>/<history_id>', methods=['PUT'])
def update_character_history(book_id, history_id):
    _require_book_access(book_id)
    data = _json_body()
    db.update_character_history(history_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/character-history/<book_id>/<history_id>', methods=['DELETE'])
def delete_character_history(book_id, history_id):
    _require_book_access(book_id)
    db.delete_character_history(history_id)
    return jsonify({'status': 'ok'})

# ===================== 模块四：文档树与版本控制 =====================

@app.route('/api/books', methods=['GET'])
def get_books():
    books = db.get_books(g.user_id)
    return jsonify(books)

@app.route('/api/books', methods=['POST'])
def create_book():
    data = _json_body()
    book_id = db.create_book(data, g.user_id)
    return jsonify({'id': book_id, 'status': 'ok'})

@app.route('/api/books/<book_id>', methods=['PUT'])
def update_book(book_id):
    _require_book_access(book_id)
    data = _json_body()
    db.update_book(book_id, data, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/books/<book_id>', methods=['DELETE'])
def delete_book(book_id):
    _require_book_access(book_id)
    db.delete_book(book_id, g.user_id)
    return jsonify({'status': 'ok'})

@app.route('/api/books/<book_id>/tree', methods=['GET'])
def get_doc_tree(book_id):
    _require_book_access(book_id)
    tree = db.get_document_tree(book_id)
    return jsonify(tree)

@app.route('/api/nodes', methods=['POST'])
def create_node():
    data = _json_body()
    _require_book_access(data.get('book_id', ''))
    if data.get('parent_id'):
        _require_node_access(data['parent_id'])
    node_id = db.create_node(data)
    return jsonify({'id': node_id, 'status': 'ok'})

@app.route('/api/nodes/<node_id>', methods=['GET'])
def get_node(node_id):
    _require_node_access(node_id)
    node = db.get_node(node_id)
    return jsonify(node)

@app.route('/api/nodes/<node_id>', methods=['PUT'])
def update_node(node_id):
    _require_node_access(node_id)
    data = _json_body()
    if data.get('parent_id'):
        _require_node_access(data['parent_id'])
    db.update_node(node_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/nodes/<node_id>', methods=['DELETE'])
def delete_node(node_id):
    _require_node_access(node_id)
    db.delete_node(node_id)
    return jsonify({'status': 'ok'})

@app.route('/api/nodes/reorder', methods=['POST'])
def reorder_nodes():
    data = _json_body()
    for item in data.get('items', []):
        _require_node_access(item.get('id', ''))
        if item.get('parent_id'):
            _require_node_access(item.get('parent_id'))
    db.reorder_nodes(data)
    return jsonify({'status': 'ok'})

@app.route('/api/nodes/<node_id>/content', methods=['GET'])
def get_node_content(node_id):
    _require_node_access(node_id)
    content = db.get_node_content(node_id)
    return jsonify(content)

@app.route('/api/nodes/<node_id>/content', methods=['PUT'])
def save_node_content(node_id):
    _require_node_access(node_id)
    data = _json_body()
    db.save_node_content(node_id, data)
    return jsonify({'status': 'ok'})

# 版本/分支
@app.route('/api/nodes/<node_id>/versions', methods=['GET'])
def get_versions(node_id):
    _require_node_access(node_id)
    versions = db.get_versions(node_id)
    return jsonify(versions)

@app.route('/api/nodes/<node_id>/versions', methods=['POST'])
def create_version(node_id):
    _require_node_access(node_id)
    data = _json_body()
    data['node_id'] = node_id
    ver_id = db.create_version(data)
    return jsonify({'id': ver_id, 'status': 'ok'})

@app.route('/api/nodes/<node_id>/versions/<ver_id>/activate', methods=['POST'])
def activate_version(node_id, ver_id):
    _require_node_access(node_id)
    db.activate_version(node_id, ver_id)
    return jsonify({'status': 'ok'})

@app.route('/api/diff', methods=['POST'])
def get_diff():
    data = _json_body()
    diff_result = compute_diff(data.get('old_text', ''), data.get('new_text', ''))
    return jsonify(diff_result)

def compute_diff(old_text, new_text):
    """简单的逐行 diff"""
    old_lines = old_text.split('\n')
    new_lines = new_text.split('\n')
    result = []
    import difflib
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for line in old_lines[i1:i2]:
                result.append({'type': 'equal', 'text': line})
        elif tag == 'delete':
            for line in old_lines[i1:i2]:
                result.append({'type': 'delete', 'text': line})
        elif tag == 'insert':
            for line in new_lines[j1:j2]:
                result.append({'type': 'insert', 'text': line})
        elif tag == 'replace':
            for line in old_lines[i1:i2]:
                result.append({'type': 'delete', 'text': line})
            for line in new_lines[j1:j2]:
                result.append({'type': 'insert', 'text': line})
    return {'lines': result}


def _walk_tree_nodes(tree):
    for node in tree or []:
        yield node
        for child in _walk_tree_nodes(node.get('children') or []):
            yield child


def _calc_tension_metrics(text):
    clean = re.sub(r'\s+', '', text or '')
    if not clean:
        return {
            'score': 0,
            'word_count': 0,
            'conflict_hits': 0,
            'pace_hits': 0,
            'calm_hits': 0,
            'energy': 0.0,
        }

    conflict_words = ['冲突', '对峙', '危机', '危险', '威胁', '追杀', '争吵', '质问', '背叛', '阴谋', '爆炸', '血', '死亡', '枪', '刀']
    pace_words = ['突然', '猛地', '立刻', '瞬间', '刹那', '急忙', '狂奔', '咆哮', '嘶吼']
    calm_words = ['平静', '安静', '缓缓', '慢慢', '温柔', '从容', '悠然', '日常', '重复', '无事']

    def _hits(words):
        return sum(clean.count(w) for w in words)

    word_count = len(clean)
    sentences = max(1, len(re.findall(r'[。！？!?]', clean)))
    norm = max(word_count / 1000.0, 0.3)

    conflict_hits = _hits(conflict_words)
    pace_hits = _hits(pace_words)
    calm_hits = _hits(calm_words)
    punct_energy = (clean.count('!') + clean.count('！') + clean.count('?') + clean.count('？') * 1.2) / sentences

    raw = 28 + (conflict_hits / norm) * 8 + (pace_hits / norm) * 6 + punct_energy * 7 - (calm_hits / norm) * 5
    if word_count < 180:
        raw *= 0.85

    score = int(round(max(0, min(100, raw))))
    return {
        'score': score,
        'word_count': word_count,
        'conflict_hits': conflict_hits,
        'pace_hits': pace_hits,
        'calm_hits': calm_hits,
        'energy': round(punct_energy, 2),
    }


def _build_tension_diagnostics(book_id):
    tree = db.get_document_tree(book_id)
    chapters = []
    for node in _walk_tree_nodes(tree):
        if node.get('type') not in ('chapter', 'scene'):
            continue
        content = db.get_node_content(node['id']).get('content', '')
        if not (content or '').strip():
            continue
        metrics = _calc_tension_metrics(content)
        chapters.append({
            'node_id': node['id'],
            'title': node.get('title') or '未命名',
            'node_type': node.get('type') or 'chapter',
            'word_count': metrics['word_count'],
            'tension_score': metrics['score'],
            'signals': {
                'conflict_hits': metrics['conflict_hits'],
                'pace_hits': metrics['pace_hits'],
                'calm_hits': metrics['calm_hits'],
                'energy': metrics['energy'],
            }
        })

    if not chapters:
        return {'book_id': book_id, 'average_tension': 0, 'chapters': [], 'warnings': []}

    avg = int(round(sum(c['tension_score'] for c in chapters) / len(chapters)))
    warnings = []

    low = [c for c in chapters if c['tension_score'] < 35 and c['word_count'] >= 300]
    if low:
        sample = '、'.join(c['title'] for c in low[:4])
        extra = '' if len(low) <= 4 else f' 等{len(low)}章'
        warnings.append({
            'type': '平淡预警',
            'message': f'低张力片段偏多（{sample}{extra}），建议插入目标冲突或倒计时压力。'
        })

    if len(chapters) >= 3:
        streak = []
        streak_groups = []
        for c in chapters:
            if c['tension_score'] < 40:
                streak.append(c)
            else:
                if len(streak) >= 2:
                    streak_groups.append(streak[:])
                streak = []
        if len(streak) >= 2:
            streak_groups.append(streak)
        if streak_groups:
            longest = max(streak_groups, key=len)
            warnings.append({
                'type': '注水风险',
                'message': f'检测到连续{len(longest)}章低张力，建议在中段引入角色损失、误判或道德抉择。'
            })

    if len(chapters) >= 2:
        max_drop = 0
        max_drop_idx = -1
        for i in range(1, len(chapters)):
            drop = chapters[i - 1]['tension_score'] - chapters[i]['tension_score']
            if drop > max_drop:
                max_drop = drop
                max_drop_idx = i
        if max_drop >= 35 and max_drop_idx >= 1:
            warnings.append({
                'type': '断崖波动',
                'message': f"《{chapters[max_drop_idx - 1]['title']}》到《{chapters[max_drop_idx]['title']}》张力骤降 {max_drop} 分，建议补一段承压过渡。"
            })

    max_score = max(c['tension_score'] for c in chapters)
    min_score = min(c['tension_score'] for c in chapters)
    if (max_score - min_score) < 15 and avg < 55:
        warnings.append({
            'type': '曲线平直',
            'message': '全书张力波动较小，建议在关键章设置反转峰值。'
        })

    return {
        'book_id': book_id,
        'average_tension': avg,
        'max_tension': max_score,
        'min_tension': min_score,
        'chapters': chapters,
        'warnings': warnings,
    }

# ===================== 模块六：导出/导入 =====================

@app.route('/api/export/<book_id>/<fmt>', methods=['GET'])
def export_book(book_id, fmt):
    _require_book_access(book_id)
    if fmt == 'markdown':
        content, filename = export_engine.to_markdown(book_id, user_id=g.user_id)
        return Response(content, mimetype='text/markdown',
                       headers={'Content-Disposition': f'attachment; filename={filename}'})
    elif fmt == 'txt':
        content, filename = export_engine.to_txt(book_id, user_id=g.user_id)
        return Response(content, mimetype='text/plain',
                       headers={'Content-Disposition': f'attachment; filename={filename}'})
    elif fmt == 'epub':
        filepath, filename = export_engine.to_epub(book_id, user_id=g.user_id)
        return send_file(filepath, as_attachment=True, download_name=filename)
    elif fmt == 'json':
        content, filename = export_engine.to_json_workspace(book_id, user_id=g.user_id)
        return Response(content, mimetype='application/json',
                       headers={'Content-Disposition': f'attachment; filename={filename}'})
    return jsonify({'error': 'unknown format'}), 400

@app.route('/api/export/<book_id>/scoped', methods=['GET'])
def export_scoped(book_id):
    _require_book_access(book_id)
    scope = request.args.get('scope', 'book')
    node_id = request.args.get('node_id')
    include = request.args.getlist('include') or None
    content, filename = export_engine.to_markdown_scoped(
        book_id, scope=scope, node_id=node_id, include=include, user_id=g.user_id
    )
    return Response(content, mimetype='text/markdown',
                   headers={'Content-Disposition': f'attachment; filename={filename}'})

@app.route('/api/import', methods=['POST'])
def import_workspace():
    if 'file' in request.files:
        f = request.files['file']
        data = json.loads(f.read().decode('utf-8'))
    else:
        data = _json_body()
    book_id = export_engine.import_json_workspace(data, user_id=g.user_id)
    return jsonify({'book_id': book_id, 'status': 'ok'})

# ===================== 模块五：划词查询 =====================

@app.route('/api/lookup', methods=['POST'])
def lookup_entity():
    data = _json_body()
    text = data.get('text', '')
    book_id = data.get('book_id', '')
    _require_book_access(book_id)
    results = memory_engine.lookup_entity(book_id, text)
    return jsonify(results)

# ===================== Inline Commands =====================

@app.route('/api/inline-command', methods=['POST'])
def inline_command():
    data = _prepare_agent_data()
    cmd = data.get('command', '')
    def generate():
        for chunk in agent_orchestrator.run_inline_command(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# ===================== 伏笔追踪 API =====================

@app.route('/api/foreshadowing/<book_id>', methods=['GET'])
def get_foreshadowing(book_id):
    _require_book_access(book_id)
    status = request.args.get('status')
    items = db.get_foreshadowing(book_id, status=status)
    return jsonify(items)

@app.route('/api/foreshadowing/<book_id>', methods=['POST'])
def add_foreshadowing(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    fs_id = db.add_foreshadowing(data)
    return jsonify({'id': fs_id, 'status': 'ok'})

@app.route('/api/foreshadowing/<book_id>/<fs_id>', methods=['PUT'])
def update_foreshadowing(book_id, fs_id):
    _require_book_access(book_id)
    data = _json_body()
    db.update_foreshadowing(fs_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/foreshadowing/<book_id>/<fs_id>', methods=['DELETE'])
def delete_foreshadowing(book_id, fs_id):
    _require_book_access(book_id)
    db.delete_foreshadowing(fs_id)
    return jsonify({'status': 'ok'})

@app.route('/api/foreshadowing/<book_id>/<fs_id>/resolve', methods=['POST'])
def resolve_foreshadowing(book_id, fs_id):
    _require_book_access(book_id)
    data = _json_body()
    db.resolve_foreshadowing(fs_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/agent/foreshadow-detect', methods=['POST'])
def agent_foreshadow_detect():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_foreshadow_detect(data)
    return jsonify(result)

@app.route('/api/agent/foreshadow-scan', methods=['POST'])
def agent_foreshadow_scan():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_foreshadow_scan(data)
    return jsonify(result)

# ===================== 潜台词 & 心理分析 API =====================

@app.route('/api/agent/subtext', methods=['POST'])
def agent_subtext():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_subtext_analysis(data)
    return jsonify(result)

@app.route('/api/agent/psychology', methods=['POST'])
def agent_psychology():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_psychology_lens(data)
    return jsonify(result)

@app.route('/api/psychology/<book_id>', methods=['GET'])
def get_character_psychology(book_id):
    _require_book_access(book_id)
    character = request.args.get('character')
    profiles = db.get_character_psychology(book_id, character_name=character)
    return jsonify(profiles)

@app.route('/api/psychology/<book_id>', methods=['POST'])
def upsert_character_psychology(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    pid = db.upsert_character_psychology(data)
    return jsonify({'id': pid, 'status': 'ok'})

@app.route('/api/psychology/<book_id>/<cp_id>', methods=['DELETE'])
def delete_character_psychology(book_id, cp_id):
    _require_book_access(book_id)
    db.delete_character_psychology(cp_id)
    return jsonify({'status': 'ok'})

# ===================== 世界状态 API =====================

@app.route('/api/world-state/<book_id>', methods=['GET'])
def get_world_state(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity')
    state_type = request.args.get('type')
    states = db.get_world_state(book_id, entity_name=entity, state_type=state_type)
    return jsonify(states)

@app.route('/api/world-state/<book_id>', methods=['POST'])
def upsert_world_state(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    ws_id = db.upsert_world_state(data)
    return jsonify({'id': ws_id, 'status': 'ok'})

@app.route('/api/world-state/<book_id>/<ws_id>', methods=['DELETE'])
def delete_world_state(book_id, ws_id):
    _require_book_access(book_id)
    db.delete_world_state(ws_id)
    return jsonify({'status': 'ok'})

@app.route('/api/agent/world-state-extract', methods=['POST'])
def agent_world_state_extract():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_world_state_extract(data)
    return jsonify(result)

@app.route('/api/agent/world-state-validate', methods=['POST'])
def agent_world_state_validate():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_world_state_validate(data)
    return jsonify(result)

# ===================== Module 11: Plan-and-Solve 深度生成 API =====================

@app.route('/api/agent/plan-and-solve', methods=['POST'])
def agent_plan_and_solve():
    data = _prepare_agent_data()
    def generate():
        for chunk in agent_orchestrator.run_plan_and_solve(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# ===================== Module 12: 幻觉检测 API =====================

@app.route('/api/agent/hallucination-check', methods=['POST'])
def agent_hallucination_check():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_hallucination_detect(data)
    return jsonify(result)

@app.route('/api/agent/draft-guarded', methods=['POST'])
def agent_draft_guarded():
    data = _prepare_agent_data()
    def generate():
        for chunk in agent_orchestrator.run_draft_with_hallucination_guard(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/api/diagnostics/tension/<book_id>', methods=['GET'])
def diagnostics_tension(book_id):
    _require_book_access(book_id)
    return jsonify(_build_tension_diagnostics(book_id))

# ===================== 写作规则中心 API =====================

@app.route('/api/rules/<book_id>/sets', methods=['GET'])
def get_rule_sets(book_id):
    _require_book_access(book_id)
    return jsonify(db.get_writing_rule_sets(book_id))

@app.route('/api/rules/<book_id>/sets', methods=['POST'])
def create_rule_set(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    sid = db.create_writing_rule_set(data)
    return jsonify({'id': sid, 'status': 'ok'})

@app.route('/api/rules/<book_id>/sets/<set_id>', methods=['PUT'])
def update_rule_set(book_id, set_id):
    _require_book_access(book_id)
    db.update_writing_rule_set(set_id, _json_body())
    return jsonify({'status': 'ok'})

@app.route('/api/rules/<book_id>/sets/<set_id>', methods=['DELETE'])
def delete_rule_set(book_id, set_id):
    _require_book_access(book_id)
    db.delete_writing_rule_set(set_id)
    return jsonify({'status': 'ok'})

@app.route('/api/rules/<book_id>/rules', methods=['GET'])
def get_rules(book_id):
    _require_book_access(book_id)
    rule_set_id = request.args.get('rule_set_id')
    category = request.args.get('category')
    return jsonify(db.get_writing_rules(book_id, rule_set_id=rule_set_id, category=category))

@app.route('/api/rules/<book_id>/rules', methods=['POST'])
def add_rule(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    rid = db.add_writing_rule(data)
    return jsonify({'id': rid, 'status': 'ok'})

@app.route('/api/rules/<book_id>/rules/<rule_id>', methods=['PUT'])
def update_rule(book_id, rule_id):
    _require_book_access(book_id)
    db.update_writing_rule(rule_id, _json_body())
    return jsonify({'status': 'ok'})

@app.route('/api/rules/<book_id>/rules/<rule_id>', methods=['DELETE'])
def delete_rule(book_id, rule_id):
    _require_book_access(book_id)
    db.delete_writing_rule(rule_id)
    return jsonify({'status': 'ok'})

@app.route('/api/rules/<book_id>/active', methods=['GET'])
def get_active_rules(book_id):
    _require_book_access(book_id)
    node_id = request.args.get('node_id')
    rules = rule_engine.get_active_rules(book_id, node_id)
    return jsonify(rules)

@app.route('/api/rules/<book_id>/validate', methods=['POST'])
def validate_rules(book_id):
    _require_book_access(book_id)
    data = _json_body()
    violations = rule_engine.validate_against_rules(book_id, data.get('text', ''), data.get('node_id'))
    return jsonify({'violations': violations})

@app.route('/api/rules/<book_id>/conflicts', methods=['GET'])
def check_rule_conflicts(book_id):
    _require_book_access(book_id)
    return jsonify({'conflicts': rule_engine.check_rule_conflicts(book_id)})

# ===================== 时间线与事件账本 API =====================

@app.route('/api/timeline/<book_id>', methods=['GET'])
def get_timeline(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity')
    node_id = request.args.get('node_id')
    event_type = request.args.get('type')
    events = db.get_timeline_events(book_id, entity_name=entity, node_id=node_id, event_type=event_type)
    return jsonify(events)

@app.route('/api/timeline/<book_id>/events', methods=['POST'])
def add_timeline_event(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    data['source'] = 'manual'
    eid = db.add_timeline_event(data)
    return jsonify({'id': eid, 'status': 'ok'})

@app.route('/api/timeline/<book_id>/events/<event_id>', methods=['PUT'])
def update_timeline_event(book_id, event_id):
    _require_book_access(book_id)
    db.update_timeline_event(event_id, _json_body())
    return jsonify({'status': 'ok'})

@app.route('/api/timeline/<book_id>/events/<event_id>', methods=['DELETE'])
def delete_timeline_event(book_id, event_id):
    _require_book_access(book_id)
    db.delete_timeline_event(event_id)
    return jsonify({'status': 'ok'})

@app.route('/api/timeline/<book_id>/extract', methods=['POST'])
def extract_timeline_events(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id')
    text = data.get('text', '')
    if node_id and not text:
        content = db.get_node_content(node_id)
        text = content.get('content', '')
    events = timeline_engine.extract_events_from_text(book_id, node_id, text,
                                                       chapter_index=data.get('chapter_index', 0))
    return jsonify({'created': len(events), 'event_ids': events})

@app.route('/api/timeline/<book_id>/detect-conflicts', methods=['POST'])
def detect_timeline_conflicts(book_id):
    _require_book_access(book_id)
    conflicts = timeline_engine.detect_conflicts(book_id)
    return jsonify({'conflicts': conflicts})

@app.route('/api/timeline/<book_id>/transitions', methods=['GET'])
def get_transitions(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity')
    return jsonify(db.get_entity_state_transitions(book_id, entity_name=entity))

# ===================== 快照与回收站 API =====================

@app.route('/api/snapshots/<book_id>', methods=['GET'])
def get_snapshots(book_id):
    _require_book_access(book_id)
    node_id = request.args.get('node_id')
    return jsonify(db.get_snapshots(book_id, node_id=node_id))

@app.route('/api/snapshots/<book_id>', methods=['POST'])
def create_snapshot(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id')
    if node_id:
        sid = snapshot_engine.create_node_snapshot(book_id, node_id,
                                                   snapshot_type=data.get('snapshot_type', 'manual'),
                                                   label=data.get('label', ''))
    else:
        sid = snapshot_engine.create_book_snapshot(book_id,
                                                   snapshot_type=data.get('snapshot_type', 'manual'),
                                                   label=data.get('label', ''))
    return jsonify({'id': sid, 'status': 'ok'})

@app.route('/api/snapshots/<snapshot_id>/preview', methods=['GET'])
def preview_snapshot(snapshot_id):
    _require_resource_owner('snapshots', snapshot_id)
    preview = snapshot_engine.preview_restore(snapshot_id)
    if not preview:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(preview)

@app.route('/api/snapshots/<snapshot_id>/restore', methods=['POST'])
def restore_snapshot(snapshot_id):
    _require_resource_owner('snapshots', snapshot_id)
    ok = snapshot_engine.restore_node_snapshot(snapshot_id)
    if not ok:
        return jsonify({'error': 'restore_failed'}), 400
    return jsonify({'status': 'ok'})

@app.route('/api/recycle-bin/<book_id>', methods=['GET'])
def get_recycle_bin(book_id):
    _require_book_access(book_id)
    return jsonify(db.get_recycle_bin(book_id))

@app.route('/api/recycle-bin/<recycle_id>/restore', methods=['POST'])
def restore_from_recycle(recycle_id):
    _require_resource_owner('recycle_bin', recycle_id)
    ok = snapshot_engine.restore_from_recycle(recycle_id)
    if not ok:
        return jsonify({'error': 'restore_failed'}), 400
    return jsonify({'status': 'ok'})

@app.route('/api/recycle-bin/<recycle_id>', methods=['DELETE'])
def delete_from_recycle(recycle_id):
    _require_resource_owner('recycle_bin', recycle_id)
    db.delete_recycle_bin_item(recycle_id)
    return jsonify({'status': 'ok'})

# ===================== 全局搜索与引用追踪 API =====================

@app.route('/api/search/<book_id>', methods=['POST'])
def global_search(book_id):
    _require_book_access(book_id)
    data = _json_body()
    results = search_engine.search(book_id, data.get('query', ''),
                                    scope=data.get('scope'),
                                    entity_type=data.get('entity_type'))
    return jsonify(results)

@app.route('/api/search/<book_id>/replace', methods=['POST'])
def global_replace(book_id):
    _require_book_access(book_id)
    data = _json_body()
    preview_only = data.get('preview_only', True)
    if not preview_only:
        snapshot_engine.create_book_snapshot(book_id, snapshot_type='auto_replace', label='替换前快照')
    result = search_engine.replace_all(book_id, data.get('search_text', ''),
                                        data.get('replace_text', ''),
                                        preview_only=preview_only)
    return jsonify(result)

@app.route('/api/search/<book_id>/references', methods=['GET'])
def find_references(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity', '')
    return jsonify(search_engine.find_references(book_id, entity))

@app.route('/api/search/<book_id>/chapter-refs/<node_id>', methods=['GET'])
def find_chapter_refs(book_id, node_id):
    _require_book_access(book_id)
    return jsonify(search_engine.find_chapter_references(book_id, node_id))

# ===================== 异步任务中心 API =====================

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    status = request.args.get('status')
    return jsonify(db.get_async_jobs(g.user_id, status=status))

@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job_detail(job_id):
    _require_job_owner(job_id)
    job = db.get_async_job(job_id)
    if not job:
        return jsonify({'error': 'not_found'}), 404
    logs = db.get_job_logs(job_id, limit=50)
    return jsonify({'job': job, 'logs': logs})

@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    _require_job_owner(job_id)
    ok = job_engine.cancel_job(job_id)
    return jsonify({'status': 'ok' if ok else 'not_running'})

@app.route('/api/jobs/<job_id>/retry', methods=['POST'])
def retry_job(job_id):
    _require_job_owner(job_id)
    new_id = job_engine.retry_job(job_id)
    if not new_id:
        return jsonify({'error': 'cannot_retry'}), 400
    return jsonify({'id': new_id, 'status': 'ok'})

# ===================== 记忆注入日志 API =====================

@app.route('/api/memory/injection-log/<book_id>', methods=['GET'])
def get_injection_logs(book_id):
    _require_book_access(book_id)
    node_id = request.args.get('node_id')
    logs = db.get_memory_injection_logs(book_id, node_id=node_id)
    return jsonify(logs)

@app.route('/api/memory/pin/<book_id>', methods=['GET'])
def get_pinned_memories(book_id):
    _require_book_access(book_id)
    return jsonify(db.get_pinned_memories(book_id))

@app.route('/api/memory/pin/<book_id>', methods=['POST'])
def pin_memory(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    pid = db.add_pinned_memory(data)
    return jsonify({'id': pid, 'status': 'ok'})

@app.route('/api/memory/pin/<book_id>/<pin_id>', methods=['DELETE'])
def unpin_memory(book_id, pin_id):
    _require_book_access(book_id)
    db.delete_pinned_memory(pin_id)
    return jsonify({'status': 'ok'})

# ===================== 一致性报告 API =====================

@app.route('/api/consistency/<book_id>/scan', methods=['POST'])
def consistency_scan(book_id):
    _require_book_access(book_id)
    report_id = db.create_consistency_report({'book_id': book_id})
    job_id = job_engine.create_and_start(g.user_id, 'consistency_scan', book_id=book_id)
    def _run_scan(jid, job_data, progress_cb, cancel_ev):
        progress_cb(1, 4, '检查伏笔...')
        consistency_engine.run_full_scan(book_id, report_id=report_id)
        return f'报告 {report_id} 完成'
    job_engine.register_handler('consistency_scan', _run_scan)
    job_engine.start_job(job_id)
    return jsonify({'report_id': report_id, 'job_id': job_id, 'status': 'ok'})

@app.route('/api/consistency/<book_id>/reports', methods=['GET'])
def get_consistency_reports(book_id):
    _require_book_access(book_id)
    return jsonify(db.get_consistency_reports(book_id))

@app.route('/api/consistency/<book_id>/reports/<report_id>', methods=['GET'])
def get_consistency_report(book_id, report_id):
    _require_book_access(book_id)
    report = db.get_consistency_report(report_id)
    if not report:
        return jsonify({'error': 'not_found'}), 404
    issues = db.get_consistency_issues(report_id=report_id)
    return jsonify({'report': report, 'issues': issues})

@app.route('/api/consistency/issues/<issue_id>', methods=['PUT'])
def update_issue(issue_id):
    _require_resource_owner('consistency_issues', issue_id)
    data = _json_body()
    db.update_consistency_issue(issue_id, data)
    return jsonify({'status': 'ok'})

# ===================== 章节工作流 API =====================

@app.route('/api/workflow/templates', methods=['GET'])
def get_workflow_templates():
    book_id = request.args.get('book_id')
    templates = db.get_workflow_templates(user_id=g.user_id, book_id=book_id)
    default = workflow_engine.get_default_template()
    default['id'] = 'default'
    return jsonify([default] + templates)

@app.route('/api/workflow/templates', methods=['POST'])
def create_workflow_template():
    data = _json_body()
    data['user_id'] = g.user_id
    tid = db.create_workflow_template(data)
    return jsonify({'id': tid, 'status': 'ok'})

@app.route('/api/workflow/run', methods=['POST'])
def start_workflow():
    data = _json_body()
    book_id = data.get('book_id')
    node_id = data.get('node_id')
    if book_id:
        _require_book_access(book_id)
    if node_id:
        _require_node_access(node_id)
    run_id = workflow_engine.create_run(book_id, node_id,
                                         goals=data.get('goals', ''),
                                         template_id=data.get('template_id'),
                                         user_id=g.user_id)
    return jsonify({'id': run_id, 'status': 'ok'})

@app.route('/api/workflow/run/<run_id>', methods=['GET'])
def get_workflow_status(run_id):
    status = workflow_engine.get_run_status(run_id)
    if not status:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(status)

@app.route('/api/workflow/run/<run_id>/step/<int:step_idx>', methods=['POST'])
def execute_workflow_step(run_id, step_idx):
    data = _json_body()
    data['user_id'] = g.user_id
    agent_orchestrator.set_request_user(g.user_id)
    result = workflow_engine.execute_step(run_id, step_idx, user_data=data)
    return jsonify(result)

@app.route('/api/workflow/run/<run_id>/confirm/<int:step_idx>', methods=['POST'])
def confirm_workflow_step(run_id, step_idx):
    workflow_engine.confirm_step(run_id, step_idx)
    return jsonify({'status': 'ok'})

# ===================== 增强统计 API =====================

@app.route('/api/stats/enhanced', methods=['GET'])
def get_enhanced_stats():
    book_id = request.args.get('book_id')
    return jsonify(stats_engine.get_dashboard(g.user_id, book_id=book_id))

@app.route('/api/stats/adopted', methods=['POST'])
def mark_adopted():
    data = _json_body()
    stats_engine.mark_adopted(g.user_id, data.get('agent_role', ''), book_id=data.get('book_id'))
    return jsonify({'status': 'ok'})

# ===================== 世界状态增强 API =====================

@app.route('/api/world-state/<book_id>/history', methods=['GET'])
def get_world_state_history(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity')
    return jsonify(db.get_world_state_history(book_id, entity_name=entity))

@app.route('/api/world-state/<book_id>/current', methods=['GET'])
def get_current_world_state(book_id):
    _require_book_access(book_id)
    entity = request.args.get('entity')
    return jsonify(db.get_current_world_state(book_id, entity_name=entity))

@app.route('/api/world-state/<book_id>/v2', methods=['POST'])
def upsert_world_state_v2(book_id):
    _require_book_access(book_id)
    data = _json_body()
    data['book_id'] = book_id
    ws_id = db.upsert_world_state_v2(data)
    return jsonify({'id': ws_id, 'status': 'ok'})

# ===================== 版本分支增强 API =====================

@app.route('/api/nodes/<node_id>/versions/v2', methods=['POST'])
def create_version_v2(node_id):
    _require_node_access(node_id)
    data = _json_body()
    data['node_id'] = node_id
    vid = db.create_version_v2(data)
    return jsonify({'id': vid, 'status': 'ok'})

@app.route('/api/versions/<ver_id>', methods=['PUT'])
def update_version(ver_id):
    # versions 通过 node_id 关联到 book -> user
    conn = db._conn()
    row = conn.execute('SELECT node_id FROM versions WHERE id=?', (ver_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    _require_node_access(row['node_id'])
    data = _json_body()
    db.update_version(ver_id, data)
    return jsonify({'status': 'ok'})

# ===================== 导入导出增强 API =====================

@app.route('/api/import/preview', methods=['POST'])
def import_preview():
    if 'file' not in request.files:
        return jsonify({'error': 'no_file'}), 400
    f = request.files['file']
    filename = f.filename.lower()

    chapters = []
    if filename.endswith('.md'):
        content = f.read().decode('utf-8', errors='replace')
        chapters = export_engine.import_markdown(content)
    elif filename.endswith('.txt'):
        content = f.read().decode('utf-8', errors='replace')
        chapters = export_engine.import_txt(content)
    elif filename.endswith('.docx'):
        raw = f.read()
        chapters = export_engine.import_docx(raw)
    else:
        return jsonify({'error': 'unsupported_format'}), 400

    total_chars = sum(len(c.get('content', '')) for c in chapters)
    return jsonify({
        'filename': f.filename,
        'total_chars': total_chars,
        'chapters': [{'title': c['title'], 'content_preview': c['content'][:200]}
                      for c in chapters]
    })

@app.route('/api/import/file', methods=['POST'])
def import_file():
    if 'file' not in request.files:
        return jsonify({'error': 'no_file'}), 400
    f = request.files['file']
    filename = f.filename.lower()
    book_title = request.form.get('title') or f.filename.rsplit('.', 1)[0]

    chapters = []
    if filename.endswith('.md'):
        content = f.read().decode('utf-8', errors='replace')
        chapters = export_engine.import_markdown(content)
    elif filename.endswith('.txt'):
        content = f.read().decode('utf-8', errors='replace')
        chapters = export_engine.import_txt(content)
    elif filename.endswith('.docx'):
        raw = f.read()
        chapters = export_engine.import_docx(raw)
    else:
        return jsonify({'error': 'unsupported_format'}), 400

    book_id = db.create_book({'title': book_title}, g.user_id)
    for ch in chapters:
        node_id = db.create_node({
            'book_id': book_id,
            'type': ch.get('type', 'chapter'),
            'title': ch['title'],
            'parent_id': ch.get('parent_id')
        })
        db.save_node_content(node_id, {'content': ch['content']})

    return jsonify({'book_id': book_id, 'chapters_imported': len(chapters), 'status': 'ok'})


def _parse_markdown_chapters(content):
    """按 Markdown 标题拆分章节"""
    lines = content.split('\n')
    chapters = []
    current_title = '未命名章节'
    current_content = []

    for line in lines:
        if line.startswith('#'):
            if current_content:
                chapters.append({'title': current_title, 'content': '\n'.join(current_content).strip()})
                current_content = []
            current_title = line.lstrip('#').strip()
        else:
            current_content.append(line)

    if current_content:
        chapters.append({'title': current_title, 'content': '\n'.join(current_content).strip()})

    return [c for c in chapters if c['content']]


def _parse_txt_chapters(content):
    """按"第X章"模式或空行分隔拆分章节"""
    import re
    pattern = re.compile(r'^(第[一二三四五六七八九十百千\d]+[章节回].*?)$', re.MULTILINE)
    splits = pattern.split(content)

    chapters = []
    if len(splits) <= 1:
        # 无章节标题，按段落分
        paragraphs = content.split('\n\n')
        if len(paragraphs) <= 1:
            return [{'title': '全文', 'content': content.strip()}]
        chunk_size = max(1, len(paragraphs) // 10)
        for i in range(0, len(paragraphs), chunk_size):
            chunk = '\n\n'.join(paragraphs[i:i+chunk_size]).strip()
            if chunk:
                chapters.append({'title': f'段落 {i//chunk_size + 1}', 'content': chunk})
        return chapters

    # splits[0] 是第一个标题前的内容
    if splits[0].strip():
        chapters.append({'title': '前言', 'content': splits[0].strip()})
    for i in range(1, len(splits), 2):
        title = splits[i].strip()
        body = splits[i+1].strip() if i+1 < len(splits) else ''
        if title or body:
            chapters.append({'title': title, 'content': body})

    return chapters

# ===================== Embedding API =====================

@app.route('/api/embedding/<book_id>/build', methods=['POST'])
def embedding_build(book_id):
    _require_book_access(book_id)
    result = embedding_engine.build_index(book_id, g.user_id)
    return jsonify(result)

@app.route('/api/embedding/<book_id>/status', methods=['GET'])
def embedding_status(book_id):
    _require_book_access(book_id)
    meta = db.get_index_meta(book_id)
    return jsonify({'has_index': meta is not None, 'meta': meta})

@app.route('/api/embedding/<book_id>/rebuild', methods=['POST'])
def embedding_rebuild(book_id):
    _require_book_access(book_id)
    db.delete_embedding_chunks_by_source(book_id)
    result = embedding_engine.build_index(book_id, g.user_id)
    return jsonify(result)

@app.route('/api/embedding/retrieve', methods=['POST'])
def embedding_retrieve():
    data = _json_body()
    book_id = data.get('book_id', '')
    _require_book_access(book_id)
    query = data.get('query', '')
    top_k = min(int(data.get('top_k', 5)), 50)
    results = embedding_engine.retrieve(book_id, g.user_id, query, top_k)
    return jsonify({'results': results})

# ===================== NER API =====================

@app.route('/api/ner/<book_id>/extract', methods=['POST'])
def ner_extract(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    text = data.get('text', '')
    if not text and node_id:
        conn = db._conn()
        row = conn.execute('SELECT content FROM node_contents WHERE node_id=?', (node_id,)).fetchone()
        conn.close()
        if row:
            from database import decrypt
            text = decrypt(row['content'] or '')
    results = ner_engine.extract_entities(book_id, node_id, text)
    return jsonify({'entities': results})

@app.route('/api/ner/<book_id>/extract-all', methods=['POST'])
def ner_extract_all(book_id):
    _require_book_access(book_id)
    conn = db._conn()
    chapters = conn.execute(
        "SELECT id FROM nodes WHERE book_id=? AND type='chapter' ORDER BY sort_order",
        (book_id,)
    ).fetchall()
    conn.close()
    node_ids = [ch['id'] for ch in chapters]
    results = ner_engine.extract_entities_batch(book_id, node_ids)
    return jsonify({'total': len(results), 'entities': results[:100]})

@app.route('/api/ner/<book_id>/entities', methods=['GET'])
def ner_entities(book_id):
    _require_book_access(book_id)
    node_id = request.args.get('node_id')
    entity_type = request.args.get('type')
    status = request.args.get('status')
    entities = db.get_extracted_entities(book_id, node_id=node_id, entity_type=entity_type, status=status)
    return jsonify(entities)

@app.route('/api/ner/<book_id>/unlinked', methods=['GET'])
def ner_unlinked(book_id):
    _require_book_access(book_id)
    return jsonify(db.get_unlinked_entities(book_id))

@app.route('/api/ner/<book_id>/entities/<entity_id>/link', methods=['POST'])
def ner_link(book_id, entity_id):
    _require_book_access(book_id)
    data = _json_body()
    lorebook_id = data.get('lorebook_id', '')
    db.link_entity_to_lorebook(entity_id, lorebook_id)
    return jsonify({'status': 'linked'})

@app.route('/api/ner/<book_id>/entities/<entity_id>/confirm', methods=['POST'])
def ner_confirm(book_id, entity_id):
    _require_book_access(book_id)
    db.update_entity_status(entity_id, 'confirmed')
    return jsonify({'status': 'confirmed'})

@app.route('/api/ner/<book_id>/entities/<entity_id>/dismiss', methods=['POST'])
def ner_dismiss(book_id, entity_id):
    _require_book_access(book_id)
    db.update_entity_status(entity_id, 'dismissed')
    return jsonify({'status': 'dismissed'})

# ===================== Disambiguation API =====================

@app.route('/api/disambiguation/<book_id>/resolve', methods=['POST'])
def disambiguation_resolve(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    entities = data.get('entities', [])
    if not entities:
        entities = db.get_extracted_entities(book_id, node_id=node_id)
    results = disambiguation_engine.resolve_mentions(book_id, node_id, entities)
    return jsonify({'results': results})

@app.route('/api/disambiguation/<book_id>/stats', methods=['GET'])
def disambiguation_stats(book_id):
    _require_book_access(book_id)
    return jsonify(disambiguation_engine.get_disambiguation_stats(book_id))

@app.route('/api/disambiguation/<book_id>/feedback', methods=['POST'])
def disambiguation_feedback_add(book_id):
    _require_book_access(book_id)
    data = _json_body()
    result = disambiguation_engine.add_user_feedback(
        book_id, data.get('mention_text', ''),
        data.get('character_id', ''),
        data.get('scope', 'book'),
        data.get('scope_node_id')
    )
    return jsonify(result)

@app.route('/api/disambiguation/<book_id>/feedback', methods=['GET'])
def disambiguation_feedback_list(book_id):
    _require_book_access(book_id)
    return jsonify(disambiguation_engine.get_feedbacks(book_id))

@app.route('/api/disambiguation/<book_id>/feedback/<feedback_id>', methods=['DELETE'])
def disambiguation_feedback_delete(book_id, feedback_id):
    _require_book_access(book_id)
    disambiguation_engine.delete_feedback(feedback_id)
    return jsonify({'status': 'deleted'})

# ===================== Knowledge Graph API =====================

@app.route('/api/knowledge/<book_id>/extract', methods=['POST'])
def knowledge_extract(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    text = data.get('text', '')
    if not text and node_id:
        conn = db._conn()
        row = conn.execute('SELECT content FROM node_contents WHERE node_id=?', (node_id,)).fetchone()
        conn.close()
        if row:
            from database import decrypt
            text = decrypt(row['content'] or '')
    result = knowledge_graph_engine.extract_from_chapter(book_id, node_id, text)
    return jsonify(result)

@app.route('/api/knowledge/<book_id>/extract-all', methods=['POST'])
def knowledge_extract_all(book_id):
    _require_book_access(book_id)
    conn = db._conn()
    chapters = conn.execute(
        "SELECT n.id, n.title, nc.content FROM nodes n LEFT JOIN node_contents nc ON n.id=nc.node_id WHERE n.book_id=? AND n.type='chapter' ORDER BY n.sort_order",
        (book_id,)
    ).fetchall()
    conn.close()
    from database import decrypt
    total_new = {'relations': 0, 'events': 0}
    for ch in chapters:
        text = decrypt(ch['content'] or '') if ch['content'] else ''
        if text:
            r = knowledge_graph_engine.extract_from_chapter(book_id, ch['id'], text)
            total_new['relations'] += len(r.get('new_edges', []))
            total_new['events'] += len(r.get('new_events', []))
    return jsonify({'status': 'done', 'total': total_new})

@app.route('/api/knowledge/<book_id>/graph', methods=['GET'])
def knowledge_graph(book_id):
    _require_book_access(book_id)
    center = request.args.get('center')
    depth = min(int(request.args.get('depth', 2)), 5)
    data = knowledge_graph_engine.get_graph_data(book_id, center_entity=center, depth=depth)
    return jsonify(data)

@app.route('/api/knowledge/<book_id>/entity/<name>', methods=['GET'])
def knowledge_entity(book_id, name):
    _require_book_access(book_id)
    result = knowledge_graph_engine.query_entity(book_id, name)
    return jsonify(result)

@app.route('/api/knowledge/<book_id>/relation', methods=['GET'])
def knowledge_relation(book_id):
    _require_book_access(book_id)
    a = request.args.get('a', '')
    b = request.args.get('b', '')
    result = knowledge_graph_engine.query_relation_evolution(book_id, a, b)
    return jsonify(result)

@app.route('/api/knowledge/<book_id>/events', methods=['GET'])
def knowledge_events(book_id):
    _require_book_access(book_id)
    node_id = request.args.get('node_id')
    actor = request.args.get('actor')
    events = db.get_story_events(book_id, node_id=node_id, actor_node_id=actor)
    return jsonify(events)

@app.route('/api/knowledge/<book_id>/nodes/merge', methods=['POST'])
def knowledge_merge(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_ids = data.get('node_ids', [])
    primary_id = data.get('primary_id', '')
    knowledge_graph_engine.merge_duplicate_nodes(book_id, node_ids, primary_id)
    return jsonify({'status': 'merged'})

@app.route('/api/knowledge/<book_id>/edges/<edge_id>', methods=['PUT'])
def knowledge_edge_update(book_id, edge_id):
    _require_book_access(book_id)
    data = _json_body()
    status = data.get('status', 'confirmed')
    db.update_knowledge_edge_status(edge_id, status)
    return jsonify({'status': status})

# ===================== Foreshadow Payoff API =====================

@app.route('/api/foreshadow/<book_id>/scan-payoffs', methods=['POST'])
def foreshadow_scan_payoffs(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    text = data.get('text', '')
    if not text and node_id:
        conn = db._conn()
        row = conn.execute('SELECT content FROM node_contents WHERE node_id=?', (node_id,)).fetchone()
        conn.close()
        if row:
            from database import decrypt
            text = decrypt(row['content'] or '')
    results = foreshadow_engine.scan_for_payoffs(book_id, node_id, text)
    return jsonify({'payoffs': results})

@app.route('/api/foreshadow/<book_id>/apply-payoff', methods=['POST'])
def foreshadow_apply_payoff(book_id):
    _require_book_access(book_id)
    data = _json_body()
    foreshadow_id = data.get('foreshadow_id', '')
    node_id = data.get('node_id', '')
    payoff_type = data.get('payoff_type', 'resolved')
    evidence = data.get('evidence', '')
    chapter_title = data.get('chapter_title', '')
    foreshadow_engine.apply_payoff(foreshadow_id, node_id, payoff_type, evidence, chapter_title)
    return jsonify({'status': 'applied'})

@app.route('/api/foreshadow/<book_id>/undo-payoff', methods=['POST'])
def foreshadow_undo_payoff(book_id):
    _require_book_access(book_id)
    data = _json_body()
    foreshadow_id = data.get('foreshadow_id', '')
    foreshadow_engine.undo_payoff(foreshadow_id)
    return jsonify({'status': 'undone'})

@app.route('/api/foreshadow/<book_id>/payoff-history', methods=['GET'])
def foreshadow_payoff_history(book_id):
    _require_book_access(book_id)
    foreshadow_id = request.args.get('foreshadow_id')
    return jsonify(foreshadow_engine.get_payoff_history(book_id, foreshadow_id))

@app.route('/api/foreshadow/<book_id>/density', methods=['GET'])
def foreshadow_density(book_id):
    _require_book_access(book_id)
    return jsonify(foreshadow_engine.get_foreshadow_density(book_id))

# ===================== Narrative Analysis API =====================

@app.route('/api/narrative/<book_id>/analyze', methods=['POST'])
def narrative_analyze(book_id):
    _require_book_access(book_id)
    data = _json_body()
    node_id = data.get('node_id', '')
    summary = data.get('summary')
    result = narrative_engine.analyze_chapter(book_id, node_id, summary)
    return jsonify(result or {})

@app.route('/api/narrative/<book_id>/analyze-all', methods=['POST'])
def narrative_analyze_all(book_id):
    _require_book_access(book_id)
    results = narrative_engine.analyze_book(book_id)
    return jsonify({'count': len(results), 'results': results})

@app.route('/api/narrative/<book_id>/tension', methods=['GET'])
def narrative_tension(book_id):
    _require_book_access(book_id)
    volume_id = request.args.get('volume_id')
    return jsonify(narrative_engine.get_tension_curve(book_id, volume_id))

@app.route('/api/narrative/<book_id>/emotions', methods=['GET'])
def narrative_emotions(book_id):
    _require_book_access(book_id)
    volume_id = request.args.get('volume_id')
    return jsonify(narrative_engine.get_emotion_profile(book_id, volume_id))

@app.route('/api/narrative/<book_id>/character-arcs', methods=['GET'])
def narrative_character_arcs(book_id):
    _require_book_access(book_id)
    names = request.args.getlist('name')
    return jsonify(narrative_engine.get_character_arcs(book_id, names or None))

@app.route('/api/narrative/<book_id>/pacing', methods=['GET'])
def narrative_pacing(book_id):
    _require_book_access(book_id)
    return jsonify(narrative_engine.get_pacing_diagnosis(book_id))

@app.route('/api/narrative/<book_id>/completeness', methods=['GET'])
def narrative_completeness(book_id):
    _require_book_access(book_id)
    volume_id = request.args.get('volume_id')
    return jsonify(narrative_engine.get_arc_completeness(book_id, volume_id))

# ===================== 启动 =====================

if __name__ == '__main__':
    db.init_db()
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    print("=" * 60)
    print("  AI 辅助长篇小说写作平台")
    print(f"  访问 http://localhost:5000  (debug={debug_mode})")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=debug_mode,
                 allow_unsafe_werkzeug=debug_mode)
