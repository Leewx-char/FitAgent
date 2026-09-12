# FitAgent 代码学习路线：从一条请求串起整个项目

> 目标：不是背目录，而是能够从用户动作出发，追踪数据经过前端、API、业务服务、外部依赖和数据库的全过程，并在面试中讲清楚“为什么这样设计”。
>
> 推荐顺序：先完成第 1 阶段的一条聊天主链路，再按阶段阅读。不要一开始通读 `app/models.py`、所有 Router 或全部前端页面。

## 0. 先建立全局地图（20 分钟）

先读本 README 的项目能力与目录说明，然后只浏览下面这些入口，不深入实现：

| 入口 | 先确认的问题 |
| --- | --- |
| [frontend/src/main.js](../frontend/src/main.js)、[router/index.js](../frontend/src/router/index.js) | Vue 从哪里启动，哪个页面负责聊天？ |
| [frontend/src/views/Chat.vue](../frontend/src/views/Chat.vue) | 用户点击发送后，发往哪个 API，如何消费 SSE？ |
| [frontend/src/components/Sidebar.vue](../frontend/src/components/Sidebar.vue) | 会话列表从哪里加载、如何新建/切换/删除会话？ |
| [app/main.py](../app/main.py) | FastAPI 如何启动、先初始化新数据库模型表并注册路由？ |
| [app/api/routers/chat.py](../app/api/routers/chat.py) | `POST /api/chat` 如何接住一次聊天？ |
| [app/services/react_agent.py](../app/services/react_agent.py) | 请求如何通过 LangGraph 图分为 Direct RAG 与个性化 Agent？ |

此时只需要记住边界：**前端负责交互和流式渲染，Router 负责 HTTP/鉴权/事务边界，Service 负责业务编排，Repository 或 Adapter 负责数据库与第三方系统。**

应用的 lifespan 会先调用 `initialize_schema()`：它创建 `.env` 指定的缺失数据库，并从 `app/models.py` 注册的 ORM 定义创建空库中缺失的表。这个 `create_all()` 路径不修改已有表；升级开发数据库前必须先备份，再由操作者删除或新建数据库后重启应用。

```mermaid
flowchart LR
  U[用户在 Chat.vue 输入问题] --> FE[fetch POST /api/chat]
  FE --> API[chat Router]
  API --> DB1[(MySQL: session / message)]
  API --> MEM[mem0 记忆候选]
  API --> A{ReactAgent 路由}
  A -->|通用知识| RAG[Dense + BM25 + RRF → DashScope 重排]
  A -->|个性化问题| LG[LangGraph Agent + 工具]
  LG -->|仅早期引用无法解释时| SS[get_session_summary]
  SS --> DB1
  RAG --> LLM[流式模型回答]
  LG --> LLM
  LLM --> SSE[SSE text / tool / evidence / error + DONE]
  SSE --> FE
  API --> DB2[(MySQL: assistant message / agent_runs / agent_tool_calls)]
```

## 1. 第一条主线：追踪一次通用健身问答（60–90 分钟）

这是最值得先掌握的事件流。启动项目并在聊天页问：`深蹲前怎样热身？`。该问题通常会命中 **Direct RAG**，比完整 Agent 链路短，最适合理解核心 RAG。

### 按顺序阅读

