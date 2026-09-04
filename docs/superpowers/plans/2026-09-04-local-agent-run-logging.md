# 轻量本地 Agent 运行记录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangChain 官方 `RunCollectorCallbackHandler` 取代手写 `AgentTrace`，在本地 MySQL 保存每轮问题、最终回答和工具输入输出。

**Architecture:** 聊天 SSE 入口为每轮请求创建一个 Collector，并以 `RunnableConfig.callbacks` 传入编译后的 LangGraph。Collector 只在内存中保留官方运行树；仓储在流结束后将根运行和工具运行投影到既有 `agent_runs`、`agent_tool_calls`。API 保持会话级鉴权和既有路径，只扩展响应字段。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Alembic、LangChain Core 1.4、LangGraph 1.2、pytest、Ruff。

**Spec:** `docs/superpowers/specs/2026-09-04-local-agent-run-logging-design.md`

## Global Constraints

- 仅使用已安装的 `langchain_core.tracers.run_collector.RunCollectorCallbackHandler`；不得导入 `LangChainTracer` 或 LangSmith。
- 保留 `agent_runs`、`agent_tool_calls` 与 `GET /api/sessions/{session_id}/agent-runs`，不新增日志表或 HTTP 路由。
- 日志持久化只能在 SSE 结束后通过短生命周期数据库会话执行，Collector 和图节点不得直接访问数据库。
- `AgentTrace`、`AgentToolTrace` 与运行时上下文中的 `trace` 字段必须删除；工具预算和业务日志行为保持不变。
- 新增或修改的函数、类使用不超过三行的精确中文文档字符串；不写冗余护栏。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/repositories/agent_trace_repository.py` | 将官方 Collector 运行树投影为现有 ORM 运行记录。 |
| `app/models.py` | 定义问题、回答、工具输入和工具输出的列。 |
| `app/schemas.py` | 将新增文本字段及 JSON 文本安全还原到会话级响应。 |
| `alembic/versions/20260904_04_local_agent_run_logging.py` | 从当前 Alembic 头版本演进两张既有表。 |
| `app/services/react_agent.py` | 透传 `RunnableConfig`，并把直接 RAG 检索包装为可收集 Runnable。 |
| `app/services/chat_routing_graph.py` | 将运行配置从图节点传给分类、直接 RAG 和内层 Agent。 |
| `app/services/middleware.py` | 移除手写 Trace 写入，不改变预算和错误返回。 |
| `app/api/routers/chat.py` | 创建 Collector、传入回调、在流结束后写入投影结果。 |
| `app/services/agent_trace.py` | 删除已废弃的手写轨迹模型。 |
| `app/tests/test_agent_trace.py` | 覆盖 Collector 到数据库投影的输入、输出、异常与顺序。 |
| `app/tests/test_agent_runtime_context.py` | 验证配置在图、意图分类、直接 RAG 和内层 Agent 间传递。 |
| `app/tests/test_chat.py` | 验证 SSE 成功/异常路径保存完整运行摘要。 |

### Task 1: 将官方运行树投影为现有数据模型

**Files:**
- Modify: `app/models.py:107-155`
- Modify: `app/schemas.py:99-127`
- Modify: `app/repositories/agent_trace_repository.py`
- Create: `alembic/versions/20260904_04_local_agent_run_logging.py`
- Modify: `app/tests/test_agent_trace.py`

**Interfaces:**
- Consumes: `RunCollectorCallbackHandler.traced_runs: list[Run]`、`Run.id`、`Run.child_runs`、`Run.run_type`、`Run.inputs`、`Run.outputs`、`Run.error`、`Run.start_time`、`Run.end_time`。
- Produces: `AgentTraceRepository.save(db, collector, *, request_id, session_id, user_id, user_question, assistant_answer, status) -> AgentRun`。

- [ ] **Step 1: 写 Collector 投影的失败测试**

```python
from langchain_core.tools import tool
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler


def test_repository_projects_tool_input_output_and_failure():
    """官方 Collector 的工具树应保存真实输入、输出和异常。"""
    @tool
    def get_weather(city: str) -> dict[str, str]:
        """返回测试城市的天气结果。"""
        return {"city": city, "weather": "晴"}

    collector = RunCollectorCallbackHandler()
    get_weather.invoke({"city": "北京"}, config={"callbacks": [collector]})
    db = FakeSession()

    run = AgentTraceRepository.save(
        db, collector, request_id="request-2", session_id="b" * 32, user_id=7,
        user_question="北京天气怎么样？", assistant_answer="北京晴。", status="succeeded",
    )

    assert run.user_question == "北京天气怎么样？"
    assert db.added[1].tool_input == '{"city": "北京"}'
    assert db.added[1].tool_output == '{"city": "北京", "weather": "晴"}'
```

再增加工具抛出 `RuntimeError("weather unavailable")` 的断言：保存行的 `status == "failed"`，且 `tool_output` 含错误文本；构造一个父 Run 含两个子工具运行，断言按 `start_time` 顺序为 1、2。

- [ ] **Step 2: 运行测试，确认旧接口无法接收 Collector**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_agent_trace.py -v`  
Expected: FAIL，提示 `AgentTraceRepository.save` 缺少新参数或模型没有 `user_question` / `tool_input`。

- [ ] **Step 3: 修改 ORM、响应模型和仓储投影**

```python
class AgentRun(Base):
    """保存一轮聊天的摘要、问题与最终回答。"""

    user_question = Column(Text, nullable=False, default="")
    assistant_answer = Column(Text, nullable=False, default="")


class AgentToolCall(Base):
    """保存 Collector 采集的单次工具输入和输出。"""

    tool_input = Column(Text, nullable=False, default="{}")
    tool_output = Column(Text, nullable=False, default="")
```

实现以下仓储签名：

```python
def save(
    db: DBSession,
    collector: RunCollectorCallbackHandler,
    *,
    request_id: str,
    session_id: str,
    user_id: int,
    user_question: str,
    assistant_answer: str,
    status: str,
) -> AgentRun:
    """将本次官方运行树投影为会话可查询的本地记录。"""
```

递归遍历 `collector.traced_runs` 的 `child_runs`，只保留 `run_type == "tool"` 或 `tags` 含 `"agent_tool"` 的运行，按 `start_time` 排序。用 `json.dumps(value, ensure_ascii=False, default=...)` 序列化 `inputs`、`outputs` 与 `error`；工具行状态由 `run.error` 决定。根运行 ID 去掉连字符后用作 `AgentRun.id`，没有根运行时使用 `uuid4().hex`；根运行耗时由 `start_time`、`end_time` 计算，没有根运行时为零。运行摘要的 `mode` 固定为 `"chat"`。

`AgentToolCallResponse` 改为 `tool_input: Any` 和 `tool_output: Any`：JSON 文本用 `json.loads` 还原，旧行中不是 JSON 的文本原样返回。`AgentRunResponse` 增加 `user_question`、`assistant_answer`。

- [ ] **Step 4: 写并验证表迁移**

```python
revision: str = "20260904_04"
down_revision: str | None = "20260817_03"

op.add_column("agent_runs", sa.Column("user_question", sa.Text(), nullable=False, server_default=""))
op.add_column("agent_runs", sa.Column("assistant_answer", sa.Text(), nullable=False, server_default=""))
op.alter_column(
    "agent_tool_calls", "argument_shape", new_column_name="tool_input",
    existing_type=sa.Text(), existing_nullable=False,
)
op.alter_column(
    "agent_tool_calls", "detail", new_column_name="tool_output",
    existing_type=sa.String(length=120), type_=sa.Text(), existing_nullable=False,
)
```

在 `downgrade()` 中反向重命名列、把 `tool_output` 收窄回 `String(120)`，并删除 `agent_runs` 的两个新增列。

Run: `& .\\.venv\\Scripts\\python.exe -m alembic upgrade head --sql`  
Expected: 输出从 `20260817_03` 到 `20260904_04` 的新增与改列 SQL，且不连接数据库。

- [ ] **Step 5: 运行投影测试并提交**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_agent_trace.py -v`  
Expected: PASS，覆盖成功工具、异常工具、嵌套顺序和无根运行摘要。

```bash
git add app/models.py app/schemas.py app/repositories/agent_trace_repository.py \
  alembic/versions/20260904_04_local_agent_run_logging.py app/tests/test_agent_trace.py
