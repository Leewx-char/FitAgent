# FitAgent — 可解释 RAG、用户可控记忆与自适应训练计划

面向私人健身场景的 LLM 应用：通用问题走带证据的快速 RAG，个性化问题才进入 Agent 工具编排；用户明确确认后才会形成跨会话记忆；周训练计划则同时受 RAG 证据、Coros 运动摘要、执行反馈与确定性安全策略约束。

> 第一次阅读代码建议从 [项目学习路线](./docs/learning-guide.md) 开始：它以一次聊天请求为主线串起前端、SSE、RAG、Agent、记忆、计划和 Coros 同步。

## 文档导航与时效性

以下文档以当前代码为准，发生架构变更时必须同步更新：

- [项目学习路线](./docs/learning-guide.md)：按真实请求链路阅读代码；
- [面试材料](./docs/interview/)：项目介绍、亮点、问答和简历写法。

## 项目能力

- **可解释 RAG**：Qdrant Dense + 离线 BM25 双路召回、RRF 融合、revision/alias 安全发布；回答带 `[证据:N]` 和来源卡片。
- **受控 Agent**：LangGraph ReAct 只在个性化问题中调用画像、已确认记忆、运动摘要、天气等工具；有递归步数、工具预算与脱敏执行轨迹。
- **用户可控记忆**：聊天只产生 `proposed` 候选；用户在“我的记忆”页确认、撤销或等待过期，模型回答不能反向写入事实。
- **自适应周计划**：Coros 近四周聚合快照 + 用户画像 + RPE/疼痛反馈 → 固定安全策略 → RAG 证据 → Pydantic JSON 契约和业务校验。
- **多模态健康信息**：体检 PDF/图片提取十项指标，用户核对后才写入画像；不做医学诊断。

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
| LLM | DashScope (deepseek-v4-pro / text-embedding-v1) |
| Agent | LangGraph + LangChain（ReAct + 受控工具调用） |
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
# 编辑 .env，填入 DASHSCOPE_API_KEY、MySQL 配置、JWT_SECRET_KEY
# Coros 同步是可选能力：另见“Coros 本地 MCP 配置”

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

## Coros 本地 MCP 配置

