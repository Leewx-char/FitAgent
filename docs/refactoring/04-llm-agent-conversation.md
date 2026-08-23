# 04 - Agent 编排与工具调用

> **状态**：当前实现的 Agent/RAG 编排已完成；本文保留演进背景，并已同步当前记忆边界。
>
> **当前口径优先级**：若本文与 [架构说明](../architecture.md)、[学习路线](../learning-guide.md) 或代码不一致，以后三者为准。

## 1. 目标与边界

FitAgent 需要同时支持两类请求：

1. **通用健身知识问答**：以尽可能少的模型调用取得带证据的回答。
2. **个性化建议或外部操作**：结合用户画像、近 4 周运动数据、天气等工具，再由 Agent 决定调用顺序。

项目不为展示而拆分多 Agent，也不把用户在对话中随口表达的信息自动写入画像。前者会增加编排和调试成本，后者会造成错误健康数据落库的风险。

## 2. 当前调用路径

```text
用户消息
  ├─ 通用知识问题（深蹲、营养、运动防护等）
  │    └─ 直接 RAG 路径
  │         ├─ 查询规范化 / 标签识别
  │         ├─ Qdrant Dense + BM25 → RRF 融合 → 可选重排序
  │         ├─ 构造 [证据:N] 上下文
  │         └─ 一次模型调用生成回答
  │
  └─ 个性化、训练报告、天气等请求
       └─ 每请求一个 ReactAgent（LangGraph）
            ├─ rag_summarize
            ├─ get_user_profile / get_fitness_summary
            ├─ get_weather 等外部工具
            └─ 流式回答
```

直接 RAG 路径不是另一套检索实现，而是复用 `RagSummarizeService.build_context()`；因此两条路径的召回、证据编号和来源信息一致。

## 3. 已实施的关键设计

| 设计 | 实现位置 | 取舍 |
|---|---|---|
| 快速 RAG 路径 | `app/services/react_agent.py` | 通用问题避免 Agent 的“决定工具 + 最终回答”两次模型调用。|
| 请求级 Agent 实例 | `app/core/deps.py` | 不使用全局 Agent 单例，避免 LangGraph 运行时上下文在并发请求间共享；底层模型客户端仍由工厂缓存。|
| 有界历史 | `app/api/routers/chat.py`、`app/services/memory_service.py` | 仅取最近 10 轮（20 条）原文；较早 user 消息被确定性提取为可重建的 `session_summaries`，不把 LLM 摘要伪装为长期事实。|
| 请求级上下文 | `app/services/agent_tools.py`、`middleware.py` | `ContextVar` 保存当前用户信息；中间件将 RAG 证据交给 SSE 输出。|
| 统一 DB 会话 | `app/core/database.py` | `get_db_session()` 统一提交、异常回滚和关闭；HTTP 依赖和 Agent 的 MySQL 工具共用。|
| 证据可见性 | `frontend/src/views/Chat.vue` | SSE `evidence` 事件渲染为可展开来源卡片，回答中的 `[证据:N]` 可回溯。|
| 工具执行防护栏 | `middleware.py`、`react_agent.py` | 单轮递归/工具调用上限、请求级上下文 reset、无原文参数的结构化审计、安全错误降级。|

### 数据库会话边界

Agent 工具不会经过 FastAPI 的 `Depends`，但仍必须获得同样的事务保障：

```python
with get_db_session() as db:
    profile = db.query(UserProfile).filter(...).first()
```

上下文管理器保证成功时提交、异常时回滚、始终关闭会话。对应行为由 `app/tests/test_database_session.py` 覆盖；画像和运动摘要工具也继续由工具测试覆盖。

## 4. 延迟预期与观测

应用启动阶段只预加载离线 BM25 工件，不发起 embedding 或 Qdrant 检索。第一次通用知识问答的主要耗时应来自一次最终回答模型调用；后端日志中的 `RAG_RETRIEVAL` 记录检索耗时、候选数、revision 和是否启用查询规划。

