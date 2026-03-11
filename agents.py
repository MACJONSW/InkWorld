"""
多智能体编排系统 - 五大 Agent 角色
Planner / Beat Generator / Drafter / Validator / Polisher
"""
import json
import time
import threading
import re
import httpx
from openai import OpenAI


class AgentOrchestrator:
    def __init__(self, db):
        self.db = db
        self._stop_flags = {}
        self._stop_lock = threading.Lock()
        self._request_ctx = threading.local()

    def set_request_user(self, user_id):
        self._request_ctx.user_id = user_id

    def _current_user_id(self):
        return getattr(self._request_ctx, 'user_id', None)

    def _stop_event(self, user_id=None):
        key = user_id or self._current_user_id() or '__global__'
        with self._stop_lock:
            evt = self._stop_flags.get(key)
            if not evt:
                evt = threading.Event()
                self._stop_flags[key] = evt
        return evt

    def stop_generation(self, user_id=None):
        self._stop_event(user_id).set()

    def _get_client(self, role):
        user_id = self._current_user_id()
        if not user_id:
            return None, None, None
        model_config = self.db.get_model_for_role(role, user_id)
        if not model_config:
            return None, None, None
        client = OpenAI(
            api_key=model_config['api_key'],
            base_url=model_config['base_url'],
            timeout=120.0,
            http_client=httpx.Client(
                timeout=120.0,
                follow_redirects=True,
                verify=False
            )
        )
        return client, model_config['model_id'], model_config

    def _get_params(self):
        user_id = self._current_user_id()
        if not user_id:
            return {
                'temperature': 0.7,
                'top_p': 0.9,
                'presence_penalty': 0.0,
                'frequency_penalty': 0.0,
                'max_tokens': 2000,
            }
        params = self.db.get_generation_params(user_id)
        return {
            'temperature': params.get('temperature', 0.7),
            'top_p': params.get('top_p', 0.9),
            'presence_penalty': params.get('presence_penalty', 0.0),
            'frequency_penalty': params.get('frequency_penalty', 0.0),
            'max_tokens': params.get('max_tokens', 2000),
        }

    def _maybe_build_character_ctx(self, mem, book_id, seed_text='', node_id=None, max_characters=5, memory_ctx=''):
        if not book_id or '=== 人物提醒 ===' in (memory_ctx or ''):
            return ''
        return mem.build_character_reminder_context(
            book_id,
            text=seed_text,
            node_id=node_id,
            max_characters=max_characters
        )

    def _call_llm(self, role, messages, stream=False):
        client, model_id, config = self._get_client(role)
        if not client:
            if stream:
                yield "⚠️ 未配置模型，请先在设置中添加模型并分配角色路由。"
                return
            return "⚠️ 未配置模型，请先在设置中添加模型并分配角色路由。"

        params = self._get_params()
        user_id = self._current_user_id()

        try:
            if stream:
                stop_event = self._stop_event()
                stop_event.clear()
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    stream=True,
                    temperature=params['temperature'],
                    top_p=params['top_p'],
                    presence_penalty=params['presence_penalty'],
                    frequency_penalty=params['frequency_penalty'],
                    max_tokens=params['max_tokens'],
                )
                full_text = ""
                for chunk in response:
                    if stop_event.is_set():
                        break
                    if chunk.choices and chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        full_text += text
                        yield text
                # Record tokens (estimate)
                try:
                    prompt_est = sum(len(m.get('content', '')) for m in messages) // 4
                    comp_est = len(full_text) // 4
                    self.db.record_tokens(config['id'], role, prompt_est, comp_est, user_id)
                except Exception:
                    pass
            else:
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=params['temperature'],
                    top_p=params['top_p'],
                    presence_penalty=params['presence_penalty'],
                    frequency_penalty=params['frequency_penalty'],
                    max_tokens=params['max_tokens'],
                )
                result = response.choices[0].message.content
                try:
                    usage = response.usage
                    if usage:
                        self.db.record_tokens(config['id'], role,
                                            usage.prompt_tokens, usage.completion_tokens, user_id)
                except Exception:
                    pass
                return result
        except Exception as e:
            err_msg = f"⚠️ API调用失败: {str(e)}"
            if stream:
                yield err_msg
            else:
                return err_msg

    # ============== Agent 1: 架构师 (Planner) ==============

    def run_planner(self, data):
        inspiration = data.get('inspiration', '')
        book_id = data.get('book_id', '')
        genre = data.get('genre', '')
        volume_count = data.get('volume_count', 3)
        chapters_per_volume = data.get('chapters_per_volume', 10)

        # 获取已有设定
        lorebook = self.db.get_lorebook_entries(book_id) if book_id else []
        setting_ctx = ""
        if lorebook:
            setting_ctx = "\n已有世界观设定：\n" + "\n".join([
                f"- {e['name']}({e['category']}): {e['description']}" for e in lorebook[:20]
            ])

        messages = [
            {"role": "system", "content": f"""你是一位资深小说架构师。根据用户的灵感和需求，输出结构严谨的长篇小说大纲。
要求：
1. 包含"起承转合"结构
2. 每卷包含主题概述
3. 每章包含章节标题和核心事件
4. 角色弧线和矛盾冲突清晰
5. 输出JSON格式
{setting_ctx}"""},
            {"role": "user", "content": f"""灵感/概况: {inspiration}
类型: {genre if genre else '自由发挥'}
规划 {volume_count} 卷，每卷约 {chapters_per_volume} 章。

请输出完整大纲，JSON格式如下：
{{
  "title": "小说标题",
  "synopsis": "总体简介",
  "volumes": [
    {{
      "title": "第一卷标题",
      "theme": "本卷主题",
      "chapters": [
        {{"title": "第一章标题", "summary": "章节概述", "key_events": "关键事件"}}
      ]
    }}
  ]
}}"""}
        ]

        result = self._call_llm('planner', messages)

        # 保存大纲
        if book_id:
            self.db.save_outline({
                'book_id': book_id,
                'content': result,
                'outline_type': 'volume'
            })

        return {'outline': result}

    # ============== Agent 2: 节拍器 (Beat Generator) ==============

    def run_beat_generator(self, data):
        chapter_outline = data.get('chapter_outline', '')
        book_id = data.get('book_id', '')

        # 收集上下文
        lorebook = self.db.get_lorebook_entries(book_id) if book_id else []
        chars = [e for e in lorebook if e['category'] == 'character']
        char_ctx = "\n可用角色：\n" + "\n".join([
            f"- {c['name']}: {c['description']}" for c in chars[:15]
        ]) if chars else ""

        messages = [
            {"role": "system", "content": f"""你是一位精细的场景节拍规划师。将章节大纲拆解为具体的场景节拍(Scene Beats)。
每个节拍需要明确：
1. 发生地点
2. 出场人物
3. 核心冲突/事件
4. 情感基调
5. 预计字数
{char_ctx}

输出JSON数组格式。"""},
            {"role": "user", "content": f"""请将以下章节大纲拆解为场景节拍：

{chapter_outline}

输出格式：
[
  {{
    "beat_number": 1,
    "location": "地点",
    "characters": ["角色1", "角色2"],
    "conflict": "核心冲突",
    "mood": "情感基调",
    "description": "场景描述",
    "estimated_words": 500
  }}
]"""}
        ]

        result = self._call_llm('beat_generator', messages)
        return {'beats': result}

    # ============== Agent 3: 执笔者 (Drafter) - 流式 ==============

    def run_drafter_stream(self, data):
        beat = data.get('beat', '')
        context = data.get('context', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')
        style = data.get('style', '自然流畅')
        previous_text = data.get('previous_text', '')

        # 构建记忆上下文
        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        memory_ctx = mem.build_context_window(book_id, node_id) if book_id else ""
        reminder_seed = "\n".join([previous_text[-1500:], beat, context])
        character_ctx = self._maybe_build_character_ctx(
            mem,
            book_id,
            seed_text=reminder_seed,
            node_id=node_id,
            max_characters=5,
            memory_ctx=memory_ctx
        )

        # 动态注入
        inject_text = ""
        if previous_text and book_id:
            injected = mem.dynamic_inject(book_id, previous_text[-500:])
            if injected:
                inject_text = "\n触发设定注入：\n" + "\n".join([
                    f"【{e['name']}】{e['content'][:200]}" for e in injected[:5]
                ])

        messages = [
            {"role": "system", "content": f"""你是一位才华横溢的小说家。根据给定的场景节拍和上下文，创作生动的长篇小说正文。

写作要求：
1. 文风: {style}
2. 保持角色性格一致
3. 注重环境描写和感官细节
4. 对话自然、符合角色身份
5. 推进情节发展
6. 直接输出正文内容，不要输出任何标题、标记或解释

{memory_ctx}
{character_ctx}
{inject_text}"""},
            {"role": "user", "content": f"""前文内容：
{previous_text[-2000:] if previous_text else '（这是开篇）'}

---
当前场景节拍：{beat if beat else '自由续写'}
附加指示：{context if context else '无'}

请直接创作正文："""}
        ]

        for chunk in self._call_llm('drafter', messages, stream=True):
            yield chunk

    # ============== Agent 4: 验证者 (Validator) ==============

    def run_validator(self, data):
        text = data.get('text', '')
        book_id = data.get('book_id', '')

        lorebook = self.db.get_lorebook_entries(book_id) if book_id else []
        setting_ctx = "\n".join([
            f"- {e['name']}({e['category']}): {e['content'][:300]}" for e in lorebook[:20]
        ])
        graph = self.db.get_entity_graph(book_id) if book_id else []
        graph_ctx = "\n".join([
            f"- {r['source_entity']} → {r['target_entity']}: {r['relation_type']}={r['relation_value']}"
            for r in graph[:20]
        ])

        messages = [
            {"role": "system", "content": f"""你是一位严谨的小说审阅专家。对比设定集与生成的文本，进行以下校验：
1. OOC检测（角色崩坏）：检查角色行为是否符合设定
2. 时间线校验：检查事件顺序逻辑
3. 设定一致性：检查是否违反世界观设定
4. 逻辑硬伤：标注不合理的情节

设定集：
{setting_ctx}

关系图谱：
{graph_ctx}

严格以JSON格式输出检测结果。"""},
            {"role": "user", "content": f"""请审查以下文本：

{text[:4000]}

输出格式：
{{
  "score": 85,
  "issues": [
    {{
      "type": "ooc|timeline|setting|logic",
      "severity": "high|medium|low",
      "location": "问题所在的文本片段",
      "description": "问题描述",
      "suggestion": "修改建议"
    }}
  ],
  "summary": "总体评价"
}}"""}
        ]

        result = self._call_llm('validator', messages)
        return {'validation': result}

    # ============== Agent 5: 润色 (Polisher) ==============

    def run_polisher(self, data):
        text = data.get('text', '')
        style = data.get('style', '华丽')
        instruction = data.get('instruction', '')

        style_guides = {
            '白描': '用最简洁的笔触描写，避免华丽辞藻，追求"言简意丰"的效果',
            '华丽': '使用丰富的修辞手法，包括比喻、拟人、排比等，营造瑰丽的文学氛围',
            '悬疑': '制造紧张氛围，使用短句增加节奏感，适度留白制造悬念',
            '幽默': '融入风趣的语言和巧妙的比喻，在叙事中穿插会心一笑的细节',
            '诗意': '注重韵律感和意象的运用，用诗化的语言营造唯美意境',
        }
        guide = style_guides.get(style, f'按照"{style}"风格进行润色')

        messages = [
            {"role": "system", "content": f"""你是一位文学润色大师。
润色风格指导：{guide}
{f'额外指令：{instruction}' if instruction else ''}

要求：
1. 保持原文的核心情节和对话内容不变
2. 提升文学表现力和感官沉浸感
3. 优化句式节奏
4. 直接输出润色后的完整文本"""},
            {"role": "user", "content": f"请润色以下文本：\n\n{text}"}
        ]

        result = self._call_llm('polisher', messages)
        return {'polished': result, 'original': text}

    # ============== 章节摘要生成 ==============

    def run_summarizer(self, data):
        text = data.get('text', '')
        chapter_title = data.get('chapter_title', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        messages = [
            {"role": "system", "content": """你是一位精准的文本摘要专家。为小说章节生成结构化摘要，用于长线写作的记忆管理。

输出JSON格式：
{
  "summary": "200字以内的章节摘要",
  "key_events": "关键事件列表（JSON数组）",
  "character_states": "主要角色在本章结束时的状态变化"
}"""},
            {"role": "user", "content": f"章节标题：{chapter_title}\n\n章节正文：\n{text[:6000]}"}
        ]

        result = self._call_llm('summarizer', messages)

        # 保存摘要
        if book_id:
            self.db.save_chapter_summary({
                'book_id': book_id,
                'node_id': node_id,
                'chapter_title': chapter_title,
                'summary': result,
                'key_events': ''
            })

        return {'summary': result}

    # ============== 智能续写 (Smart Continuation) ==============

    def run_smart_continuation(self, data):
        """
        智能续写：上下文窗口 + 依赖注入 + 目标对齐 + 批评-重试循环
        """
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')
        previous_text = data.get('previous_text', '')
        goal = data.get('goal', '')  # 用户期望(可选)
        max_retries = data.get('max_retries', 2)
        style = data.get('style', '自然流畅')

        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        memory_ctx = mem.build_context_window(book_id, node_id) if book_id else ""
        character_ctx = self._maybe_build_character_ctx(
            mem,
            book_id,
            seed_text=previous_text[-2500:],
            node_id=node_id,
            max_characters=5,
            memory_ctx=memory_ctx
        )

        # 动态注入设定
        inject_text = ""
        if previous_text and book_id:
            injected = mem.dynamic_inject(book_id, previous_text[-800:])
            if injected:
                inject_text = "\n触发设定注入：\n" + "\n".join([
                    f"【{e['name']}】{e['content'][:200]}" for e in injected[:5]
                ])

        # 获取大纲目标
        outline_ctx = ""
        if book_id:
            outlines = self.db.get_outlines(book_id) if hasattr(self.db, 'get_outlines') else []
            if outlines:
                outline_ctx = f"\n大纲参考(保持方向对齐)：\n{outlines[-1].get('content','')[:600]}"

        goal_prompt = f"\n用户期望方向：{goal}" if goal else ""

        draft_messages = [
            {"role": "system", "content": f"""你是一位才华横溢的小说家，负责续写长篇小说。

写作要求：
1. 文风: {style}
2. 保持角色性格、语气、行为风格一致(OOC检测)
3. 推进情节发展，不陷入重复或循环
4. 保持时间线和因果逻辑自洽
5. 直接输出续写正文，不要标题或解释
{goal_prompt}
{memory_ctx}
{character_ctx}
{inject_text}
{outline_ctx}"""},
            {"role": "user", "content": f"""前文内容（截取最后部分）：
{previous_text[-3000:] if previous_text else '（这是开篇）'}

请直接续写正文："""}
        ]

        best_draft = ""
        best_score = 0

        for attempt in range(max_retries):
            # ---- 生成草稿 ----
            draft = self._call_llm('drafter', draft_messages, stream=False)
            if not draft or draft.startswith('⚠️'):
                # streaming fallback
                for chunk in self._call_llm('drafter', draft_messages, stream=True):
                    yield chunk
                return

            # ---- 批评者审查 ----
            critique_messages = [
                {"role": "system", "content": """你是一位严格的小说续写审查员。评估续写文本是否满足以下标准，输出JSON：
{
  "score": 0-100,
  "issues": ["问题1", "问题2"],
  "ooc": false,
  "timeline_break": false,
  "repetitive": false,
  "suggestion": "改进建议"
}
评分标准：
- 角色一致性(25分)
- 情节推进(25分)
- 文笔质量(25分)
- 逻辑连贯(25分)"""},
                {"role": "user", "content": f"前文：\n{previous_text[-1500:]}\n\n续写：\n{draft[:2000]}"}
            ]

            critique_result = self._call_llm('validator', critique_messages, stream=False)

            # 解析分数
            score = 75  # 默认
            try:
                # 尝试从JSON提取score
                import re as _re
                json_match = _re.search(r'\{[^{}]*"score"\s*:\s*(\d+)[^{}]*\}', critique_result, _re.DOTALL)
                if json_match:
                    score = int(json_match.group(1))
            except Exception:
                pass

            if score > best_score:
                best_score = score
                best_draft = draft

            # 如果质量足够，直接输出
            if score >= 70:
                break

            # 否则将批评加入context重试
            draft_messages.append({"role": "assistant", "content": draft})
            draft_messages.append({"role": "user", "content": f"审查反馈（请改进）：{critique_result}\n请重新续写："})

        # 流式输出最终结果
        stop_event = self._stop_event()
        for char in best_draft:
            if stop_event.is_set():
                break
            yield char

    def run_smart_continuation_stream(self, data):
        """直接流式续写（无批评循环，低延迟版）"""
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')
        previous_text = data.get('previous_text', '')
        goal = data.get('goal', '')
        style = data.get('style', '自然流畅')

        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        memory_ctx = mem.build_context_window(book_id, node_id) if book_id else ""
        character_ctx = self._maybe_build_character_ctx(
            mem,
            book_id,
            seed_text=previous_text[-2500:],
            node_id=node_id,
            max_characters=5,
            memory_ctx=memory_ctx
        )

        inject_text = ""
        if previous_text and book_id:
            injected = mem.dynamic_inject(book_id, previous_text[-800:])
            if injected:
                inject_text = "\n触发设定注入：\n" + "\n".join([
                    f"【{e['name']}】{e['content'][:200]}" for e in injected[:5]
                ])

        goal_prompt = f"\n用户期望方向：{goal}" if goal else ""

        messages = [
            {"role": "system", "content": f"""你是一位才华横溢的小说家，负责续写长篇小说。
写作要求：
1. 文风: {style}
2. 保持角色性格一致
3. 推进情节发展
4. 直接续写正文
{goal_prompt}
{memory_ctx}
{character_ctx}
{inject_text}"""},
            {"role": "user", "content": f"""前文：
{previous_text[-3000:] if previous_text else '（开篇）'}

请直接续写："""}
        ]

        for chunk in self._call_llm('drafter', messages, stream=True):
            yield chunk

    # ============== 自动补全 (Autocomplete / Ghost Text) ==============

    def run_autocomplete(self, data):
        """
        低延迟自动补全：微上下文 + 低max_tokens + 低temperature
        返回短预测文本(ghost text)
        """
        text = data.get('text', '')  # 光标前文本
        book_id = data.get('book_id', '')

        if not text or len(text.strip()) < 10:
            return {'prediction': ''}

        # 微上下文：仅最近800字符 + 少量设定
        micro_context = text[-800:]

        # 快速设定注入（只取3条最相关）
        inject = ""
        character_ctx = ""
        if book_id:
            from memory_engine import MemoryEngine
            mem = MemoryEngine(self.db)
            injected = mem.dynamic_inject(book_id, text[-200:])
            character_ctx = mem.build_character_reminder_context(book_id, text=micro_context, max_characters=3)
            if injected:
                inject = "\n参考设定：" + "；".join([
                    f"{e['name']}:{e['content'][:60]}" for e in injected[:3]
                ])

        messages = [
            {"role": "system", "content": f"""你是一个小说续写助手。根据上文预测接下来最可能的1-2句话。
要求：
1. 预测内容自然衔接上文
2. 保持文风一致
3. 只输出预测文本，不要任何解释或标记
4. 控制在30-80字以内
{character_ctx}
{inject}"""},
            {"role": "user", "content": f"上文：\n{micro_context}\n\n预测续文："}
        ]

        # 使用低temperature和低max_tokens实现快速预测
        client, model_id, config = self._get_client('autocomplete')
        if not client:
            return {'prediction': ''}

        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.3,
                top_p=0.85,
                max_tokens=120,
                presence_penalty=0.0,
                frequency_penalty=0.0,
            )
            prediction = response.choices[0].message.content.strip()
            # 记录tokens
            if config and response.usage:
                self.db.record_tokens(config['id'], 'autocomplete',
                                    response.usage.prompt_tokens, response.usage.completion_tokens,
                                    self._current_user_id())
            return {'prediction': prediction}
        except Exception as e:
            return {'prediction': '', 'error': str(e)}

    # ============== 冲突设计 (Conflict Design Agent) ==============

    def run_conflict_design(self, data):
        """
        冲突设计Agent：生成对抗矩阵和三种冲突方案(A/B/C)
        """
        book_id = data.get('book_id', '')
        characters = data.get('characters', '')  # 涉及角色
        context = data.get('context', '')  # 当前情节上下文
        conflict_type = data.get('conflict_type', 'all')  # 人vs人/人vs环境/人vs自我/all

        lorebook = self.db.get_lorebook_entries(book_id) if book_id else []
        chars = [e for e in lorebook if e['category'] == 'character']
        char_ctx = "\n角色设定：\n" + "\n".join([
            f"- {c['name']}: {c['description']}｜{c['content'][:150]}" for c in chars[:15]
        ]) if chars else ""

        graph = self.db.get_entity_graph(book_id) if book_id else []
        graph_ctx = "\n角色关系：\n" + "\n".join([
            f"- {r['source_entity']}→{r['target_entity']}:{r['relation_type']}={r['relation_value']}"
            for r in graph[:20]
        ]) if graph else ""

        type_instruction = ""
        if conflict_type == 'person_vs_person':
            type_instruction = "聚焦于人物间的冲突对抗"
        elif conflict_type == 'person_vs_env':
            type_instruction = "聚焦于人物与环境/社会/命运的冲突"
        elif conflict_type == 'person_vs_self':
            type_instruction = "聚焦于人物内心的挣扎和矛盾"

        messages = [
            {"role": "system", "content": f"""你是一位擅长制造戏剧冲突的故事顾问。基于角色设定和关系图谱，设计引人入胜的冲突。
{type_instruction}
{char_ctx}
{graph_ctx}

你需要输出严格的JSON格式，包含对抗矩阵和三种不同走向的冲突方案：
{{
  "antagonist_matrix": {{
    "protagonist": "主角",
    "antagonist": "对手/对立面",
    "stakes": "赌注(失去什么)",
    "power_dynamic": "力量对比",
    "emotional_core": "情感内核"
  }},
  "conflicts": [
    {{
      "id": "A",
      "title": "方案标题",
      "type": "person_vs_person|person_vs_env|person_vs_self",
      "severity": "high|medium|low",
      "description": "冲突描述(100字)",
      "trigger": "触发条件",
      "escalation": "升级路径",
      "resolution_hint": "可能的化解方向",
      "affected_chars": ["角色1", "角色2"],
      "tension_score": 85
    }}
  ]
}}"""},
            {"role": "user", "content": f"""当前情节背景：
{context if context else '（请基于角色设定自由设计）'}

涉及角色：{characters if characters else '自动选择'}

请设计3种不同的冲突方案（A/B/C），涵盖不同冲突类型和严重程度："""}
        ]

        result = self._call_llm('planner', messages, stream=False)
        return {'conflicts': result}

    # ============== 联想/头脑风暴 (Association / Brainstorm) ==============

    def run_association(self, data):
        """
        发散联想Agent：高temperature多探针并行生成
        支持因果链、反转、细节放大三种探针维度
        """
        book_id = data.get('book_id', '')
        seed_text = data.get('seed_text', '')  # 触发联想的种子文本
        dimension = data.get('dimension', 'all')  # causal/reverse/detail/all

        lorebook = self.db.get_lorebook_entries(book_id) if book_id else []
        setting_hints = "；".join([
            f"{e['name']}({e['category']})" for e in lorebook[:10]
        ]) if lorebook else "无"

        probes = {
            'causal': f"基于这段文本，推演3个可能的因果发展方向（蝴蝶效应式展开）：\n{seed_text}",
            'reverse': f"基于这段文本，假设出现意外反转，设计3个出人意料的转折点：\n{seed_text}",
            'detail': f"基于这段文本，从一个细节切入放大，发散出3个深入描写的可能：\n{seed_text}",
        }

        if dimension == 'all':
            selected_probes = probes
        else:
            selected_probes = {dimension: probes.get(dimension, probes['causal'])}

        all_cards = []

        for probe_type, prompt in selected_probes.items():
            messages = [
                {"role": "system", "content": f"""你是一位极富想象力的故事创意顾问。以发散思维和高度创造性生成灵感卡片。
世界观元素：{setting_hints}

要求输出JSON数组，每个元素是一个创意卡片：
[
  {{
    "title": "卡片标题(6字以内)",
    "type": "{probe_type}",
    "content": "详细描述(80-150字)",
    "hook": "一句话勾子（引发好奇心的问题或悬念）",
    "usability": 75,
    "tags": ["标签1", "标签2"]
  }}
]"""},
                {"role": "user", "content": prompt}
            ]

            # 使用高temperature激发创意
            client, model_id, config = self._get_client('association')
            if not client:
                continue

            try:
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=1.0,
                    top_p=0.95,
                    max_tokens=1500,
                    presence_penalty=0.6,
                    frequency_penalty=0.3,
                )
                result_text = response.choices[0].message.content
                if config and response.usage:
                    self.db.record_tokens(config['id'], 'association',
                                        response.usage.prompt_tokens, response.usage.completion_tokens,
                                        self._current_user_id())

                # 解析JSON
                try:
                    import re as _re
                    json_match = _re.search(r'\[.*\]', result_text, _re.DOTALL)
                    if json_match:
                        cards = json.loads(json_match.group())
                        for c in cards:
                            c['probe_type'] = probe_type
                        all_cards.extend(cards)
                    else:
                        all_cards.append({
                            'title': f'{probe_type}联想',
                            'type': probe_type,
                            'content': result_text,
                            'hook': '',
                            'usability': 50,
                            'tags': [probe_type]
                        })
                except (json.JSONDecodeError, Exception):
                    all_cards.append({
                        'title': f'{probe_type}联想',
                        'type': probe_type,
                        'content': result_text,
                        'hook': '',
                        'usability': 50,
                        'tags': [probe_type]
                    })
            except Exception as e:
                all_cards.append({
                    'title': '错误',
                    'type': probe_type,
                    'content': f'联想生成失败: {str(e)}',
                    'hook': '',
                    'usability': 0,
                    'tags': ['error']
                })

        return {'cards': all_cards}

    # ============== 伏笔检测 (Foreshadowing Detection) ==============

    def run_foreshadow_detect(self, data):
        """自动检测文本中的潜在伏笔"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        if not text or len(text.strip()) < 50:
            return {'foreshadowing': []}

        # 获取已有伏笔避免重复
        existing = self.db.get_foreshadowing(book_id) if book_id else []
        existing_texts = [f['text'][:50] for f in existing]
        existing_ctx = ""
        if existing_texts:
            existing_ctx = f"\n已标记的伏笔（避免重复）：\n" + "\n".join([f"- {t}" for t in existing_texts[:10]])

        messages = [
            {"role": "system", "content": f"""你是一位经验丰富的小说编辑，擅长识别文本中的伏笔和悬念。
