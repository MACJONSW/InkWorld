# 墨境 · AI 长篇小说写作平台

墨境是一个面向长篇小说创作的 AI 辅助写作工作台。它将目录管理、世界观设定、章节写作、多智能体协作、三层记忆、伏笔追踪、角色心理分析、世界状态校验和工作区导入导出整合在同一个 Web 界面里，适合用于中长篇网文、类型小说和复杂多线叙事项目。

本仓库当前版本已经支持“人物历史提醒”能力：当你在新章节中提到某个角色时，系统会根据该角色的 Lorebook 设定、历史事件记录、未回收伏笔、角色心理和世界状态自动生成提醒，并在 AI 写作提示词和右侧记忆面板中同时展示，帮助你避免角色崩坏、遗忘伏笔或时间线错乱。

如果你需要查看底层实现细节、运行时架构和各个智能体的执行流程，请直接阅读 [TECHNICAL.md](TECHNICAL.md)。

如果你准备做二次开发，例如新增智能体、接入新的记忆模块或扩展模型路由，请直接阅读 [EXTENDING.md](EXTENDING.md)。

## 核心能力

- 多智能体写作
  - 架构师：生成整本书或卷章大纲
  - 节拍器：把章节拆成场景节拍
  - 执笔者：根据前文、节拍和上下文流式写作
  - 验证者：检查 OOC、时间线和设定一致性
  - 润色器：根据风格要求润色文本
  - 摘要器：生成章节摘要并沉淀长期记忆
  - 续写、自动补全、联想、Plan 模式、幻觉检测等扩展 Agent
- 三层记忆系统
  - Tier 1：当前章节工作记忆
  - Tier 2：滚动章节摘要
  - Tier 3：向量检索记忆（TF-IDF，支持 FAISS）
- 世界观与角色资料管理
  - Lorebook 支持角色、地点、物品、派系、法则
  - 实体关系图谱
  - 角色心理档案
  - 世界状态追踪
- 叙事辅助
  - 伏笔检测与伏笔池管理
  - 潜台词分析
  - 情绪张力曲线诊断
  - 幻觉检测与自动重试
- 写作工程能力
  - 目录树：书 > 卷 > 章 > 场景
  - 章节内容自动保存
  - 版本分支与 Diff
  - Markdown / TXT / EPUB / JSON 工作区导出
  - JSON 工作区导入
- 新增：人物历史提醒
  - 自动识别当前章节提及的人物
  - 聚合该人物的性格、驱动力、近期事件、未回收伏笔和当前状态
  - 提醒既会出现在右侧记忆面板，也会自动注入执笔、续写、自动补全和行内编辑的提示词
  - 支持手工添加、编辑、删除人物历史记录
  - 支持单章节刷新和全书回填

## 界面概览

应用是一个三栏写作工作台：

- 左栏：目录树、Lorebook、实体图谱
- 中栏：正文编辑器、Slash 指令、Ghost Text 自动补全、版本和 Diff
- 右栏：智能体工作台、记忆面板、章节摘要、向量检索、人物提醒

## 技术栈

- 后端：Flask、Flask-CORS、Flask-SocketIO
- 数据库：SQLite
- 模型接入：OpenAI 兼容接口，支持自定义 Base URL
- 向量/检索：NumPy、scikit-learn，可选 FAISS
- 前端：原生 HTML / CSS / JavaScript
- 导出：Markdown、TXT、EPUB、JSON

## 项目结构

```text
agents.py           多智能体编排与提示词构建
app.py              Flask API 入口、认证、导入导出、诊断接口
database.py         SQLite schema 与持久化访问层
memory_engine.py    三层记忆、动态注入、人物提醒聚合
export_engine.py    Markdown/TXT/EPUB/JSON 导入导出
requirements.txt    Python 依赖
static/
  css/style.css     前端样式
  js/app.js         前端应用逻辑
templates/
  index.html        主界面模板
novel_platform.db   运行后生成的 SQLite 数据库
.encryption_key     运行后生成的本地加密密钥
```

## 快速开始

### 1. 环境要求

建议使用：

- Python 3.10 或更高版本
- Linux / macOS / Windows
- 可访问的 LLM API（OpenAI 兼容接口即可）

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 设置环境变量

最少建议配置一个安全的应用密钥：

```bash
export APP_SECRET_KEY="replace-this-with-a-random-secret"
```

如果不设置，应用会使用开发默认值，仅适合本地试验。

### 4. 启动服务

```bash
python app.py
```

默认监听：

- http://127.0.0.1:5000
- http://0.0.0.0:5000

### 5. 首次使用

