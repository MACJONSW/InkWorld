# 墨境技术文档

本文档面向开发者和维护者，解释墨境这一 AI 长篇小说写作平台的技术实现细节。重点包括：运行时架构、数据模型、三层记忆、人物提醒、前端交互、导入导出，以及所有智能体的底层运行逻辑与执行流程。

本文档默认你已经看过 [README.md](README.md)。README 负责说明“这个项目能做什么、如何启动”，而本文档回答“这个项目内部是怎么工作的”。

## 1. 系统总览

系统本质上是一个由 Flask 驱动的多层写作平台：

1. 浏览器前端提供三栏写作工作台，负责状态管理、流式展示和用户交互。
2. Flask API 层负责认证、权限校验、书籍/章节管理、导入导出和智能体调用。
3. `AgentOrchestrator` 负责把用户输入、记忆上下文和系统约束组装为提示词，并调用外部大模型。
4. `MemoryEngine` 负责构建三层记忆窗口、动态注入 Lorebook、向量检索和人物提醒上下文。
5. `Database` 负责 SQLite 持久化、字段加密、树结构、摘要、伏笔、心理档案、世界状态和人物历史。

从模块边界上看，可以理解为：

```text
Browser UI
  -> app.py (Flask routes + auth + API)
    -> agents.py (LLM orchestration)
    -> memory_engine.py (context assembly)
    -> database.py (SQLite persistence)
    -> export_engine.py (workspace import/export)
```

## 2. 目录与职责

### 2.1 核心文件

| 文件 | 主要职责 |
| --- | --- |
| `app.py` | Flask 应用入口、认证、权限校验、REST API、SSE 流式响应、启动服务 |
| `agents.py` | `AgentOrchestrator`，统一管理模型路由、参数、流式和非流式调用、所有智能体实现 |
| `memory_engine.py` | 三层记忆、向量检索、Lorebook 动态注入、人物提醒聚合、人物历史自动沉淀 |
| `database.py` | SQLite schema、CRUD、加密/解密、导入导出底层数据访问 |
| `export_engine.py` | Markdown/TXT/EPUB/JSON 工作区导入导出 |
| `templates/index.html` | 三栏主工作台结构 |
| `static/js/app.js` | 前端状态机、API 调用、编辑器逻辑、流式消费、人物提醒刷新 |
| `static/css/style.css` | 工作台布局、记忆面板、人物提醒卡、智能体区域样式 |

### 2.2 运行时角色划分

- `app.py` 是请求入口。
- `AgentOrchestrator` 是 LLM 调度核心。
- `MemoryEngine` 是提示词上下文构建器。
- `Database` 是唯一的持久化边界。
- `ExportEngine` 负责把内部数据结构投影成外部文件格式。

## 3. 运行时入口与请求流

## 3.1 启动方式

项目启动入口在 `app.py` 的 `if __name__ == '__main__':` 代码块。服务通过 `socketio.run()` 启动，而不是 `flask run` 或 `app.run()`。

```python
if __name__ == '__main__':
    db.init_db()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
```

虽然当前版本里 SocketIO 主要承担运行时能力预留，核心流式生成仍然走的是 Flask 的 `text/event-stream` 响应。

## 3.2 请求进入 Flask 之后发生什么

每个 `/api/*` 请求会先经过 `@app.before_request` 的 `authenticate_api()`：

1. 跳过非 `/api` 请求和 `OPTIONS` 预检。
2. 放行白名单接口，例如注册和登录。
3. 从 `Authorization: Bearer <token>` 中解析 JWT。
4. 验证签名与过期时间。
5. 查询用户是否真实存在。
6. 把 `g.user_id` 和 `g.user_email` 写入 Flask request context。

这一步的结果是：后续任意数据库或智能体调用都可以通过 `g.user_id` 建立用户边界。

## 3.3 资源级访问控制

系统并不是只凭 JWT 判定权限，还会做资源级访问控制：

- `_require_book_access(book_id)`：确认这本书属于当前用户。
- `_require_node_access(node_id)`：确认这个节点属于当前用户的某本书。
- `_require_node_in_book(node_id, book_id)`：确认该节点确实属于当前 `book_id`。

最后这一条很重要，它防止了“拿 A 书的 `book_id` 去读取/刷新 B 书的章节节点”的跨书污染问题。人物提醒和人物历史刷新相关接口都强制经过这个校验。

## 3.4 Agent 请求的标准预处理

大多数智能体接口都会先调用 `_prepare_agent_data()`：

1. 读取 JSON body。
2. 抽出 `book_id` 和 `node_id`。
3. 做书级和节点级权限校验。
4. 如果只有 `node_id` 没有 `book_id`，就通过数据库反查节点所属书籍。
5. 再做一次 `node_id` 与 `book_id` 的一致性校验。
6. 把 `g.user_id` 写入 payload。
7. 调用 `agent_orchestrator.set_request_user(g.user_id)`，让智能体层知道当前请求属于哪个用户。