git commit -m "feat: persist collected agent runs"
```

### Task 2: 在图和执行器中透传官方回调配置

**Files:**
- Modify: `app/services/react_agent.py:39-311`
- Modify: `app/services/chat_routing_graph.py:54-232`
- Modify: `app/services/middleware.py:1-150`
- Modify: `app/tests/test_agent_runtime_context.py`
- Modify: `app/tests/test_direct_rag_router.py`

**Interfaces:**
- Consumes: `RunnableConfig` 的 `callbacks` 与 `recursion_limit`。
- Produces: `ReactAgent.execute_stream(..., config: RunnableConfig | None = None)`、`DirectRagExecutor.stream(..., config: RunnableConfig | None = None)`；分类器和执行节点接收同一配置。

- [ ] **Step 1: 写配置透传的失败测试**

```python
def test_direct_rag_uses_collector_for_named_retrieval_runnable():
    """直接检索步骤应作为 agent_tool 运行被 Collector 捕获。"""
    collector = RunCollectorCallbackHandler()
    executor = DirectRagExecutor(model=FakeModel(), rag_service_factory=FakeRagService)

    list(executor.stream(query="深蹲怎么做？", history=[], config={"callbacks": [collector]}))

    assert any(
        run.name == "rag_summarize" and "agent_tool" in run.tags
        for root in collector.traced_runs
        for run in [root, *root.child_runs]
    )
```

另加断言：意图分类器的 `.invoke(..., config=config)`、内层 `self.agent.stream(..., config=...)` 与 `routing_graph.stream(..., config=...)` 均收到同一回调配置；`ChatRuntimeContext` 没有 `trace` 属性。

- [ ] **Step 2: 运行范围测试，确认当前实现未透传配置**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_agent_runtime_context.py -v`  
Expected: FAIL，显示 `execute_stream`、分类器或直接 RAG 还不接受/传递 `config`，且运行时上下文仍有 `trace`。

- [ ] **Step 3: 实现统一配置传递与直接 RAG 可追踪步骤**

```python
def stream(
    self,
    *,
    query: str,
    history: list[dict],
    config: RunnableConfig | None = None,
) -> Iterator[dict]:
    """执行直接检索并让调用配置贯穿检索与模型流。"""
    rag_context = self._rag_context_runnable.invoke(
        {"query": query, "history": history}, config=config
    )
```

在 `DirectRagExecutor.__init__` 创建：

```python
self._rag_context_runnable = RunnableLambda(self._build_rag_context).with_config(
    run_name="rag_summarize", tags=["agent_tool"]
)
```

`_build_rag_context(payload)` 调用原有 `rag_service_factory().build_context(payload["query"], history=payload["history"])`，不改变检索业务逻辑。模型 `.stream` 接收相同 `config`。

`ChatRuntimeContext` 只保留 `user_id`、`city`、`session_id`、`dependencies`。删除 `context.trace` 读写和中间件的 `_record_trace_event`；保留工具调用上限、`_log_tool_event` 与受控错误 `Command`。图的分类、直接 RAG、个性化节点声明 `config: RunnableConfig` 参数，并把它传给分类器、执行器和内层 Agent；使用 `merge_configs(config, {"recursion_limit": self.max_steps})` 给内层 Agent 增加步数限制。

- [ ] **Step 4: 运行范围测试并提交**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_agent_runtime_context.py app/tests/test_direct_rag_router.py -v`  
Expected: PASS，直接 RAG 包含命名检索运行，个性化路径保留工具预算，且无运行时 `trace` 属性。

```bash
git add app/services/react_agent.py app/services/chat_routing_graph.py \
  app/services/middleware.py app/tests/test_agent_runtime_context.py \
  app/tests/test_direct_rag_router.py
git rm app/services/agent_trace.py
git commit -m "feat: collect agent runs through callbacks"
```

### Task 3: 在 SSE 收尾保存 Collector 结果并维持查询兼容性

**Files:**
- Modify: `app/api/routers/chat.py:10-177`
- Modify: `app/tests/test_chat.py`
- Modify: `app/api/routers/agent_runs.py:17-39`（仅在类型推断要求显式修改时）

**Interfaces:**
- Consumes: `ReactAgent.execute_stream(..., config={"callbacks": [collector]})` 与 `AgentTraceRepository.save(...)`。
- Produces: 成功或失败流结束后保存的 `AgentRun`，包含原始用户问题、面向客户端的最终回答及 Collector 工具明细。

- [ ] **Step 1: 写 SSE 成功与失败持久化的失败测试**

```python
def test_sse_saves_collected_question_answer_and_status(monkeypatch):
    """成功流结束后应将问题、回答和 Collector 交给仓储。"""
    saved = {}
    monkeypatch.setattr(
        chat_router.AgentTraceRepository,
        "save",
        lambda _db, collector, **kwargs: saved.update(collector=collector, **kwargs),
    )

    chunks = asyncio.run(collect_sse())

    assert chunks[-1] == "data: [DONE]\\n\\n"
    assert saved["user_question"] == "深蹲怎么做？"
    assert saved["assistant_answer"] == "膝盖跟随脚尖。"
    assert saved["status"] == "succeeded"
    assert isinstance(saved["collector"], RunCollectorCallbackHandler)
