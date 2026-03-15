# 墨境 · AI 长篇小说写作平台

墨境是一个面向长篇小说创作的 AI 写作工作台。它把目录树、正文编辑器、多智能体协作、三层记忆、世界状态、时间线、伏笔追踪、角色心理、知识图谱、快照/回收站、工作流、全书体检和导入导出整合在一个 Flask 单体应用里。

这份 README 的目标不是只告诉你“怎么跑起来”，而是把下面几件事都讲清楚：

1. 这个仓库当前的真实技术架构是什么。
2. 你需要准备哪些外部依赖，才能把“所有功能”都跑起来。
3. 应用本身应该怎么部署。
4. Embedding 模型应该怎么部署、怎么接进系统。
5. 当前代码里有哪些会直接影响部署方案的约束。

如果你要看更细的实现细节，请继续阅读：

- [TECHNICAL.md](TECHNICAL.md)：内部技术实现、模块调用链、数据流
- [FEATURES.md](FEATURES.md)：功能盘点与数据库/API清单
- [EXTENDING.md](EXTENDING.md)：二次开发与新增 Agent/记忆模块

## 1. 当前版本的真实技术架构

### 1.1 总体结构

```text
Browser
  ├─ templates/index.html
  ├─ static/js/app.js
  └─ static/css/style.css
        │
        │ HTTP / SSE
        ▼
Flask app (app.py)
  ├─ JWT 认证 / 资源权限校验
  ├─ 模型配置 / 路由 / 生成参数
  ├─ 书籍 / 节点 / 版本 / 快照 / 回收站
  ├─ Agent API / SSE 流式生成
  ├─ 记忆 / Embedding / NER / 图谱 / 时间线
  ├─ 一致性扫描 / 工作流 / 任务中心 / 统计
  └─ 导入 / 导出
        │
        ├─ AgentOrchestrator (agents.py)
        ├─ MemoryEngine (memory_engine.py)
        ├─ EmbeddingEngine (embedding_engine.py)
        ├─ NEREngine / DisambiguationEngine
        ├─ KnowledgeGraphEngine / ForeshadowEngine / NarrativeEngine
        ├─ RuleEngine / TimelineEngine / SnapshotEngine
        ├─ SearchEngine / WorkflowEngine / ConsistencyEngine / StatsEngine
        ├─ JobEngine
        └─ ExportEngine
                │
                ▼
          SQLite (novel_platform.db)
          + .encryption_key
          + .jwt_secret
```

### 1.2 当前部署形态的关键事实

这几个事实会直接影响你的部署方式：

- 这是一个单体 Flask 应用，没有拆成前后端分离、没有 Redis、没有 Celery、没有消息队列。
- 异步任务中心由 `job_engine.py` 在当前进程里起后台线程完成，不是外部 worker。
- Embedding 索引和 TF-IDF / FAISS 索引有一部分驻留在应用进程内存里。
- 数据库存储是单文件 SQLite：`novel_platform.db`。
- 关键密钥文件是本地文件：`.encryption_key` 和 `.jwt_secret`。
- 核心流式生成靠 SSE，不是 WebSocket；`Flask-SocketIO` 目前主要是预留能力。

这意味着当前版本**最适合单机单进程部署**。如果你上多 worker、多副本或多节点，需要自己处理：

- SQLite 并发与锁竞争
- 进程内后台任务状态不共享
- 进程内 Embedding / FAISS 缓存不共享
- 密钥文件分发与一致性

## 2. 功能与外部依赖矩阵

下面这张表是“所有功能要跑起来到底需要什么”的总表。

| 功能 | 必需依赖 | 可选/增强依赖 | 说明 |
| --- | --- | --- | --- |
| 登录、书籍、目录树、编辑器、自动保存、版本、快照、回收站 | Python + SQLite | 无 | 这些不依赖大模型 |
| 架构师 / 节拍器 / 执笔者 / 续写 / Plan 模式 / 润色 / 自动补全 | OpenAI 兼容聊天模型 | 分角色多模型 | 由 `agents.py` 调用 `chat.completions` |
| 验证者 / 幻觉检测 / 世界状态提取 / NER / 关系抽取 / 事件抽取 / 潜台词 / 心理分析 / 叙事分析 | OpenAI 兼容聊天模型 | 单独 validator / hallucination 模型 | 这些高级能力大多复用 `validator` 或 `hallucination` 路由 |
| Tier 1 / Tier 2 记忆 | 数据库内正文与摘要 | 无 | 不依赖 embedding |
| Tier 3 检索（基础版） | `scikit-learn` TF-IDF | `rank-bm25`、`jieba` | 这些依赖已在 `requirements.txt` |
| Tier 3 检索（语义增强） | OpenAI 兼容 `/v1/embeddings` 服务 | `faiss-cpu` | `embedding_engine.py` 会调用 embedding API |
| Embedding 检索加速 | 无 | `faiss-cpu` | 未安装时退回 NumPy 余弦相似度 |
| 实体图谱可视化 | 浏览器可访问 `vis-network` 资源 | 本地自托管静态资源 | 当前模板默认走 CDN |
| 字体/图标 | 浏览器可访问 Google Fonts / Font Awesome | 本地自托管静态资源 | 内网或离线环境要自己落地 |
| EPUB 导出 | `ebooklib` | 无 | 已在 `requirements.txt` |
| DOCX 导入 | `python-docx` | 无 | 已在 `requirements.txt` |