这一步建立了后续模型路由、参数选择、token 统计和停止生成的用户隔离基础。

## 3.5 流式响应机制

正文生成、续写、行内命令等写作型能力使用的是 SSE：

```python
def generate():
    for chunk in agent_orchestrator.run_drafter_stream(data):
        yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

这里的关键点：

- `stream_with_context()` 防止 Flask 在生成器执行期间丢失 request context。
- 每个 chunk 都被包装成 `data: {...}\n\n`。
- 结束时发送 `data: [DONE]`，前端据此结束流式消费。
- 真正的流式颗粒度由底层大模型 SDK 的 streaming 返回决定。

## 4. 数据层与持久化设计

## 4.1 SQLite 连接策略

项目没有使用 ORM，所有读写都直接走 `sqlite3`。`Database._conn()` 每次创建新连接，并统一设置：

- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- `row_factory = sqlite3.Row`

这样做的意义：

- WAL 模式提升读写并发体验。
- 外键约束避免孤儿数据。
- `sqlite3.Row` 允许上层直接 `dict(row)`，减少手工映射代码。

虽然文件里保留了 SQLAlchemy engine 的可选初始化逻辑，但当前业务路径实际依赖的是原生 `sqlite3` 连接。

## 4.2 本地加密策略

平台对部分字段使用 `cryptography.Fernet` 对称加密。

### 4.2.1 密钥文件

- 首次运行时自动生成 `.encryption_key`
- 之后所有加解密都使用同一个本地密钥

如果这个文件丢失，数据库中的加密文本将无法被正确解密，因此它是数据可恢复性的关键资产。

### 4.2.2 加密字段类型

被加密的并不只是 API Key，还包括大量业务文本：

- 模型配置中的 `api_key`
- 书籍描述、作者、题材等元数据
- Lorebook 描述、关键词、正文内容
- 章节摘要、关键事件
- 伏笔描述、世界状态文本
- 角色心理档案
- 人物历史摘要和细节

系统通过 `_decrypt_fields()` 在查询后按字段名单批量解密，对上层代码透明。

## 4.3 核心表设计

### 4.3.1 用户与模型配置

- `users`
  - 登录身份
  - 存储邮箱和密码哈希
- `models`
  - 每个用户可配置多个模型
  - 关键字段：`provider`、`base_url`、`api_key_enc`、`model_id`、`max_context`
- `user_routing`
  - 把角色名映射到具体模型
  - 例如 `drafter -> 某个模型配置`
- `user_generation_params`
  - 当前用户的生成参数覆盖
  - 包括 `temperature`、`top_p`、`max_tokens` 等
- `token_stats`
  - 记录每次调用的 token 消耗

### 4.3.2 书籍与文档树

- `books`
  - 一本书的顶层实体
  - `user_id` 建立用户边界
- `nodes`
  - 树节点，支持 `volume/chapter/scene`
  - `parent_id` 建立层级结构
- `node_contents`
  - 当前主线正文内容
  - 每个节点只有一份“当前内容”
- `versions`
  - 版本/分支系统
  - 允许同一节点保存多个分支内容

这里的关键设计是：

- `node_contents` 表示“当前激活版本”。
- `versions` 表示“历史或分支版本”。
- 激活某个分支时，会把对应内容复制回 `node_contents`。

### 4.3.3 设定、摘要和叙事状态

- `lorebook`
  - 角色、地点、物品、派系、法则等设定条目
  - `keywords` 同时承担动态注入和人物识别触发器
- `entity_graph`
  - 实体关系图谱
  - 保存 `source_entity -> target_entity` 的关系及值
- `chapter_summaries`
  - 章节级长期记忆
  - 是 Tier 2 滚动摘要的重要数据源
- `foreshadowing`
  - 伏笔池
  - 支持 `unresolved/resolved` 状态
- `world_state`
  - 动态世界状态
  - 可记录位置、物品状态、关系状态、身体状态等
- `character_psychology`
  - 角色心理档案
  - 驱动力、恐惧、防御机制、潜台词风格、核心矛盾
- `character_history`
  - 人物历史档案
  - 当前新增功能的核心持久化表

## 4.4 人物历史表的设计意义

`character_history` 的关键字段包括：

- `book_id`：保证人物历史按书隔离
- `character_name`：人物主键视角
- `entry_type`：`event/note/personality/foreshadow` 等类型
- `summary`：简要记录
- `details`：详细上下文
- `source_node_id`：来源章节
- `chapter_title`：来源章节标题
- `source_excerpt`：原文片段
- `foreshadow_refs`：相关伏笔
- `is_manual`：手工/自动生成区分

这张表的设计决定了系统并不是临时在内存里算人物提醒，而是把可复用的人物叙事记忆独立沉淀成可编辑、可导出、可回填的数据层。

## 5. 前端工作台实现

## 5.1 三栏布局

前端主界面是一个三栏工作台：

- 左栏：目录树、Lorebook、实体图谱
- 中栏：编辑器、版本栏、Slash 菜单、Ghost Text、流式状态
- 右栏：智能体工作台、记忆面板

这种布局的工程意义是：

- 文档结构与正文编辑分离
- AI 工具与写作上下文同屏可见
- 记忆系统可以作为右栏的常驻辅助层，不打断主编辑区

## 5.2 前端状态管理方式

前端没有使用 React/Vue，而是一个全局 `App` 对象维护状态和行为。常见状态包括：

- `currentBookId`
- `currentNodeId`
- `currentAgent`
- `isStreaming`
- `characterReminderTimer`

这种实现方式的优点是轻量、直接，代价是状态约束主要依赖开发者自己维护约定。

## 5.3 编辑器能力

中间编辑器是 `contenteditable` 容器，而不是 `textarea` 或富文本编辑器框架。它提供：

- 正文输入
- 自动保存
- Slash 指令触发
- Ghost Text 自动补全
- SSE 流式写入
- 版本比较与切换

## 5.4 人物提醒在前端如何刷新

前端通过 `loadCharacterReminders()` 调用 `/api/character-reminders`。这个方法会把：

- `currentBookId`
- `currentNodeId`
- 当前编辑器文本

一起发给后端，后端返回：

- `characters`：结构化提醒卡片数据
- `context`：可直接注入提示词的人物提醒文本

前端刷新触发点包括：

- `switchBook()`
- `selectNode()`
- 编辑器输入防抖
- 生成摘要后
- 自动补全前后
- 右栏切到“记忆”标签时

这里的重要约束是：前端始终围绕 `currentBookId` 调接口，因此提醒天然以“当前书”为边界。

## 5.5 流式输出在前端的处理

对于 SSE 类接口，前端逐步消费 `data:` 消息，并把 `text` 内容增量写入输出区域或编辑器。与此同时，`isStreaming` 被置为 `true`，这会影响：

- 自动补全是否触发
- 人物提醒是否继续高频刷新
- 停止按钮是否显示

## 6. 导入导出实现

## 6.1 内容导出

`ExportEngine` 支持导出为：

- Markdown
- TXT
- EPUB
- HTML fallback
- JSON workspace

实现思路是统一先调用 `_get_ordered_contents(book_id)`：

1. 从文档树读取节点层级。
2. 递归 flatten。
3. 按深度和顺序拼接内容。

Markdown/TXT/EPUB 的差异只在于“如何投影同一份树结构数据”。

## 6.2 工作区导出

`to_json_workspace()` 调用 `db.export_all_book_data()`，把整本书的内部状态完整打包成 JSON。这个导出不只是正文，还包括：

- 书籍元数据
- 节点树
- 正文内容
- 版本分支
- Lorebook
- 关系图谱
- 摘要
- 大纲
- 伏笔池
- 世界状态
- 心理档案
- 人物历史

因此 JSON workspace 是“完整工程状态快照”，而不是阅读成品格式。

## 6.3 工作区导入

`import_json_workspace()` 的关键技术点：

1. 先创建新书。
2. 逐个重建节点，并维护 `旧节点 ID -> 新节点 ID` 的映射表。
3. 导入正文和版本时通过映射表修正 `node_id`。
4. 导入伏笔、世界状态、心理档案和人物历史时同样重写关联节点。
5. 最后恢复激活版本。

这意味着导入过程不是“原样复制 ID”，而是“重建图结构并映射引用”。

## 7. 三层记忆系统

`MemoryEngine` 是整个上下文构建系统的核心。

## 7.1 Tier 1：工作记忆

`get_working_memory(node_id, max_chars=3000)` 读取当前节点正文，并截取最后一段文本作为局部上下文。

这个层级的目标不是复述整章，而是保留“当前写作现场”的近因。

## 7.2 Tier 2：滚动摘要

`get_rolling_summaries(book_id, limit=10)` 读取最近章节摘要，用于跨章节连续性。

这层记忆能让 AI 在当前章节里知道：

- 前几章发生过什么
- 角色关系推进到哪里
- 哪些事件是最近的重要转折

## 7.3 Tier 3：向量检索

### 7.3.1 数据来源

`vectorize_book(book_id)` 会把三类文本切成 chunk：

- Lorebook 条目
- 章节摘要
- 节点正文

每个 chunk 都附带元信息，例如 `source`、`source_id`、`name`、`category`。

### 7.3.2 切片策略

- 默认 chunk 大小：500 字符
- overlap：100 字符

这是一种以字符为单位的近似切片策略，适合中文长文本场景，不依赖复杂 tokenizer。

### 7.3.3 向量策略

系统优先使用 TF-IDF，而不是 embedding API：

- 低依赖
- 零外部向量成本
- 本地即可运行

如果安装了 FAISS，则使用 `IndexFlatIP` 进行内积检索，并在检索前做 L2 归一化，把内积近似为余弦相似度；如果没有 FAISS，则回退到 scikit-learn 的余弦相似度计算。

## 7.4 Tier 0：全局设定注入

虽然文档中常说“三层记忆”，但 `build_context_window()` 实际还会先拼一个常驻层，可视为 Tier 0：

- 所有启用的 Lorebook 条目

这样做是因为某些世界观设定需要始终存在，而不是只在向量检索命中时出现。

## 7.5 上下文拼装顺序

`build_context_window(book_id, current_node_id)` 的拼装顺序是：

1. 世界观设定（Tier 0）
2. 滚动摘要（Tier 2）
3. 工作记忆（Tier 1）
4. 基于工作记忆的“人物提醒”
5. 基于工作记忆末尾文本的向量检索结果（Tier 3）

这意味着人物提醒在默认情况下被视为工作记忆的增强层，而不是单独平铺在所有场景外部。

## 8. Lorebook 动态注入

`dynamic_inject(book_id, text)` 采用两阶段策略：

### 8.1 第一阶段：关键词/正则匹配

对于每个 Lorebook 条目：

1. 拆分 `keywords`
2. 逐个尝试用正则匹配当前文本
3. 如果正则非法，则回退为简单子串匹配

一旦命中，条目以 `match_type='keyword'` 加入注入结果。

### 8.2 第二阶段：TF-IDF 相似度补充

对尚未命中的 Lorebook 条目：

1. 用 `name + content` 构成语料
2. 与当前文本做 TF-IDF
3. 计算 cosine similarity
4. 阈值大于 0.15 时纳入结果

这使得系统即使没有显式命中关键词，也能把语义上接近的设定补进来。

## 9. 人物提醒系统

人物提醒并不是一个单点功能，而是一条跨数据层、记忆层、提示词层和前端展示层的完整链路。

## 9.1 人物识别

`extract_character_mentions(book_id, text, max_characters=5)` 的实现基于 Lorebook：

1. 只取 `category == 'character'` 且启用的 Lorebook 条目。
2. 用角色名和 `keywords` 共同组成命中词表。
3. 在文本中做 case-insensitive 统计。
4. 记录命中次数、首次出现位置和命中词。
5. 按“命中频次降序 + 首次位置升序”排序。

当前版本没有引入 NER，因此识别质量高度依赖 Lorebook 中角色名与关键词的维护质量。

## 9.2 四层聚合结构

`build_character_reminders()` 会为每个命中的角色聚合四类信息：

1. **静态人格层**
   - Lorebook 描述
   - 角色心理中的驱动力、恐惧、核心矛盾
2. **历史事件层**
   - 来自 `character_history`
   - 默认取最近 3 条
3. **未回收伏笔层**
   - 在 `foreshadowing` 中筛选与角色名相关且 `status='unresolved'` 的记录
4. **动态状态层**
   - 来自 `world_state`

最终每个提醒卡都包含：

- `name`
- `personality`
- `recent_history`
- `foreshadowing`
- `world_state`
- `last_seen_chapter`
- `matched_terms`

## 9.3 人物提醒文本格式化

`build_character_reminder_context()` 把结构化提醒转成提示词片段，例如：

```text
=== 人物提醒 ===
【王岚】
性格/底色：...
历史事件：
- [第一章] ...
未回收伏笔：
- 雨夜约定：...
当前状态：
- location: ...
```

这个格式被多处 Agent 直接插入 system prompt。

## 9.4 人物历史自动沉淀

`refresh_character_history_for_node()` 是人物历史自动生成的核心。

执行过程：

1. 确认 `node_id` 的确属于该书。
2. 取章节正文，如果调用方没传正文则回库读取。
3. 删除该章节旧的自动生成人物历史，避免重复。
4. 识别本章出现的人物。
5. 解析摘要器返回的结构化 summary。
6. 为每个角色提取：
   - 本章摘要
   - 关键事件
   - 角色状态
   - 原文片段
   - 相关未回收伏笔
7. 写入 `character_history`，并标记 `is_manual = 0`。

这样设计的结果是：人物提醒不仅依赖当前文本实时计算，还依赖前面章节已经沉淀好的角色事件轨迹。

## 9.5 全书回填

`refresh_character_history_for_book()` 会：

1. 扁平化整棵文档树
2. 逐节点读取正文
3. 查找对应章节摘要
4. 对每个非空章节调用 `refresh_character_history_for_node()`

这给旧书提供了一次性补齐人物历史的能力。

## 10. AgentOrchestrator 的共用运行骨架

`AgentOrchestrator` 不是单纯的“函数集合”，而是所有智能体的统一运行时壳层。

如果你要基于现有架构继续新增 Agent 或记忆模块，请配合阅读 [EXTENDING.md](EXTENDING.md)。

## 10.1 用户上下文隔离

### 10.1.1 thread-local 用户

`self._request_ctx = threading.local()` 用于在当前线程中记录 `user_id`。

调用链一般是：

1. `app.py` 中 `_prepare_agent_data()` 调用 `set_request_user(g.user_id)`
2. 后续 `_get_client()`、`_get_params()`、`record_tokens()` 都通过 `_current_user_id()` 获取当前用户

### 10.1.2 停止生成

`_stop_flags` 是一个 `user_id -> threading.Event` 的映射。流式生成时会周期性检查 stop event：

- 如果用户点击“停止生成”
- `stop_generation(user_id)` 会把该用户的 event 设为已触发
- streaming 生成器在下一次循环时终止

## 10.2 模型解析与参数解析

### 10.2.1 `_get_client(role)`

执行步骤：

1. 根据当前线程的 `user_id` 查询当前用户。
2. 根据角色名去 `user_routing` 查对应模型。
3. 如果没配置该角色，则回退为该用户第一个模型。
4. 用 OpenAI SDK 构造 client。
5. `base_url` 允许指向任意 OpenAI 兼容接口。

### 10.2.2 `_get_params()`

从 `user_generation_params` 读取：

- `temperature`
- `top_p`
- `presence_penalty`
- `frequency_penalty`
- `max_tokens`

如果用户没有显式设置，则使用代码内默认值。

## 10.3 `_call_llm()`

这是所有智能体最终与外部模型交互的统一入口。

### 10.3.1 非流式模式

1. 构建 `client.chat.completions.create(...)`
2. 传入模型、消息和生成参数
3. 返回完整文本
4. 读取官方 usage 统计写入 `token_stats`

### 10.3.2 流式模式

1. 发起 `stream=True` 的 chat completion
2. 循环读取每个 chunk
3. 如果 stop event 被触发则中断
4. 持续 `yield text`
5. 最后根据文本长度估算 token，用于记录统计

### 10.3.3 错误处理

如果模型没配置或者 API 调用失败：

- 非流式：直接返回错误文本
- 流式：yield 一段错误文本

这种策略让上层无需为异常和正常返回写两套完全不同的消费逻辑。

## 11. 智能体系统总览

从职责上，可以把系统里的智能体分为六类：

1. **规划类**：Planner、Beat Generator
2. **正文生成类**：Drafter、Continuation、Plan-and-Solve
3. **文本修整类**：Polisher、Inline Command、Autocomplete
4. **分析类**：Validator、Subtext、Psychology、Conflict、Association
5. **状态追踪类**：Summarizer、Foreshadow、World State
6. **一致性保护类**：Hallucination Detect、Draft Guarded

下文逐个说明底层逻辑和执行流程。

## 12. 逐个智能体的底层逻辑与运行流程

## 12.1 Planner：`run_planner()`

### 作用

根据用户灵感、类型、卷章规模生成整本书的结构化大纲。

### 输入

- `inspiration`
- `genre`
- `volume_count`
- `chapters_per_volume`
- `book_id`

### 运行逻辑

1. 读取该书已有 Lorebook 设定作为背景约束。
2. 构造 system prompt，要求输出严格 JSON。
3. 用户 prompt 提供灵感和目标规模。
4. 调用 `_call_llm('planner', messages)`。
5. 如果有 `book_id`，把返回结果写入 `outlines`。

### 输出

- `outline`：结构化大纲文本

### 是否写库

是。会调用 `db.save_outline()`。

## 12.2 Beat Generator：`run_beat_generator()`

### 作用

把单章大纲拆解成一组场景级 beat。

### 输入

- `chapter_outline`
- `book_id`

### 运行逻辑

1. 读取书中的角色 Lorebook 条目。
2. 把角色设定作为上下文拼进 system prompt。
3. 要求模型输出场景级 JSON 数组。
4. 每个 beat 包含地点、角色、冲突、氛围和预计字数。

### 输出

- `beats`

### 是否写库

否。它是中间规划结果。

## 12.3 Drafter：`run_drafter_stream()`

### 作用

生成正文，是平台最核心的创作型 Agent。

### 输入

- `beat`
- `context`
- `book_id`
- `node_id`
- `style`
- `previous_text`

### 底层运行逻辑

1. 调用 `MemoryEngine.build_context_window()` 生成三层记忆融合上下文。
2. 额外构造人物提醒种子文本：`previous_text + beat + context`。
3. 如果综合记忆窗口中还没有人物提醒，则通过 `_maybe_build_character_ctx()` 再补一段人物提醒。
4. 调用 `dynamic_inject()` 对前文末尾做 Lorebook 相关设定注入。
5. 拼出最终 system prompt：
   - 写作风格约束
   - 三层记忆
   - 人物提醒
   - 动态注入设定
6. 调用 `_call_llm('drafter', ..., stream=True)`。
7. 按 chunk 向上游 yield。

### 运行流程

```text
用户请求正文生成
  -> build_context_window
  -> maybe_build_character_ctx
  -> dynamic_inject
  -> _call_llm(stream=True)
  -> SSE 返回前端