前端 SSE 事件包括：

- `tool`：实际执行的工具或直接 RAG 检索；
- `evidence`：结构化来源卡片；
- `text`：模型逐步输出；
- `error`：本轮生成异常的用户可见提示；
- `data: [DONE]`：SSE 流的最终哨兵。它不是 JSON `done` 事件，前端收到后必须结束加载状态。

### 工具调用的生产防护栏

`AGENT_MAX_STEPS`（默认 8）限制 LangGraph 的递归步数，`AGENT_MAX_TOOL_CALLS`（默认 6）限制一轮实际工具调用次数。超过预算时，中间件返回一个让模型综合已有信息的 ToolMessage，而不是继续循环调用。

每次工具调用都会输出 `AGENT_TOOL_CALL` JSON 日志，至少包含请求 ID、工具名、参数**形状**、耗时、状态和本轮第几次调用；不记录问题文本、城市或异常原文。异常仍保留服务器端堆栈用于排查，但回给模型的是固定的安全降级提示，避免把连接串、令牌或内部异常扩散进回答。

工具执行时临时写入 `_user_context`，结束后通过 `ContextVar.reset()` 恢复，避免线程池复用时遗留上一个用户的画像或检索历史。

### 可查询的执行轨迹

结构化日志用于实时排障，但不适合回答“这个会话刚才调用了什么工具、慢在哪里”。因此每轮聊天结束后，服务会把无敏感摘要写入 `agent_runs` 与 `agent_tool_calls`：执行模式、成功/失败状态、总耗时，以及每次工具调用的顺序、名称、参数**类型**、状态和耗时。

写入通过独立的 `get_db_session()` 事务完成；轨迹表不可用时只记录服务端异常，不会影响已经流式返回的聊天结果。已登录用户可用 `GET /api/sessions/{session_id}/agent-runs` 读取自己的会话轨迹。该接口不返回问题原文、工具参数值、模型输出或异常原文。

## 5. 明确延后的能力

| 能力 | 暂不实现的原因 | 触发条件 |
|---|---|---|
| Agent 单例或实例池 | 当前实例隔离更安全，尚无 graph 初始化的性能证据。 | 压测确认实例构建成为主要瓶颈，并能证明运行时状态可安全隔离。|
| LLM 对话摘要记忆 | 当前已采用不调用 LLM 的确定性会话暂存状态；不引入模型自由摘要，避免错误压缩和事实污染。 | 确有未被结构化事实覆盖的长任务上下文需求，且评测证明固定事实摘要不足时。|
| LLM 自动事实提取并写画像 | 健康/伤病信息不能因模型误判直接落库。 | 有用户确认界面、字段级审计和准确率评测。|
| 多 Agent / Supervisor | 增加路由、状态和可观测复杂度，当前一个 Agent + 快速 RAG 已覆盖场景。 | 有互不相同的长任务和可量化的成功率或成本收益。|
| 完整意图分类服务 | 现有规则只承担“是否走直接 RAG”的窄职责。 | 规则误判可量化，且需要至少三个稳定意图分支。|

## 6. 验收方式

1. 启动后端并查看 `RAG 预热完成` 日志；
2. 提问“深蹲时膝盖应该朝哪里？”：应走直接 RAG，前端可展开证据卡片；
3. 提问“结合我的膝盖不适和近四周数据，今天怎么练？”：应进入 Agent 工具路径；
4. 执行 `pytest app/tests/test_direct_rag_router.py app/tests/test_database_session.py`。
5. 执行 `pytest app/tests/test_agent_execution_policy.py`，验证工具预算、隐私审计与递归上限。
6. 执行 `alembic upgrade head` 后完成一次聊天，调用 `GET /api/sessions/{session_id}/agent-runs`，验证可查询的工具顺序与耗时不包含原文数据。

完整演示脚本见 [面试演示指南](../interview-demo.md)。