1. [Chat.vue](../frontend/src/views/Chat.vue) 的 `sendMessage`：确认它使用 `fetch` 发送 JWT、`message` 和 `session_id`，再用 `ReadableStream` 逐段解析 SSE。
2. [chat.py](../app/api/routers/chat.py) 的 `chat`：依次完成参数校验、创建/校验会话、持久化用户消息、创建 mem0 记忆候选、读取最近历史，然后返回 `StreamingResponse`；它不会每轮刷新会话摘要。
3. 同文件的 `sse_generator`：它在请求开始时创建 LangChain 官方 `RunCollectorCallbackHandler`，把它经 `RunnableConfig.callbacks` 传给图；随后把同步生成器放到 executor 中逐块取值，转换为 `text`、`tool`、`evidence`、`error` 四类 SSE 事件。正常结束额外发送 `[DONE]`，前端在 `[DONE]` 或 `error` 时收敛加载状态；SSE 流结束后，才将 Collector 在内存中的运行树投影到既有 MySQL `agent_runs`、`agent_tool_calls`，并持久化 assistant 回复。这里不使用 LangSmith，也不新增日志表或路由。
4. [react_agent.py](../app/services/react_agent.py) 的 `execute_stream`：它构造 `ChatRuntimeContext` 与初始 `ChatGraphState`，消费图的 custom stream，再编码为既有 SSE JSON 行。
5. [chat_routing_graph.py](../app/services/chat_routing_graph.py)：`StateGraph` 先让模型以结构化 `IntentDecision` 分类；明确的通用知识进入 Direct RAG，个性化、模糊或分类失败都进入个性化 Agent。Direct RAG 先发“检索知识库”事件，发送真实证据卡片，再流式生成答案。
6. [rag_service.py](../app/services/rag_service.py) 的 `RagSummarizeService.build_context`：查看它如何把一次 Qdrant Query API 的 Dense + BM25 prefetch、RRF Top-30 候选交给 [reranker.py](../app/services/reranker.py)，再将 DashScope 排出的最终 Top-6 与上下文预算转换为 `RagContext`。

### 要追踪的三个数据

| 数据 | 产生位置 | 去向 | 你应能解释的价值 |
| --- | --- | --- | --- |
| `session_id` | `chat()` | 响应头 `X-Session-Id` 与 `sessions/messages` | 新会话与后续多轮会话如何关联 |
| `RagContext.result.hits` | `RagSummarizeService` | `build_evidence_cards` → 前端证据卡片 | 证据来自真实检索，不靠解析模型文本猜测 |
| SSE `type` | `ReactAgent` / `sse_generator` | `sse_generator` → `Chat.vue` | 工具状态、证据、模型增量文本与服务异常为何可以分别渲染，并由 `[DONE]` 收口 |

### 这阶段的完成标准

你能不看代码讲出：

> “用户问题先被存为消息，再产生待确认记忆候选；`ReactAgent` 将短期状态交给 StateGraph，由结构化分类决定 Direct RAG 或个性化 Agent。分类异常保守进入个性化分支；两条分支都把真实证据与回答增量通过 SSE 回给前端。流结束后再存 assistant 消息，并把官方 Collector 的内存运行树投影为本地 `agent_runs`、`agent_tool_calls` 记录。”

先跑这些测试巩固，不需要真实 LLM：

```powershell
.\.venv\Scripts\python.exe -m pytest app/tests/test_chat.py app/tests/test_direct_rag_router.py app/tests/test_online_rag_pipeline.py -q
```

## 2. 第二条主线：个性化问题如何进入 Agent（45–60 分钟）

改问：`结合我的膝盖情况和最近训练数据，安排今天的训练。` 这会绕过 Direct RAG，进入 LangGraph Agent。此时不要试图读懂 LangGraph 内部实现，先关注**本项目给模型什么工具、什么上下文、什么预算**。

1. 回看 `ReactAgent.execute_stream` 与 [chat_routing_graph.py](../app/services/chat_routing_graph.py)：GraphState 只保存原始消息、路由、检索产物、工具计数和 SSE 事件；`ChatRuntimeContext` 是可信请求身份与依赖，**没有 city 字段**。分类器至多看到最新 6 条规范化消息，个性化节点把最新 20 条原始消息和同一个 context 传给内层 Agent。运行记录不在 context 中：HTTP 层把官方 Collector 放入 `RunnableConfig.callbacks`。
2. 看 [agent_tools.py](../app/services/agent_tools.py)：重点查看 `rag_summarize`、`get_user_profile`、`get_session_summary`、`get_confirmed_memories`、`get_fitness_summary`。它们通过 `ToolRuntime` 读取请求上下文、把证据等短期产物写回 state。只有个性化 Agent 能调用 `get_session_summary`；天气没有当前窗口或该摘要中的明确城市时，必须追问，不能编造城市。
3. 看 [middleware.py](../app/services/middleware.py)：理解递归步数、按同批工具位置计算的预算、脱敏审计分别在哪里被约束；并行工具调用的状态更新由 reducer 合并。
4. 看 [repositories/agent_trace_repository.py](../app/repositories/agent_trace_repository.py)：理解 `RunCollectorCallbackHandler` 如何只在本次请求内存中收集根运行和工具运行，并在 SSE 结束后投影到既有 `agent_runs`、`agent_tool_calls`。记录包含用户问题、最终回答、工具输入和工具输出；不接入 LangSmith，也不增加日志表或查询路由。