```

### 是否写库

否。正文保存由前端单独调用内容保存接口。

## 12.4 Validator：`run_validator()`

### 作用

对生成文本做 OOC、时间线、设定一致性和逻辑硬伤检查。

### 输入

- `text`
- `book_id`

### 底层运行逻辑

1. 读取 Lorebook 设定。
2. 读取实体图谱。
3. 把这些信息拼成前提条件。
4. 要求模型以 JSON 返回问题列表和评分。

### 输出

- `validation`

### 是否写库

否。

## 12.5 Polisher：`run_polisher()`

### 作用

对现有文本进行风格化润色。

### 输入

- `text`
- `style`
- `instruction`

### 底层运行逻辑

1. 根据风格名选出预置风格说明，例如白描、华丽、悬疑、幽默、诗意。
2. 把润色要求和额外指令写入 system prompt。
3. 用户 prompt 提供待润色文本。
4. 模型返回完整润色结果。

### 输出

- `polished`
- `original`

### 是否写库

否。

## 12.6 Summarizer：`run_summarizer()`

### 作用

为章节生成结构化摘要，并驱动长期记忆沉淀。

### 输入

- `text`
- `chapter_title`
- `book_id`
- `node_id`

### 底层运行逻辑

1. 要求模型输出：
   - `summary`
   - `key_events`
   - `character_states`
2. 把结果写入 `chapter_summaries`。
3. 在 API 层，`/api/agent/summarize` 在拿到结果后，会继续调用 `refresh_character_history_for_node()`。

### 运行流程

```text
章节正文
  -> Summarizer 生成结构化摘要
  -> save_chapter_summary
  -> refresh_character_history_for_node
  -> 后续章节可被人物提醒复用
