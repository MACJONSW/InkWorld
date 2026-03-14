"""
统计指标引擎
"""
from datetime import datetime


class StatsEngine:
    def __init__(self, db):
        self.db = db

    def record_call(self, user_id, agent_role, book_id=None,
                    first_token_latency_ms=None, total_duration_ms=None,
                    success=True, retried=False,
                    prompt_tokens=0, completion_tokens=0):
        """记录一次 agent 调用的增强统计"""
        self.db.record_enhanced_stat({
            'user_id': user_id,
            'book_id': book_id,
            'agent_role': agent_role,
            'first_token_latency_ms': first_token_latency_ms,
            'total_duration_ms': total_duration_ms,
            'success': 1 if success else 0,
            'retried': 1 if retried else 0,
            'adopted': 0,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens
        })

    def mark_adopted(self, user_id, agent_role, book_id=None):
        """标记最近一次调用结果被用户采纳"""
        self.db.mark_stat_adopted(user_id, agent_role, book_id)

    def get_dashboard(self, user_id, book_id=None):
        """获取统计面板数据"""
        stats = self.db.get_enhanced_stats(user_id, book_id=book_id)
        if not stats:
            return {'agents': [], 'totals': {}}

        total_calls = sum(s.get('call_count', 0) for s in stats)
        total_success = sum(s.get('success_count', 0) for s in stats)
        total_adopted = sum(s.get('adopt_count', 0) for s in stats)
        total_prompt = sum(s.get('total_prompt_tokens', 0) or 0 for s in stats)
        total_completion = sum(s.get('total_completion_tokens', 0) or 0 for s in stats)

        return {
            'agents': [{
                'role': s['agent_role'],
                'call_count': s.get('call_count', 0),
                'success_rate': round(s['success_count'] / s['call_count'] * 100, 1) if s.get('call_count') else 0,
                'retry_rate': round(s['retry_count'] / s['call_count'] * 100, 1) if s.get('call_count') else 0,
                'adoption_rate': round(s['adopt_count'] / s['call_count'] * 100, 1) if s.get('call_count') else 0,
                'avg_first_token_ms': round(s.get('avg_first_token_ms') or 0),
                'avg_duration_ms': round(s.get('avg_duration_ms') or 0),
                'total_tokens': (s.get('total_prompt_tokens') or 0) + (s.get('total_completion_tokens') or 0)
            } for s in stats],
            'totals': {
                'total_calls': total_calls,
                'success_rate': round(total_success / total_calls * 100, 1) if total_calls else 0,
                'adoption_rate': round(total_adopted / total_calls * 100, 1) if total_calls else 0,
                'total_tokens': total_prompt + total_completion,
                'estimated_cost_usd': round((total_prompt * 3 + total_completion * 15) / 1_000_000, 2)
            }
        }