分析文本，找出其中潜在的伏笔元素，包括：
1. 悬而未决的问题或谜团
2. 暗示未来情节的描写
3. 故意留下的线索
4. 角色反常行为或未解释的动机
5. 有象征意义的物品或场景
{existing_ctx}

以JSON数组格式输出，每个伏笔包含：
[
  {{
    "text": "伏笔所在的原文片段(20-60字)",
    "label": "简短标签(5字以内)",
    "description": "为什么这是伏笔及可能的展开方向",
    "severity": "high|medium|low"
  }}
]
如果没有发现伏笔，返回空数组 []"""},
            {"role": "user", "content": f"请分析以下文本中的伏笔：\n\n{text[:5000]}"}
        ]

        result = self._call_llm('validator', messages, stream=False)
        return {'foreshadowing': result}

    def run_foreshadow_scan(self, data):
        """扫描未填坑伏笔池，为当前场景建议payoff"""
        book_id = data.get('book_id', '')
        current_text = data.get('text', '')
        chapter_title = data.get('chapter_title', '')

        unresolved = self.db.get_foreshadowing(book_id, status='unresolved') if book_id else []
        if not unresolved:
            return {'suggestions': '当前没有未填的伏笔。'}

        pool_ctx = "\n".join([
            f"【{f['label']}】{f['text'][:100]} — {f['description'][:100]}"
            for f in unresolved
        ])

        messages = [
            {"role": "system", "content": f"""你是一位精通叙事结构的小说顾问。