`get_fitness_summary` 不接受模型传入的用户 ID。默认读取近 4 周；给出不同的 `start_day` / `end_day` 时读取最多 90 天的闭区间。若两个日期相同，第一次调用会列出当天活动的稳定 `activity_id` 候选，模型必须携带该 ID 再调用一次才能读取某次活动，不能仅按日期猜测晨跑或夜跑。

这阶段最重要的判断是：**Agent 不是拥有全部数据库权限的万能类；它只能调用在 `agent_tools.py` 中显式注册的只读/受限工具。**

建议测试：

```powershell
.\.venv\Scripts\python.exe -m pytest app/tests/test_agent_execution_policy.py app/tests/test_agent_rag_context.py app/tests/test_agent_trace.py -q
```

自测问题：为什么“给我制定计划”不应靠聊天 Agent 直接写入训练计划表？答案要落到“显式 API、结构化契约、确定性安全策略和事务边界”。

## 3. 读 RAG 时必须区分离线与在线（60 分钟）

许多面试讲不清 RAG，是因为把“导入知识”和“回答时检索”混为一谈。这里要从两个方向读。

| 路径 | 主要文件 | 一句话职责 |
| --- | --- | --- |
| 离线构建 | [knowledge_indexer.py](../app/services/knowledge_indexer.py) → [vector_repository.py](../app/services/vector_repository.py) | 对受控文件做有限的 Unicode/空白规范化、切分与 embedding，再显式、破坏性地重建 `fitagent_knowledge` |
| 在线检索 | [rag_service.py](../app/services/rag_service.py) → [vector_store.py](../app/services/vector_store.py) → [vector_repository.py](../app/services/vector_repository.py) → [reranker.py](../app/services/reranker.py) | 一次 Qdrant Query API 完成 Dense + BM25 prefetch 与 RRF，取 Top-30 候选交给 DashScope 重排，再返回最终 Top-6 证据并裁剪上下文 |

建议按这个顺序提问自己：

1. 为什么索引构建需要显式确认？——`knowledge_indexer` 会破坏性重建 `fitagent_knowledge`，应用启动不会做这件事。
2. 为什么清洗范围很小？——输入是受控文件，只做 Unicode/空白规范化；它不是网页内容抓取或网页清洗管线。
3. 为什么使用 RRF 而不混合相似度分数？——Dense 与 BM25 的分数没有可比的量纲，Qdrant 按排名融合更稳妥。
4. 为什么要二阶段排序？——RRF 适合合并不同量纲的 Dense/BM25 排名；将不超过 30 个已召回候选交给专用重排模型，能在受控延迟和成本内提高最终证据的精度。
5. 为什么上下文要预算？——召回多不等于应该全部塞给模型，需控制成本与噪声。

配合阅读 [test_retrieval_evaluator.py](../app/tests/test_retrieval_evaluator.py)。

## 4. 重点补课：记忆不是“全量聊天记录”（45–60 分钟）

从 [记忆架构](memory-architecture.md)、[session_summary_service.py](../app/services/session_summary_service.py) 和 [memory_service.py](../app/services/memory_service.py) 开始，再看 [mem0_backend.py](../app/integrations/mem0_backend.py)、[memory.py](../app/api/routers/memory.py) 与 [test_memory.py](../app/tests/test_memory.py)。

| 层级 | 载体 | 进入模型的方式 |
| --- | --- | --- |
| 近期会话 | 当前会话最近 20 条原始消息 | 个性化 Agent 初始上下文；分类器仅见最新 6 条 |
| 早期会话背景 | MySQL session_summaries v3 缓存 | 当前窗口不足以解释早期引用时，Agent 按需调用 get_session_summary；压缩早期全部已存储消息，不是长期记忆 |
| 长期记忆 | mem0 | 用户消息提取为 proposed；模型按需调用 get_confirmed_memories(query)，只读 confirmed、未过期结果 |

