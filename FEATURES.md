# 墨境 · AI 长篇小说写作平台 — 产品功能分析文档

> 版本日期：2026-03-14
> 技术栈：Flask · SQLite · 原生 JS · OpenAI 兼容接口

---

## 目录

1. [产品定位](#1-产品定位)
2. [整体架构](#2-整体架构)
3. [功能模块总览](#3-功能模块总览)
4. [模块一：认证与账户](#4-模块一认证与账户)
5. [模块二：书籍与文档树](#5-模块二书籍与文档树)
6. [模块三：多智能体写作系统](#6-模块三多智能体写作系统)
7. [模块四：三层记忆系统](#7-模块四三层记忆系统)
8. [模块五：世界观与角色资料库](#8-模块五世界观与角色资料库)
9. [模块六：叙事辅助工具](#9-模块六叙事辅助工具)
10. [模块七：版本管理](#10-模块七版本管理)
11. [模块八：写作规则中心](#11-模块八写作规则中心)
12. [模块九：时间线与事件台账](#12-模块九时间线与事件台账)
13. [模块十：自动快照与回收站](#13-模块十自动快照与回收站)
14. [模块十一：全局搜索与替换](#14-模块十一全局搜索与替换)
15. [模块十二：异步任务中心](#15-模块十二异步任务中心)
16. [模块十三：记忆注入可解释性面板](#16-模块十三记忆注入可解释性面板)
17. [模块十四：一致性报告](#17-模块十四一致性报告)
18. [模块十五：章节工作流](#18-模块十五章节工作流)
19. [模块十六：导入导出](#19-模块十六导入导出)
20. [模块十七：统计面板](#20-模块十七统计面板)
21. [数据库架构](#21-数据库架构)
22. [API 端点清单](#22-api-端点清单)
23. [前端界面说明](#23-前端界面说明)
24. [已知限制与扩展方向](#24-已知限制与扩展方向)

---

## 1 产品定位

墨境是一个面向**长篇小说**创作者的 AI 辅助写作工作台，核心价值在于：

- 将目录管理、世界观设定、多智能体全流程写作、三层记忆、伏笔追踪、角色心理分析、一致性校验和工程化导入导出整合在**同一个 Web 界面**中。
- 通过结构化的长期记忆和规则约束，解决 AI 写作在长文中的**角色崩坏、设定遗忘、时间线混乱**三大顽疾。
- 面向个人作者本地部署，支持接入任意 OpenAI 兼容接口，不绑定特定模型厂商。

典型适用场景：中长篇网络文学、类型小说、多线叙事、需要严格世界观管理的奇幻/科幻创作。

---

## 2 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        浏览器                                │
│  ┌──────────┐   ┌───────────────┐   ┌─────────────────────┐ │
│  │  左栏     │   │    中栏        │   │      右栏            │ │
│  │ 目录树    │   │ 正文编辑器     │   │  智能体工作台         │ │
│  │ Lorebook │   │ Ghost Text    │   │  记忆面板             │ │
│  │ 实体图谱  │   │ Slash 指令    │   │  人物提醒             │ │
│  │ 时间线    │   │ Diff 视图     │   │  章节摘要             │ │
│  └──────────┘   └───────────────┘   └─────────────────────┘ │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / SSE / WebSocket
┌───────────────────────────▼─────────────────────────────────┐
│                     Flask (app.py)                           │
│  JWT 认证 · 路由分发 · SSE 流式输出 · SocketIO               │
├──────────────────────────────────────────────────────────────┤
│  AgentOrchestrator │ MemoryEngine │ ExportEngine             │
│  RuleEngine        │ TimelineEngine │ SnapshotEngine         │
│  SearchEngine      │ JobEngine    │ WorkflowEngine           │
│  ConsistencyEngine │ StatsEngine                             │
├──────────────────────────────────────────────────────────────┤
│                   Database (SQLite)                          │
│  33 张表 · Fernet 加密敏感字段 · 统一 CRUD 访问层            │
└──────────────────────────────────────────────────────────────┘
```

**技术栈一览**

| 层次 | 技术 |
|------|------|
| 前端 | 原生 HTML5 / CSS3 / ES6+ JavaScript，无框架依赖 |
| 后端框架 | Flask 3.x + Flask-CORS + Flask-SocketIO |
| 数据库 | SQLite（单文件，支持本地部署） |
| 认证 | JWT（PyJWT），Fernet 对称加密敏感字段 |
| 模型接入 | OpenAI 兼容 HTTP 接口，支持自定义 Base URL |
| 向量检索 | NumPy + scikit-learn TF-IDF，可选 FAISS 加速 |
| 中文分词 | jieba（可选，未安装时降级为字符级匹配） |
| 混合检索 | BM25（rank-bm25，可选） + RRF 融合排序 |
| 文档导出 | Markdown / TXT / EPUB（ebooklib）/ JSON |
| 文档导入 | JSON / Markdown / TXT / DOCX（python-docx） |
| 实时通信 | SSE（Server-Sent Events）流式输出 |

---

## 3 功能模块总览

| # | 模块 | 核心价值 |
|---|------|----------|
| 1 | 认证与账户 | 多用户隔离，JWT 无状态认证 |
| 2 | 书籍与文档树 | 四层节点结构（书 > 卷 > 章 > 场景），自动保存 |
| 3 | 多智能体写作 | 14 个专用智能体，流式输出，任务分组管理 |
| 4 | 三层记忆系统 | 工作记忆 + 滚动摘要 + 向量 RAG，动态注入 |
| 5 | 世界观与角色资料 | Lorebook + 实体图谱 + 心理档案 + 世界状态 |
| 6 | 叙事辅助 | 伏笔追踪、潜台词分析、情绪张力诊断 |
| 7 | 版本管理 | 版本分支 + Diff 对比 + 一键采纳 |
| 8 | 写作规则中心 | 按范围（书/卷/章）继承的约束规则，自动注入提示词 |
| 9 | 时间线与事件台账 | 全书事件时序管理，冲突检测，LLM 自动抽取 |
| 10 | 自动快照与回收站 | 写前自动备份，软删除，一键还原 |
| 11 | 全局搜索与替换 | 全书跨节点搜索，预览模式安全替换 |
| 12 | 异步任务中心 | 后台长任务队列，进度追踪，取消/重试 |
| 13 | 记忆注入可解释性 | 每次生成的注入明细，分层标注，排除/固定控制 |
| 14 | 一致性报告 | 四维扫描（伏笔/世界状态/时间线/角色行为），问题分级 |
| 15 | 章节工作流 | 六步标准化写作流程，可编排执行 |
| 16 | 导入导出 | 范围化导出 + 四格式导入（JSON/MD/TXT/DOCX） |
| 17 | 统计面板 | 智能体调用延迟、采纳率、Token 消耗可视化 |

---

## 4 模块一：认证与账户

### 4.1 功能描述

- **注册/登录**：用户名 + 密码，密码使用 Werkzeug `generate_password_hash` 存储
- **JWT 鉴权**：每次请求携带 Bearer Token，服务端验证后将 `user_id` 写入 Flask `g` 上下文
- **Token 有效期**：默认 30 天
- **白名单路由**：`/api/auth/register`、`/api/auth/login` 无需鉴权
- **敏感字段加密**：API Key、Lorebook 内容、摘要等在数据库层使用 Fernet 对称加密存储

### 4.2 模型配置（用户级）

用户可在「设置中心」中管理多个模型配置，每条配置包含：

| 字段 | 说明 |
|------|------|
| 名称 | 显示标签 |
| 提供商 | openai / anthropic / 自定义 |
| Base URL | 支持代理接入 |
| API Key | Fernet 加密存储 |
| 模型标识 | 如 `gpt-4o`、`claude-opus-4-6` |
| 最大上下文 | Token 上限声明 |

### 4.3 任务路由

可按智能体角色独立分配模型：

```
planner / beat_generator / drafter / validator / polisher
summarizer / autocomplete / association / plan_and_solve / hallucination
conflict / psychologist / worldstate / subtext / foreshadow / brainstorm
```

未配置角色路由时，回退到该用户的第一个模型配置。

### 4.4 生成参数

每个用户可单独保存：temperature、top_p、max_tokens、frequency_penalty、presence_penalty。

---

## 5 模块二：书籍与文档树

### 5.1 节点结构

文档树采用四层递归节点模型：

```
书（book）
  └─ 卷（volume）
       └─ 章（chapter）
            └─ 场景（scene）
```

每个节点独立存储标题和正文内容，通过 `parent_id` 链式关联，支持任意深度嵌套。

### 5.2 核心操作

| 操作 | 说明 |
|------|------|
| 创建书籍 | 指定标题和描述 |
| 节点拖拽排序 | 前端拖拽，后端更新 `position` 字段 |
| 节点重命名 | 双击标题就地编辑 |
| 节点复制 | 复制标题和正文 |
| 软删除 | 删除时先送入回收站，可还原 |
| 自动保存 | 编辑停止 2 秒后自动触发 PUT 保存 |
| 字数统计 | 实时统计当前章节字数 |

### 5.3 大纲管理

每个节点可关联一条独立大纲文本（`outlines` 表），与正文内容分开存储，支持独立查看和 AI 生成。

---

## 6 模块三：多智能体写作系统

### 6.1 智能体分组一览

系统共 14 个智能体，按任务性质分为四组：

**规划组（Planning）**

| 智能体 | 角色标识 | 功能 |
|--------|----------|------|
| 架构师 | `planner` | 生成整本书或卷章结构化大纲，支持流式输出 |
| 节拍器 | `beat_generator` | 将章节拆分为场景节拍序列，细化写作节奏 |
| 冲突设计师 | `conflict` | 分析现有剧情提出矛盾冲突设计方案 |
| 头脑风暴 | `brainstorm` | 开放式创意联想，生成多个剧情走向选项 |

**写作组（Writing）**

| 智能体 | 角色标识 | 功能 |
|--------|----------|------|
| 执笔者 | `drafter` | 根据节拍和上下文流式生成正文，注入写作规则 |
| 智能续写 | `continuation` | 基于工作记忆续写后续内容，支持流式 |
| 润色器 | `polisher` | 按风格要求润色文本 |
| Plan-Solve | `plan_and_solve` | 先生成写作计划再输出正文（两阶段生成） |

**验证组（Validation）**

| 智能体 | 角色标识 | 功能 |
|--------|----------|------|
| 验证者 | `validator` | 四维校验：OOC、时间线、世界状态、语言一致性；结果按严重程度分级（critical / major / minor / ok） |
| 幻觉检测 | `hallcheck` | 构建 premise 集合，逐条检测文本是否违反已知设定 |
| 世界状态提取 | `worldstate` | 从文本中提取并更新世界状态变化 |
| 伏笔检测 | `foreshadow` | 识别文本中埋下的伏笔并写入伏笔池 |

**分析组（Analysis）**

| 智能体 | 角色标识 | 功能 |
|--------|----------|------|
| 潜台词分析 | `subtext` | 分析文本的隐含含义、人物潜台词和深层动机 |
| 角色心理 | `psychology` | 生成或更新角色的驱动力、恐惧、防御机制和核心矛盾 |

### 6.2 Slash 指令（行内编辑）

在编辑器中输入 `/` 触发行内指令面板，支持：

| 指令 | 功能 |
|------|------|
| `/改写` | 改写选中文本 |
| `/扩写` | 扩展选中文本 |
| `/精简` | 压缩选中文本 |
| `/续写` | 续写选中文本之后 |
| `/翻译` | 翻译选中内容 |
| 自定义指令 | 自由输入任意指令 |

### 6.3 Ghost Text 自动补全

- 编辑停顿 800ms 后自动触发
- 以灰色透明文字显示预测内容（Ghost Text）
- 按 `Tab` 键采纳，继续输入跳过
- 采纳时自动记录到统计系统

### 6.4 深度生成（Guarded Draft）

包含守卫机制的执笔模式：

1. 生成前运行验证者预检
2. 若发现 critical 级问题，进入阻断流程，强制修正后继续
3. 最终生成前再次校验，确保输出与设定一致

### 6.5 流式输出

- 执笔者、续写、行内编辑等生成类接口全部支持 SSE 流式
- 前端逐 token 渲染，支持中途中断
- 中断后已输出内容可单独采纳

### 6.6 写作规则注入

所有生成类智能体（drafter、continuation、plan_and_solve）在构建提示词时，自动调用 `RuleEngine.build_rule_prompt()` 按范围获取有效规则并注入系统提示词前缀，让 LLM 遵循用户定义的写作约束。

---

## 7 模块四：三层记忆系统

### 7.1 架构概览

```
Tier 0：全局设定（Lorebook 常驻注入）
  ↓
Tier 1：工作记忆（当前章节最近 3000 字符）
  ↓
Tier 2：滚动摘要（最近 10 章摘要）
  ↓
Tier 3：向量 RAG（TF-IDF + FAISS + BM25 混合检索）
  ↓
人物提醒（角色设定 + 历史事件 + 伏笔 + 状态聚合）
```

### 7.2 Tier 0 — 全局设定

Lorebook 中所有启用条目在每次生成时作为系统提示词的常驻前缀注入，确保基础世界观始终可见。

### 7.3 Tier 1 — 工作记忆

- 从当前编辑节点的正文内容中截取**最近 3000 字符**
- 代表当前写作最直接的局部上下文
- 每次生成前实时读取，无需预计算

### 7.4 Tier 2 — 滚动摘要

- 摘要器智能体生成的章节摘要存入 `chapter_summaries` 表
- 构建上下文时取最近 10 条摘要拼接为前情回顾
- 支持结构化摘要格式（JSON），包含关键事件、角色状态变化等

### 7.5 Tier 3 — 向量长记忆

**索引内容：**
- Lorebook 所有条目（名称 + 内容）
- 章节摘要
- 节点正文（500 字切片，100 字重叠）

**检索流程：**

```
TF-IDF 向量检索（NumPy/FAISS）
        ↓
BM25 关键词检索（rank-bm25）
        ↓
RRF 倒数排名融合（k=60）
        ↓
Top-K 结果（默认 Top-3 注入，Top-10 记录候选）
        ↓
失败时降级为关键词匹配（_keyword_fallback_retrieve）
```

**增量更新：**
每次保存节点内容后可调用 `incremental_update_index()` 仅重建该节点的向量，避免全量重建。

**jieba 分词加速：**
检测到 jieba 时，自动从 Lorebook 加载角色名和关键词作为自定义词典，提升特有名词的检索精度。

### 7.6 人物提醒系统

**触发场景：**

| 场景 | 触发方式 |
|------|----------|
| 执笔者生成前 | 自动 |
| 智能续写前 | 自动 |
| 自动补全前 | 自动 |
| 行内编辑前 | 自动 |
| 切换章节时 | 自动 |
| 幻觉检测 | 自动 |

**提醒内容来源：**

| 来源 | 数据描述 |
|------|----------|
| Lorebook 角色条目 | 角色基础设定、性格、描述 |
| 角色心理档案 | 驱动力、恐惧、防御机制、核心矛盾 |
| 人物历史档案 | 角色在各章节的关键事件，可自动生成或手工维护 |
| 伏笔池 | 与该角色相关的未回收伏笔 |
| 世界状态 | 位置、关系、身体状态等 |

**人物识别算法：**

1. 若安装 jieba，优先使用分词级精确匹配
2. 回退到子串匹配
3. 支持别名（aliases 字段）和自定义关键词权重（keyword_weights 字段，JSON 格式）
4. 计算**置信度**：基于加权命中分 × 共现奖励系数
5. 按（频次降序, 首次出现位置, 名称字典序）排序

**人物历史生成：**

- 每次保存章节后可调用「刷新当前章节人物历史」
- 系统识别章节中提及的角色，自动为每个角色写入事件摘要
- 无现有摘要时自动触发摘要器生成
- 提取伏笔标签、角色状态变化（结构化 character_states 数组）写入详情

### 7.7 注入预算控制

- `build_context_window()` — 标准调用，仅返回上下文文本
- `build_context_window_with_log()` — 增强调用，同时返回注入明细和候选明细，供可解释性面板展示
- `_apply_pin_exclude()` — 过滤固定/排除标记的条目

---

## 8 模块五：世界观与角色资料库

### 8.1 Lorebook

支持五类条目：

| 类别 | 典型内容 |
|------|----------|
| `character` | 姓名、性格、背景、关键词、别名、关键词权重 |
| `location` | 地名、描述、地理关系 |
| `item` | 道具名称、能力、归属 |
| `faction` | 势力名称、立场、成员 |
| `rule` | 世界法则、魔法体系、社会规则 |

每条 Lorebook 条目支持：
- 启用/禁用控制
- 关键词触发（正则或子串匹配）
- 向量语义检索补充触发

### 8.2 实体关系图谱

- 手工添加实体间关系（source_entity → relation_type → target_entity）
- 描述字段记录关系细节
- 参与划词查询（lookup_entity）的结果聚合

### 8.3 角色心理档案

为每个角色单独维护：

| 字段 | 说明 |
|------|------|
| drives | 核心驱动力 |
| fears | 深层恐惧 |
| defense_mechanism | 防御机制 |
| subtext_style | 潜台词风格 |
| core_contradiction | 核心矛盾（外表 vs 内心）|

心理档案由「角色心理分析」智能体自动生成，也可手工编辑。

### 8.4 世界状态追踪

记录动态变化的实体状态：

| 字段 | 说明 |
|------|------|
| entity_name | 实体名称 |
| state_type | 状态类型（位置/关系/身体/物品） |
| state_value | 当前状态值 |
| scene_context | 发生变化的场景上下文 |

世界状态参与：
- 验证者一致性校验
- 幻觉检测 premise 构建
- 人物提醒聚合
- 一致性扫描

### 8.5 人物历史档案

每条记录关联一个章节节点，包含：
- 事件类型（event / state_change / relationship / foreshadow）
- 事件摘要
- 详细描述（含结构化角色状态解析）
- 伏笔引用
- 原文片段

支持手工添加，适合补充暗线发展、特殊设定等自动识别遗漏的内容。

---

## 9 模块六：叙事辅助工具

### 9.1 伏笔管理

**伏笔池** 记录：
- 伏笔文本（原文片段）
- 标签（短名称）
- 描述（含义说明）
- 状态：`unresolved` / `resolved`
- 解决所在章节

**智能体支持：**
- `foreshadow-detect`：自动从文本中识别伏笔并写入伏笔池
- `foreshadow-scan`：给定当前章节，扫描哪些已有伏笔适合在此处 payoff

**显示：**
人物提醒中会高亮展示与当前角色相关的**未回收伏笔**，辅助作者及时回收。

### 9.2 潜台词分析

由「潜台词分析」智能体（`subtext`）对选中文本进行深度解读，输出：
- 表层对话的隐含意图
- 人物潜在动机
- 未说出口的情感张力

### 9.3 情绪张力诊断

「验证者」智能体在四维校验之外，可单独运行针对叙事节奏和情绪张力的诊断，识别平淡段落和张力断裂点。

### 9.4 冲突设计

「冲突设计师」智能体分析当前剧情脉络，提出：
- 角色间矛盾激化方案
- 外部压力设计
- 多个剧情走向选项

结果缓存在 `lastConflictData`，下次打开编辑器时可复用。

### 9.5 划词查询

选中任意文本后弹出浮动面板，聚合显示：
- 匹配的 Lorebook 条目（名称／类别／描述）
- 涉及该实体的关系图谱节点
- 该实体的当前世界状态

---

## 10 模块七：版本管理

### 10.1 手动版本分支

每次点击「保存版本」时，对当前节点正文创建一个带备注的版本快照：
- 版本号（字符串标签）
- 版本备注
- 正文完整内容

### 10.2 Diff 对比

选中两个版本后，前端逐词 diff 高亮展示：
- 绿色：新增内容
- 红色：删除内容
- 白色：未变化内容

### 10.3 一键采纳

在 Diff 视图中可采纳某个版本，立即用该版本内容替换当前正文。

---

## 11 模块八：写作规则中心

### 11.1 规则集管理

用户可以创建多个**规则集**（`writing_rule_sets`），每个规则集包含多条**规则**（`writing_rules`）。

规则字段：

| 字段 | 说明 |
|------|------|
| title | 规则名称 |
| category | 分类（视角/语气/伏笔/逻辑/风格/自定义） |
| content | 规则正文描述 |
| scope_type | 适用范围：`book` / `volume` / `chapter` |
| scope_node_id | 范围绑定的节点 ID（`book` 时为 null） |
| rule_type | 规则类型：`instruction`（建议）/ `prohibition`（禁用）/ `hard_lock`（强制） |
| enabled | 启用状态 |
| priority | 优先级（数字越大越优先） |

### 11.2 范围继承规则

```
书级规则（book）
  ↓ 被卷级规则覆盖
卷级规则（volume）
  ↓ 被章级规则覆盖
章级规则（chapter）— 最终生效
```

同一 scope + category + title 组合，更细粒度的规则覆盖更粗粒度的规则。

### 11.3 自动注入提示词

在执笔者、智能续写、Plan-Solve 等生成类智能体启动前，`RuleEngine.build_rule_prompt()` 自动构建分类规则提示词段并注入系统消息：

```
=== 写作规则 ===
【视角规则】
- 始终保持第三人称限制视角
【风格规则】
- 避免大段心理独白，多用行为外化情绪
...
```

### 11.4 文本违规检测

`validate_against_rules()` 扫描已生成文本，对 `prohibition` 和 `hard_lock` 类型的规则进行关键词检测，返回违规清单用于验证反馈。

### 11.5 规则冲突检测

`check_rule_conflicts()` 扫描同一书中相同范围和分类下是否存在语义矛盾的规则，结果显示在规则设置面板中，帮助作者整理一致的规则体系。

---

## 12 模块九：时间线与事件台账

### 12.1 事件模型

每条时间线事件包含：

| 字段 | 说明 |
|------|------|
| entity_name | 涉及的实体/角色 |
| event_type | 分类（appearance / departure / conflict / revelation 等） |
| description | 事件描述 |
| chapter_index | 所在章节的顺序号 |
| story_time | 故事内时间（字符串，用于跨章节时序） |
| location | 发生地点 |
| significance | 重要性（1-5） |
| source_type | 来源（manual / auto_extract） |
| source_node_id | 关联的文档节点 |

### 12.2 事件台账过滤

时间线面板支持按以下维度过滤：
- 实体名称
- 事件类型

### 12.3 自动抽取

点击「从正文抽取」时，调用 LLM（通过 `set_event_extractor` 注入的回调）分析当前章节，自动识别并写入时间线事件。重新抽取时会先清除该节点的历史自动事件。

### 12.4 实体状态转移

`entity_state_transitions` 表记录每次世界状态变更：
- 旧值 → 新值
- 发生的章节顺序号

### 12.5 时间线冲突检测

`detect_conflicts()` 扫描同一实体的状态转移序列，发现转移不连续（t₁.new_value ≠ t₂.old_value）的断层，返回冲突报告，帮助作者发现遗漏的状态过渡描写。

### 12.6 注入 LLM 上下文

`build_timeline_context()` 构建时间线文本段，可注入到当前章节的提示词中，确保 AI 生成内容与时间线一致。

---

## 13 模块十：自动快照与回收站

### 13.1 自动快照

**触发时机：**
- 执行深度生成（Guarded Draft）前自动创建节点快照
- 执行批量替换前自动创建书籍快照
- 手动点击编辑器工具栏「快照」按钮

**快照内容：**

| 范围 | 保存内容 |
|------|----------|
| 节点快照 | 当前节点的正文内容 + 所有版本列表 |
| 全书快照 | 全书所有节点的正文内容 |

**快照元数据：**
- 类型（`auto_rewrite` / `manual` / `pre_batch_replace`）
- 标签（自定义备注）
- 创建时间

**保留策略：**
`cleanup()` 方法按书自动清理超出保留上限（默认 50 条）的旧快照。

### 13.2 快照还原

- 预览一览模式：展示500字内容预览、版本数，不执行写入
- 一键还原：将节点正文恢复为快照时的内容

### 13.3 回收站

所有删除操作（节点、Lorebook 条目、伏笔、世界状态等）均先进入软删除状态，存入 `recycle_bin` 表：

| 字段 | 说明 |
|------|------|
| item_type | 类型（node / lorebook / foreshadowing / world_state） |
| item_id | 原始记录 ID |
| item_data | 完整原始数据（JSON 序列化） |
| deleted_at | 删除时间 |

**还原操作：**
`restore_from_recycle()` 按 `item_type` 路由还原：节点类型重建节点和内容，其他类型重新插入对应表。

---

## 14 模块十一：全局搜索与替换

### 14.1 全文搜索

- 搜索范围：正文内容 / 章节摘要 / Lorebook / 人物历史 / 世界状态
- 结果分组：按搜索范围类别分组显示
- 上下文摘录：每个命中结果展示前后各 60 字的上下文，关键词高亮
- 实时搜索：输入停顿后自动触发

### 14.2 安全替换

- **预览模式**（默认）：统计受影响章节数和命中次数，展示每处修改前后内容对比，不写入数据库
- **确认替换**：明确确认后执行全书范围替换，并自动创建快照备份

### 14.3 引用追踪

- `find_references(entity_name)` — 找出全书所有提及某实体的章节，汇总命中次数和上下文摘录
- `find_chapter_references(node_id)` — 给定一个章节，找出其关联的 Lorebook 条目、伏笔、世界状态

### 14.4 首末出现定位

`find_first_last_occurrence()` 精确定位某词或短语在全书中**首次**和**最后一次**出现的章节，适合追踪概念引入时机和设定收尾情况。

---

## 15 模块十二：异步任务中心

### 15.1 任务生命周期

```
created → running → completed
                 → cancelled
                 → failed
```

每次状态变更写入 `job_logs` 表，保留完整的执行日志。

### 15.2 任务类型

当前已注册的任务类型：

| 类型 | 触发场景 |
|------|----------|
| `vectorize` | 向量索引全量重建 |
| `consistency_scan` | 全书一致性扫描 |
| `batch_summary` | 全书章节批量摘要生成 |
| `character_history_refresh` | 全书人物历史回填 |

### 15.3 进度追踪

任务执行时通过 `progress_callback(current, total, message)` 回调更新数据库中的 `progress` 字段（0–100），前端轮询显示进度条。

### 15.4 取消与重试

- **取消**：设置 cancel_event，任务在下次进度回调时检测并优雅退出
- **重试**：基于失败或取消的任务，克隆相同参数创建新任务并立即启动，返回新任务 ID

### 15.5 任务中心 UI

- 展示所有任务的状态、类型、进度条
- 运行中任务显示取消按钮
- 失败任务显示重试按钮
- 顶部栏 Badge 显示当前运行中任务数量

---

## 16 模块十三：记忆注入可解释性面板

### 16.1 注入明细记录

每次调用 `build_context_window_with_log()` 时，同时记录：

**已注入条目**（injected_items）：

| 字段 | 说明 |
|------|------|
| tier | 来源层级（0-3） |
| type | 类型（lorebook / summary / working / character_reminder / vector） |
| source | 来源 ID |
| content_preview | 前 200 字预览 |
| reason | 注入原因（keyword_match / rolling_summary / vector_similarity 等） |
| source_chapter | 来源章节名 |

**候选条目**（candidate_items）：
- 同格式，但因排名不足（Top-K 外）或预算限制未被注入
- 附带相似度分数

### 16.2 注入日志持久化

`save_injection_log()` 将每次生成的注入明细以 JSON 存入 `memory_injection_logs` 表，供后续查阅。

### 16.3 可解释性 UI

「注入日志」面板展示：
- 层级色标（Tier 0-3 对应不同颜色）
- 来源类型标签
- 内容预览
- 注入原因文字说明

### 16.4 固定 / 排除控制

通过 `pinned_memories` 表，用户可以：
- **固定（pin）**：指定某条记忆条目强制始终注入，不受 Top-K 限制
- **排除（exclude）**：永久从注入队列中屏蔽某条记忆条目

UI 支持在记忆面板中直接右键操作固定/排除。

---

## 17 模块十四：一致性报告

### 17.1 四维扫描

`ConsistencyEngine.run_full_scan()` 同时运行以下四项检查：

| 检查维度 | 说明 | 默认严重度 |
|----------|------|-----------|
| 伏笔检测 | 状态为 `unresolved` 的所有伏笔 | Low |
| 世界状态 | 同实体同状态类型存在多个非超越式活跃值 | Medium |
| 时间线冲突 | 实体状态转移序列不连续（断层） | High |
| 角色行为 | 角色心理（恐惧/驱动力）在近期章节中无任何体现 | Low |

### 17.2 问题分级

每个问题归属三个严重度之一：

| 级别 | 含义 | UI 颜色 |
|------|------|---------|
| High | 直接矛盾，影响故事逻辑 | 红色 |
| Medium | 潜在不一致，可能影响读者体验 | 黄色 |
| Low | 待处理事项，改善叙事质量 | 蓝色 |

### 17.3 问题处置

每个问题可标记四种处置状态：

| 状态 | 说明 |
|------|------|
| `open` | 待处理 |
| `fixed` | 已修复 |
| `ignored` | 作者确认可忽略 |
| `exception` | 合理例外（有意为之的写法） |

标记时可附加备注说明处置理由。

### 17.4 扫描报告

每次扫描生成一条 `consistency_reports` 记录，包含高/中/低问题数量统计，历史报告可查看对比。

---

## 18 模块十五：章节工作流

### 18.1 标准六步工作流

```
Step 0: input_goal      → 用户输入章节创作目标（手动）
Step 1: generate_beats  → 节拍器生成场景序列
Step 2: draft           → 执笔者根据节拍生成正文
Step 3: validate        → 验证者进行四维校验
Step 4: summarize       → 摘要器生成章节摘要
Step 5: update_state    → 世界状态提取器更新状态
```

### 18.2 工作流模板

- 支持保存自定义工作流模板（`workflow_templates` 表）
- 模板可按用户或书籍范围创建
- 每个步骤可独立配置 `agent_name`、是否跳过、是否自动确认

### 18.3 工作流执行

**`execute_step(run_id, step_index, user_data)`：**

1. `input_goal` 步骤：直接写入用户输入，标记 completed
2. 智能体步骤：
   - 从已完成步骤中提取上下文（前情摘要式拼接）
   - 调用注册的 agent_runner
   - 写入结果预览（前 500 字）
3. 未注册智能体：标记 `skipped`

### 18.4 步骤状态

```
pending → running → completed
                 → failed
                 → skipped
```

每个步骤的结果持久化存储，支持中途断点恢复。

---

## 19 模块十六：导入导出

### 19.1 导出格式

| 格式 | 说明 |
|------|------|
| Markdown | 按章节标题分层，四级标题嵌套 |
| TXT | 纯文本，章节标题 + 正文 |
| EPUB | 完整电子书格式，包含封面和章节导航 |
| JSON 工作区 | 完整项目快照，包含所有元数据 |

**JSON 工作区包含内容：**
书籍元数据 · 文档树与正文 · 版本分支 · Lorebook · 实体图谱 · 章节摘要 · 大纲 · 伏笔池 · 世界状态 · 角色心理档案 · 人物历史档案

### 19.2 范围化导出（新增）

`/api/export/<book_id>/scoped` 支持三种范围：

| scope | 导出范围 |
|-------|----------|
| `book` | 整本书 |
| `volume` | 指定卷（含下属章节） |
| `chapter` | 单个章节节点 |

`include` 参数控制附加内容（可多选）：`content` / `summary` / `settings` / `timeline` / `history` / `versions`

### 19.3 导入格式

| 格式 | 解析逻辑 |
|------|----------|
| JSON | 完整工作区还原（含设定/历史等全部数据） |
| Markdown | 按 `#` / `##` / `###` 标题层级自动生成节点树 |
| TXT | 按「第X章」正则匹配切分；无章节标题时按段落等分为 10 份 |
| DOCX | 按 Word 标题样式（Heading 1/2/3）解析节点层级 |

### 19.4 导入预览

`/api/import/preview` 接口解析文件但不入库，返回：
- 文件名、总字符数
- 识别到的章节列表（标题 + 前 200 字摘要）

用户确认后再调用 `/api/import/file` 正式导入。

---

## 20 模块十七：统计面板

### 20.1 记录维度

每次智能体调用记录：

| 字段 | 说明 |
|------|------|
| agent_role | 智能体角色标识 |
| first_token_latency_ms | 首 token 延迟（毫秒）|
| total_duration_ms | 总生成时长（毫秒）|
| success | 是否成功 |
| retried | 是否重试过 |
| adopted | 内容是否被用户采纳 |
| prompt_tokens | 提示词 Token 数 |
| completion_tokens | 生成 Token 数 |

### 20.2 面板指标

**全局汇总卡片：**
- 总调用次数
- 整体成功率（%）
- 整体采纳率（%）
- 累计 Token 用量
- 估算费用（USD，按 $3/M 输入 + $15/M 输出 计算）

**按智能体明细表：**

| 列 | 含义 |
|----|------|
| 角色 | 智能体标识 |
| 调用次数 | 历史累计 |
| 成功率 | % |
| 重试率 | % |
| 采纳率 | % |
| 平均首 Token 延迟 | ms |
| 平均总延迟 | ms |
| 累计 Token | 输入+输出 |

### 20.3 采纳追踪

用户点击「采纳」按钮时，前端调用 `POST /api/stats/adopted`，后端调用 `StatsEngine.mark_adopted()` 将最近一条对应角色的调用记录的 `adopted` 字段置 1。

---

## 21 数据库架构

### 21.1 表清单（共 33 张）

**基础表（15 张）**

| 表名 | 说明 |
|------|------|
| `users` | 用户账号 |
| `models` | 模型配置 |
| `routing` | 全局任务路由 |
| `user_routing` | 用户级任务路由 |
| `generation_params` | 全局生成参数 |
| `user_generation_params` | 用户级生成参数 |
| `token_stats` | 旧版 Token 统计 |
| `books` | 书籍信息 |
| `nodes` | 文档节点树 |
| `node_contents` | 节点正文内容 |
| `versions` | 版本快照 |
| `outlines` | 节点大纲 |
| `lorebook` | 世界观条目 |
| `entity_graph` | 实体关系图谱 |
| `chapter_summaries` | 章节摘要 |

**叙事辅助表（5 张）**

| 表名 | 说明 |
|------|------|
| `foreshadowing` | 伏笔池 |
| `world_state` | 世界状态 |
| `character_psychology` | 角色心理档案 |
| `character_history` | 人物历史档案 |
| `user_routing` | （已计入基础）|

**新增功能表（18 张，本次迭代）**

| 表名 | 对应模块 |
|------|----------|
| `writing_rule_sets` | 写作规则中心 - 规则集 |
| `writing_rules` | 写作规则中心 - 规则条目 |
| `timeline_events` | 时间线事件台账 |
| `entity_state_transitions` | 实体状态转移记录 |
| `snapshots` | 快照表 |
| `recycle_bin` | 回收站 |
| `async_jobs` | 异步任务 |
| `job_logs` | 任务日志 |
| `memory_injection_logs` | 记忆注入日志 |
| `pinned_memories` | 固定/排除记忆 |
| `consistency_reports` | 一致性扫描报告 |
| `consistency_issues` | 一致性问题条目 |
| `workflow_templates` | 工作流模板 |
| `workflow_runs` | 工作流执行实例 |
| `enhanced_stats` | 增强统计数据 |

### 21.2 数据安全

- **字段加密**：API Key、书籍描述、Lorebook 内容、摘要等敏感字段使用 Fernet 对称加密
- **加密密钥**：本地 `.encryption_key` 文件，首次运行自动生成，请妥善保管
- **用户隔离**：所有书籍和设定数据通过 `user_id` 绑定，API 层强制访问权限校验

---

## 22 API 端点清单

### 认证
```
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
```

### 模型与参数
```
GET/POST/PUT /api/models
GET/POST     /api/routing
GET/POST     /api/generation-params
```

### 书籍与节点
```
GET/POST     /api/books
GET/PUT/DEL  /api/books/<book_id>
GET          /api/books/<book_id>/tree
POST         /api/nodes
GET/PUT/DEL  /api/nodes/<node_id>
GET/PUT      /api/nodes/<node_id>/content
GET/POST     /api/nodes/<node_id>/outline
```

### 版本管理
```
GET/POST /api/versions/<node_id>
PUT      /api/versions/<version_id>
```

### 世界观与角色
```
GET/POST/PUT/DEL  /api/lorebook/<book_id>/<entry_id>
GET/POST          /api/entity-graph/<book_id>
GET/POST          /api/psychology/<book_id>
GET/POST          /api/world-state/<book_id>
GET/POST/PUT/DEL  /api/character-history/<book_id>
GET/POST/PUT/DEL  /api/foreshadowing/<book_id>
```

### 多智能体
```
POST /api/agent/plan          (流式)
POST /api/agent/beats
POST /api/agent/draft         (流式)
POST /api/agent/draft-guarded (流式，含守卫)
POST /api/agent/continue      (流式)
POST /api/agent/continue-fast (流式)
POST /api/agent/autocomplete
POST /api/agent/validate
POST /api/agent/polish
POST /api/agent/summarize
POST /api/agent/hallucination-check
POST /api/agent/plan-and-solve (流式)
POST /api/agent/conflict
POST /api/agent/psychology
POST /api/agent/subtext
POST /api/agent/worldstate-extract
POST /api/agent/foreshadow-detect
POST /api/agent/foreshadow-scan
POST /api/agent/brainstorm
POST /api/inline-command       (流式)
```

### 记忆系统
```
GET  /api/memory/summary/<book_id>
POST /api/memory/vectorize/<book_id>
POST /api/memory/retrieve
POST /api/memory/inject
GET  /api/memory/status/<book_id>/<node_id>
POST /api/character-reminders
POST /api/character-history/<book_id>/refresh
POST /api/character-history/<book_id>/refresh-all
GET  /api/memory/injection-log/<book_id>
GET/POST /api/memory/pin/<book_id>
DEL  /api/memory/pin/<book_id>/<pin_id>
```

### 写作规则中心
```
GET/POST     /api/rules/<book_id>/sets
PUT/DEL      /api/rules/<book_id>/sets/<set_id>
GET/POST     /api/rules/<book_id>/rules
PUT/DEL      /api/rules/<book_id>/rules/<rule_id>
GET          /api/rules/<book_id>/active
POST         /api/rules/<book_id>/validate
GET          /api/rules/<book_id>/conflicts
```

### 时间线
```
GET          /api/timeline/<book_id>
POST         /api/timeline/<book_id>/events
PUT/DEL      /api/timeline/<book_id>/events/<event_id>
POST         /api/timeline/<book_id>/extract
POST         /api/timeline/<book_id>/detect-conflicts
GET          /api/timeline/<book_id>/transitions
```

### 快照与回收站
```
GET/POST     /api/snapshots/<book_id>
GET          /api/snapshots/<snapshot_id>/preview
POST         /api/snapshots/<snapshot_id>/restore
GET          /api/recycle-bin/<book_id>
POST         /api/recycle-bin/<recycle_id>/restore
DEL          /api/recycle-bin/<recycle_id>
```

### 全局搜索
```
POST /api/search/<book_id>
POST /api/search/<book_id>/replace
GET  /api/search/<book_id>/references
GET  /api/search/<book_id>/chapter-refs/<node_id>
```

### 异步任务
```
GET  /api/jobs
GET  /api/jobs/<job_id>
POST /api/jobs/<job_id>/cancel
POST /api/jobs/<job_id>/retry
```

### 一致性报告
```
POST /api/consistency/<book_id>/scan
GET  /api/consistency/<book_id>/reports
GET  /api/consistency/<book_id>/reports/<report_id>
PUT  /api/consistency/issues/<issue_id>
```

### 章节工作流
```
GET/POST /api/workflow/templates
POST     /api/workflow/run
GET      /api/workflow/run/<run_id>
POST     /api/workflow/run/<run_id>/step/<step_idx>
POST     /api/workflow/run/<run_id>/confirm/<step_idx>
```

### 导入导出
```
GET  /api/export/<book_id>/<fmt>          (markdown/txt/epub/json)
GET  /api/export/<book_id>/scoped         (范围化导出)
POST /api/import                          (JSON 工作区导入)
POST /api/import/preview                  (预览解析结果)
POST /api/import/file                     (MD/TXT/DOCX 导入)
```

### 统计
```
GET  /api/stats/enhanced
POST /api/stats/adopted
```

### 其他
```
POST /api/lookup           (划词查询)
GET  /api/diagnostics      (系统诊断)
```

---

## 23 前端界面说明

### 23.1 布局结构

**三栏 + 顶栏 + 模态层**

```
┌──────────────────────────────────────────────────────┐
│  顶栏：书名 · 全局搜索 · 一致性检查 · 任务中心 · 导入 │
│         · 导出 · 专注模式 · 设置 · 用户              │
├──────────┬─────────────────────────┬────────────────┤
│  左栏    │        中栏              │    右栏         │
│          │  ┌──────────────────┐   │                │
│ [书籍]   │  │   正文编辑器      │   │  [规划组]      │
│ [目录树] │  │   + Ghost Text   │   │  [写作组]      │
│  ·卷     │  │   + Slash 指令   │   │  [验证组]      │
│  ·章     │  └──────────────────┘   │  [分析组]      │
│  ·场景   │  工具栏: 快照·工作流     │                │
│ [世界观] │  ·版本·字数·自动补全     │  === 记忆面板 ===│
│ [图谱]   │                         │  Tier 状态     │
│ [时间线] │                         │  向量检索      │
│          │                         │  人物提醒      │
│          │                         │  章节摘要      │
└──────────┴─────────────────────────┴────────────────┘
```

### 23.2 模态弹窗（共 15 个）

| ID | 名称 | 触发 |
|----|------|------|
| `authModal` | 登录/注册 | 未认证时自动弹出 |
| `settingsModal` | 设置中心（含规则/统计标签页）| 顶栏设置图标 |
| `newBookModal` | 新建书籍 | 书籍列表 + 按钮 |
| `lorebookModal` | Lorebook 管理 | 左栏「世界观」 |
| `relationModal` | 实体关系编辑 | 图谱节点 |
| `diffModal` | 版本 Diff 对比 | 版本列表 |
| `exportModal` | 导出格式选择 | 顶栏导出按钮 |
| `searchModal` | 全局搜索与替换 | 顶栏搜索图标 |
| `consistencyModal` | 一致性报告 | 顶栏一致性图标 |
| `jobModal` | 异步任务中心 | 顶栏任务图标（含 Badge）|
| `workflowModal` | 章节工作流 | 编辑器工具栏 |
| `snapshotModal` | 快照与回收站 | 编辑器工具栏 |
| `importModal` | 文件导入预览 | 顶栏导入按钮 |
| `ruleEditModal` | 规则编辑 | 规则设置面板 |
| `injectionLogModal` | 记忆注入日志 | 记忆面板「解释」按钮 |

### 23.3 智能体分组界面

右栏智能体工作台按任务性质分为四个标签组，点击标签切换可见的智能体按钮：

```
[规划] [写作] [验证] [分析]
 ↓当前激活组内的 Agent 按钮列表
```

### 23.4 编辑器功能

| 功能 | 描述 |
|------|------|
| 自动保存 | 停止输入 2 秒后触发 |
| Ghost Text | 停止输入 800ms 后显示 AI 预测，Tab 采纳 |
| Slash 指令 | 输入 `/` 弹出指令选择面板 |
| 字数统计 | 底部实时显示当前章节字数 |
| 版本快照 | 工具栏手动保存快照 |
| 工作流 | 一键启动六步章节工作流 |
| 专注模式 | 隐藏左右栏，仅保留编辑区 |

---

## 24 已知限制与扩展方向

### 24.1 已知限制

| 限制 | 说明 |
|------|------|
| 人物识别依赖 Lorebook | 不是通用 NER，别名需手工在 keywords 字段补充 |
| 向量检索为 TF-IDF | 非神经嵌入，跨语言/语义相似度能力有限 |
| 所有 AI 能力依赖外部模型 | 无模型配置时生成类功能不可用 |
| 摘要质量影响历史沉淀 | 无摘要时角色历史质量下降，但仍可手工维护 |
| 单机单库 | SQLite 不适合高并发多用户部署 |
| DOCX 导入需 python-docx | 未安装时降级跳过，不报错 |

### 24.2 推荐扩展方向

1. **角色消歧与 NER**：基于 NER 模型替代关键词匹配，提升别名和代词识别能力
2. **神经向量嵌入**：接入 text-embedding 模型替代 TF-IDF，提升语义检索精度
3. **知识图谱增强**：从正文自动抽取结构化事件和关系，构建可查询的知识图
4. **多人协作**：基于 WebSocket 和行级锁实现多用户实时协作编辑
5. **自动回填伏笔**：在 payoff 时自动回溯找到初始伏笔位置并标记为 resolved
6. **情节弧光可视化**：基于章节摘要的情感/张力时序图
7. **批量并行生成**：JobEngine 扩展支持多章节并行草稿生成
8. **PostgreSQL 迁移**：将 database.py 改写为 SQLAlchemy ORM 支持 PG，提升并发性能
