"""
统一角色注册表

集中维护前端智能体分组、智能体按钮与后端模型路由角色，避免模板/JS/Python 各写一份。
"""

ROUTING_ROLES = [
    {'id': 'planner', 'name': '架构师', 'icon': 'fa-sitemap'},
    {'id': 'beat_generator', 'name': '节拍器', 'icon': 'fa-music'},
    {'id': 'drafter', 'name': '执笔者', 'icon': 'fa-pen-nib'},
    {'id': 'validator', 'name': '验证者', 'icon': 'fa-check-double'},
    {'id': 'polisher', 'name': '润色', 'icon': 'fa-gem'},
    {'id': 'summarizer', 'name': '摘要', 'icon': 'fa-scroll'},
    {'id': 'autocomplete', 'name': '自动补全', 'icon': 'fa-wand-magic-sparkles'},
    {'id': 'association', 'name': '联想', 'icon': 'fa-lightbulb'},
    {'id': 'plan_and_solve', 'name': 'Plan 模式', 'icon': 'fa-layer-group'},
    {'id': 'hallucination', 'name': '幻觉检测', 'icon': 'fa-shield-halved'},
    {'id': 'embedding', 'name': 'Embedding 检索', 'icon': 'fa-vector-square'},
]

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
    }


def get_routing_role_ids():
    return set(ROUTING_ROLE_IDS)


def get_agent_group_map():
    return {item['id']: item['group'] for item in AGENTS}


def get_agent_panel_map():
    return {item['id']: item['panel_id'] for item in AGENTS}
