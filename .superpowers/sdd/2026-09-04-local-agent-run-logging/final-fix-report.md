# 本地 Agent 运行记录最终修复报告

日期：2026-09-04
工作树：`D:\FitAgent\.worktrees\local-agent-run-logging`

## 发现与修复映射

| 审查发现 | 修复 | 验证 |
| --- | --- | --- |
| `20260817_03` 替换唯一索引时会短暂失去 `user_id` 前缀索引，可能破坏 MySQL 外键要求。 | 升级改为先建 `ix_fitness_user_type_external` 再删 `ix_fitness_user_date_type`；降级反向先建旧索引。 | 新增离线操作顺序测试；`upgrade head --sql` 和降级 SQL 均验证顺序。 |
| 四个测试仍传入或断言已删除的 `ChatRuntimeContext.trace`，且部分假对象不接受 `config`。 | 删除旧参数/断言；分类器、模型和图节点假对象接受 `config`，保留原有业务断言。 | 相关无需数据库测试通过，搜索四个文件无 `trace=` 或 `context.trace`。 |
| `20260904_04` 降级直接收窄 `tool_output`，可能触发严格 MySQL 截断，且 `tool_input` 不满足旧 JSON 对象契约。 | 重命名前先将 `tool_input` 统一设为 `'{}'`，将 `tool_output` 以 `LEFT(..., 120)` 截断。 | 新增离线操作顺序测试；降级 SQL 显示两个 `UPDATE` 在两次 `CHANGE` 之前。 |
| 新增测试存在 E501，模型的 `mode` 注释陈旧，图节点别名漏掉回调配置。 | 拆分测试长行；注释改为固定 `chat`；`ChatGraphNode` 改为第三参数 `RunnableConfig`。 | `ruff check app` 通过；相关图路由测试通过。 |

## TDD 记录

RED：

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m pytest app/tests/test_local_agent_run_logging_migrations.py -q
```

真实输出：`2 failed`。第一个失败显示创建替代索引事件序号 `4 < 3` 不成立；第二个失败显示降级没有任何 `execute` 数据规范化语句。

GREEN：

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m pytest app/tests/test_local_agent_run_logging_migrations.py -q
```

真实输出：`2 passed, 3 warnings in 0.14s`。警告为既有 `slowapi` 与 `pytest-asyncio` 弃用警告。

## 离线 SQL 顺序

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m alembic upgrade head --sql
```

`20260817_03` 输出顺序为：

```sql
CREATE UNIQUE INDEX ix_fitness_user_type_external ON fitness_data (user_id, data_type, external_id);
DROP INDEX ix_fitness_user_date_type ON fitness_data;
```

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m alembic downgrade 20260817_03:20260724_02 --sql
```

输出顺序为：

```sql
CREATE UNIQUE INDEX ix_fitness_user_date_type ON fitness_data (user_id, date, data_type);
DROP INDEX ix_fitness_user_type_external ON fitness_data;
```

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m alembic downgrade 20260904_04:20260817_03 --sql
```

输出顺序为：

```sql
UPDATE agent_tool_calls SET tool_input = '{}';
UPDATE agent_tool_calls SET tool_output = LEFT(tool_output, 120);
ALTER TABLE agent_tool_calls CHANGE tool_output detail VARCHAR(120) NOT NULL;
ALTER TABLE agent_tool_calls CHANGE tool_input argument_shape TEXT NOT NULL;
```

## 最终自检

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m pytest app/tests/test_local_agent_run_logging_migrations.py app/tests/test_agent_execution_policy.py app/tests/test_agent_rag_context.py app/tests/test_chat_routing_graph.py app/tests/test_direct_rag_router.py app/tests/test_fitness_summary.py::TestFitnessSummary::test_missing_user_id -q
```

真实输出：`33 passed, 9 warnings in 0.39s`。警告均为第三方弃用警告。

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m pytest app/tests/test_agent_trace.py app/tests/test_agent_runtime_context.py app/tests/test_chat.py::TestChat::test_sse_saves_collected_question_answer_and_status app/tests/test_chat.py::TestChat::test_sse_keeps_text_and_done_when_run_record_save_fails app/tests/test_chat.py::TestChat::test_sse_preserves_tool_event_before_graph_execution_error -q
```

真实输出：`16 passed, 7 warnings in 0.17s`。

```powershell
& 'D:\FitAgent\.venv\Scripts\python.exe' -m ruff check app
```

真实输出：`All checks passed!`。

`git diff --check` 无输出。新增迁移测试经 `ruff format --check` 单文件校验。全仓 `ruff format --check app alembic` 仍报告此前已有的 26 个未格式化文件；未把无关格式化改动混入本次修复。

## 环境阻碍与顾虑

完整命令 `pytest -q --tb=short --show-capture=no` 的真实输出为：`6 failed, 121 passed, 14 warnings, 27 errors in 4.30s`。6 个失败和 27 个错误都始于 HTTP 鉴权 fixture 的注册/登录请求；本工作树没有可用 `.env`，MySQL 返回 `Access denied for user 'root'@'localhost' (using password: NO)`。未读取、复制或输出任何 `.env`。因此需在配置了 MySQL 凭据的本地环境中补跑完整 HTTP 集成测试和真实数据库迁移。
