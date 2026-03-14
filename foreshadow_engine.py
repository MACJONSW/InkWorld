"""
伏笔回填引擎 - 自动检测章节是否回收了已有伏笔
支持 embedding 语义召回 + LLM 判断 + 手动确认/撤销
"""
import uuid
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ForeshadowEngine:
    def __init__(self, db):
        self.db = db
        self._embedding_engine = None
        self._llm_judge = None

    def set_embedding_engine(self, engine):
        """注入 embedding 引擎，用于语义召回伏笔候选"""
        self._embedding_engine = engine

    def set_llm_judge(self, callback):
        """注入 LLM 判断回调
        签名: callback(chapter_text, foreshadow_item) -> {is_payoff, payoff_type, confidence, evidence}
        """
        self._llm_judge = callback

    # ------------------------------------------------------------------
    #  Scan
    # ------------------------------------------------------------------

    def scan_for_payoffs(self, book_id, node_id, text):
        """扫描当前章节是否回收了已有伏笔"""
        if not text or not text.strip():
            return []

        # 获取所有未回收伏笔
        conn = self.db._conn()
        unresolved = conn.execute(
            "SELECT * FROM foreshadowing WHERE book_id=? AND status='unresolved'",
            (book_id,)
        ).fetchall()
        conn.close()

        if not unresolved:
            return []

        unresolved_list = [dict(f) for f in unresolved]

        # 候选集：先语义召回再关键词补充
        candidates = set()

        # 1. Embedding 语义召回
        if self._embedding_engine and self._embedding_engine.has_index(book_id):
            for f_item in unresolved_list:
                query = f"{f_item.get('label','')} {f_item.get('text','')} {f_item.get('description','')}"
                try:
                    results = self._embedding_engine.retrieve(
                        book_id, None, query[:500], top_k=3,
                        source_filter='content'
                    )
                    # 检查是否命中当前章节
                    for r in results:
                        if r.get('source_id') == node_id:
                            candidates.add(f_item['id'])
                            break
                except Exception:
                    pass

        # 2. 关键词匹配补充
        text_lower = text.lower()
        for f_item in unresolved_list:
            if f_item['id'] in candidates:
                continue
            check_text = f"{f_item.get('label','')} {f_item.get('text','')}".lower()
            keywords = [kw.strip() for kw in re.split(r'[,，、\s]+', check_text) if len(kw.strip()) >= 2]
            match_count = sum(1 for kw in keywords if kw in text_lower)
            if match_count >= 2 or (match_count >= 1 and len(keywords) <= 2):
                candidates.add(f_item['id'])

        if not candidates:
            return []

        # 3. LLM 判断每个候选
        results = []
        candidate_items = [f for f in unresolved_list if f['id'] in candidates]

        if self._llm_judge:
            for f_item in candidate_items:
                try:
                    judgment = self._llm_judge(text[:4000], f_item)
                    if isinstance(judgment, dict) and judgment.get('is_payoff'):
                        results.append({
                            'foreshadow_id': f_item['id'],
                            'foreshadow': f_item,
                            'payoff_type': judgment.get('payoff_type', 'resolved'),
                            'confidence': judgment.get('confidence', 0.5),
                            'evidence': judgment.get('evidence', ''),
                            'reasoning': judgment.get('reasoning', '')
                        })
                except Exception as e:
                    logger.error("Payoff judge failed for %s: %s", f_item['id'], e)
        else:
            # 无 LLM 时，所有候选以低置信度返回
            for f_item in candidate_items:
                results.append({
                    'foreshadow_id': f_item['id'],
                    'foreshadow': f_item,
                    'payoff_type': 'resolved',
                    'confidence': 0.3,
                    'evidence': '关键词匹配',
                    'reasoning': '未配置 LLM 判断'
                })

        return results

    # ------------------------------------------------------------------
    #  Apply / Undo payoff
    # ------------------------------------------------------------------

    def apply_payoff(self, foreshadow_id, node_id, payoff_type, evidence, chapter_title=''):
        """应用伏笔回填"""
        conn = self.db._conn()

        # 更新 foreshadowing 表
        conn.execute(
            '''UPDATE foreshadowing SET status=?, resolved_chapter=?, resolved_node_id=?,
               resolved_text=?, payoff_type=?, payoff_evidence=?
               WHERE id=?''',
            (payoff_type, chapter_title, node_id, evidence[:500],
             payoff_type, evidence[:500], foreshadow_id)
        )

        # 写入 payoff_links 表
        link_id = str(uuid.uuid4())[:12]
        conn.execute(
            '''INSERT INTO foreshadow_payoff_links
               (id, book_id, foreshadow_id, payoff_node_id, payoff_type, confidence, evidence_text, auto_detected, created_at)
               VALUES (?, (SELECT book_id FROM foreshadowing WHERE id=?), ?, ?, ?, ?, ?, 0, ?)''',
            (link_id, foreshadow_id, foreshadow_id, node_id,
             payoff_type, 0.9, evidence[:500], datetime.now().isoformat())
        )

        conn.commit()
        conn.close()
        return link_id

    def undo_payoff(self, foreshadow_id):
        """撤销回填"""
        conn = self.db._conn()
        conn.execute(
            '''UPDATE foreshadowing SET status='unresolved', resolved_chapter='',
               resolved_node_id=NULL, resolved_text='', payoff_type=NULL, payoff_evidence=''
               WHERE id=?''',
            (foreshadow_id,)
        )
        conn.execute('DELETE FROM foreshadow_payoff_links WHERE foreshadow_id=?', (foreshadow_id,))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    #  Query
    # ------------------------------------------------------------------

    def get_payoff_history(self, book_id, foreshadow_id=None):
        """获取伏笔回填历史"""
        conn = self.db._conn()
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

    def get_foreshadow_density(self, book_id):
        """按章节统计伏笔埋设/回收密度"""
        conn = self.db._conn()

        # 获取所有章节
        chapters = conn.execute(
            "SELECT n.id, n.title, n.sort_order FROM nodes n WHERE n.book_id=? AND n.type='chapter' ORDER BY n.sort_order",
            (book_id,)
        ).fetchall()

        density = []
        for ch in chapters:
            ch_d = dict(ch)
            # 埋设数
            planted = conn.execute(
                'SELECT COUNT(*) as cnt FROM foreshadowing WHERE book_id=? AND node_id=?',
                (book_id, ch_d['id'])
            ).fetchone()['cnt']
            # 回收数
            resolved = conn.execute(
                'SELECT COUNT(*) as cnt FROM foreshadowing WHERE book_id=? AND resolved_node_id=?',
                (book_id, ch_d['id'])
            ).fetchone()['cnt']
            # 当前活跃（在此章之前埋设、尚未回收）
            active = conn.execute(
                '''SELECT COUNT(*) as cnt FROM foreshadowing
                   WHERE book_id=? AND status='unresolved'
                   AND sort_order <= (SELECT sort_order FROM nodes WHERE id=?)''',
                (book_id, ch_d['id'])
            ).fetchone()

            density.append({
                'chapter_index': ch_d.get('sort_order', 0),
                'chapter_title': ch_d.get('title', ''),
                'node_id': ch_d['id'],
                'planted_count': planted,
                'resolved_count': resolved,
                'active_count': active['cnt'] if active else 0
            })

        conn.close()
        return density
