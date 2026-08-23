# 05 - 数据治理与运行边界

> **状态**：为面试 Demo 保留可追溯、可重建、可验证的最小数据治理；重型运维能力按明确条件演进。

## 1. 当前数据边界

| 数据 | 权威来源与写入方 | 在线读取方 | 当前保障 |
|---|---|---|---|
| 账户、画像、会话、消息 | MySQL；各自 API 路由 | API、Agent 工具 | SQLAlchemy 模型、Pydantic API Schema、Alembic 迁移、请求/工具统一事务边界。|
| 高驰运动数据 | `fitness` 同步路由 | `get_fitness_summary`、数据面板读接口 | 以 `(user_id, data_type, external_id)` 幂等写入；日指标/睡眠按日期生成 ID，活动优先使用上游 ID，从而保留同日多次活动。|
| 中文健身知识源 | `data/` 下经审核的 Markdown / TXT / PDF | 离线索引器 | 来源文件 SHA256、来源元数据、标题感知切分、内容去重。|
| Qdrant 向量索引 | `knowledge_indexer` 显式构建 | `RagSummarizeService` | revision collection + `rag_active` alias；发布前校验，在线只读。|
| BM25 工件 | 与同一 revision 一同离线生成 | `RagSummarizeService` | 启动预加载；工件缺失时降级为 Dense 检索。|
| 上传的健康文档 | 上传接口临时处理 | 文档解析服务 | MIME 校验、用户确认后才写入画像。|

> Agent 执行轨迹由聊天 SSE 收尾时的独立事务写入，在线通过会话轨迹只读接口查询。轨迹只保留请求 ID、模式、状态、耗时、工具名与参数类型；不保存问题、参数值、回复原文或异常原文。

## 2. 当前已完成的治理能力

### 2.1 可重建的知识索引

索引不是运行时副作用。知识文件变更后执行：

```powershell
docker compose up -d qdrant
.\.venv\Scripts\python.exe -m app.services.knowledge_indexer
```

索引器将文件版本、切分与去重配置共同计算为 revision，生成新的 collection；写入和校验通过后才切换 `rag_active`。旧 collection 没有被在线服务覆盖，因此问题可回溯到当时的知识版本。

在调用 embedding 前，可先运行不访问 Qdrant 的数据预检：

```powershell
.\.venv\Scripts\python.exe -m app.services.knowledge_preflight
```

预检复用正式索引器的读取、清洗、去重和切片逻辑，检查最小来源数、最小切片数、`chunk_id` 唯一性和切片来源引用；报告写入 Git 忽略的 `storage/rag/index_preflight_report.json`。正式构建也会执行同一门禁，并把来源级文档数、切片数、清洗统计和告警写入 index manifest。它不调用 embedding、不会创建 collection 或切换 alias。

### 2.2 数据库变更入口

关系型模型的变更通过 Alembic 迁移，而不是由路由启动时自动改表：

```powershell
alembic revision --autogenerate -m "describe_change"
alembic upgrade head
```

新增或修改 API 字段时，必须同步检查 ORM 模型、Pydantic schema、迁移和相关测试；前端不直接依赖数据库字段结构。

Agent 轨迹对应迁移 `20260724_02_agent_traces`。部署新版本后执行 `alembic upgrade head`；它只创建 `agent_runs` 与 `agent_tool_calls` 两张表，不影响 Qdrant revision，也不需要重建索引。

### 2.3 运行检查

- `GET /api/health/rag` 验证当前 `rag_active` revision 可读；
- Qdrant 容器通过 `docker compose ps qdrant` 查看健康状态；
- `RAG_RETRIEVAL` 日志记录本次检索使用的 revision、候选数与耗时；
- 在线接口不触发知识库导入、向量重建或 BM25 全量构建。

### 2.4 可重复的检索基线

仓库内已有 24 条覆盖动作、营养、防护、训练计划和基础知识的中文检索用例：
`app/evaluation/retrieval_cases.json`。评测不调用回答模型，只验证 Top-6 中是否召回预期来源和关键证据，因此同一 revision 的结果可以比较。

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.retrieval_evaluator
```

报告写入被 Git 忽略的 `storage/rag/retrieval_evaluation_report.json`，包含 `Recall@6`、Top-1 来源正确率、证据支持率、每条用例的命中 evidence ID，以及本次在线排序配置快照。运行需要 Qdrant 与 DashScope embedding 服务可访问。

## 3. 不把“治理”误做成无效复杂度

以下能力适合生产系统，但当前 Demo 没有触发条件，故不提前实现：

| 能力 | 为什么现在不做 | 何时必须做 |
|---|---|---|
| 将所有 JSON TEXT 拆成多张表 | 当前动态字段没有稳定的跨用户查询需求，过早拆表会让 schema 和页面更复杂。 | 出现高频筛选、统计或强约束查询，且字段已稳定。|
| 定时清理任务 | 需要明确保留期、审计要求和失败告警；“自动删除”不可逆。 | 有可量化的文件/日志增长和经确认的保留策略。|
| Qdrant 快照与跨主机恢复演练 | 单机 Demo 的源 Markdown 和显式索引命令已可重建。 | 数据不能从源重建、多个环境或有 RPO/RTO 要求。|
| Docker 化 MySQL、测试容器矩阵 | 会提高本地启动与 CI 成本。 | 团队协作、CI 不稳定或需要可重复的集成环境。|
| Coros 守护进程 / 自动重试编排 | 当前连接器是受控的按需外部依赖。 | 外部同步成为核心链路且失败率、队列积压可观测。|

## 4. 后续演进顺序

1. 使用当前 24 条中文检索评测集形成 revision 基线，用 Recall@6、来源正确率和证据覆盖率判断问题；
2. 对知识源变更先运行预检，再构建并比较检索基线；预检失败不发起昂贵的向量化；
3. 若数据模型的真实查询需求稳定，再为相关 JSON 字段设计迁移和回填；
4. 若部署到非本机环境，再补备份、恢复演练、监控告警与保留策略；
5. 每项演进都附带触发证据、回滚方案和自动化验证。

这条路径能在面试中清楚展示：项目并非忽略生产问题，而是以数据规模、风险和可测量信号决定何时增加复杂度。
