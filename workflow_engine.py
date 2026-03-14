"""
章节工作流引擎
"""
import json
from datetime import datetime


DEFAULT_STEPS = [
    {'step_id': 'input_goal', 'name': '输入本章目标', 'agent': None, 'auto_confirm': False},
    {'step_id': 'generate_beats', 'name': '生成节拍', 'agent': 'beat_generator', 'auto_confirm': False},
    {'step_id': 'draft', 'name': '生成草稿', 'agent': 'drafter', 'auto_confirm': False},
    {'step_id': 'validate', 'name': '一致性检查', 'agent': 'validator', 'auto_confirm': True},
    {'step_id': 'summarize', 'name': '生成摘要', 'agent': 'summarizer', 'auto_confirm': True},
    {'step_id': 'update_state', 'name': '更新人物历史与世界状态', 'agent': 'world_state_extract', 'auto_confirm': True},
]


class WorkflowEngine:
    def __init__(self, db):
        self.db = db
        self._agent_runners = {}

    def register_agent_runner(self, agent_name, runner):
        """注册 agent 执行器
        签名: runner(data) -> result_dict
        """
        self._agent_runners[agent_name] = runner

    def get_default_template(self):
        """获取默认工作流模板"""
        return {
            'name': '标准章节工作流',
            'steps': DEFAULT_STEPS
        }

    def create_run(self, book_id, node_id, goals='', template_id=None, user_id=None):
        """创建工作流实例"""
        if template_id:
            templates = self.db.get_workflow_templates(user_id=user_id)
            template = next((t for t in templates if t['id'] == template_id), None)
        else:
            template = None

        steps = json.loads(template['steps']) if template else DEFAULT_STEPS

        run_id = self.db.create_workflow_run({
            'book_id': book_id,
            'node_id': node_id,
            'template_id': template_id,
            'goals': goals
        })

        # 初始化步骤结果
        step_results = [
            {'step_id': s['step_id'], 'name': s['name'], 'status': 'pending',
             'result_preview': '', 'started_at': None, 'completed_at': None}
            for s in steps
        ]
        self.db.update_workflow_run(run_id, {
            'step_results': json.dumps(step_results, ensure_ascii=False)
        })

        return run_id

    def execute_step(self, run_id, step_index, user_data=None):
        """执行工作流中的单个步骤"""
        run = self.db.get_workflow_run(run_id)
        if not run:
            return {'error': '工作流不存在'}

        step_results = json.loads(run.get('step_results', '[]'))
        if step_index >= len(step_results):
            return {'error': '步骤索引越界'}

        step = step_results[step_index]
        step_def = DEFAULT_STEPS[step_index] if step_index < len(DEFAULT_STEPS) else None
        if not step_def:
            return {'error': '步骤定义不存在'}

        step['status'] = 'running'
        step['started_at'] = datetime.now().isoformat()
        self.db.update_workflow_run(run_id, {
            'current_step': step_index,
            'step_results': json.dumps(step_results, ensure_ascii=False)
        })

        try:
            agent_name = step_def.get('agent')

            if step_def['step_id'] == 'input_goal':
                # 用户输入步骤，只记录目标
                result = {'goals': user_data or run.get('goals', '')}
                step['result_preview'] = result.get('goals', '')[:200]
            elif agent_name and agent_name in self._agent_runners:
                runner = self._agent_runners[agent_name]
                agent_data = {
                    'book_id': run['book_id'],
                    'node_id': run['node_id'],
                    'goals': run.get('goals', ''),
                    'user_id': user_data.get('user_id') if isinstance(user_data, dict) else None,
                    'step_context': self._build_step_context(step_results, step_index)
                }
                if isinstance(user_data, dict):
                    agent_data.update(user_data)
                result = runner(agent_data)
                step['result_preview'] = str(result)[:500] if result else ''
            else:
                result = {'skipped': True, 'reason': f'Agent "{agent_name}" 未注册'}
                step['result_preview'] = '已跳过'

            step['status'] = 'completed'
            step['completed_at'] = datetime.now().isoformat()

        except Exception as e:
            step['status'] = 'failed'
            step['result_preview'] = f'错误: {str(e)}'
            result = {'error': str(e)}

        # 检查是否所有步骤完成
        all_done = all(s['status'] in ('completed', 'skipped') for s in step_results)
        updates = {
            'step_results': json.dumps(step_results, ensure_ascii=False),
            'current_step': step_index
        }
        if all_done:
            updates['status'] = 'completed'
            updates['completed_at'] = datetime.now().isoformat()

        self.db.update_workflow_run(run_id, updates)
        return result

    def confirm_step(self, run_id, step_index):
        """确认步骤结果，允许进入下一步"""
        run = self.db.get_workflow_run(run_id)
        if not run:
            return False
        step_results = json.loads(run.get('step_results', '[]'))
        if step_index < len(step_results):
            step_results[step_index]['status'] = 'completed'
            self.db.update_workflow_run(run_id, {
                'step_results': json.dumps(step_results, ensure_ascii=False)
            })
        return True

    def get_run_status(self, run_id):
        """获取工作流运行状态"""
        run = self.db.get_workflow_run(run_id)
        if not run:
            return None
        run['step_results'] = json.loads(run.get('step_results', '[]'))
        return run

    def _build_step_context(self, step_results, current_index):
        """构建前序步骤的上下文，供当前步骤使用"""
        context_parts = []
        for i in range(current_index):
            s = step_results[i]
            if s.get('result_preview'):
                context_parts.append(f"[{s['name']}] {s['result_preview']}")
        return '\n'.join(context_parts)