以下是未填坑的伏笔池：
{pool_ctx}

基于当前章节内容，分析哪些伏笔适合在本章或近期章节中"填坑"（payoff），
并给出具体的填坑建议。

以JSON格式输出：
{{
  "ready_to_resolve": [
    {{
      "label": "伏笔标签",
      "reason": "为什么现在适合填坑",
      "suggestion": "具体的填坑方式建议",
      "integration_text": "建议的融入文本(2-3句)"
    }}
  ],
  "keep_pending": [
    {{
      "label": "伏笔标签",
      "reason": "为什么还不适合填坑"
    }}
  ],
  "overall_advice": "整体伏笔管理建议"
}}"""},
            {"role": "user", "content": f"当前章节：{chapter_title}\n\n当前内容：\n{current_text[:3000]}"}
        ]

        result = self._call_llm('planner', messages, stream=False)
        return {'suggestions': result}

    # ============== 潜台词分析 (Subtext Analysis) ==============

    def run_subtext_analysis(self, data):
        """双轨对话分析：表面台词 + 潜台词"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')
        characters = data.get('characters', '')

        # 获取角色心理档案
        psych_ctx = ""
        if book_id:
            profiles = self.db.get_character_psychology(book_id)
            if profiles:
                psych_ctx = "\n角色心理档案：\n" + "\n".join([
                    f"- {p['character_name']}: 驱动力={p['drives'][:60]}; 恐惧={p['fears'][:60]}; 防御机制={p['defense_mechanisms'][:60]}"
                    for p in profiles[:8]
                ])

        # 获取角色设定
        char_ctx = ""
        if book_id:
            lorebook = self.db.get_lorebook_entries(book_id)
            chars = [e for e in lorebook if e['category'] == 'character']
            if chars:
                char_ctx = "\n角色设定：\n" + "\n".join([
                    f"- {c['name']}: {c['content'][:120]}" for c in chars[:10]
                ])

        messages = [
            {"role": "system", "content": f"""你是一位精通叙事心理学的文学分析大师。
对文本中的对话和行为进行"双轨分析"：
- 表面轨道：角色表面上说了什么、做了什么
- 潜台词轨道：角色真正想表达的、隐含的情感和动机

{psych_ctx}
{char_ctx}

以JSON格式输出分析：
{{
  "dialogues": [
    {{
      "character": "角色名",
      "surface_text": "表面台词或行为",
      "subtext": "潜台词/真实意图",
      "emotion_beneath": "隐藏的情感",
      "motivation": "行为动机",
      "power_dynamic": "权力关系变化"
    }}
  ],
  "scene_subtext": "整个场景的潜台词层面总结",
  "tension_sources": ["张力来源1", "张力来源2"],
  "rewrite_suggestions": [
    {{
      "original": "原文",
      "enhanced": "增加潜台词后的改写建议"
    }}
  ]
}}"""},
            {"role": "user", "content": f"请进行双轨对话分析：\n\n{text[:5000]}"}
        ]

        result = self._call_llm('validator', messages, stream=False)
        return {'analysis': result}

    # ============== 心理透视镜 (Psychology Lens) ==============

    def run_psychology_lens(self, data):
        """深层心理分析：角色行为和对话的心理学解读"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')
        character = data.get('character', '')

        psych_profile = ""
        if book_id and character:
            profiles = self.db.get_character_psychology(book_id, character)
            if profiles:
                p = profiles[0]
                psych_profile = f"""