```

为异常流增加断言：`status == "failed"`，已有友好错误 SSE 与 `[DONE]` 不变。

- [ ] **Step 2: 运行聊天测试，确认旧路径不满足新断言**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_chat.py -v`  
Expected: FAIL，旧实现创建 `AgentTrace` 且没有把 Collector、问题或回答传给仓储。

- [ ] **Step 3: 在 SSE 入口创建并保存 Collector**

```python
collector = RunCollectorCallbackHandler()
gen = iter(
    agent.execute_stream(
        messages,
        user_id=user_id,
        session_id=session_id,
        session_summary=session_summary,
        config={"callbacks": [collector]},
    )
)
```

在原有 `try/except` 后保持 `stream_failed` 语义，并调用：

```python
AgentTraceRepository.save(
    trace_db,
    collector,
    request_id=request_id_var.get(),
    session_id=session_id,
    user_id=current_user.id,
    user_question=user_message,
    assistant_answer=full_response.strip(),
    status="failed" if stream_failed else "succeeded",
)
```

保留独立 `get_db_session()`、记录失败不影响 SSE、助手消息落库、标题生成和敏感文本向客户端脱敏。移除 `AgentTrace` 导入和变量。

- [ ] **Step 4: 运行聊天测试并提交**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_chat.py app/tests/test_agent_trace.py -v`  
Expected: PASS，成功/失败记录均完整，`GET` 响应可序列化新增字段，SSE 事件格式不变。

```bash
git add app/api/routers/chat.py app/api/routers/agent_runs.py app/tests/test_chat.py
git commit -m "feat: save local agent run content"
```

### Task 4: 完整验证与文档同步

**Files:**
- Modify: `docs/learning-guide.md`
- Modify: `docs/superpowers/specs/2026-09-04-local-agent-run-logging-design.md`（仅在实现发现与已确认设计不一致时）

**Interfaces:**
- Consumes: 已完成的迁移、仓储、图配置和 SSE 集成。
- Produces: 与实现一致的学习文档及可复现验证结果。

- [ ] **Step 1: 搜索并更新过时的 AgentTrace 文档描述**

```powershell
rg -n "AgentTrace|agent_tool_calls|执行轨迹" docs README.md app
```

仅更新仍描述“只保存安全元数据”或“运行时 context 含 trace”的文字；保留与本次无关的历史设计文档，不重写项目文档结构。

- [ ] **Step 2: 执行本次相关测试套件**

Run: `& .\\.venv\\Scripts\\python.exe -m pytest app/tests/test_agent_trace.py app/tests/test_agent_runtime_context.py app/tests/test_direct_rag_router.py app/tests/test_chat.py -v`  
Expected: PASS。

- [ ] **Step 3: 执行代码质量、迁移 SQL 与全量测试**

Run:

```powershell
& .\\.venv\\Scripts\\python.exe -m ruff check app
& .\\.venv\\Scripts\\python.exe -m ruff format --check app
& .\\.venv\\Scripts\\python.exe -m alembic upgrade head --sql
& .\\.venv\\Scripts\\python.exe -m pytest
```

Expected: Ruff 检查通过；新增或改动文件格式检查通过；迁移 SQL 可生成；全量测试通过。若仓库已有与本任务无关的格式、权限或弃用警告，记录其命令输出并和本次结果分开报告。

- [ ] **Step 4: 复查差异并提交验证与文档**

```bash
git diff --check
git status --short
git add docs/ app/ alembic/
git commit -m "docs: explain local agent run logging"
```

确认提交只包含本计划涉及的文件；若无文档变化，不创建空提交。