## 3. 代码里对部署最关键的约束

### 3.1 模型 Base URL 不能直接填本地地址

`app.py::_validate_base_url()` 会拒绝下面这些主机名：

- `localhost`
- `127.0.0.1`
- `0.0.0.0`
- `10.*`
- `192.168.*`
- `172.16.*` 到 `172.31.*`

这意味着：

- 你不能在设置页里直接填 `http://127.0.0.1:11434/v1`
- 你不能直接填 `http://192.168.1.50:8000/v1`

如果你要部署本地或内网模型服务，正确方式是：

1. 先把模型服务跑在本机或内网。
2. 用 Nginx / Caddy / Traefik 反代到一个域名，例如 `https://llm.example.com/v1`。
3. 在墨境里填这个域名。

### 3.2 `POST /api/routing` 会覆盖整张路由表

`database.py::set_routing()` 的逻辑是先删当前用户所有路由，再重新写入。  
因此如果你手工调用 `POST /api/routing` 去补 `embedding` 路由，**必须把已有路由一起带上**，不能只发一条：

```json
{"embedding":"..."}
```

否则你会把原来的 `planner`、`drafter`、`validator` 等全部清空。

### 3.3 设置页当前没有暴露 `embedding` 路由

前端设置页的“任务路由”只覆盖了这些角色：

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

但是 `embedding_engine.py` 实际查的是：

- `embedding`

也就是说，**Embedding 路由目前要用 API 手工补**，后面 README 会给命令示例。

### 3.4 当前版本建议单进程部署

推荐原因：

- SQLite 是单文件数据库。
- `JobEngine` 使用进程内线程跑后台任务。
- Embedding / FAISS / BM25 缓存驻留在当前进程。
- 没有 Redis / MQ / 外部任务调度器。

如果你直接上多进程 Gunicorn、多副本容器，系统不会立刻崩，但会出现：

- 任务状态分散
- 检索缓存重复构建
- 部分请求打到不同 worker 时感知不一致

## 4. 环境要求

建议环境：

- Python `3.10+`
- Linux（推荐）；macOS / Windows 也能本地运行
- 一台能访问外部模型服务的机器，或你自己的 OpenAI 兼容模型服务
- 磁盘可写目录，用于：
  - `novel_platform.db`
  - `.encryption_key`
  - `.jwt_secret`
- 浏览器可访问这些静态资源域名，或者你自己把它们改成本地文件：
  - `fonts.googleapis.com`
  - `fonts.gstatic.com`
  - `cdnjs.cloudflare.com`
  - `unpkg.com`

可选增强：

- `faiss-cpu`：更快的向量检索

## 5. 本地启动

### 5.1 安装依赖

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

如果你要启用 FAISS：

```bash
pip install faiss-cpu
```

### 5.2 配置环境变量

最少建议设置下面三个：

```bash
export APP_SECRET_KEY="$(python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
export ALLOWED_ORIGINS="http://localhost:5000"
export FLASK_DEBUG=0
```

说明：

- `APP_SECRET_KEY`
  - JWT 签名密钥
  - 如果不设置，应用会自动生成 `.jwt_secret`
- `ALLOWED_ORIGINS`
  - CORS 允许源
  - 默认值是 `http://localhost:5000`
- `FLASK_DEBUG`
  - `1` 表示 debug 模式
  - 生产环境请保持 `0`

### 5.3 启动应用

```bash
python app.py
```

默认监听：

- `http://127.0.0.1:5000`
- `http://0.0.0.0:5000`

### 5.4 首次进入系统后的最小步骤

1. 打开浏览器访问 `http://127.0.0.1:5000`
2. 注册用户
3. 进入“设置中心”
4. 添加至少一个聊天模型
5. 配置任务路由
6. 创建书籍并开始使用

