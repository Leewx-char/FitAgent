# FitAgent 面试演示指南

目标是在 5–7 分钟内展示一个可运行、可解释、可演进的 Agentic RAG 最小闭环。

## 1. 演示前准备

```powershell
# 终端 1：Qdrant 与后端
docker compose up -d qdrant
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000

# 终端 2：前端
cd frontend
npm run dev
```

首次启动或知识源变更后，先执行：

```powershell
.\.venv\Scripts\python.exe -m app.services.knowledge_preflight
.\.venv\Scripts\python.exe -m app.services.knowledge_indexer
```

浏览器访问 `http://localhost:5173`，登录后进入聊天页。

## 2. 推荐演示顺序

| 时间 | 操作 | 想说明的工程点 |
|---|---|---|
| 0:00–0:40 | 打开 `GET /api/health/rag` 或 Qdrant dashboard | 向量库有健康检查；在线服务只读当前 `rag_active` revision。|
| 可选 | 展示 `storage/rag/index_preflight_report.json` | 数据在向量化前已检查来源数、切片数、唯一 ID 和来源级统计；失败不会创建 collection。|
| 0:40–2:00 | 提问“深蹲时膝盖应该朝哪里？” | 通用问题走直接 RAG：Dense + BM25 + RRF，一次回答模型调用。|
| 2:00–2:40 | 展开“证据来源” | 回答中的 `[证据:N]` 与来源卡片、切片 ID、标签一一对应。|
| 2:40–3:20 | 提问“我住成都、想减脂，膝盖偶尔不适”后打开“我的记忆” | 聊天只生成待确认候选；确认、撤销和过期由用户控制，assistant 消息不能污染事实。|
| 3:20–4:20 | 在“本周计划”生成计划，并提交一天 RPE/疼痛反馈 | 计划由 Coros 聚合快照、RAG 证据和确定性安全策略共同约束；展示安全强度上限。|
| 4:20–5:00 | 展示 `RAG_RETRIEVAL` 日志或 Coros 数据面板 | 可看到 revision、Dense/BM25 候选数、选择数和耗时；说明同日多活动按 external id 幂等。|
| 5:00–5:30 | 调用 `GET /api/sessions/{session_id}/agent-runs` | 展示一次 Agent 的工具顺序、状态与耗时；强调只存参数类型，不存问题和工具实参。|
| 5:30–6:00 | 打开 `architecture.md` | 说明离线构建/在线检索分离、记忆确认边界与计划安全门禁。|
| 可选 | 运行 `python -m app.evaluation.retrieval_evaluator` | 以 24 条中文问题输出当前 revision 的 Recall@6、来源正确率和证据支持率。|

## 3. 一分钟架构说明

```text
审核后的中文知识源
  → 离线切分、去重、embedding
  → Qdrant revision + BM25 工件
  → rag_active alias
  → 在线 Dense / BM25 / RRF 检索
  → 带 [证据:N] 的生成回答和来源卡片
```

个人数据走 MySQL，知识检索走 Qdrant；两者只在 Agent 与计划服务的编排层汇合。API、服务和仓储层各自承担 HTTP、业务流程、第三方/数据库访问职责。

## 4. 面试中应主动说明的取舍

- 没有一开始就上多 Agent、集群和快照：这些能力没有当前数据量和协作规模的触发证据。
- 为了体验，通用知识问答不经过完整 Agent 决策；但个性化和外部工具请求仍由 Agent 编排。
- Agent 运行有步骤与工具预算，并持久化无敏感执行轨迹；因此能定位慢调用或异常链路，而不会把用户原文带入审计数据。
- 当前引用是检索证据，不等于医学诊断；健康文档写入需要用户确认。
- 长期记忆不是模型自动写库：只读已确认项，候选、撤销和过期项不会被 Agent 当作事实。
- 周计划不是“模型说了算”：模型 JSON 仍要经过固定安全策略、训练日/强度/证据 ID 校验；当前不自动下发到设备。
- 本地 Coros 使用社区 `cygnusb/coros-mcp`，固定提交并隔离到 `.tools` 虚拟环境，避免 FastMCP 依赖污染 FastAPI；stdio 调用被串行化，超时后销毁并重建进程，且仅开放只读工具。真实同步演示前需完成该 MCP 的独立认证。
- 生产下一步不是盲目加组件，而是先用小型中文评测集识别“召回、排序还是生成”中的具体短板；本项目已据此对低信息量外部三元组加入可配置的排序软惩罚，而非直接过滤来源。

## 5. 可复现实验

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app
.\.venv\Scripts\python.exe -m ruff check app
.\.venv\Scripts\python.exe -m pytest app/tests
npm --prefix frontend run build
```

重点测试包括：`test_direct_rag_router.py`（快速路径及 SSE 事件顺序）、`test_memory.py`（候选/确认/撤销）、`test_training_safety.py`（安全强度与证据拒绝）、`test_coros_client.py`（stdio 超时重置）、`test_rag_service.py`（检索融合）和 `test_database_session.py`（事务边界）。

检索基线需要本机能访问 DashScope embedding 服务；它不调用回答模型，生成的 JSON 报告默认不提交 Git。
