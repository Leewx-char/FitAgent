# 本地 Agent 运行记录架构

## 目标

本方案使用 LangChain 官方的 `RunCollectorCallbackHandler` 在每次聊天请求内收集运行树，随后将其投影到项目已有的 MySQL 表。它记录用户问题、最终回答及真实的工具输入输出，但不使用 LangSmith，也不增加独立的追踪服务。

核心原则：追踪属于可观测性能力，写入失败不能影响用户已收到的流式回答。

## 请求与采集数据流

```mermaid
flowchart LR
    U[用户] --> F[前端聊天页]
    F -->|POST /api/chat| R[Chat Router]
    R --> S[sse_generator]
    S -->|每请求创建| C[RunCollectorCallbackHandler]
    S -->|携带 callbacks 配置| A[ReactAgent.execute_stream]

    A --> G[LangGraph 路由图]
    G --> P[个性化 Agent]
    G --> D[Direct RAG]
    P --> T[LangChain 工具调用]
    D --> Q[带 agent_tool 标签的检索 Runnable]

    T -.子运行.-> C
    Q -.子运行.-> C
    A -.根运行与子运行.-> C

    A -->|text / tool / evidence 事件| S
    S -->|SSE| F
    S -->|流结束后，尽力写入| Repo[AgentTraceRepository]
    C --> Repo
    Repo --> Runs[(agent_runs)]
    Repo --> Calls[(agent_tool_calls)]

    Repo -.写入异常仅记录日志.-> L[应用日志]
```

`RunCollectorCallbackHandler` 是请求级对象，不跨请求复用。它接收 React Agent、工具与 Direct RAG 检索节点产生的运行事件；聊天路由只负责把 Collector 与会话元数据交给仓储层。

## 运行记录的落库结构

```mermaid
classDiagram
    direction LR

    class AgentRun {
        +id: CHAR(32)
        +request_id: VARCHAR(32)
        +session_id: CHAR(32)
        +user_id: INTEGER
        +mode: VARCHAR(20)
        +status: succeeded | failed
        +elapsed_ms: INTEGER
        +tool_call_count: INTEGER
        +user_question: TEXT
        +assistant_answer: TEXT
        +created_at: DATETIME
    }

    class AgentToolCall {
        +id: INTEGER
        +agent_run_id: CHAR(32)
        +sequence: INTEGER
        +tool_name: VARCHAR(80)
        +tool_input: TEXT (JSON)
        +tool_output: TEXT (JSON)
        +status: succeeded | failed
        +elapsed_ms: INTEGER
        +created_at: DATETIME
    }

    AgentRun "1" --> "0..*" AgentToolCall : 按 sequence 排序
```

| 采集来源 | 写入位置 | 保存内容 |
| --- | --- | --- |
| HTTP 请求与 SSE 聚合结果 | `agent_runs` | 问题、最终回答、会话、状态、总耗时、工具次数 |
| 官方 Collector 的工具子运行 | `agent_tool_calls` | 工具名、真实输入、真实输出或异常、单次耗时 |
| 未命中根运行的兜底 | `agent_runs` | 生成 UUID，仍保存本次问答与状态 |

## 一次请求的时序与失败隔离

```mermaid
sequenceDiagram
    autonumber
    participant Client as 前端
    participant Router as Chat Router / SSE
    participant Agent as ReactAgent + LangGraph
    participant Collector as RunCollector
    participant Repository as AgentTraceRepository
    participant DB as MySQL

    Client->>Router: 发送用户问题
    Router->>Collector: 创建请求级 Collector
    Router->>Agent: execute_stream(messages, callbacks)
    Agent->>Collector: 记录根运行
    Agent->>Collector: 记录工具和检索子运行
    Agent-->>Router: 流式 text / tool / evidence
    Router-->>Client: SSE 事件
    Agent-->>Router: 流结束或抛出异常
    Router->>Repository: save(Collector, 问题, 聚合回答, 状态)
    Repository->>DB: 写入 agent_runs
    Repository->>DB: 写入 agent_tool_calls

    alt 运行记录写入失败
        Repository-->>Router: 抛出数据库或迁移异常
        Router-->>Client: 保持既有 SSE 回答不受影响
        Router->>Router: 仅记录异常日志
    end
```

## 模块职责

```mermaid
flowchart TB
    API[api/routers/chat.py<br/>创建 Collector、聚合 SSE 文本、隔离写入异常]
    Agent[services/react_agent.py<br/>向 LangGraph 与 LangChain 传递 RunnableConfig]
    Graph[services/chat_routing_graph.py<br/>运行个性化或 Direct RAG 分支]
    Repo[repositories/agent_trace_repository.py<br/>从运行树提取工具并投影为数据库模型]
    Model[models.py / schemas.py<br/>定义本地记录与 API 返回结构]
    Migration[alembic/versions/20260904_04_...py<br/>扩展问答字段和工具明细列]

    API --> Agent --> Graph
    API --> Repo --> Model
    Repo --> Model
    Migration --> Model
```

## 配置与迁移边界

- 不配置 LangSmith，也不会将追踪数据上传到第三方云端。
- 迁移 `20260904_04` 为 `agent_runs` 增加 `user_question`、`assistant_answer`，并将工具字段升级为 `tool_input`、`tool_output` 文本列。
- 回滚到旧结构时，工具输入重置为 `{}`，工具输出截断为旧列允许的 120 字符；这是为了保证旧结构可恢复，代价是回滚会丢失详细追踪数据。
- 日志查询继续使用现有的会话与用户边界，避免新的跨用户数据访问入口。

## 阅读路径

1. 从 [`app/api/routers/chat.py`](../app/api/routers/chat.py) 查看 Collector 如何随 SSE 请求创建和持久化。
2. 从 [`app/repositories/agent_trace_repository.py`](../app/repositories/agent_trace_repository.py) 查看运行树到两张表的转换规则。
3. 从 [`alembic/versions/20260904_04_local_agent_run_logging.py`](../alembic/versions/20260904_04_local_agent_run_logging.py) 查看数据库迁移与回滚约束。