`session_summaries` 是 LLM 生成、可再生成的 v3 缓存：仅在按需调用时压缩早期全部已存储消息（不按角色过滤）。模型结合当前系统提示词、最近消息和早期摘要综合判断。它不是长期记忆，不会每轮预先生成，也不会改动 mem0。一定要追踪这条防污染规则：`chat()` 在模型生成前只将**用户消息**交给 mem0，通过 LLM 提取待确认候选，按用户和会话隔离上下文；assistant/tool 文本不进入该输入。候选必须经过用户确认，才能进入语义检索结果。

完成标准：你能解释“为什么摘要不是长期记忆”“为什么候选不能直接提供给 Agent”“模型何时选择查询长期记忆”“冲突候选为何需要用户自行确认新值并撤销旧值”。旧 `memory_facts` 只保留待显式迁移，在线链路不再读写该表。

## 5. 训练计划是一条独立、安全优先的业务链路（60 分钟）

从 [training_plans.py](../app/api/routers/training_plans.py) 进入 [training_plan_service.py](../app/services/training_plan_service.py)，然后读 [fitness_insights.py](../app/services/fitness_insights.py) 与 [schemas.py](../app/schemas.py) 中的 `WeeklyTrainingPlan`。

```mermaid
flowchart LR
  P[用户画像] --> S[FitnessSnapshot]
  C[Coros 近四周数据] --> S
  F[近期 RPE / 疼痛反馈] --> G[TrainingSafetyPolicy]
  S --> G
  P --> G
  G --> E[RAG 训练证据]
  E --> L[LLM 输出 WeeklyTrainingPlan JSON]
  G --> V[Pydantic + 业务校验]
  L --> V
  V -->|通过| DB[(TrainingPlan / Feedback)]
  V -->|失败| X[拒绝，不写半成品]
```

阅读时只关注顺序不能颠倒：先 `TrainingSafetyPolicy.assess` 计算最大强度，再调用模型；之后 `_validate_plan` 校验 7 天覆盖、训练天数、动作强度和证据 ID。Prompt 只能引导模型，不能替代业务门禁。

建议测试：

```powershell
.\.venv\Scripts\python.exe -m pytest app/tests/test_training_safety.py app/tests/test_training_plans.py app/tests/test_fitness_summary.py -q
```

## 6. COROS：把远程 MCP 收敛到实时读取 Gateway（45 分钟）

从 [coros.py](../app/api/routers/coros.py) 和 [fitness.py](../app/api/routers/fitness.py) 开始，依次读 [coros_oauth.py](../app/services/coros_oauth.py)、[coros_live_gateway.py](../app/services/coros_live_gateway.py) 和 [fitness_insights.py](../app/services/fitness_insights.py)。最后对照 `test_coros_oauth.py`、`test_coros_live_gateway.py` 与 `test_fitness.py`。

需要特别掌握的事件流：

1. 用户在 Dashboard 发起 `POST /api/coros/connect`；服务端创建短期 `state`、PKCE verifier、动态 client 注册，并只保存 state hash 与加密 verifier。
2. 官方回调只依据 state 绑定 FitAgent 用户，换取的 access/refresh token 用 Fernet 加密写入 `coros_connections`；state 成功、过期或失败后都不可重放。
3. `GET /api/fitness/snapshot` 每次只为当前用户创建带其 bearer token 的 HTTP MCP session，固定读取活动、日健康、睡眠三项工具；无本地子进程、SQLite cache 或运动数据落库。
4. Gateway 先验证工具白名单和日期范围 schema，再把响应裁剪为 `LiveFitnessData`；单源失败返回 `partial`，全源失败才报上游不可用。
5. 聊天 Agent 仅在用户明确询问运动数据时调用汇总工具；单次活动必须先在同日实时候选中验证 external id 后才读取详情。
6. 训练计划在已连接时必须取得完整的近四周快照；未连接才走画像/RAG 路径，局部或全量读取失败都拒绝生成。