## 6. 生产部署建议：单机 + systemd + Nginx

当前代码最稳妥的生产方式是：

- 1 个应用进程
- 1 个 SQLite 文件
- 1 份密钥文件
- Nginx 反向代理

### 6.1 目录示例

```text
/srv/inkworld/
  app.py
  database.py
  agents.py
  ...
  static/
  templates/
  .venv/
  novel_platform.db
  .encryption_key
  .jwt_secret
```

### 6.2 systemd 服务示例

创建 `/etc/systemd/system/inkworld.service`：

```ini
[Unit]
Description=InkWorld Flask App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/inkworld
Environment=APP_SECRET_KEY=replace-with-a-long-random-secret
Environment=ALLOWED_ORIGINS=https://novel.example.com
Environment=FLASK_DEBUG=0
ExecStart=/srv/inkworld/.venv/bin/python /srv/inkworld/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now inkworld
sudo systemctl status inkworld
```

### 6.3 Nginx 反向代理示例

创建站点配置：

```nginx
server {
    listen 80;
    server_name novel.example.com;
    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }
}
```

注意：

- `proxy_buffering off` 对 SSE 很重要。
- `client_max_body_size 50m` 要和应用的 `MAX_CONTENT_LENGTH = 50MB` 对齐。

### 6.4 为什么这里不推荐直接多 worker

因为当前版本不是“无状态 API + 外部 worker + 外部缓存”的架构，而是：

- 应用进程里有线程任务
- 应用进程里有 Embedding/FAISS 缓存
- 数据库是 SQLite

所以当前 README 明确推荐：**单机、单应用实例**。

## 7. 模型接入与任务路由

### 7.1 应用支持什么模型接口

这个仓库的模型调用方式很统一：

- 聊天模型：OpenAI 兼容 `chat.completions`
- Embedding 模型：OpenAI 兼容 `embeddings`

也就是说，只要你的模型服务满足：

- `base_url`
- `api_key`
- `model_id`
- OpenAI 兼容 JSON 协议

就能被接进来。

### 7.2 最低可用模型配置

最小可用方案：

- 1 个通用聊天模型

这时所有角色都会回退到这一个模型上运行，系统能工作，但体验一般。

更稳妥的方案：

| 路由角色 | 推荐用途 |
| --- | --- |
| `planner` | 整书/章节规划、冲突设计、伏笔扫描 |
| `beat_generator` | 节拍拆分 |
| `drafter` | 正文生成、行内编辑、续写 |
| `validator` | 校验、NER、关系抽取、事件抽取、潜台词、心理、世界状态、叙事分析 |
| `polisher` | 润色 |
| `summarizer` | 章节摘要 |
| `autocomplete` | Ghost Text 自动补全 |
| `association` | 头脑风暴 |
| `plan_and_solve` | 分阶段写作 |
| `hallucination` | 幻觉检测 |
| `embedding` | 向量语义检索 |

### 7.3 高级功能实际复用哪些路由

这点很重要，因为前端看起来有很多“功能名”，但后端不一定每个都单独占一条模型路由。

| 功能 | 实际调用的角色路由 |
| --- | --- |
| 冲突设计 | `planner` |
| 伏笔检测 | `validator` |
| 伏笔回收扫描 | `planner` |
| 潜台词分析 | `validator` |
| 心理分析 | `validator` |
| 世界状态提取 / 校验 | `validator` 或 `hallucination` |
| NER / 共指 / 关系 / 事件抽取 | `validator` |
| 叙事分析 | `validator` |
| 幻觉检测 | `hallucination` |

如果你只配了基础的 `planner / drafter / validator / hallucination`，大多数高级功能都能工作。

## 8. Embedding 模型部署

### 8.1 代码到底要求什么

`embedding_engine.py` 的要求很明确：

- 查找当前用户的 `embedding` 路由
- 用 OpenAI Python SDK 初始化客户端
- 调用 `client.embeddings.create(model=model_id, input=batch)`

所以你的 Embedding 服务必须满足：

1. 有 OpenAI 兼容的 `/v1/embeddings`
2. 返回标准 embedding 向量数组
3. Base URL 能通过应用的 SSRF 校验

### 8.2 如果不部署 Embedding，会发生什么

不会导致整站不可用。

不部署 Embedding 时：

- Tier 1 / Tier 2 记忆照常工作
- `memory_engine.py` 仍会用 TF-IDF + BM25 + 可选 FAISS 跑检索
- 但 `embedding_engine.py` 的语义检索、Embedding 索引构建、语义 chunk recall 不可用

