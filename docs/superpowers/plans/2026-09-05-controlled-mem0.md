# Controlled mem0 Memory Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this approved plan task-by-task.

**Goal:** LLM 提取受控长期记忆，并由模型自主调用语义检索工具。

**Architecture:** mem0 是唯一长期记忆源；MySQL 保存账号、完整消息和训练业务。记忆状态放入向量库元数据，SDK 隔离在适配层。

**Tech Stack:** FastAPI, SQLAlchemy, LangGraph, mem0ai==2.0.20, Qdrant.

**Spec:** `docs/superpowers/specs/2026-09-05-controlled-mem0-design.md`

## Global Constraints

- 模型自行决定是否调用 `get_confirmed_memories(query)`，不添加自动召回节点。
- 只提取用户消息；未确认、撤销、过期及其他用户的事实不可读。
- SDK 类型只存在于适配器；不重写现有短期会话摘要。
- 保留现有未提交文档变更；功能分支 `codex/mem0-controlled-memory`。
- 旧 MySQL memory_facts 不删除、不在线回读；显式迁移默认 dry-run。
- 依赖安装到项目虚拟环境；不推送或部署。

## Task 1: adapter and contract

Files: `app/services/memory_backend.py`, `app/integrations/mem0_backend.py`, `app/tests/test_mem0_backend.py`, `app/core/settings.py`, `pyproject.toml`.

Interfaces: `MemoryRecord(id, user_id, text, metadata, created_at, updated_at, score=None)`；keyword-only `extract(user_id,message_id,text,session_id=None)`, `create(user_id,text,metadata)`, `list(user_id,include_revoked=False)`, `get(user_id,memory_id)`, `update(user_id,memory_id,text=None,metadata)`, `search(user_id,query,limit)`；`get_memory_backend()` 惰性初始化。

- [x] Write failing tests: scoped user extraction, proposed metadata, confirmed search filters, owned updates, idempotent source-message processing and exact explicit inserts.
- [x] Run `.venv/Scripts/python.exe -m pytest app/tests/test_mem0_backend.py -q -p no:cacheprovider` before implementation.
- [x] Implement against installed SDK 2.0.20; configure finite network timeouts, separate RAG collection, explicit embedding dimensions, local SQLite history and disabled telemetry.
- [x] Run tests against actual SDK with deterministic model and local Qdrant; record results.

## Task 2: service and API

Files: `app/services/memory_service.py`, `app/api/routers/memory.py`, `app/api/routers/chat.py`, `app/services/agent_tools.py`, `app/tests/test_memory_service.py`, `app/tests/test_memory.py`, `app/tests/memory_fakes.py`, `app/tests/conftest.py`.

- [x] Write tests and verify missing injected service/behavior failures.
- [x] Replace rule extraction with backend.extract; preserve existing session summary implementation.
- [x] Replace SQL memory queries with metadata CRUD and query-based retrieval. Filter ownership/status/expiry; use current record text; enforce count and character budgets.
- [x] Keep API response shape, map not-owned to 404, invalid transition to 409, provider failure to 503. Run blocking extraction in threadpool; no automatic memory query in graph.
- [x] Run service/API and Agent regressions.

## Task 3: migration, documentation, review

Files: `app/services/memory_migration.py`, `app/tests/test_memory_migration.py`, `.env.example`, `README.md`, `AGENTS.md`, `docs/memory-architecture.md`, `prompts/main_prompt.txt`.

- [x] Write migration tests: default preview, idempotent apply, original status/expiry preserved, source table unchanged on errors.
- [x] Implement `python -m app.services.memory_migration [--user-id ID] [--apply]` with nonzero failure exit status.
- [x] Document SDK storage roles, config, explicit legacy migration, Chinese retrieval limitations and request latency.
- [x] Finish broad independent review and fixes. Both P2 findings resolved; scoped real-SDK re-review passed.

## Execution ledger

- Baseline: 17 relevant tests passed. Existing warnings: Python 3.14 deprecations and unwritable pytest cache; later runs disable cacheprovider.
- Ruling: retain current checkout/runtime and user document changes on a new feature branch. No main-branch implementation.
- Ruling (user approved): switch from SQL-governed dual storage to mem0-only memory; undo own index-state migration/fields, keep legacy table intact. MySQL message persistence remains independent.
- Interface preflight: Task 1 produces MemoryRecord and adapter methods used by Task 2; Task 3 consumes list/create and reads legacy ORM. Only Task 1 edits settings; only parent edits service/routes/docs. No contradictory shared file ownership.

- Verification: full backend suite 187 passed; frontend production build passed; app lint, compileall, dependency check and all 14 touched Python files format check passed. Nine untouched files have pre-existing formatting drift; no unrelated reformatting.
- Live smoke: configured DashScope LLM and embeddings with isolated local Qdrant produced two Chinese proposed facts, confirmed semantic recall succeeded, foreign-user and revoked recall returned none. No production memory collection was used.
- Migration CLI preview for user 1 passed with selected=0 and no writes; --apply has not been run on legacy data.
- Review fixes: API expiry serializes explicit UTC and frontend localizes the date; current learning/routing/interview docs describe mem0-only memory. New timezone regression verified red then green.
- Ruling: extraction context uses user+session; idempotency uses user+source message, so later messages retain recent user context without skipping extraction.

- Final-fix verification: full backend suite 190 passed after concurrency/logging regressions were added. Single-worker service mutation is serialized by bounded shared locks; raw SDK logs are contained at adapter initialization.
- Runtime gate: isolated app.main:app process start/health/stop/restart/health/stop passed, with source cwd, PID, log and metadata recorded under .tools/mem0-runtime.*. Existing running services were not stopped.

- Final review: both reported P2 findings resolved; four scoped real-SDK/service privacy and concurrency regressions passed. No unresolved blocking findings. Required AGENTS memory/chat command passed 48 tests on final adapter code.
- Completion: implementation retained uncommitted on codex/mem0-controlled-memory at D:/FitAgent, consistent with the authorized local scope. No merge, push, deployment or production legacy migration performed.