角色心理档案：
- 核心驱动力：{p['drives']}
- 深层恐惧：{p['fears']}
- 防御机制：{p['defense_mechanisms']}
- 潜台词风格：{p['subtext_style']}
- 核心矛盾：{p['core_contradiction']}"""

        messages = [
            {"role": "system", "content": f"""你是一位融合了弗洛伊德、荣格和阿德勒理论的文学心理分析师。
对角色的行为、对话和决策进行深层心理解读。
{psych_profile}

分析维度：
1. 意识/潜意识层面的行为动机
2. 防御机制的运用（否认、投射、合理化、升华等）
3. 阴影原型的显现
4. 依恋模式在关系中的表现
5. 未说出口的话（潜台词）

以JSON格式输出：
{{
  "character": "{character or '自动识别'}",
  "conscious_motivation": "意识层面的动机",
  "unconscious_motivation": "潜意识动机",
  "defense_mechanisms_used": ["使用的防御机制1", "防御机制2"],
  "shadow_manifestation": "阴影原型如何显现",
  "attachment_pattern": "依恋模式分析",
  "inner_conflict": "内心冲突描述",
  "growth_trajectory": "心理成长轨迹建议",
  "writing_advice": "基于心理分析的写作建议（如何让角色更有深度）"
}}"""},
            {"role": "user", "content": f"请对以下文本中{'角色'+character+'的' if character else '主要角色的'}行为进行深层心理分析：\n\n{text[:5000]}"}
        ]

        result = self._call_llm('validator', messages, stream=False)
        return {'psychology': result}

    # ============== 世界状态提取 (World State Extraction) ==============

    def run_world_state_extract(self, data):
        """从文本中自动提取时间/空间/物品/身体状态"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        messages = [
            {"role": "system", "content": """你是一位严谨的世界状态追踪引擎。
从文本中提取所有可追踪的状态变化，包括：

1. 时间(time)：具体时间点、时间跨度、时间顺序
2. 位置(location)：角色当前位置、场景转换
3. 物品(item)：物品归属变化、物品状态（损坏/丢失/获得等）
4. 身体状态(physical)：角色受伤/恢复/特殊状态
5. 关系变化(relation)：角色间关系的重大变化

以JSON格式输出：
{
  "states": [
    {
      "entity_name": "实体名(角色名/物品名)",
      "state_type": "location|time|item|physical|relation",
      "state_value": "新的状态值",
      "scene_context": "状态变化发生的场景描述(15字)"
    }
  ],
  "timeline_note": "本段文本的时间线标注",
  "spatial_map": "空间关系简述"
}"""},
            {"role": "user", "content": f"请提取以下文本中的世界状态：\n\n{text[:5000]}"}
        ]

        result = self._call_llm('validator', messages, stream=False)

        # 尝试自动保存提取的状态
        if book_id and result:
            try:
                import re as _re
                json_match = _re.search(r'\{[\s\S]*\}', result)
                if json_match:
                    parsed = json.loads(json_match.group())
                    for state in parsed.get('states', []):
                        self.db.upsert_world_state({
                            'book_id': book_id,
                            'entity_name': state.get('entity_name', ''),
                            'state_type': state.get('state_type', 'location'),
                            'state_value': state.get('state_value', ''),
                            'scene_context': state.get('scene_context', ''),
                            'last_updated_node': node_id or ''
                        })
            except Exception:
                pass

        return {'world_state': result}

    # ============== 世界状态验证 (World State Validation) ==============

    def run_world_state_validate(self, data):
        """验证草稿是否与已知世界状态一致"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')

        # 获取已知世界状态
        known_states = self.db.get_world_state(book_id) if book_id else []
        if not known_states:
            return {'validation': '暂无已知世界状态记录，无法进行一致性验证。请先提取世界状态。'}

        state_ctx = "\n".join([
            f"- {s['entity_name']}[{s['state_type']}]: {s['state_value']}（{s['scene_context']}）"
            for s in known_states[:30]
        ])

        messages = [
            {"role": "system", "content": f"""你是一位严谨的连续性校验引擎。
