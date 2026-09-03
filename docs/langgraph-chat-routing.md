# LangGraph 聊天路由改造说明

## 改造目标

本次改造将聊天请求从关键词启发式分流升级为 LangGraph 图编排。系统根据 LLM 的结构化意图判断选择直接 RAG 或个性化 Agent，同时保持 `/api/chat` 的 SSE 协议、MySQL 长期记忆和既有工具能力不变。

```mermaid
flowchart LR
    Question[用户问题] --> Classifier[结构化意图识别]
    Classifier -->|通用训练知识| DirectRag[直接 RAG]
    Classifier -->|个人计划、伤病、饮食或不确定| Personalized[个性化 Agent]
    DirectRag --> Stream[SSE 事件流]
    Personalized --> Stream
```

## 改造前后

改造前，`ReactAgent` 根据中英文关键词决定是否走直接 RAG。关键词很难表达“这个动作适合我吗”这类同时包含知识与个人上下文的问题，并且请求信息需要经进程级 `ContextVar` 传递给工具。

改造后，路由、短期状态与工具运行时数据的职责被明确拆分：

```mermaid
flowchart TB
    subgraph Before[改造前]
        B1[chat.py] --> B2[ReactAgent 关键词判断]
        B2 -->|命中关键词| B3[直接 RAG]
        B2 -->|其余问题| B4[ReAct Agent]
        B4 --> B5[ContextVar 传递用户上下文]
    end

    subgraph After[改造后]
        A1[chat.py] --> A2[ReactAgent.execute_stream]
        A2 --> A3[构造 GraphState 与 Runtime Context]
        A3 --> A4[LangGraph classify_intent]
        A4 -->|direct_rag| A5[直接 RAG 节点]
        A4 -->|personalized_agent| A6[个性化 Agent 节点]
        A5 --> A7[custom SSE 流]
        A6 --> A7
    end
```

## 当前请求链路

`chat.py` 只负责会话保存和 SSE 响应；它将稳定的 `session_id` 传入 `ReactAgent.execute_stream`，不再承担城市提取或业务分流。

```mermaid
sequenceDiagram
    participant C as 客户端
    participant API as chat.py
    participant RA as ReactAgent
    participant G as LangGraph
    participant LLM as 分类/回答模型
    participant T as RAG 与 Agent 工具

    C->>API: POST /api/chat
    API->>RA: messages, user_id, session_id, summary
    RA->>RA: 构造初始 ChatGraphState
    RA->>RA: 构造 ChatRuntimeContext
    RA->>G: stream(state, context)
    G->>LLM: 结构化意图分类
    alt 直接 RAG
        G->>T: 检索知识库并生成回答
    else 个性化 Agent 或分类失败
        G->>LLM: 执行现有 ReAct Agent
        LLM->>T: 调用画像、记忆、运动摘要等工具
    end
    T-->>G: tool / evidence / text 事件
    G-->>RA: custom stream
    RA-->>API: 既有 JSON SSE 行
    API-->>C: tool、evidence、text、error、[DONE]
```

## 状态边界

一次请求内可序列化的数据放入 `ChatGraphState`；需要信任或不可序列化的对象仅通过 `ChatRuntimeContext` 注入。跨会话数据仍然只由 MySQL 管理。

```mermaid
flowchart LR
    subgraph Runtime[ChatRuntimeContext：一次请求的可信依赖]
        R1[user_id / session_id / city]
        R2[追踪对象]
        R3[直接 RAG 与个性化执行器]
        R4[工具预算配置]
    end

    subgraph State[ChatGraphState：一次图执行的 JSON 数据]
        S1[消息与会话事实]
        S2[路由结果]
        S3[检索历史与证据]
        S4[工具计数与 SSE 事件]
    end

    subgraph Database[MySQL：长期数据]
        D1[SessionSummary]
        D2[MemoryFact]
    end

    Runtime -->|只读注入| State
    State -->|既有服务读写| Database
```

这条边界带来三个约束：

- GraphState 不写入 API 密钥、数据库连接、权限或跨请求共享对象。
- 直接 RAG 与个性化 Agent 在写回状态前都校验 JSON 安全性。
- 不启用 LangGraph Store 或 checkpointer，避免与 MySQL 的会话摘要、确认记忆形成双写来源。

## 意图识别与保守回退

分类器使用模型的 `with_structured_output(IntentDecision)`，只接受两种结果：`direct_rag` 或 `personalized_agent`。分类提示词只包含最后一条用户消息和最小会话事实。

```mermaid
flowchart TD
    Start([START]) --> Inspect[读取最后一条用户消息与最小事实]
    Inspect --> Invoke[调用结构化分类模型]
    Invoke --> Valid{输出有效且明确?}
    Valid -->|是：通用知识| Direct[direct_rag]
    Valid -->|是：需要个人上下文| Agent[personalized_agent]
    Valid -->|否：异常、超时或不明确| Agent
    Direct --> End([END])
    Agent --> End
```

因此，训练计划、伤病、饮食、历史训练数据、个人目标和任何模糊问题都会走个性化 Agent；模型不可用或输出不合规时也会保守回退，不会中断聊天。

## SSE 实时性与工具并发

图节点使用 LangGraph custom stream，在工具事件生成时立即输出，而不是等待整个节点结束后再回放状态。因此即使后续模型或检索发生异常，已经产生的 `tool` 或 `evidence` 事件仍会先到达客户端，之后再输出 `error` 与 `[DONE]`。

同一条模型消息可以并行提出多个工具调用。系统使用工具在当前批次中的稳定位置计算预算序号，并用 reducer 合并并行状态更新，避免多个工具同时写 `tool_call_count` 时触发状态冲突或绕过额度。

```mermaid
flowchart LR
    M[模型同批提出多个工具] --> P[按调用位置编号]
    P --> B{预算允许?}
    B -->|允许| Run[执行工具并记录事件]
    B -->|超限| Reject[返回受控拒绝结果]
    Run --> Merge[max/add reducer 合并状态]
    Reject --> Merge
    Merge --> SSE[实时 SSE custom stream]
```

## 主要代码职责

| 文件 | 职责 |
| --- | --- |
| `app/services/chat_routing_graph.py` | GraphState、运行时上下文、结构化分类、条件边与两个图节点。 |
| `app/services/react_agent.py` | 图执行门面、Direct RAG 执行器、内层 ReAct Agent 与 SSE 事件适配。 |
| `app/services/agent_tools.py` | 从 `ToolRuntime` 读取当前请求上下文，并把检索产物写回本次 Agent 状态。 |
| `app/services/middleware.py` | 工具预算、并行调用序号、审计日志和受控错误处理。 |
| `app/api/routers/chat.py` | 会话持久化与 SSE 响应，不承担聊天业务路由。 |

## 兼容性与验证

- `/api/chat` 的请求结构和 SSE 事件类型保持兼容。
- 客户端仍按 `tool`、`evidence`、`text`、`error` 和 `[DONE]` 消费流。
- 完整后端测试在合并前通过：147 项通过；警告仅来自既有依赖弃用提示和本地 Qdrant 的 payload-index 限制。
- `ruff check`、指定修改文件的 `ruff format --check`、编译检查和 `git diff --check` 均已通过。
