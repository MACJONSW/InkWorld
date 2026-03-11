# 墨境二次开发指南

本文档面向准备二次开发墨境的开发者，重点说明两类扩展：

1. 如何新增一个智能体。
2. 如何扩展一个新的记忆模块。

如果你想先了解系统整体实现，请先看 [TECHNICAL.md](TECHNICAL.md)。本文档不重复讲系统原理，而是聚焦“要改哪里、按什么顺序改、哪些坑要避开”。

## 1. 先理解当前扩展点

当前系统的扩展边界很稳定，基本都落在下面几个文件：

| 文件 | 扩展职责 |
| --- | --- |
| `agents.py` | 新增 Agent 的核心逻辑、提示词、上下文拼装、流式/非流式调用 |
| `app.py` | 新增 API 路由、权限校验、SSE 封装 |
| `static/js/app.js` | 新增前端调用方法、状态切换、输出展示、设置页路由配置 |
| `templates/index.html` | 新增 Agent 面板、按钮、表单控件 |
| `memory_engine.py` | 新增上下文构建、检索、提醒、记忆聚合逻辑 |
| `database.py` | 新增持久化表、CRUD、书籍/节点级作用域约束 |
| `export_engine.py` | 新增 JSON 工作区导入导出字段 |

一个功能如果只涉及推理和展示，通常只要动 `agents.py + app.py + app.js + index.html`。

一个功能如果还需要长期保存状态，通常还要动 `database.py + export_engine.py + memory_engine.py`。

## 2. 当前系统里已经有哪些 Plan 能力

当前版本并不是完全没有 plan 能力，而是有两条不同的 plan 管线：

1. `Planner`：整书/整卷级规划，对应 `run_planner()` 和 `/api/agent/plan`。
2. `Plan-and-Solve`：场景级先规划再生成，对应 `run_plan_and_solve()` 和 `/api/agent/plan-and-solve`。

本次改动后，右侧“智能续写”面板里的模式选择新增了 `Plan 模式`。这个模式不会重新实现一套新 Agent，而是直接复用 `Plan-and-Solve` 管线：

- 用户在续写面板选择 `Plan 模式`
- 前端把“期望方向”转成 `beat`
- 自动切换到 Plan 面板
- 调用现有的 `runPlanAndSolve()`

这样做的好处是：

- 用户入口更统一
- 后端不需要维护两套相似的深度生成逻辑
- Plan 模式仍然保留原有的阶段进度展示

## 3. 新增一个智能体的标准步骤

建议按下面顺序做，不要跳步骤。

## 3.1 定义这个 Agent 的边界

先回答四个问题：

1. 它是生成型还是分析型？
2. 它是流式还是非流式？
3. 它是否需要三层记忆、Lorebook 动态注入、人物提醒？
4. 它是否会写数据库？

如果这四个问题没有先明确，后面很容易把 Agent 写成“提示词能跑，但无法维护”的状态。

## 3.2 在 `agents.py` 中实现 `run_xxx()`

所有 Agent 都建议遵循同一个骨架：

```python
def run_my_agent(self, data):
    book_id = data.get('book_id', '')
    node_id = data.get('node_id', '')
    text = data.get('text', '')

    mem = MemoryEngine(self.db)
    context_window = ''
    if book_id and node_id:
        context_window = mem.build_context_window(book_id, node_id)

    messages = [
        {"role": "system", "content": "你的系统提示词"},
        {"role": "user", "content": text},
    ]

    return self._call_llm('my_agent_role', messages, stream=False)
```

实现时注意：

1. 统一从 `data` 取输入，不要直接依赖 Flask request 对象。
2. 需要三层记忆时，优先复用 `MemoryEngine.build_context_window()`。
3. 需要设定补丁时，优先复用 `dynamic_inject()`。
4. 需要角色连续性时，优先复用人物提醒上下文，而不是自己手拼一份角色历史。
5. 如果是流式 Agent，就 `yield` chunk；如果不是流式，就直接返回 dict 或字符串。

## 3.3 选择正确的角色名

`_call_llm(role, ...)` 里的 `role` 不只是字符串，它会影响：

- 当前用户的模型路由选择
- token 统计
- 设置页中的任务路由配置

如果你新增了一个角色，例如 `scene_rewriter`，要同步考虑三件事：