检查文本内容是否与已知的世界状态存在矛盾。

已知世界状态：
{state_ctx}

检查要点：
1. 角色位置是否合理（不能瞬移）
2. 时间线是否连续
3. 物品归属是否矛盾（已失去的物品不能使用）
4. 角色身体状态是否矛盾（重伤后不能剧烈运动）
5. 已死亡角色不能出现
6. 关系变化后行为是否匹配

以JSON格式输出：
{{
  "consistent": true/false,
  "conflicts": [
    {{
      "type": "location|time|item|physical|relation",
      "entity": "冲突实体",
      "known_state": "已知状态",
      "text_state": "文本中的状态",
      "description": "矛盾描述",
      "severity": "critical|warning|info",
      "fix_suggestion": "修复建议"
    }}
  ],
  "new_states": [
    {{
      "entity_name": "待更新实体",
      "state_type": "类型",
      "state_value": "新值"
    }}
  ],
  "summary": "一致性总结"
}}"""},
            {"role": "user", "content": f"请验证以下文本与世界状态的一致性：\n\n{text[:5000]}"}
        ]

        role = data.get('model_role', 'validator')
        result = self._call_llm(role, messages, stream=False)
        return {'validation': result}

    # ============== Module 11: Plan-and-Solve 深度生成 ==============

    def run_plan_and_solve(self, data):
        """三阶段深度生成管线：规划解析 → 分步求解 → 整合润色"""
        beat = data.get('beat', '')
        style = data.get('style', '自然流畅')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')
        previous_text = data.get('previous_text', '')

        # 构建上下文
        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        context_window = ""
        inject_ctx = ""
        if book_id and node_id:
            context_window = mem.build_context_window(book_id, node_id)
        if book_id and beat:
            injected = mem.dynamic_inject(book_id, beat)
            if injected:
                inject_ctx = "\n相关设定：\n" + "\n".join([
                    f"【{e['name']}】{e['content'][:200]}" for e in injected[:5]
                ])

        # ===== Phase 1: Plan（规划解析） =====
        yield "[PHASE:1]"

        plan_messages = [
            {"role": "system", "content": f"""你是一位资深小说架构师。将场景节拍拆解为结构化创作计划。
{inject_ctx}

