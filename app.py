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
import threading
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

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('APP_SECRET_KEY', 'dev-secret-change-me')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

db = Database()
agent_orchestrator = AgentOrchestrator(db)
memory_engine = MemoryEngine(db)
export_engine = ExportEngine(db)
db.init_db()

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
    return render_template('index.html')


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
    model_id = db.add_model(data, g.user_id)
    return jsonify({'id': model_id, 'status': 'ok'})

@app.route('/api/models/<model_id>', methods=['PUT'])
def update_model(model_id):
    data = _json_body()
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
    top_k = data.get('top_k', 5)
    results = memory_engine.vector_retrieve(book_id, query, top_k)
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

# ===================== 启动 =====================

if __name__ == '__main__':
    db.init_db()
    print("=" * 60)
    print("  AI 辅助长篇小说写作平台")
    print("  访问 http://localhost:5000")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
