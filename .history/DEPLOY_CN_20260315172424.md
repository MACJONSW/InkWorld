# InkWorld 部署指南（含 Embedding 模型下载）

本指南对应当前仓库代码（Flask + SQLite + 本地线程任务 + 可选 FAISS）。

## 1. 快速部署（单机）

```bash
cd /data/whr/InkWorld

# 1) 创建/激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2) 安装依赖
python -m ensurepip --upgrade
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install faiss-cpu

# 3) 启动
export FLASK_DEBUG=0
export ALLOWED_ORIGINS="http://127.0.0.1:5000"
python app.py
```

默认访问：

- <http://127.0.0.1:5000>

首次启动会自动创建：

- `.jwt_secret`
- `.encryption_key`（若不存在）
- `novel_platform.db`（若不存在）

## 2. Embedding 模型下载

仓库已提供脚本：`scripts/download_embedding_models.sh`

### 2.1 下载全部（Ollama + Hugging Face）

```bash
cd /data/whr/InkWorld
bash scripts/download_embedding_models.sh --provider all
```

### 2.2 只下载 Ollama Embedding 模型

```bash
bash scripts/download_embedding_models.sh --provider ollama
```

默认模型：

- `nomic-embed-text`
- `mxbai-embed-large`
- `bge-m3`
- `embeddinggemma`

自定义模型列表示例：

```bash
bash scripts/download_embedding_models.sh \
  --provider ollama \
  --ollama-models "nomic-embed-text,bge-m3"
```

### 2.3 只下载 Hugging Face Embedding 模型（本地缓存）

```bash
bash scripts/download_embedding_models.sh --provider st
```

默认模型：

- `BAAI/bge-m3`
- `BAAI/bge-large-zh-v1.5`
- `intfloat/multilingual-e5-large`
- `sentence-transformers/all-MiniLM-L6-v2`

自定义缓存目录：

```bash
bash scripts/download_embedding_models.sh --provider st --hf-cache /data/hf-cache
```

## 3. 在系统里接入 Embedding 服务

`embedding_engine.py` 走的是 OpenAI 兼容 `/v1/embeddings` 协议。

你需要在“设置中心 -> 模型管理”新增一条 embedding 模型配置：

- `base_url`: 你的 OpenAI 兼容服务地址（如 `https://embed.example.com/v1`）
- `api_key`: 对应服务密钥（若服务不校验，可填占位）
- `model_id`: embedding 模型名

然后在“设置中心 -> 路由配置”里，把 `Embedding 检索` 绑定到该模型。

## 4. 重要约束

### 4.1 Base URL 会拦截 localhost 和内网 IP

后端会拒绝以下主机：

- `localhost`
- `127.0.0.1`
- `0.0.0.0`
- `10.*`
- `192.168.*`
- `172.16.*` 到 `172.31.*`

所以本机 Ollama/vLLM 不能直接填 `http://127.0.0.1:11434/v1`。

建议做法：

1. 用 Nginx/Caddy 反代成本地域名（如 `https://embed.example.com/v1`）
2. 在 InkWorld 中填该域名

### 4.2 路由保存是覆盖写入

`POST /api/routing` 会整体覆盖当前用户路由表。请通过 UI 保存，或 API 时提交完整路由字典。

## 5. 构建与验证 Embedding 索引

1) 在 UI 打开一本书，点击 Embedding 索引构建。

2) 或 API 手动构建：

```bash
# 先登录拿 token
curl -s http://127.0.0.1:5000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}'

# 然后构建索引
curl -s -X POST http://127.0.0.1:5000/api/embedding/<book_id>/build \
  -H "Authorization: Bearer <TOKEN>"

# 查看状态
curl -s http://127.0.0.1:5000/api/embedding/<book_id>/status \
  -H "Authorization: Bearer <TOKEN>"
```

## 6. 生产建议

当前架构建议单实例部署：

- 1 个应用进程
- 1 个 SQLite 文件
- 1 套密钥文件

必要备份文件：

- `novel_platform.db`
- `.encryption_key`
- `.jwt_secret`