请分析场景节拍，输出严格JSON：
{{
  "variables": {{
    "必要人物": ["角色1", "角色2"],
    "核心冲突": "一句话描述核心冲突",
    "必须道具": ["道具1"],
    "情感极性": "从X到Y的情感变化弧线",
    "场景基调": "氛围关键词"
  }},
  "steps": {{
    "A_motivation": "角色内在动机和心理状态的推理要求",
    "B_dialogue": "对话骨架的写作要求",
    "C_action_env": "动作与环境描写的写作要求"
  }},
  "constraints": ["不可违背的约束1", "约束2"],
  "target_word_count": 800
}}"""},
            {"role": "user", "content": f"场景节拍：{beat}\n\n前文摘要：{previous_text[-1000:] if previous_text else '（无前文）'}"}
        ]

        plan_result = self._call_llm('plan_and_solve', plan_messages, stream=False)

        # 尝试解析JSON
        plan_data = {}
        try:
            json_match = re.search(r'\{[\s\S]*\}', plan_result)
            if json_match:
                plan_data = json.loads(json_match.group())
        except Exception:
            pass

        variables = plan_data.get('variables', {})
        steps = plan_data.get('steps', {})
        constraints = plan_data.get('constraints', [])

        # ===== Phase 2A: Solve - 角色动机推理（内部链式思考） =====
        yield "[PHASE:2A]"

        step_a_messages = [
            {"role": "system", "content": f"""你是一位角色心理分析师。进行角色内在动机的深度推理。