1. `agents.py` 里调用 `_call_llm('scene_rewriter', ...)`
2. 前端设置页 `loadRoutingGrid()` 里把它加到角色列表中
3. 让用户在“任务路由”页可以把这个角色绑到具体模型

注意：后端 `/api/routing` 和 `database.py::set_routing()` 当前没有角色白名单校验，所以新增角色不需要额外改后端白名单；但如果你不更新前端设置页，这个角色虽然能工作，却无法在 UI 中单独配置模型。

## 3.4 在 `app.py` 中新增 API 路由

非流式 Agent 的写法：

```python
@app.route('/api/agent/my-agent', methods=['POST'])
def agent_my_agent():
    data = _prepare_agent_data()
    result = agent_orchestrator.run_my_agent(data)
    return jsonify(result)
```

流式 Agent 的写法：

```python
@app.route('/api/agent/my-agent-stream', methods=['POST'])
def agent_my_agent_stream():
    data = _prepare_agent_data()

    def generate():
        for chunk in agent_orchestrator.run_my_agent(data):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

这里必须优先走 `_prepare_agent_data()`，不要自己在路由里重写权限逻辑。这样可以自动获得：

- JWT 用户上下文
- `book_id` / `node_id` 访问校验
- `node_id` 与 `book_id` 的一致性校验
- Agent 层的 `set_request_user()`

## 3.5 在前端面板接入

前端接入通常有三处：

1. 在 `templates/index.html` 里加按钮。
2. 在 `templates/index.html` 里加对应表单面板。
3. 在 `static/js/app.js` 里加 `runMyAgent()`。

最小接入模板：

```javascript
async runMyAgent() {
    const output = document.getElementById('agentOutput');
    output.innerHTML = '<div class="loading-spinner"></div> Agent 处理中...';

    const res = await this.api('/api/agent/my-agent', 'POST', {
        book_id: this.currentBookId,
        node_id: this.currentNodeId,
        text: document.getElementById('editorArea').innerText
    });

    this.agentOutputText = res.result;
    output.textContent = res.result;
}
```

如果是流式 Agent，参考已有的：

- `runDrafter()`
- `runContinuation()`
- `runPlanAndSolve()`
- `runInlineCommand()`

## 3.6 如果 Agent 会写数据库

例如摘要、伏笔、世界状态这类 Agent，不只是“返回一段文本”，还会改变系统状态。这类 Agent 需要额外确认：

1. 数据写入是发生在 `agents.py` 里，还是在 `app.py` 路由层做后处理。
2. 是否需要导出到 JSON workspace。
3. 是否需要在前端补对应展示区域。

当前代码里一个比较清晰的参考是摘要器：

- `run_summarizer()` 负责生成结构化摘要
- `/api/agent/summarize` 在拿到结果后继续刷新人物历史

这种“生成 + 路由后处理”的分层是推荐做法，因为副作用逻辑更容易收口。

## 3.7 建议的自测清单

至少走完下面几项：

1. 空书、空章节、无选中节点时是否安全失败。
2. 同一个用户切换不同书时是否严格隔离。
3. 流式接口遇到报错时前端是否能正确结束 loading。
4. 设置页能否为新角色单独绑定模型。
5. 如果 Agent 写库，导出再导入后数据是否保留。

## 4. 扩展新的记忆模块

记忆模块比新增 Agent 更敏感，因为它会直接影响多个提示词的 token 开销和一致性表现。

## 4.1 先决定这是快路径还是慢路径

新增记忆前，先判断它属于哪类：

1. **快路径记忆**：会在高频交互中调用，例如自动补全、续写、编辑器输入后的提醒刷新。
2. **慢路径记忆**：只在章节摘要、手动刷新、批处理回填时更新。

人物历史就是典型的慢路径更新 + 快路径消费：

- 慢路径：摘要后沉淀 `character_history`
- 快路径：提到人物时聚合提醒并注入提示词

如果你把重型抽取逻辑塞到快路径里，编辑器响应速度会明显下降。

## 4.2 决定它是否持久化

如果新的记忆模块需要跨章节、跨会话复用，通常应该落库。

常见步骤：

1. 在 `database.py` 的 `init_db()` 中新增表。
2. 新增 CRUD 方法。
3. 如果字段包含长文本或敏感文本，加入加解密字段名单。
4. 在 `export_engine.py` 的导出和导入里补上对应数据。

如果只是一次性临时上下文，可以只在 `MemoryEngine` 内构建，不一定需要表。

## 4.3 在 `memory_engine.py` 中新增能力

推荐的分层方式是：

1. `extract_xxx(...)`：识别当前文本中需要关注的对象。
2. `build_xxx(...)`：聚合数据库和当前文本，形成结构化结果。
3. `build_xxx_context(...)`：把结构化结果转成可注入 prompt 的文本。
4. `refresh_xxx_for_node(...)` / `refresh_xxx_for_book(...)`：负责慢路径沉淀和批量回填。

人物提醒系统就是这个模板：

- `extract_character_mentions()`
- `build_character_reminders()`
- `build_character_reminder_context()`
- `refresh_character_history_for_node()`
- `refresh_character_history_for_book()`

## 4.4 决定它注入到哪里

记忆模块有三种常见接法：

1. 注入 `build_context_window()`，成为通用上下文的一部分。
2. 只给特定 Agent 单独注入。
3. 只用于前端展示，不进 prompt。

怎么选：

- 需要被多个创作型 Agent 复用，就接进 `build_context_window()`。
- 只服务某个单独任务，就在对应 Agent 内局部拼接。
- 只是为了可视化，不一定要进 prompt，避免浪费 token。

## 4.5 一定要做去重

这是扩展记忆模块最容易踩的坑。

当前仓库已经踩过一次：`build_context_window()` 里有一份人物提醒，而续写/执笔流程又额外追加了一份，导致 token 浪费。后来通过 `_maybe_build_character_ctx()` 才修掉这个问题。

所以你新增任何新记忆模块时，都要先确认：

1. 它是否已经在综合上下文里存在。
2. 它是否又被某个 Agent 手工追加了一次。
3. 它是否会在不同调用链上被重复展开。

## 4.6 一定要守住书级作用域

所有长期记忆都必须至少绑定 `book_id`。如果它还和章节强相关，就再挂 `node_id` 或 `source_node_id`。

不要只做“用户拥有这本书”和“用户拥有这个节点”两次独立校验，而忽略“这个节点是否属于这本书”。人物历史功能已经证明，跨书串数据是非常真实的风险。

推荐做法：

- API 层统一走 `_require_node_in_book(node_id, book_id)`
- MemoryEngine 的批处理和刷新逻辑里也做一次保护

## 4.7 前端接入新的记忆模块

如果新的记忆模块需要可视化，一般要补四样东西：

1. 右侧记忆面板中的展示容器。
2. `loadXxx()` 前端请求函数。
3. `renderXxx()` 渲染函数。
4. 合适的刷新时机。

刷新时机通常包括：

- 切书时
- 切章节时
- 编辑器输入防抖后
- 摘要或分析动作完成后
- 右栏切回记忆面板时

## 5. 推荐的开发顺序

无论是新增 Agent 还是记忆模块，都建议按这个顺序：

1. 先定义输入输出契约。
2. 再实现后端核心逻辑。
3. 然后补 API。
4. 再接前端入口。
5. 最后补导入导出和文档。

不要先堆 UI，再临时拼后端。这个项目的复杂度主要不在界面，而在上下文、作用域和副作用管理。

## 6. 这个仓库里最值得复用的现成模式

如果你要做功能扩展，优先参考下面这些现成模式：

1. **标准流式 Agent**：参考 `run_drafter_stream()` + `/api/agent/draft` + `runDrafter()`。
2. **标准分析型 Agent**：参考 `run_validator()` + `/api/agent/validate` + `runValidator()`。
3. **生成后做副作用处理**：参考摘要器和人物历史刷新。
4. **慢路径沉淀 + 快路径消费**：参考人物历史/人物提醒。
5. **多阶段生成**：参考 `run_plan_and_solve()`。
6. **质量护栏闭环**：参考 `run_draft_with_hallucination_guard()`。

## 7. 最后建议

这个项目最怕的不是“功能少”，而是“同一份叙事状态被多处各自维护”。

所以二次开发时尽量遵循三条原则：

1. 已有上下文能力优先复用，不要复制一套相似逻辑。
2. 长期状态优先落到结构化表里，不要把它藏在 prompt 文本中。
3. 高频链路尽量轻，重型抽取和回填尽量走慢路径。

按这三个原则扩展，系统会稳定很多。