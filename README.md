# FitAgent — 可解释 RAG、用户可控记忆与自适应训练计划

面向私人健身场景的 LLM 应用：通用问题走带证据的快速 RAG，个性化问题才进入 Agent 工具编排；用户明确确认后才会形成跨会话记忆；周训练计划则同时受 RAG 证据、Coros 运动摘要、执行反馈与确定性安全策略约束。

> 第一次阅读代码建议从 [项目学习路线](./docs/learning-guide.md) 开始：它以一次聊天请求为主线串起前端、SSE、RAG、Agent、记忆、计划和 Coros 同步。

## 文档导航与时效性

以下文档以当前代码为准，发生架构变更时必须同步更新：

- [项目学习路线](./docs/learning-guide.md)：按真实请求链路阅读代码；
- [面试材料](./docs/interview/)：项目介绍、亮点、问答和简历写法。

## 项目能力

- **可解释 RAG**：一次 Qdrant Query API 请求完成 Dense + BM25 prefetch 与 RRF 融合，DashScope 重排候选后返回最终证据；回答带 `[证据:N]` 和来源卡片。
- **受控 Agent**：LangGraph ReAct 只在个性化问题中调用画像、已确认记忆、运动摘要、天气等工具；有递归步数、工具预算，以及基于官方 Collector 的本地执行记录。
- **用户可控记忆**：mem0 调用 LLM 从用户消息提取 `proposed` 候选；用户在“我的记忆”页确认后，由模型自主选择调用工具进行语义检索。状态和有效期随记忆保存在向量库，助手回答不进入提取输入。
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
| LLM | DashScope (deepseek-v4-pro / text-embedding-v1 / gte-rerank-v2) |
| Agent | LangGraph + LangChain（ReAct + 受控工具调用） |
| 向量数据库 | Qdrant（单节点 Docker，生产演进 demo） |
| 混合检索 | Qdrant 原生 Dense + BM25 + RRF + DashScope 二阶段重排 |
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

# 3. 启动 Qdrant，并显式重建知识库索引（首次或知识文件变更后执行）
docker compose up -d qdrant
# 该命令会**重建** `fitagent_knowledge`；执行前确认可丢弃现有知识库数据。
python -m app.services.knowledge_indexer

# 4. 确保 MySQL 服务已启动，然后启动后端
# 应用启动只创建缺失关系表，不重建知识库
uvicorn app.main:app --reload --port 8000

# 5. 启动前端（新终端）
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

# 3. 启动 Qdrant，并显式重建知识库索引
docker compose up -d qdrant
# 该命令会**重建** `fitagent_knowledge`；执行前确认可丢弃现有知识库数据。
python -m app.services.knowledge_indexer

# 4. 确保 MySQL 服务已启动，然后启动后端
# 应用启动只创建缺失关系表，不重建知识库
uvicorn app.main:app --reload --port 8000

# 5. 启动前端
cd frontend && npm install && npm run dev
```

浏览器打开 http://localhost:5173

只安装运行依赖时使用：`python -m pip install .`。

## 数据库 Schema 初始化与升级

应用启动时会根据 `app/models.py` 执行 `create_all()`，仅创建空 MySQL 数据库中缺失的模型表；它不会变更、删除或升级已存在的表。升级到包含新模型字段的版本前，必须先备份现有数据库，并由操作者删除或新建一个开发数据库后再启动应用。`create_all()` 不是数据库升级工具。

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

当前测试还覆盖：assistant/tool 输出不能进入 mem0 候选、记忆确认/撤销、训练计划的强度与证据校验、Coros stdio 超时重置，以及同日多次活动不被覆盖。

检索质量基线（需要 Qdrant 与 DashScope embedding、重排服务可访问）：

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.retrieval_evaluator
```

评测只检查最终返回的 Top-6 证据是否命中人工标注的 `source_id`/`chunk_id`，并强制 `Recall@6 >= 0.90` 与 `MRR >= 0.70`。它不调用回答模型，也不会改写索引；报告打印到标准输出。

## Agent 运行防护栏

完整 Agent 请求使用受配置约束的递归步数与工具调用预算，避免模型陷入工具循环。中间件的脱敏业务日志与下文的运行记录是两套用途不同的机制：前者不记录用户原文或工具参数值，后者在 SSE 结束后由官方 Collector 投影到本地 MySQL。可在 `.env` 中按部署环境调整：

```dotenv
AGENT_MAX_STEPS=8
AGENT_MAX_TOOL_CALLS=6
```

## 聊天路由与状态边界

`ReactAgent.execute_stream` 会在每次请求开始时构造 LangGraph 短期状态，并由 LLM 的结构化意图分类决定进入直接 RAG 或个性化 Agent。GraphState 只保存原始消息、路由、检索产物、工具计数和 SSE 事件等可序列化数据；RuntimeContext 只保存可信的请求身份和依赖，**没有 city 字段**。执行记录不写入 RuntimeContext：HTTP 层为每次请求创建官方 `RunCollectorCallbackHandler`，并通过 `RunnableConfig.callbacks` 传给图。

| 层级 | 载体 | 进入模型的方式 |
| --- | --- | --- |
| 近期会话 | 当前会话最近 20 条原始消息 | 个性化 Agent 初始上下文；分类器仅见最新 6 条 |
| 早期会话背景 | MySQL session_summaries v3 缓存 | 当前窗口不足以解释早期引用时，Agent 按需调用 get_session_summary；压缩早期全部已存储消息，不是长期记忆 |
| 长期记忆 | mem0 | 用户消息提取为 proposed；模型按需调用 get_confirmed_memories(query)，只读 confirmed、未过期结果 |