## 7. 健康文档：上传后必须由用户确认（30 分钟）

这条链路独立于首次建档：健康文件的选择、提取结果确认和画像写入都在 [Chat.vue](../frontend/src/views/Chat.vue)；[Onboarding.vue](../frontend/src/views/Onboarding.vue) 只负责基础画像表单。

```mermaid
sequenceDiagram
  participant UI as Chat.vue
  participant U as upload.py
  participant P as doc_parser.py
  participant Profile as profile.py
  participant DB as Profile.health_data
  UI->>U: POST /api/upload/health-doc (file)
  U->>P: handle_upload / parse_health_doc
  P-->>UI: metrics + conflicts + messages
  UI->>UI: 用户编辑并解决冲突
  UI->>Profile: PUT /api/profile (health_data)
  Profile->>DB: 仅确认后写入
```

按 `Chat.vue` 的 `handleFileSelect` → `upload.py` 的 `upload_health_doc` → `doc_parser.py` 的 `parse_health_doc` → `Chat.vue` 的 `confirmHealthData` → `profile.py` 阅读。回答时要强调：提取结果不是医疗诊断；用户取消、关闭或保留未解决冲突时，不会写入画像。

## 8. 最后再读数据模型、初始化、API 契约与前端页面（45 分钟）

此时再读 [models.py](../app/models.py)、[schemas.py](../app/schemas.py) 和 [database.py](../app/core/database.py)，你会知道每一张表和每个 Schema 为哪条事件流服务，以及服务如何在启动时创建缺失的数据库表。

前端按用户闭环读：

1. [Onboarding.vue](../frontend/src/views/Onboarding.vue) / [Profile.vue](../frontend/src/views/Profile.vue)：画像；
2. [Dashboard.vue](../frontend/src/views/Dashboard.vue)：COROS 连接、断开、实时刷新与图表；
3. [Memory.vue](../frontend/src/views/Memory.vue)：确认/撤销候选；
4. [TrainingPlan.vue](../frontend/src/views/TrainingPlan.vue)：显式生成计划和提交反馈；
5. [Chat.vue](../frontend/src/views/Chat.vue)：RAG/Agent/SSE/证据，以及健康文档的确认入口。

## 建议的 7 天学习安排

| 天 | 主题 | 可交付的口述结果 |
| --- | --- | --- |
| Day 1 | 架构与通用聊天主链路 | 用 3 分钟讲完一次 Direct RAG SSE 请求 |
| Day 2 | Agent 工具与执行轨迹 | 解释何时进入 Agent、工具为何受限 |
| Day 3 | 离线/在线 RAG | 解释显式重建、一次 Qdrant Query API、BM25、RRF、DashScope 二阶段重排与证据卡片 |
| Day 4 | 三层记忆 | 解释候选—确认—撤销与防模型污染 |
| Day 5 | 训练计划 | 解释“先规则、后生成、再校验” |
| Day 6 | COROS/MCP 与 OAuth | 解释 PKCE、逐用户 token、工具白名单、partial 与无运动数据落库 |
| Day 7 | 演示与面试 | 参考 [interview/项目简介.md](./interview/项目简介.md) 演练，并回答 [interview/常见面试题.md](./interview/常见面试题.md) |

## 卡住时的排查顺序

1. **先确定用户动作对应哪个 HTTP API**：看浏览器 Network 或前端 `src/api/`。
2. **再找到 Router**：它定义鉴权、输入 Schema、HTTP/SSE 输出与事务边界。
3. **进入 Service，而不是直接钻第三方 SDK**：先理解本项目做了什么决策。
4. **查看同名测试**：测试通常比旧文档更能说明当前保证了什么边界。
5. **最后才看模型、Qdrant、MCP 的外部实现**：它们是依赖，不应模糊项目自己的责任边界。

学习过程中请始终用这五个问题检查一段代码：**谁调用它？输入从哪里来？它做出什么业务决策？状态写到哪里？失败时如何表现？** 只要能回答这五点，你就已经能把局部代码串回完整事件流。