1. 打开浏览器访问应用。
2. 注册或登录。
3. 进入“设置中心”，添加模型配置。
4. 配置任务路由，把不同角色分配到对应模型。
5. 创建一本书并开始写作。

## 模型配置说明

应用不在 `.env` 中硬编码模型配置，而是采用“用户登录后在 UI 中管理模型”的方式。

在“设置中心”中，你可以配置：

- 名称
- 提供商
- Base URL
- API Key
- 模型标识
- 最大上下文

然后再把模型分配给不同角色，例如：

- `planner`
- `beat_generator`
- `drafter`
- `validator`
- `polisher`
- `summarizer`
- `autocomplete`
- `association`
- `plan_and_solve`
- `hallucination`

如果某个角色没有显式路由，系统会回退到当前用户的第一个模型配置。

## 主要写作流程

### 从灵感到正文

1. 创建书籍。
2. 用“架构师”生成大纲。
3. 用“节拍器”拆出场景节拍。
4. 在目录树中建立卷 / 章 / 场景结构。
5. 在编辑器中写正文，或用“执笔者”“续写”“深度生成”协助生成内容。
6. 用“验证者”与“幻觉检测”检查一致性。
7. 用“摘要器”生成章节摘要并沉淀记忆。
8. 用“伏笔检测”“心理分析”“潜台词分析”等工具优化叙事质量。

### 角色持续写作流程

推荐在每章完成后做两件事：

1. 生成章节摘要。
2. 刷新当前章节人物历史。

这样下一章提到角色时，系统就能更稳定地提醒：

- 这个人物是什么性格
- 之前做过什么关键行动
- 现在处于什么状态
- 还有哪些伏笔没有回收

## 人物历史提醒机制

这是本仓库当前版本最重要的新增能力之一。

### 提醒数据来源

人物提醒不是单一表，而是以下信息的聚合结果：

- Lorebook 角色条目：角色基础设定、性格、背景、关键词
- 角色心理档案：驱动力、恐惧、防御机制、核心矛盾
- 人物历史档案：角色在各章节的关键事件记录，可自动生成也可手工维护
- 伏笔池：与该角色相关、尚未回收的伏笔
- 世界状态：位置、关系、身体状态、物品状态等

### 如何触发提醒

系统会在这些场景里自动识别当前文本提到的人物，并构建提醒：

- 切换到某个章节时
- 编辑器输入时的侧边刷新
- 自动补全前
- 执笔者生成前
- 智能续写前
- 行内改写 / 扩写 / 精简等指令执行前
- 幻觉检测构建 premise 时

### 人物历史如何生成

人物历史支持两种来源：

- 自动生成
  - 通过章节内容和章节摘要识别本章出现的角色
  - 为每个角色写入一条与章节关联的事件记录
- 手工维护
  - 在右侧“人物提醒”面板中添加、编辑、删除人物记录
  - 适合补充重要设定、角色弧线、暗线、特殊伏笔

### 何时回填

对于已经写了很多章节的旧书，建议点一次：

- “回填全书”

它会遍历已有章节内容，为书中角色补齐基础事件记录。

### 第一版实现边界

当前版本的人物识别主要依赖：

- Lorebook 的角色名
- Lorebook 的关键词字段

如果一个角色有别名、称号、绰号，建议把这些变体写进 `keywords`，这样提醒命中会更稳定。

## 三层记忆系统

### Tier 1：工作记忆

- 从当前编辑章节截取最近一段正文
- 作为当前写作最直接的局部上下文

### Tier 2：滚动摘要

- 每章摘要写入数据库
- 便于长线写作时快速回顾前情

### Tier 3：向量检索

- 收集 Lorebook、章节摘要和正文切片
- 使用 TF-IDF 建立索引
- 如果环境安装了 FAISS，会自动使用 FAISS 加速检索

### 动态注入

写作时系统会根据当前文本匹配 Lorebook 条目，把相关设定动态注入提示词，而不是把全部设定一次性塞进上下文。

## 伏笔、心理与世界状态

### 伏笔系统

支持：

- 自动检测伏笔
- 保存到伏笔池
- 标记已回收 / 未回收
- 基于当前章节扫描哪些伏笔适合 payoff

### 角色心理档案

支持为角色记录：

- 驱动力
- 恐惧
- 防御机制
- 潜台词风格
- 核心矛盾

### 世界状态

系统可记录和校验：

- 时间
- 位置
- 物品状态
- 身体状态
- 关系变化

这些内容也会参与幻觉检测和人物提醒聚合。

## 导入导出

支持以下格式：

- Markdown
- TXT
- EPUB
- JSON 工作区

其中：

- Markdown / TXT / EPUB 用于导出最终内容阅读或排版
- JSON 工作区用于完整迁移项目数据