```

### 是否写库

是。会写摘要，并通过 API 触发人物历史沉淀。

## 12.7 Smart Continuation：`run_smart_continuation()`

### 作用

实现一个带质量回路的智能续写器。

### 输入

- `book_id`
- `node_id`
- `previous_text`
- `goal`
- `max_retries`
- `style`

### 底层运行逻辑

1. 构造三层记忆上下文。
2. 必要时补人物提醒。
3. 动态注入 Lorebook。
4. 如果有大纲，则注入大纲片段保持方向一致。
5. 先生成一版草稿。
6. 再调用验证型提示词做 critic 评分。
7. 如果评分足够高，输出该草稿。
8. 如果评分不足，把审查反馈作为新上下文继续重试。
9. 最终以字符流方式把最佳稿返回。

### 运行流程

```text
前文 + 目标
  -> 生成草稿
  -> critic 评分
  -> 如果不足则带反馈重试
  -> 输出最佳版本
```

### 是否写库

否。

## 12.8 Smart Continuation Stream：`run_smart_continuation_stream()`

### 作用

低延迟续写版本，用于牺牲“批评-重试”换取更快首字节返回。

### 与 `run_smart_continuation()` 的区别

- 保留上下文注入逻辑
- 不做 critic 评分回路
- 直接流式调用 drafter

### 适用场景

- 快速续写
- 用户更在意实时性而不是额外质量筛查

前端现在还额外提供了一个显式的 `Plan 模式` 入口。这个模式不直接走 `run_smart_continuation*()`，而是把续写意图转交给 `run_plan_and_solve()`，用多阶段规划生成来换取更强的结构控制。

## 12.9 Autocomplete：`run_autocomplete()`

### 作用

提供 Ghost Text 自动补全。

### 输入

- `text`
- `book_id`

### 底层运行逻辑

1. 如果当前文本过短，直接返回空预测。
2. 只取最近 800 字符作为 micro-context。
3. 调用 `dynamic_inject()` 只看最近 200 字符，提高性能。
4. 人物提醒最多补 3 个角色。
5. 使用较低 `temperature` 和较小 `max_tokens`，减少时延和随机性。

### 设计意义

这个 Agent 不是“迷你版 drafter”，而是专门为了交互式低时延预测设计的。它故意缩小上下文窗口和生成预算。

## 12.10 Conflict Design：`run_conflict_design()`

### 作用

为当前情节设计多种冲突方案。

### 底层运行逻辑

1. 读取角色设定和关系图谱。
2. 根据 `conflict_type` 调整冲突方向：
   - 人对人
   - 人对环境
   - 人对自我
3. 让模型输出一个对抗矩阵和三种冲突方案。

### 输出结构

- antagonist matrix
- A/B/C 三种冲突方案

## 12.11 Association：`run_association()`

### 作用

进行发散式联想和头脑风暴。

### 底层运行逻辑

1. 先准备三种 probe：
   - 因果链
   - 反转
   - 细节放大
2. 按用户维度选择一个或全部 probe。
3. 对每种 probe 单独调一次模型。
4. 使用较高温度提升创意发散。
5. 尝试解析模型返回的 JSON 卡片列表。

### 工程特点

它不是一次 prompt 包打天下，而是“多 probe 并行生成，再聚合结果”的实现思路。

## 12.12 Foreshadow Detect：`run_foreshadow_detect()`

### 作用

从当前文本中检测可能的新伏笔并写入伏笔池。

### 底层运行逻辑

1. 把正文送给模型识别潜在伏笔。
2. 要求以结构化 JSON 输出。
3. 逐条写入 `foreshadowing` 表。

### 是否写库

是。

## 12.13 Foreshadow Scan：`run_foreshadow_scan()`

### 作用

扫描已有伏笔池，判断哪些伏笔适合在当前章节回收。

### 输入依赖

- 伏笔池
- 当前章节文本
- 章节上下文

### 输出

- 建议当前可回收的伏笔列表
- 回收方向建议

## 12.14 Subtext Analysis：`run_subtext_analysis()`

### 作用

分析文本表层含义与潜台词层之间的差异。

### 底层运行逻辑

1. 输入正文
2. 要求模型从对话和叙述中拆出：
   - surface meaning
   - hidden intention
   - emotional implication

这种能力更偏编辑分析，而不是直接改写。

## 12.15 Psychology Lens：`run_psychology_lens()`

### 作用

站在角色心理层面分析行为和冲突来源。

### 底层运行逻辑

1. 读取角色心理档案。
2. 把驱动力、恐惧、防御机制、核心矛盾注入上下文。
3. 模型输出心理层面的解释和建议。

### 适用场景

- 角色动机不清时
- 怀疑角色行为 OOC 时
- 需要补强角色深层矛盾时

## 12.16 World State Extract：`run_world_state_extract()`

### 作用

从正文中抽取世界状态变化并写库。

### 底层运行逻辑

1. 让模型从文本中识别实体状态变化。
2. 要求结构化输出：实体名、状态类型、状态值、场景上下文。
3. 调用 `db.upsert_world_state()` 逐条写入。

### 是否写库

是。

## 12.17 World State Validate：`run_world_state_validate()`

### 作用

把当前文本与数据库中的世界状态做一致性比对。

### 底层运行逻辑

1. 读取 `world_state`。
2. 构造“已知状态前提”。
3. 让模型检查当前文本是否与这些状态冲突。

### 输出

- `consistent`
- `conflicts`

## 12.18 Plan and Solve：`run_plan_and_solve()`

### 作用

比普通续写更重的深度生成管线。

### 底层运行逻辑

这个 Agent 采用阶段式工作流，而不是一次 completion：

1. **Plan**：先拆出写作子问题和策略。
2. **Solve**：对多个子问题分别求解。
3. **Integrate**：把子答案整合成最终正文。
4. 最终支持流式返回。

### 设计意义

它适合复杂场景、信息密度高、需要多步推理的生成任务。

## 12.19 Hallucination Detect：`run_hallucination_detect()`

### 作用

做多维一致性检测，是系统质量护栏的重要组成部分。

### 底层运行逻辑

它不是简单一次“请检查幻觉”，而是两层校验：

#### 第一层：NLI 风格验证

构造 premise：

- Lorebook
- World State
- Character Psychology
- Context Window
- 人物提醒

然后把待检测文本作为 hypothesis，要求模型判断：

- Entailment
- Neutral
- Contradiction

并输出结构化 conflict 列表。

#### 第二层：World State Validate

额外调用 `run_world_state_validate()`，对世界状态冲突做二次补充。

#### 第三层：结果合并

最终把两部分冲突合并成统一 verdict。

### 输出

- `has_contradiction`
- `nli_verdict`
- `world_state_consistent`
- `conflicts`
- `fix_suggestions`

## 12.20 Draft with Hallucination Guard：`run_draft_with_hallucination_guard()`

### 作用

在生成时把“正文生成”和“幻觉检测”串成闭环。

### 运行流程

```text
生成草稿
  -> 幻觉检测
  -> 如果通过，直接输出
  -> 如果不通过，把矛盾描述附加到下一轮约束里重采样
  -> 最多重试 3 次