这是内部思考阶段，请详细推理每个角色的：
1. 当前心理状态和情绪
2. 这个场景中的核心诉求
3. 潜在的冲突点和让步底线
4. 可能的行为倾向
{inject_ctx}
{('创作约束：' + '；'.join(constraints)) if constraints else ''}
以内部分析笔记的形式输出，不需要文学性。"""},
            {"role": "user", "content": f"""场景节拍：{beat}
变量提取：{json.dumps(variables, ensure_ascii=False)}
Step A 要求：{steps.get('A_motivation', '分析角色动机')}
前文（最后500字）：{previous_text[-500:] if previous_text else '无'}"""}
        ]

        motivation_analysis = self._call_llm('plan_and_solve', step_a_messages, stream=False)

        # ===== Phase 2B: Solve - 对话骨架 =====
        yield "[PHASE:2B]"

        step_b_messages = [
            {"role": "system", "content": f"""你是一位对话大师。基于角色动机分析，构建对话骨架。
要求：
- 每句对话都有潜台词层
- 对话推动冲突发展
- 符合角色性格和当前心理状态
- 文风：{style}
{inject_ctx}
只输出对话及简要动作提示，不写大段叙述。"""},
            {"role": "user", "content": f"""场景节拍：{beat}
角色动机分析：{motivation_analysis[:2000]}
Step B 要求：{steps.get('B_dialogue', '编写对话骨架')}"""}
        ]

        dialogue_skeleton = self._call_llm('plan_and_solve', step_b_messages, stream=False)

        # ===== Phase 2C: Solve - 动作与环境 =====
        yield "[PHASE:2C]"

        step_c_messages = [
            {"role": "system", "content": f"""你是一位场景描写专家。为对话骨架补充动作描写和环境细节。
要求：
- 感官细节丰富（视觉/听觉/嗅觉/触觉）
- 动作反映角色心理状态
- 环境渲染与情感极性呼应
- 文风：{style}
{inject_ctx}
输出包含动作和环境描写的叙述片段，稍后将与对话整合。"""},
            {"role": "user", "content": f"""场景节拍：{beat}
情感极性：{variables.get('情感极性', '未指定')}
场景基调：{variables.get('场景基调', '未指定')}
角色动机分析摘要：{motivation_analysis[:800]}
Step C 要求：{steps.get('C_action_env', '编写动作与环境描写')}
前文最后300字：{previous_text[-300:] if previous_text else '无'}"""}
        ]

        action_env = self._call_llm('plan_and_solve', step_c_messages, stream=False)

        # ===== Phase 3: Integration（整合润色） =====
        yield "[PHASE:3]"

        integrate_messages = [
            {"role": "system", "content": f"""你是一位顶级文学编辑。将对话骨架和动作环境描写整合成流畅的小说段落。
整合要求：
1. 对话与叙述自然穿插，避免对话块和叙述块分离
2. 保持文学性，文风：{style}
3. 确保情感弧线完整：{variables.get('情感极性', '')}
4. 直接输出最终文本，无标记无注释
{('5. 约束：' + '；'.join(constraints)) if constraints else ''}
前文衔接：确保与前文最后几句话自然过渡。"""},
            {"role": "user", "content": f"""请将以下素材整合为最终段落：

【对话骨架】
{dialogue_skeleton[:3000]}

【动作与环境】
{action_env[:3000]}

【前文最后200字】
{previous_text[-200:] if previous_text else '（开篇）'}

请直接输出整合后的文学段落："""}
        ]

        for chunk in self._call_llm('plan_and_solve', integrate_messages, stream=True):
            yield chunk

    # ============== Module 12: 幻觉检测与自动纠正 ==============

    def run_hallucination_detect(self, data):
        """多维幻觉检测：NLI蕴含验证 + 世界状态一致性"""
        text = data.get('text', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        if not text.strip():
            return {'has_contradiction': False, 'conflicts': [], 'overall_verdict': '无文本可检测'}

        # ===== Dimension 1: NLI-based Verification =====
        premise_parts = []

        # Lorebook entries
        if book_id:
            lorebook = self.db.get_lorebook_entries(book_id)
            if lorebook:
                for entry in lorebook[:15]:
                    if entry.get('enabled', 1):
                        premise_parts.append(f"[{entry['category']}:{entry['name']}] {entry['content'][:200]}")

        # World state
        if book_id:
            world_states = self.db.get_world_state(book_id)
            if world_states:
                for ws in world_states[:20]:
                    premise_parts.append(f"[世界状态:{ws['entity_name']}({ws['state_type']})] {ws['state_value']}")

        # Character psychology
        if book_id:
            psych = self.db.get_character_psychology(book_id)
            if psych:
                for p in psych[:10]:
                    premise_parts.append(f"[角色心理:{p['character_name']}] 驱动力:{p['drives'][:80]}; 恐惧:{p['fears'][:80]}")

        # Context window
        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        if book_id and node_id:
            ctx = mem.build_context_window(book_id, node_id, max_chars=3000)
            if ctx:
                premise_parts.append(f"[前文上下文] {ctx[:2000]}")
        if book_id:
            character_ctx = mem.build_character_reminder_context(book_id, text=text[:3000], node_id=node_id, max_characters=6)
            if character_ctx:
                premise_parts.append(f"[人物提醒] {character_ctx[:1800]}")

        premise = "\n".join(premise_parts)

        nli_messages = [
            {"role": "system", "content": """你是一个严格的自然语言推理(NLI)验证引擎。
给定前提(Premise)和假说(Hypothesis)，判断它们之间的关系。

判断标准：
- Entailment（蕴含）：假说内容与前提一致，没有矛盾
- Neutral（中性）：假说涉及前提未提及的新内容，但不矛盾
- Contradiction（矛盾）：假说与前提存在事实性冲突

特别关注：
1. 角色设定冲突（性格/能力/背景与设定不符）
2. 地理位置跳跃（不合理的瞬移）
3. 时间线混乱（白天/夜晚矛盾，事件顺序错误）
4. 物品状态矛盾（已损毁的物品再次出现）
5. 角色存活状态（已死亡角色出现）
6. 能力边界违反（超出设定能力范围）