JSON 工作区导出内容包括：

- 书籍元数据
- 文档树与正文内容
- 版本分支
- Lorebook
- 实体图谱
- 章节摘要
- 大纲
- 伏笔池
- 世界状态
- 角色心理档案
- 人物历史档案

## 主要 API 概览

这里只列最常用的接口分组。

### 认证

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`

### 书籍与目录

- `GET /api/books`
- `POST /api/books`
- `GET /api/books/<book_id>/tree`
- `POST /api/nodes`
- `GET /api/nodes/<node_id>/content`
- `PUT /api/nodes/<node_id>/content`

### Lorebook / 图谱

- `GET /api/lorebook/<book_id>`
- `POST /api/lorebook/<book_id>`
- `GET /api/entity-graph/<book_id>`
- `POST /api/entity-graph/<book_id>`

### 记忆与检索

- `GET /api/memory/summary/<book_id>`
- `POST /api/memory/vectorize/<book_id>`
- `POST /api/memory/retrieve`
- `POST /api/memory/inject`
- `GET /api/memory/status/<book_id>/<node_id>`
- `POST /api/character-reminders`

### 人物历史

- `GET /api/character-history/<book_id>`
- `POST /api/character-history/<book_id>`
- `POST /api/character-history/<book_id>/refresh`
- `PUT /api/character-history/<book_id>/<history_id>`
- `DELETE /api/character-history/<book_id>/<history_id>`

### 多智能体

- `POST /api/agent/plan`
- `POST /api/agent/beats`
- `POST /api/agent/draft`
- `POST /api/agent/draft-guarded`
- `POST /api/agent/continue`
- `POST /api/agent/continue-fast`
- `POST /api/agent/autocomplete`
- `POST /api/agent/validate`
- `POST /api/agent/polish`
- `POST /api/agent/summarize`
- `POST /api/inline-command`
- `POST /api/agent/plan-and-solve`
- `POST /api/agent/hallucination-check`

### 伏笔 / 心理 / 世界状态

- `GET /api/foreshadowing/<book_id>`
- `POST /api/agent/foreshadow-detect`
- `POST /api/agent/foreshadow-scan`
- `GET /api/psychology/<book_id>`
- `POST /api/psychology/<book_id>`
- `GET /api/world-state/<book_id>`
- `POST /api/world-state/<book_id>`
- `POST /api/agent/world-state-extract`
- `POST /api/agent/world-state-validate`

### 导入导出

- `GET /api/export/<book_id>/<fmt>`
- `POST /api/import`

## 数据存储说明

本地运行后会生成：

- `novel_platform.db`：SQLite 主数据库
- `.encryption_key`：用于加密敏感字段的本地密钥文件

加密字段包括：

- API Key
- 书籍描述等敏感文本
- Lorebook 内容
- 摘要、心理档案、人物历史等部分文本数据

请不要随意丢失 `.encryption_key`，否则旧数据中的加密字段可能无法正常解密。

## 开发说明

### 代码分层建议

- `database.py` 负责 schema 与 CRUD
- `memory_engine.py` 负责记忆与上下文聚合
- `agents.py` 负责提示词与模型交互
- `app.py` 负责 API 编排与权限控制
- `static/js/app.js` 负责页面状态和交互逻辑

### 适合优先扩展的方向

- 更强的人名别名识别与角色消歧
- 自动从正文中抽取更细粒度的人物事件
- 更稳定的结构化摘要与结构化角色事件抽取
- 人物关系时间线视图
- 更细致的冲突与弧光追踪
- 多人协作与评论系统

## 已知限制

- 人物识别第一版主要依赖角色名和 Lorebook 关键词，不是通用 NER。
- 人物历史的自动生成目前偏摘要型，适合辅助提醒，不等于严格的知识图谱。
- 向量检索默认使用 TF-IDF，不是神经嵌入模型；更轻量，但语义能力有限。
- 若未生成章节摘要，角色历史自动沉淀的质量会下降，但仍可使用手工记录和章节刷新。
- 大部分 AI 能力依赖外部模型服务，模型未配置时相关功能无法工作。

## 推荐使用习惯

为了让系统的长期记忆最稳定，建议按下面的节奏写作：

1. 为主要角色建立 Lorebook 条目，并在 `keywords` 中写入别名。
2. 每完成一章就生成摘要。
3. 每完成关键章节就刷新一次当前章节人物历史。
4. 定期检查未回收伏笔和世界状态。
5. 在大转折前先运行一次幻觉检测和一致性验证。

## 许可证

仓库中暂未提供单独的许可证文件。如需开源发布，请补充明确的 LICENSE。