项目当前接入社区维护的 [`cygnusb/coros-mcp`](https://github.com/cygnusb/coros-mcp) **本地 stdio MCP**（固定到 `71d594c`），而非浏览器中的远程 OAuth connector。它是一个外部进程，刻意安装到 `.tools/coros-mcp-venv`，不写入后端 `.venv`：该 MCP 的 FastMCP 依赖可能升级 Starlette，从而破坏 FastAPI 服务的锁定依赖。

首次安装、认证与确认：

```powershell
# 1. 创建隔离的本地 MCP 虚拟环境并安装固定版本
.\scripts\install_coros_mcp.ps1

# 2. 完成 Coros 认证（交互式，不要把凭据写进 .env）
# 本项目要同步活动、日指标和睡眠；因此使用 auth，而非只覆盖 Web 数据的 auth-web。
$env:PYTHONUTF8=1  # PowerShell 默认 GBK 时避免 CLI 输出 Unicode 状态符失败
& .\.tools\coros-mcp-venv\Scripts\coros-mcp.exe auth
& .\.tools\coros-mcp-venv\Scripts\coros-mcp.exe auth-status

# 3. 可选：先检查本地缓存；FitAgent 仍以用户点击同步为准写入 MySQL
& .\.tools\coros-mcp-venv\Scripts\coros-mcp.exe cache-status
```

`.env` 使用示例已写入 `.env.example`：

```dotenv
COROS_MCP_COMMAND=[".\\.tools\\coros-mcp-venv\\Scripts\\python.exe", "-m", "app.integrations.coros_mcp_runner", "serve"]
COROS_MCP_SYNC_COMMAND=[".\\.tools\\coros-mcp-venv\\Scripts\\python.exe", "-m", "app.integrations.coros_mcp_runner", "sync"]
COROS_MCP_CACHE_HOME=.tools/coros-mcp-home
COROS_MCP_TOOLSET=readonly
COROS_MCP_HIDE_AUTH_TOOLS=true
```

然后在数据面板点击“同步高驰数据”，或调用 `POST /api/fitness/sync`；未传日期默认只同步最近 7 天。该接口会先用隔离解释器执行显式缓存同步，再由 stdio MCP **只读本地缓存**；不会由聊天 Agent 自动触发或在读取时重复请求上游。某类记录为空（例如未佩戴手表睡眠而没有睡眠记录）是正常成功，返回空列表且不会标为 `partial`。只有单一源明确请求失败时，日指标和活动仍会写入 MySQL，响应以 `partial` / `unavailable_sources` 明示。FitAgent 只向 MCP 调用 `list_activities`、`get_daily_metrics`、`get_sleep_data`，并强制 MCP 使用 `readonly` 工具集、隐藏认证工具；认证令牌由 MCP 的操作系统安全存储管理。社区 MCP 的 SQLite 缓存固定写入 `COROS_MCP_CACHE_HOME`，不会触碰用户目录中的 `.config/coros-mcp`。完整 `auth` 可能影响 Coros App 登录状态，认证前请确认可接受重新登录。若使用 COROS 官方远程 OAuth MCP，需要另建 HTTP/OAuth adapter，不能直接替换本项目的 stdio 命令。

## 开发门禁

```bash
ruff format --check app
ruff check app
pytest app/tests
```

当前测试还覆盖：assistant 输出不能污染会话事实、记忆确认/撤销、训练计划的强度与证据校验、Coros stdio 超时重置，以及同日多次活动不被覆盖。

当前 Qdrant revision 的检索基线（需要 Qdrant 与 DashScope embedding 服务可访问）：

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.retrieval_evaluator
```

知识源变更后，可先运行不调用 embedding、不访问 Qdrant 的发布前数据预检：

```powershell
.\.venv\Scripts\python.exe -m app.services.knowledge_preflight
```

预检报告位于 Git 忽略的 `storage/rag/index_preflight_report.json`；通过后再执行索引构建。

该命令不调用回答模型、不修改索引，输出的评测报告位于 Git 忽略的 `storage/rag/`。

## Agent 运行防护栏

完整 Agent 请求使用受配置约束的递归步数与工具调用预算，避免模型陷入工具循环；工具调用以请求 ID、耗时和参数形状写入结构化日志，不记录用户原文或工具参数值。可在 `.env` 中按部署环境调整：

```dotenv
AGENT_MAX_STEPS=8
AGENT_MAX_TOOL_CALLS=6
```

## 聊天路由与状态边界

`ReactAgent.execute_stream` 会在每次请求开始时构造 LangGraph 短期状态，并由 LLM 的结构化意图分类决定进入直接 RAG 或个性化 Agent。图状态只保存消息、会话事实、检索历史、证据和 SSE 事件等可序列化数据；用户身份、会话标识、追踪对象和执行依赖仅存在请求级运行时上下文中。

MySQL 仍是跨会话记忆和会话摘要的唯一长期存储，LangGraph 不启用 Store 或 checkpointer。分类模型不可用、返回异常或意图不明确时，系统会保守回退到个性化 Agent；HTTP 层继续输出既有 `tool`、`evidence`、`text` 和 `[DONE]` SSE 契约。

## Agent 执行轨迹

每轮聊天会以独立事务写入 `agent_runs` 和 `agent_tool_calls`：记录请求 ID、执行路径（`agent` / `direct_rag`）、状态、总耗时、工具顺序、参数类型和工具耗时。为保护隐私，不保存用户问题、工具参数值或模型回复原文。

升级代码后先执行数据库迁移并重启后端：

```powershell
alembic upgrade head
```

登录后可调用 `GET /api/sessions/{session_id}/agent-runs` 查看该会话最近的执行轨迹。此操作不需要重新构建知识库索引。

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
│   │   ├── routers/            # auth/chat/profile/fitness/memory/training_plans 等
│   │   ├── exception_handlers.py
│   │   └── response.py
│   ├── services/               # 业务逻辑层
│   │   ├── factory.py          # LLM/VL/Embedding 模型工厂
│   │   ├── react_agent.py      # 聊天图执行门面与内层 ReAct Agent
│   │   ├── chat_routing_graph.py # LangGraph 短期状态与意图路由图
│   │   ├── agent_tools.py      # 工具定义
│   │   ├── memory_service.py   # 候选、确认、过期与会话摘要
│   │   ├── training_plan_service.py # 计划编排与安全策略
│   │   ├── fitness_insights.py # Coros 数据受限聚合快照
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
├── data/                       # 经审核的知识源（Markdown / TXT / PDF）
├── frontend/                   # Vue 3 前端
├── docs/                       # 学习路线与面试文档
│   ├── learning-guide.md        # 按事件流阅读代码的学习路线
│   └── interview/               # 项目简介、技术亮点、问答与简历写法
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

更多设计决策和技术细节请查看 [项目学习路线](./docs/learning-guide.md)，以及独立的 [项目简介](./docs/interview/项目简介.md)、[技术亮点](./docs/interview/技术亮点.md)、[常见面试题](./docs/interview/常见面试题.md)、[简历写法](./docs/interview/简历写法.md)。

## Qdrant 演进 Demo

演示使用单节点 Qdrant 与离线构建：`data/` 中的知识文件经标题感知切分、父子关联、内容去重和 embedding 后，生成带 revision 的 collection；校验完成后才切换 `rag_active` 别名。在线 API 只读检索，绝不自动导入或重建索引。

- `GET /api/health/rag`：检查当前 Qdrant collection 是否可读。
- `python -m app.services.knowledge_indexer`：知识文件更新后显式构建并激活新 revision。
- BM25 文档工件随离线构建生成；缺失时系统自动降级为 dense 检索，不会在请求中全量同步。
- 索引发布后重启后端，使常驻的 BM25 工件与新的 Qdrant revision 一致。

## License

MIT