以JSON格式输出：
{
  "verdict": "Entailment|Neutral|Contradiction",
  "confidence": 0.85,
  "conflicts": [
    {
      "type": "character|location|time|item|ability|logic",
      "hypothesis_claim": "假说中的描述",
      "premise_fact": "前提中的事实",
      "description": "矛盾描述",
      "severity": "critical|warning|info"
    }
  ],
  "reasoning": "推理过程说明"
}"""},
            {"role": "user", "content": f"""Premise（前提/已知事实）：
{premise[:5000]}

Hypothesis（假说/待验证文本）：
{text[:4000]}

请进行NLI验证："""}
        ]

        nli_result_raw = self._call_llm('hallucination', nli_messages, stream=False)

        # Parse NLI result
        nli_data = {}
        try:
            json_match = re.search(r'\{[\s\S]*\}', nli_result_raw)
            if json_match:
                nli_data = json.loads(json_match.group())
        except Exception:
            nli_data = {'verdict': 'Neutral', 'confidence': 0.5, 'conflicts': [], 'reasoning': nli_result_raw}

        # ===== Dimension 2: World State Consistency =====
        ws_result = {}
        if book_id:
            ws_raw = self.run_world_state_validate({'text': text, 'book_id': book_id, 'model_role': 'hallucination'})
            ws_text = ws_raw.get('validation', '')
            try:
                ws_match = re.search(r'\{[\s\S]*\}', ws_text)
                if ws_match:
                    ws_result = json.loads(ws_match.group())
            except Exception:
                ws_result = {'consistent': True, 'conflicts': []}

        # Merge results
        all_conflicts = list(nli_data.get('conflicts', []))
        ws_conflicts = ws_result.get('conflicts', [])
        for wc in ws_conflicts:
            all_conflicts.append({
                'type': wc.get('type', 'world_state'),
                'hypothesis_claim': wc.get('text_state', ''),
                'premise_fact': wc.get('known_state', ''),
                'description': wc.get('description', ''),
                'severity': wc.get('severity', 'warning')
            })

        has_contradiction = (
            nli_data.get('verdict') == 'Contradiction' or
            ws_result.get('consistent') == False or
            any(c.get('severity') == 'critical' for c in all_conflicts)
        )

        return {
            'has_contradiction': has_contradiction,
            'nli_verdict': nli_data.get('verdict', 'Neutral'),
            'nli_confidence': nli_data.get('confidence', 0.5),
            'nli_reasoning': nli_data.get('reasoning', ''),
            'world_state_consistent': ws_result.get('consistent', True),
            'conflicts': all_conflicts,
            'overall_verdict': '发现矛盾，需要修正' if has_contradiction else '未发现明显矛盾',
            'fix_suggestions': [c.get('description', '') for c in all_conflicts if c.get('severity') == 'critical']
        }

    def run_draft_with_hallucination_guard(self, data):
        """带幻觉防护的创作管线：生成 → 检测 → 重试（最多3次）"""
        max_retries = data.get('max_retries', 3)
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        for attempt in range(max_retries + 1):
            # Generate draft
            if attempt == 0:
                yield "[GUARD:generating]"
            else:
                yield f"[GUARD:retry:{attempt}]"

            # Collect full draft
            draft_text = ""
            for chunk in self.run_drafter_stream(data):
                draft_text += chunk

            # Run hallucination detection
            yield "[GUARD:checking]"
            detect_result = self.run_hallucination_detect({
                'text': draft_text,
                'book_id': book_id,
                'node_id': node_id
            })

            if not detect_result.get('has_contradiction', False):
                yield "[GUARD:passed]"
                yield draft_text
                return

            # Has contradiction - prepare for retry
            if attempt < max_retries:
                conflict_desc = "; ".join([
                    c.get('description', '') for c in detect_result.get('conflicts', [])[:3]
                ])
                additional_constraint = f"\n\n【幻觉防护约束 - 第{attempt+1}次检测发现以下矛盾，请避免】：{conflict_desc}"
                data = dict(data)
                existing_beat = data.get('beat', '')
                data['beat'] = existing_beat + additional_constraint

        # All retries exhausted
        yield "[GUARD:failed]"
        conflict_info = json.dumps(detect_result.get('conflicts', [])[:5], ensure_ascii=False)
        yield f"[HALLUCINATION_ALERT]{conflict_info}"
        yield "\n\n---\n⚠️ 以下文本存在矛盾，请人工审查：\n---\n\n"
        yield draft_text

    # ============== 行内指令 ==============

    def run_inline_command(self, data):
        command = data.get('command', '')
        text = data.get('text', '')
        context = data.get('context', '')
        book_id = data.get('book_id', '')
        node_id = data.get('node_id', '')

        cmd_prompts = {
            'continue': f"请续写以下文本，保持风格和情节连贯：\n\n{text[-2000:]}\n\n请直接续写：",
            'rewrite': f"请改写以下段落，保持核心内容但提升表现力：\n\n{text}\n\n改写后：",
            'expand_env': f"请扩写以下文本中的环境描写，增加感官细节（视觉、听觉、嗅觉、触觉）：\n\n{text}\n\n扩写后：",
            'simplify_dialogue': f"请精简以下文本中的对话，使其更加干练自然：\n\n{text}\n\n精简后：",
            'add_tension': f"请为以下段落增加紧张感和冲突：\n\n{text}\n\n修改后：",
            'inner_monologue': f"请为以下段落中的主要角色增加内心独白：\n\n{text}\n\n修改后：",
        }

        if command in cmd_prompts:
            prompt_content = cmd_prompts[command]
        else:
            prompt_content = f"执行以下指令：{command}\n\n原文：\n{text}\n\n{context}"

        # 获取设定上下文
        from memory_engine import MemoryEngine
        mem = MemoryEngine(self.db)
        inject_ctx = ""
        character_ctx = ""
        if book_id and text:
            injected = mem.dynamic_inject(book_id, text[:500])
            character_ctx = mem.build_character_reminder_context(book_id, text=text[:2000], node_id=node_id, max_characters=4)
            if injected:
                inject_ctx = "\n相关设定：\n" + "\n".join([
                    f"【{e['name']}】{e['content'][:150]}" for e in injected[:3]
                ])

        messages = [
            {"role": "system", "content": f"""你是一位专业的小说编辑助手。执行用户的编辑指令，直接输出修改后的文本。
不要添加任何标记、标题或解释。
{character_ctx}
{inject_ctx}"""},
            {"role": "user", "content": prompt_content}
        ]

        for chunk in self._call_llm('drafter', messages, stream=True):
            yield chunk