`session_summaries` 是 LLM 生成、可再生成的 v3 缓存：仅在按需调用时压缩早期全部已存储消息（不按角色过滤），绝不每轮预先生成，也不写入 mem0、用户画像或长期记忆。模型结合当前系统提示词、最近消息和早期摘要综合判断。分类器实际只读取最新 6 条**规范化** user/assistant 消息；只有个性化 Agent 能调用 `get_session_summary`。天气工具必须从当前窗口或该摘要得到明确城市，否则先追问，不能编造城市。MySQL 保存账号、完整聊天、会话摘要及训练业务，旧 `memory_facts` 表保留待显式迁移。LangGraph 不启用 Store 或 checkpointer，不自动召回记忆；分类失败时仍保守回退个性化 Agent，既有 SSE 契约保持。

## mem0 长期记忆

安装项目依赖会安装固定的 `mem0ai==2.0.20`。在 `.env` 中设置现有 `DASHSCOPE_API_KEY` 和 `QDRANT_URL`，其余 `MEMORY_*` 配置见 `.env.example`。默认使用 `config/models.yml` 的模型及 1536 维嵌入，记忆使用独立 Qdrant collection，不写入知识库 RAG 集合。

mem0 主向量库存记忆正文和元数据；Entity Store 按实体关联主库记忆；SQLite 存变更日志与每个 scope 最近 10 条消息。当前基础安装使用语义检索，不安装 NLP extras，也不启用图谱记忆。详细数据流、状态边界与故障行为见 [记忆架构说明](docs/memory-architecture.md)。

旧 MySQL 记忆不会自动迁移。先预览，再显式写入 mem0；两个命令都保留源表数据，迁移可重跑：

```powershell
.\.venv\Scripts\python.exe -m app.services.memory_migration --user-id 1
.\.venv\Scripts\python.exe -m app.services.memory_migration --user-id 1 --apply
```

模型只通过只读工具查询长期记忆。提取调用在线程池执行，失败不阻断聊天；管理接口失败返回 503。成功撤销后，后续工具查询排除该条记忆。切换 `MEMORY_ENABLED=false` 会停用提取与记忆读写，不影响短期聊天历史。

当前按上述单 worker 启动方式运行，同一记忆的状态修改使用进程内互斥。多 worker 或多实例部署前需补充跨进程状态协调，详见 [记忆架构](docs/memory-architecture.md)。

## Agent 执行轨迹

每轮聊天的官方 `RunCollectorCallbackHandler` 只在内存中采集本次运行树。SSE 流结束后，系统以独立事务将其投影到既有 MySQL `agent_runs` 和 `agent_tool_calls`：前者保存请求 ID、状态、总耗时、用户问题和最终回答；后者按顺序保存工具名、真实工具输入、工具输出（或错误）及耗时。

该记录功能只使用本地 MySQL，不接入 LangSmith；不新增日志表、HTTP 路由或长期运行时 `trace` 字段。

服务启动时会创建缺失的数据库和模型表。模型字段变更需要制定显式的数据库维护方案；重启服务不会修改已有表结构。

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
│   │   ├── database.py         # MySQL 连接与新数据库模型表初始化
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
│   │   ├── memory_service.py   # mem0 长期记忆权限与候选/确认生命周期
│   │   ├── session_summary_service.py # 按需生成和读取早期已存储消息的 v3 摘要缓存
│   │   ├── memory_backend.py   # 与 SDK 无关的记忆接口
│   │   ├── memory_migration.py # 旧记忆显式迁移，默认只预览
│   │   ├── training_plan_service.py # 计划编排与安全策略
│   │   ├── fitness_insights.py # Coros 数据受限聚合快照
│   │   ├── middleware.py       # Agent 中间件
│   │   ├── rag_service.py      # RAG 检索、RRF 融合与二阶段排序
│   │   ├── reranker.py         # DashScope 重排边界
│   │   ├── vector_repository.py # Qdrant 仓储边界
│   │   ├── vector_store.py     # Qdrant 查询与 embedding 服务
│   │   ├── knowledge_indexer.py # 离线索引构建入口
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
├── storage/uploads/            # 上传文件临时目录
└── docker-compose.yml           # Qdrant 单节点演示部署
```

## RAG 检索流程

```
用户提问
  └── 一次 Qdrant Query API
      ├── Dense prefetch（语义）
      ├── Qdrant BM25 prefetch（关键词）
      ├── RRF 融合
      └── 取 Top-30 候选
          └── DashScope `gte-rerank-v2` 重排
              └── 返回最终 Top-6 证据
```

更多设计决策和技术细节请查看 [项目学习路线](./docs/learning-guide.md)，以及独立的 [项目简介](./docs/interview/项目简介.md)、[技术亮点](./docs/interview/技术亮点.md)、[常见面试题](./docs/interview/常见面试题.md)、[简历写法](./docs/interview/简历写法.md)。

## Qdrant 演进 Demo

演示使用单节点 Qdrant 与显式离线构建：`data/` 中受控 TXT、Markdown 或 PDF 文件只做 Unicode 与空白规范化，再经切分和 embedding 写入 `fitagent_knowledge`。`python -m app.services.knowledge_indexer` 是破坏性重建命令；在线 API 只读检索，应用启动也只创建缺失关系表，不会自动导入或重建知识库。

- `GET /api/health/rag`：检查当前 Qdrant collection 是否可读。
- `python -m app.services.knowledge_indexer`：知识文件更新后显式、破坏性地重建 `fitagent_knowledge`。
- 每个查询只发起一次 Qdrant Query API：Dense + BM25 prefetch 经 RRF 选出最多 30 个候选，再由 DashScope `gte-rerank-v2` 返回最终 Top-6；没有本地 BM25 工件。重排请求仅发送用户问题和这些已召回的候选文本，且接口设置 `return_documents=false`，响应不回传候选正文。

## License

MIT