换句话说：

- **Embedding 是增强项，不是整站硬依赖**

### 8.3 方案 A：直接接云端 Embedding API

如果你已经有一个兼容 OpenAI Embeddings 的远程服务，这是最简单的做法：

1. 在“设置中心 -> 模型管理”里新增一条模型
2. `base_url` 填服务地址，例如 `https://api.example.com/v1`
3. `model_id` 填你的 embedding 模型名
4. 后面再把它绑定到 `embedding` 路由

优点：

- 不需要单独运维本地模型
- 不会遇到 `localhost` 被阻止的问题

### 8.4 方案 B：自建 vLLM Embedding 服务

如果你要自己部署 Embedding 模型，并希望走 OpenAI 兼容协议，vLLM 是一个适配度较高的选择。官方文档提供了 Embedding API 和 OpenAI 兼容服务说明：

- Embeddings API: `https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#embeddings-api`
- Pooling / Embedding runner: `https://docs.vllm.ai/en/latest/models/supported_models.html`

部署思路：

1. 在模型机上安装 vLLM
2. 使用支持 embedding / pooling 的模型启动服务
3. 以 OpenAI 兼容方式暴露 `/v1`
4. 再通过域名反代到外部可访问地址，例如 `https://embed.example.com/v1`

示意命令（具体参数以官方文档为准）：

```bash
vllm serve <your-embedding-model> --runner pooling --host 127.0.0.1 --port 8001
```

然后再用 Nginx 暴露：

```nginx
server {
    listen 80;
    server_name embed.example.com;

    location /v1/ {
        proxy_pass http://127.0.0.1:8001/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 8.5 方案 C：自建 Ollama Embedding 服务

如果你已经在用 Ollama，也可以接。关键点是：**墨境需要的是 Ollama 的 OpenAI 兼容层 `/v1/embeddings`，不是原生 `/api/embed`。**

官方参考：

- Embeddings: `https://docs.ollama.com/capabilities/embeddings`
- OpenAI compatibility: `https://docs.ollama.com/openai`
- 官方 Embedding 模型介绍：`https://ollama.com/blog/embedding-models`

部署思路：

1. 安装并启动 Ollama
2. 拉取一个 embedding 模型
3. 使用其 OpenAI 兼容接口 `/v1`
4. 用域名反代后再填进墨境

示意：

```bash
ollama serve
ollama pull embeddinggemma
```

如果你把 Ollama 跑在本机 `127.0.0.1:11434`，不要直接把这个地址填进墨境；要先反代：

```nginx
server {
    listen 80;
    server_name ollama-api.example.com;

    location /v1/ {
        proxy_pass http://127.0.0.1:11434/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

然后在墨境模型管理里填：

- `base_url`: `https://ollama-api.example.com/v1`
- `api_key`: 任意非空占位值即可，例如 `ollama`
- `model_id`: 你拉下来的 embedding 模型名

### 8.6 把 Embedding 模型绑定到系统

这里是当前项目最容易踩坑的地方。

因为设置页没有 `embedding` 路由配置项，所以你要手工补路由。

#### 第一步：登录并拿到 JWT

```bash
curl -s http://127.0.0.1:5000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}'
```

响应里会有：

```json
{"token":"...","user":{...}}
```

#### 第二步：查看模型列表

```bash
TOKEN="替换成上一步返回的 token"

curl -s http://127.0.0.1:5000/api/models \
  -H "Authorization: Bearer $TOKEN"
```

找到你刚创建的 embedding 模型的 `id`。

#### 第三步：取出现有路由

```bash
curl -s http://127.0.0.1:5000/api/routing \
  -H "Authorization: Bearer $TOKEN"
```

#### 第四步：把完整路由连同 `embedding` 一起回写

示例：

```bash
curl -s http://127.0.0.1:5000/api/routing \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "planner": "model-planner-id",
    "beat_generator": "model-beat-id",
    "drafter": "model-drafter-id",
    "validator": "model-validator-id",
    "polisher": "model-polisher-id",
    "summarizer": "model-summarizer-id",
    "autocomplete": "model-autocomplete-id",
    "association": "model-association-id",
    "plan_and_solve": "model-plan-id",
    "hallucination": "model-hallucination-id",
    "embedding": "model-embedding-id"
  }'
```

再次强调：这一步是**整张表覆盖写入**，不是局部 patch。

### 8.7 构建 Embedding 索引

配置完成后，在 UI 中：

- 右栏 `记忆`
- `记忆状态`
- 点击 `Embedding 索引`