```

### 实现特点

- 它会先完整收集一版草稿
- 再做检测
- 若有问题，则把冲突描述拼进 `beat` 形成新的硬约束

因此这是一个“生成后校验，再带反馈重试”的 guard loop。

## 12.21 Inline Command：`run_inline_command()`

### 作用

响应编辑器中的 Slash 类局部编辑命令。

### 支持的典型指令

- `continue`
- `rewrite`
- `expand_env`
- `simplify_dialogue`
- `add_tension`
- `inner_monologue`

### 底层运行逻辑

1. 根据指令选择预置 prompt 模板。
2. 注入相关 Lorebook 设定。
3. 注入人物提醒。
4. 使用 `drafter` 角色流式返回修改结果。

### 工程意义

它让“生成型模型”在前端表现为“局部编辑器命令”。

## 13. 关键端到端链路

## 13.1 正文写作链路

```text
前端选择书和章节
  -> selectNode()
  -> 读取正文 + 记忆状态 + 人物提醒
  -> 用户触发 drafter/continue
  -> app.py 预处理 book/node/user
  -> AgentOrchestrator 构造上下文
  -> _call_llm(stream=True)
  -> SSE 返回前端
  -> 前端增量渲染
```

## 13.2 摘要沉淀链路

```text
用户点击生成摘要
  -> run_summarizer
  -> save_chapter_summary
  -> refresh_character_history_for_node
  -> character_history 新增事件记录
  -> 后续章节和 AI 提示词可复用这些事件
