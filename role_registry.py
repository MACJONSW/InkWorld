"""
统一角色注册表

集中维护前端智能体分组、智能体按钮与后端模型路由角色，避免模板/JS/Python 各写一份。
"""

ROUTING_ROLES = [
    {'id': 'planner', 'name': '架构师', 'icon': 'fa-sitemap', 'category': 'reasoning'},
    {'id': 'beat_generator', 'name': '节拍器', 'icon': 'fa-music', 'category': 'reasoning'},
    {'id': 'drafter', 'name': '执笔者', 'icon': 'fa-pen-nib', 'category': 'generative'},
    {'id': 'validator', 'name': '验证者', 'icon': 'fa-check-double', 'category': 'reasoning'},
    {'id': 'polisher', 'name': '润色', 'icon': 'fa-gem', 'category': 'generative'},
    {'id': 'summarizer', 'name': '摘要', 'icon': 'fa-scroll', 'category': 'economic'},
    {'id': 'autocomplete', 'name': '自动补全', 'icon': 'fa-wand-magic-sparkles', 'category': 'economic'},
    {'id': 'association', 'name': '联想', 'icon': 'fa-lightbulb', 'category': 'generative'},
    {'id': 'plan_and_solve', 'name': 'Plan 模式', 'icon': 'fa-layer-group', 'category': 'reasoning'},
    {'id': 'hallucination', 'name': '幻觉检测', 'icon': 'fa-shield-halved', 'category': 'reasoning'},
    {'id': 'embedding', 'name': 'Embedding 检索', 'icon': 'fa-vector-square', 'category': 'embedding'},
]

# 角色 → 模型类别映射（供 database.py fallback 使用）
ROLE_CATEGORY_MAP = {item['id']: item['category'] for item in ROUTING_ROLES}

# 模型类别定义
MODEL_CATEGORIES = [
    {'id': 'generative', 'name': '生成型', 'desc': '执笔/续写/润色', 'icon': 'fa-pen-fancy'},
    {'id': 'reasoning', 'name': '校验推理型', 'desc': '验证/幻觉检测/信息抽取', 'icon': 'fa-brain'},
    {'id': 'economic', 'name': '经济型', 'desc': '摘要/自动补全', 'icon': 'fa-bolt'},
    {'id': 'embedding', 'name': 'Embedding', 'desc': '向量检索', 'icon': 'fa-vector-square'},
]

# 各类别默认生成参数
CATEGORY_PARAM_DEFAULTS = {
    'generative': {'temperature': 0.85, 'top_p': 0.92, 'presence_penalty': 0.1, 'frequency_penalty': 0.1, 'max_tokens': 3000},
    'reasoning':  {'temperature': 0.4,  'top_p': 0.9,  'presence_penalty': 0.0, 'frequency_penalty': 0.0, 'max_tokens': 2000},
    'economic':   {'temperature': 0.5,  'top_p': 0.85, 'presence_penalty': 0.0, 'frequency_penalty': 0.0, 'max_tokens': 800},
    'embedding':  {'temperature': 0.0,  'top_p': 1.0,  'presence_penalty': 0.0, 'frequency_penalty': 0.0, 'max_tokens': 512},
}

AGENT_GROUPS = [
    {'id': 'planning', 'name': '规划'},
    {'id': 'writing', 'name': '写作'},
    {'id': 'validation', 'name': '校验'},
    {'id': 'analysis', 'name': '分析'},
]

AGENTS = [
    {
        'id': 'planner',
        'name': '架构师',
        'icon': 'fa-sitemap',
        'group': 'planning',
        'panel_id': 'agentPlanner',
        'route_role': 'planner',
    },
    {
        'id': 'beats',
        'name': '节拍器',
        'icon': 'fa-music',
        'group': 'planning',
        'panel_id': 'agentBeats',
        'route_role': 'beat_generator',
    },
    {
        'id': 'conflict',
        'name': '冲突',
        'icon': 'fa-fire',
        'group': 'planning',
        'panel_id': 'agentConflict',
        'route_role': 'planner',
    },
    {
        'id': 'brainstorm',
        'name': '联想',
        'icon': 'fa-lightbulb',
        'group': 'planning',
        'panel_id': 'agentBrainstorm',
        'route_role': 'association',
    },
    {
        'id': 'drafter',
        'name': '执笔者',
        'icon': 'fa-pen-nib',
        'group': 'writing',
        'panel_id': 'agentDrafter',
        'route_role': 'drafter',
    },
    {
        'id': 'continuation',
        'name': '续写',
        'icon': 'fa-forward',
        'group': 'writing',
        'panel_id': 'agentContinuation',
        'route_role': 'drafter',
    },
    {
        'id': 'polisher',
        'name': '润色',
        'icon': 'fa-gem',
        'group': 'writing',
        'panel_id': 'agentPolisher',
        'route_role': 'polisher',
    },
    {
        'id': 'plansolve',
        'name': 'Plan模式',
        'icon': 'fa-layer-group',
        'group': 'writing',
        'panel_id': 'agentPlansolve',
        'route_role': 'plan_and_solve',
    },
    {
        'id': 'validator',
        'name': '验证者',
        'icon': 'fa-check-double',
        'group': 'validation',
        'panel_id': 'agentValidator',
        'route_role': 'validator',
    },
    {
        'id': 'hallcheck',
        'name': '幻觉检测',
        'icon': 'fa-shield-halved',
        'group': 'validation',
        'panel_id': 'agentHallcheck',
        'route_role': 'hallucination',
    },
    {
        'id': 'worldstate',
        'name': '世界态',
        'icon': 'fa-globe',
        'group': 'validation',
        'panel_id': 'agentWorldstate',
        'route_role': 'validator',
    },
    {
        'id': 'foreshadow',
        'name': '伏笔',
        'icon': 'fa-eye',
        'group': 'validation',
        'panel_id': 'agentForeshadow',
        'route_role': 'validator',
    },
    {
        'id': 'subtext',
        'name': '潜台词',
        'icon': 'fa-masks-theater',
        'group': 'analysis',
        'panel_id': 'agentSubtext',
        'route_role': 'validator',
    },
    {
        'id': 'psychology',
        'name': '心理',
        'icon': 'fa-brain',
        'group': 'analysis',
        'panel_id': 'agentPsychology',
        'route_role': 'validator',
    },
]

ROUTING_ROLE_IDS = {item['id'] for item in ROUTING_ROLES}
AGENT_IDS = {item['id'] for item in AGENTS}
AGENT_GROUP_IDS = {item['id'] for item in AGENT_GROUPS}


def get_frontend_role_registry():
    return {
        'routing_roles': [dict(item) for item in ROUTING_ROLES],
        'agent_groups': [dict(item) for item in AGENT_GROUPS],
        'agents': [dict(item) for item in AGENTS],
        'model_categories': [dict(item) for item in MODEL_CATEGORIES],
    }


def get_routing_role_ids():
    return set(ROUTING_ROLE_IDS)


def get_agent_group_map():
    return {item['id']: item['group'] for item in AGENTS}


def get_agent_panel_map():
    return {item['id']: item['panel_id'] for item in AGENTS}
