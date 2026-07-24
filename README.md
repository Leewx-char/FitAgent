# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索、用户画像和多模态健康文档上传，提供个性化健身指导。

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | |
| Node.js | 20+ | |
| Docker Compose | v2+ | Qdrant demo 容器 |
| MySQL | 8.0+ | 需提前安装并启动服务 |
| [Windows] poppler | 最新版 | pdf2image 依赖,[下载地址](https://github.com/oschwartz10612/poppler-windows/releases),将 `bin/` 加入系统 PATH |

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.136 + Uvicorn 0.47 |
| 数据库 | MySQL 8.0 + SQLAlchemy 2.0 |
| 认证 | JWT (python-jose) + bcrypt |
| LLM | DashScope (deepseek-v4-pro / text-embedding-v4) |
| Agent | LangGraph + LangChain |
| 向量数据库 | Qdrant（单节点 Docker，生产演进 demo） |
| 关键词检索 | rank-bm25 (BM25) |
| 文档处理 | PyPDF + pdf2image + python-magic + Pillow |
| 前端框架 | Vue 3 + Vite + Pinia + Naive UI |
| 图表 | ECharts |

## Windows 启动指南

```powershell
# 0. 允许 PowerShell 脚本执行（仅首次需要）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 1. 克隆并配置环境变量
cp .env.example .env
# 编辑 .env,填入 DASHSCOPE_API_KEY、MySQL 配置、JWT_SECRET_KEY

# 2. Python 环境与开发依赖（pyproject.toml 是唯一依赖入口）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 3. 初始化空开发数据库并执行迁移
# .env 中 AUTO_CREATE_DATABASE=true 时才允许创建数据库
python -c "from app.core.database import ensure_database_exists; ensure_database_exists()"
alembic upgrade head

# 4. 启动 Qdrant，并构建知识库索引（首次或知识文件变更后执行）
docker compose up -d qdrant
python -m app.services.knowledge_indexer

# 5. 确保 MySQL 服务已启动，然后启动后端
uvicorn app.main:app --reload --port 8000

# 6. 启动前端（新终端）
cd frontend
npm install
npm run dev
```

## macOS / Linux 启动指南

```bash
# 1. 克隆并配置环境变量
cp .env.example .env

# 2. Python 环境与开发依赖
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 3. 初始化空开发数据库并执行迁移
python -c "from app.core.database import ensure_database_exists; ensure_database_exists()"
alembic upgrade head

# 4. 启动 Qdrant，并构建知识库索引
docker compose up -d qdrant
python -m app.services.knowledge_indexer

# 5. 确保 MySQL 服务已启动，然后启动后端
uvicorn app.main:app --reload --port 8000

# 6. 启动前端
cd frontend && npm install && npm run dev
```

浏览器打开 http://localhost:5173

只安装运行依赖时使用：`python -m pip install .`。

## 开发门禁

```bash
ruff format --check app
ruff check app
pytest app/tests
```

## 健康文档处理提示

- 可选文字的 PDF 使用文本模型；扫描版 PDF 和图片先使用 Qwen-VL Plus，失败页才以更高精度交给 Max 重试。扫描 PDF 会处理全部页面，默认最多 20 页。
- 上传前会提示文件将发送至 DashScope 用于指标提取；原始临时文件在处理完成后删除。
- 健康文档接口统一返回 `{code, messages, data}`：成功时 `data` 包含指标和冲突候选，失败时为 `null`。系统只整理十项体检指标及单位，不提供医疗诊断。识别结果必须经用户编辑/确认后才写入健康画像。

## 项目结构

```
FitAgent/
├── app/                        # 后端代码（FastAPI 标准结构）
│   ├── main.py                 # 应用入口 + CORS + lifespan
│   ├── models.py               # ORM 模型
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── core/                   # 基础设施
│   │   ├── database.py         # MySQL 连接
│   │   ├── settings.py         # 环境配置
│   │   ├── auth.py             # JWT 认证
│   │   └── deps.py             # 依赖注入
│   ├── api/                    # HTTP 层
│   │   ├── routers/            # auth/chat/sessions/messages/profile/upload/fitness
│   │   ├── exception_handlers.py
│   │   └── response.py
│   ├── services/               # 业务逻辑层
│   │   ├── factory.py          # LLM/VL/Embedding 模型工厂
│   │   ├── react_agent.py      # LangGraph ReAct Agent
│   │   ├── agent_tools.py      # 工具定义
│   │   ├── middleware.py       # Agent 中间件
│   │   ├── rag_service.py      # RAG 检索与 RRF 融合
│   │   ├── vector_repository.py # Qdrant 仓储边界
│   │   ├── vector_store.py     # Qdrant 查询服务
│   │   ├── knowledge_indexer.py # 离线索引构建入口
│   │   ├── bm25_retriever.py   # BM25 关键词检索
│   │   └── doc_parser.py       # 多模态文档解析
│   └── utils/                  # 工具函数
│       ├── config_handler.py
│       ├── logger_handler.py
│       ├── file_handler.py
│       ├── prompt_loader.py
│       └── bootstrap.py
├── config/                     # YAML 配置（含 vector_store.yml）
├── prompts/                    # 系统提示词
├── data/                       # 知识库（txt）
├── frontend/                   # Vue 3 前端
├── docs/                       # 架构文档
│   ├── architecture.md
│   ├── decisions.md
│   ├── design.md
│   └── product.md
├── alembic/                     # 数据库迁移
├── alembic.ini
├── storage/uploads/            # 上传文件临时目录
└── docker-compose.yml           # Qdrant 单节点演示部署
```

## RAG 检索流程

```
用户提问
  ├── 查询处理：归一化（口语→术语）+ 同义词扩展
  ├── 双路并行检索：
  │   ├── Qdrant 向量检索（语义、来源过滤）
  │   └── BM25 关键词检索（字级分词 + TF-IDF + 长度归一化）
  ├── RRF 排名融合
  ├── Jaccard 去重（阈值 0.8）
  └── 取 Top-6 返回
```

更多设计决策和技术细节请查看 [docs/decisions.md](./docs/decisions.md)。

## Qdrant 演进 Demo

演示采用单 collection、单层切片和离线构建：`data/` 中的知识文件经过
`knowledge_indexer` 生成带 revision 的 Qdrant collection，校验完成后才切换 `rag_active`
别名。在线 API 只读检索，绝不自动导入或重建索引。

- `GET /api/health/rag`：检查当前 Qdrant collection 是否可读。
- `python -m app.services.knowledge_indexer`：知识文件更新后显式构建并激活新 revision。
- BM25 文档工件随离线构建生成；缺失时系统自动降级为 dense 检索，不会在请求中全量同步。
- 索引发布后重启后端，使常驻的 BM25 工件与新的 Qdrant revision 一致。

## License

MIT