```

## 13.3 人物提醒链路

```text
章节切换 / 编辑器输入 / 自动补全前
  -> loadCharacterReminders()
  -> /api/character-reminders
  -> extract_character_mentions
  -> build_character_reminders
  -> 返回结构化人物卡片 + prompt 片段
  -> 右栏展示卡片
  -> 写作型 Agent 注入相同上下文
```

## 13.4 一致性保护链路

```text
生成文本
  -> hallucination_detect
     -> NLI premise/hypothesis 检测
     -> world_state_validate
     -> merge conflicts
  -> 如果是 guarded draft，则带约束重试
```

## 13.5 导入导出链路

```text
导出 JSON workspace
  -> export_all_book_data
  -> to_json_workspace

导入 JSON workspace
  -> create_book
  -> rebuild nodes + id_map
  -> restore contents/versions/lorebook/summaries/foreshadowing/world_state/psychology/character_history
```

## 14. 性能、限制与设计取舍

## 14.1 关键设计取舍

### 14.1.1 为什么用 SQLite

- 适合单机本地写作工具
- 零额外服务依赖
- 便于用户自托管和导出备份

### 14.1.2 为什么向量检索优先用 TF-IDF

- 不依赖 embedding API
- 不增加运行成本
- 对中文长篇写作的关键词和局部语义检索已足够实用

### 14.1.3 为什么人物历史抽取不在每次击键时运行

- 实时阶段只做“人物识别 + 已有历史聚合”
- 昂贵的“历史沉淀”只在摘要生成后或手动刷新时触发

这是一种典型的“快路径 / 慢路径”拆分设计。

### 14.1.4 为什么做人物提醒去重

`build_context_window()` 已经可能包含人物提醒，因此续写和正文生成前又额外补一份会造成 token 浪费。当前实现通过 `_maybe_build_character_ctx()` 检查综合上下文是否已包含 `=== 人物提醒 ===`，若已存在则不重复注入。

## 14.2 当前限制

1. 人物识别主要依赖 Lorebook 名称和关键词，而不是 NER。
2. 多个别名或代称复杂时，需要用户手动维护关键词。
3. `_call_llm()` 当前默认使用 OpenAI 兼容 SDK，不同提供商的高级特性未统一抽象。
4. 流式 stop flag 以用户为粒度；如果同一用户并发多个生成请求，停止逻辑可能相互影响。
5. 前端是单体脚本式状态管理，随着功能继续增长，维护复杂度会上升。

## 14.3 重要约束

### 14.3.1 书级隔离

人物历史、人物提醒、节点刷新都严格以 `book_id` 为边界。任何使用 `book_id + node_id` 的接口都必须确认该节点确实属于该书。

### 14.3.2 人物历史的双来源模型

人物历史并非纯自动，也不是纯手工，而是：

- 自动提取负责覆盖常规章节事件
- 手工记录负责补充复杂设定和叙事意图

系统通过 `is_manual` 字段让两者并存。

## 15. 新增智能体或功能时的接入模板

如果未来要增加新的 Agent，通常需要完成四件事：

1. 在 `agents.py` 中新增 `run_xxx()`。
2. 决定它是否需要：
   - 三层记忆
   - 人物提醒
   - 动态 Lorebook 注入
   - 流式返回
   - 写库
3. 在 `app.py` 中新增对应 API 路由。
4. 在前端 `app.js` 中新增调用入口。

推荐遵循现有约定：

- 写作型 Agent：优先复用 `drafter` 角色和流式返回。
- 分析型 Agent：优先返回 JSON。
- 会改变持久化状态的 Agent：必须明确标注写库路径。
- 需要角色连续性的 Agent：优先复用 `build_context_window()` 与人物提醒，而不是手工重新拼提示词。

## 16. 结语

墨境并不是一个单纯把大模型接到编辑器上的项目。它的核心实现思想是：

- 用 SQLite 和结构化表把小说工程的长期状态固化下来；
- 用 `MemoryEngine` 把静态设定、滚动摘要、局部工作记忆和语义检索整合成统一上下文；
- 用 `AgentOrchestrator` 把不同写作任务拆成不同角色和不同执行流程；
- 用人物历史、伏笔池、心理档案、世界状态等中间结构，把“长篇小说的一致性”从一次性 prompt 工程提升为持续维护的系统能力。

这也是该系统与“普通文本生成器”的本质区别。