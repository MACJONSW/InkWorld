"""
叙事弧光分析引擎
基于章节摘要的张力曲线、情绪构成、角色弧线、节奏诊断
"""
import uuid
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class NarrativeEngine:
    def __init__(self, db):
        self.db = db
        self._llm_analyzer = None

    def set_llm_analyzer(self, callback):
        """注入 LLM 分析回调
        签名: callback(summary_text, chapter_title) ->
              {tension, conflict_level, pacing, emotions, character_focus, key_tension_point, overall_role}
        """
        self._llm_analyzer = callback

    # ------------------------------------------------------------------
    #  Analyze
    # ------------------------------------------------------------------

    def analyze_chapter(self, book_id, node_id, summary_text=None):
        """分析单章叙事指标"""
        conn = self.db._conn()

        # 获取章节信息
        node_row = conn.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        chapter_title = dict(node_row).get('title', '') if node_row else ''
        chapter_index = dict(node_row).get('sort_order', 0) if node_row else 0

        # 获取摘要
        if not summary_text:
            s_row = conn.execute(
                'SELECT summary FROM chapter_summaries WHERE node_id=? AND book_id=?',
                (node_id, book_id)
            ).fetchone()
            if s_row:
                summary_text = s_row['summary']

        # 如果没有摘要，用正文前 2000 字
        if not summary_text:
            c_row = conn.execute(
                'SELECT content FROM node_contents WHERE node_id=?', (node_id,)
            ).fetchone()
            if c_row and c_row['content']:
                summary_text = c_row['content'][:2000]

        conn.close()

        if not summary_text:
            return None

        # LLM 分析
        analysis = None
        if self._llm_analyzer:
            try:
                analysis = self._llm_analyzer(summary_text, chapter_title)
            except Exception as e:
                logger.error("Narrative analysis failed: %s", e)

        if not analysis:
            analysis = {
                'tension': 50, 'conflict_level': 50, 'pacing': 'moderate',
                'emotions': {}, 'character_focus': [], 'key_tension_point': '',
                'overall_role': '发展'
            }

        # 存入数据库
        record_id = str(uuid.uuid4())[:12]
        now = datetime.now().isoformat()
        db_conn = self.db._conn()
        # 删除旧记录
        db_conn.execute(
            'DELETE FROM narrative_analysis WHERE book_id=? AND node_id=?',
            (book_id, node_id)
        )
        db_conn.execute(
            '''INSERT INTO narrative_analysis
               (id, book_id, node_id, chapter_index, chapter_title, tension,
                conflict_level, pacing, emotions, character_focus, overall_role,
                key_tension_point, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (record_id, book_id, node_id, chapter_index, chapter_title,
             analysis.get('tension', 50),
             analysis.get('conflict_level', 50),
             analysis.get('pacing', 'moderate'),
             json.dumps(analysis.get('emotions', {}), ensure_ascii=False),
             json.dumps(analysis.get('character_focus', []), ensure_ascii=False),
             analysis.get('overall_role', ''),
             analysis.get('key_tension_point', ''),
             now)
        )
        db_conn.commit()
        db_conn.close()

        analysis['id'] = record_id
        analysis['chapter_index'] = chapter_index
        analysis['chapter_title'] = chapter_title
        return analysis

    def analyze_book(self, book_id):
        """全书分析，逐章调用"""
        conn = self.db._conn()
        chapters = conn.execute(
            "SELECT id FROM nodes WHERE book_id=? AND type='chapter' ORDER BY sort_order",
            (book_id,)
        ).fetchall()
        conn.close()

        results = []
        for ch in chapters:
            result = self.analyze_chapter(book_id, ch['id'])
            if result:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    #  Data getters for visualization
    # ------------------------------------------------------------------

    def get_tension_curve(self, book_id, volume_id=None):
        """获取张力曲线数据"""
        conn = self.db._conn()
        if volume_id:
            # 获取该卷下的章节
            chapter_ids = [r['id'] for r in conn.execute(
                "SELECT id FROM nodes WHERE parent_id=? AND type='chapter' ORDER BY sort_order",
                (volume_id,)
            ).fetchall()]
            if not chapter_ids:
                conn.close()
                return []
            placeholders = ','.join('?' * len(chapter_ids))
            rows = conn.execute(
                f'SELECT * FROM narrative_analysis WHERE book_id=? AND node_id IN ({placeholders}) ORDER BY chapter_index',
                [book_id] + chapter_ids
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM narrative_analysis WHERE book_id=? ORDER BY chapter_index',
                (book_id,)
            ).fetchall()
        conn.close()

        return [{
            'chapter_index': r['chapter_index'],
            'chapter_title': r['chapter_title'],
            'node_id': r['node_id'],
            'tension': r['tension'],
            'conflict_level': r['conflict_level'],
            'pacing': r['pacing'],
            'overall_role': r['overall_role']
        } for r in rows]

    def get_emotion_profile(self, book_id, volume_id=None):
        """获取情绪构成"""
        conn = self.db._conn()
        if volume_id:
            chapter_ids = [r['id'] for r in conn.execute(
                "SELECT id FROM nodes WHERE parent_id=? AND type='chapter' ORDER BY sort_order",
                (volume_id,)
            ).fetchall()]
            if not chapter_ids:
                conn.close()
                return []
            placeholders = ','.join('?' * len(chapter_ids))
            rows = conn.execute(
                f'SELECT * FROM narrative_analysis WHERE book_id=? AND node_id IN ({placeholders}) ORDER BY chapter_index',
                [book_id] + chapter_ids
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM narrative_analysis WHERE book_id=? ORDER BY chapter_index',
                (book_id,)
            ).fetchall()
        conn.close()

        result = []
        for r in rows:
            emotions = {}
            try:
                emotions = json.loads(r['emotions']) if r['emotions'] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            result.append({
                'chapter_index': r['chapter_index'],
                'chapter_title': r['chapter_title'],
                'node_id': r['node_id'],
                'emotions': emotions
            })
        return result

    def get_character_arcs(self, book_id, character_names=None):
        """获取角色弧线"""
        conn = self.db._conn()
        rows = conn.execute(
            'SELECT * FROM narrative_analysis WHERE book_id=? ORDER BY chapter_index',
            (book_id,)
        ).fetchall()
        conn.close()

        arcs = {}  # character_name -> [{chapter_index, presence, emotion, goal_status}]
        for r in rows:
            focus = []
            try:
                focus = json.loads(r['character_focus']) if r['character_focus'] else []
            except (json.JSONDecodeError, TypeError):
                pass
            for cf in focus:
                name = cf.get('name', '')
                if character_names and name not in character_names:
                    continue
                if name not in arcs:
                    arcs[name] = []
                arcs[name].append({
                    'chapter_index': r['chapter_index'],
                    'chapter_title': r['chapter_title'],
                    'node_id': r['node_id'],
                    'presence': cf.get('presence', 0),
                    'emotion': cf.get('emotion', ''),
                    'goal_status': cf.get('goal_status', '')
                })

        return arcs

    def get_pacing_diagnosis(self, book_id):
        """节奏诊断"""
        conn = self.db._conn()
        rows = conn.execute(
            'SELECT * FROM narrative_analysis WHERE book_id=? ORDER BY chapter_index',
            (book_id,)
        ).fetchall()
        conn.close()

        if not rows:
            return {'issues': [], 'overall': 'no_data'}

        issues = []
        data = [dict(r) for r in rows]

        # 检测连续低张力段
        low_streak = []
        for d in data:
            if d['tension'] < 30:
                low_streak.append(d)
            else:
                if len(low_streak) >= 3:
                    titles = [s['chapter_title'] for s in low_streak]
                    issues.append({
                        'type': 'low_tension_streak',
                        'severity': 'medium',
                        'message': f"第{low_streak[0]['chapter_index']}-{low_streak[-1]['chapter_index']}章连续张力偏低",
                        'chapters': titles,
                        'suggestion': '考虑在此区间加入冲突或转折'
                    })
                low_streak = []

        # 检测密度过高
        for d in data:
            if d['tension'] > 85 and d['conflict_level'] > 80:
                issues.append({
                    'type': 'high_density',
                    'severity': 'low',
                    'message': f"第{d['chapter_index']}章「{d['chapter_title']}」冲突密度极高",
                    'chapters': [d['chapter_title']],
                    'suggestion': '注意读者的情绪承受，可适当加入喘息空间'
                })

        # 检测断裂（相邻章节张力差 > 50）
        for i in range(1, len(data)):
            diff = abs(data[i]['tension'] - data[i-1]['tension'])
            if diff > 50:
                issues.append({
                    'type': 'tension_break',
                    'severity': 'high',
                    'message': f"第{data[i-1]['chapter_index']}-{data[i]['chapter_index']}章之间张力断裂（差值{diff}）",
                    'chapters': [data[i-1]['chapter_title'], data[i]['chapter_title']],
                    'suggestion': '考虑在两章之间增加过渡段落'
                })

        # 整体评价
        avg_tension = sum(d['tension'] for d in data) / len(data) if data else 50
        overall = 'balanced'
        if avg_tension < 35:
            overall = 'too_flat'
        elif avg_tension > 75:
            overall = 'too_intense'

        return {'issues': issues, 'overall': overall, 'avg_tension': round(avg_tension, 1)}

    def get_arc_completeness(self, book_id, volume_id=None):
        """弧光完整度评估"""
        curve = self.get_tension_curve(book_id, volume_id)
        if not curve or len(curve) < 3:
            return {'score': 0, 'structure': 'insufficient_data', 'details': '章节数不足，无法评估'}

        n = len(curve)
        tensions = [c['tension'] for c in curve]
        max_tension = max(tensions)
        max_idx = tensions.index(max_tension)

        # 分析结构
        rising = sum(1 for i in range(1, max_idx + 1) if tensions[i] >= tensions[i-1])
        falling = sum(1 for i in range(max_idx + 1, n) if tensions[i] <= tensions[i-1])

        # 是否有明确高潮
        has_climax = max_tension >= 70
        # 是否有开端铺垫（前 1/4 张力较低）
        q1 = n // 4 or 1
        has_setup = sum(tensions[:q1]) / q1 < 50
        # 是否有结尾收束（后 1/4 张力下降）
        q4_start = n - (n // 4 or 1)
        has_resolution = sum(tensions[q4_start:]) / max(1, n - q4_start) < max_tension * 0.7

        score = 0
        if has_setup:
            score += 25
        if rising > 0:
            score += 25
        if has_climax:
            score += 25
        if has_resolution:
            score += 25

        structure = '起承转合' if score >= 75 else ('有高潮缺收束' if not has_resolution else '结构待完善')

        return {
            'score': score,
            'structure': structure,
            'has_setup': has_setup,
            'has_rising': rising > 0,
            'has_climax': has_climax,
            'has_resolution': has_resolution,
            'climax_chapter': curve[max_idx]['chapter_title'] if has_climax else None,
            'total_chapters': n
        }