或者直接走 API：

```bash
curl -s -X POST http://127.0.0.1:5000/api/embedding/<book_id>/build \
  -H "Authorization: Bearer $TOKEN"
```

查看状态：

```bash
curl -s http://127.0.0.1:5000/api/embedding/<book_id>/status \
  -H "Authorization: Bearer $TOKEN"
```

## 9. 全功能部署检查清单

部署完成后，建议按下面顺序验收：

### 9.1 基础功能

1. 能访问首页
2. 能注册 / 登录
3. 能创建书籍
4. 能新建卷 / 章 / 场景
5. 编辑器可自动保存

### 9.2 模型功能

1. 设置页能新增模型
2. 任务路由能保存
3. 架构师、节拍器、执笔者、验证者、润色可调用
4. 自动补全和流式生成可正常结束

### 9.3 记忆 / Embedding / 检索

1. 章节摘要能生成
2. `记忆状态` 正常显示
3. TF-IDF / 向量检索能返回结果
4. Embedding 索引能构建成功
5. 人物提醒能刷新

### 9.4 叙事与知识功能

1. NER 可抽取实体
2. 实体图谱可视化正常
3. 时间线提取与冲突检测可用
4. 世界状态提取 / 校验可用
5. 伏笔检测 / payoff 扫描可用
6. 潜台词、心理分析、叙事分析面板可出结果

### 9.5 工程化功能

1. 快照可创建 / 恢复
2. 回收站可恢复节点
3. 全局搜索 / 替换可用
4. 一致性扫描会生成任务与报告
5. 工作流模板可创建并运行
6. Markdown / TXT / EPUB / JSON 导出可用
7. JSON / MD / TXT / DOCX 导入可用

## 10. 数据文件与备份

运行后必须一起备份的文件：

- `novel_platform.db`
- `.encryption_key`
- `.jwt_secret`

理由：

- `novel_platform.db` 是主数据。
- `.encryption_key` 用于解密数据库内的敏感字段。
- `.jwt_secret` 用于 JWT 签名；丢失后旧 token 失效。

建议：

- 备份时三者一起打包。
- 不要只备份数据库而丢失 `.encryption_key`。
- 上生产时确保这两个隐藏文件权限只对服务账号可读。

## 11. 仓库结构

```text
app.py                    Flask 入口、认证、API、SSE
database.py               SQLite schema、CRUD、加密
agents.py                 AgentOrchestrator，多智能体与提示词
memory_engine.py          三层记忆、动态注入、混合检索
embedding_engine.py       Embedding API 适配、向量索引、语义检索
search_engine.py          全书搜索、引用追踪
rule_engine.py            写作规则中心
timeline_engine.py        时间线抽取与冲突检测
snapshot_engine.py        快照与回收站
job_engine.py             进程内异步任务
workflow_engine.py        章节工作流
consistency_engine.py     全书体检
knowledge_graph_engine.py 知识图谱
foreshadow_engine.py      伏笔与 payoff
narrative_engine.py       叙事分析
ner_engine.py             NER 管线
disambiguation_engine.py  共指/消歧
export_engine.py          JSON / Markdown / TXT / EPUB / DOCX 导入导出
static/                   前端资源
templates/                HTML 模板
TECHNICAL.md              技术架构与实现说明
FEATURES.md               功能与 API 长文档
EXTENDING.md              二次开发指南
```

## 12. 当前架构的已知限制

- 当前没有 Dockerfile / docker-compose / Helm，README 采用的是源码部署方案。
- 当前推荐单实例部署，不建议无脑横向扩容。
- Base URL SSRF 校验会阻止直连本机 / 内网模型服务。
- 设置页没有 `embedding` 路由配置项，需要手工补 API。
- 前端默认依赖外部 CDN 资源；离线环境要自己替换。

## 13. 一句话部署建议

如果你要最快把“所有功能”跑起来，建议按这条路径：

1. 单机部署墨境：`python app.py`
2. Nginx 反代应用域名
3. 配一个通用聊天模型
4. 再配一个独立 Embedding 模型
5. 用 API 手工补 `embedding` 路由
6. 安装 `faiss-cpu`
7. 构建 Embedding 索引

这样可以覆盖：

- 多智能体写作
- 三层记忆
- 人物提醒
- 语义检索
- 知识图谱 / NER / 时间线
- 伏笔 / 心理 / 世界状态
- 快照 / 搜索 / 工作流 / 一致性报告

## 许可证

仓库当前未附带单独的 `LICENSE` 文件。如果你准备公开发布，请先补充许可证。
