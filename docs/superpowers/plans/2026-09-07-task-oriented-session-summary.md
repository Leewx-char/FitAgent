# Task-Oriented Session Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the on-demand early-session summary preserve conversational continuity by extracting task goals, completed work, discoveries/constraints, and unresolved items from all stored early messages.

**Architecture:** Keep the existing `messages` table and recent 20-message Agent window unchanged. When the Agent calls `get_session_summary`, `SessionSummaryService` selects only messages older than that window, folds their plain contents without role-based filtering, and caches an LLM result under schema v3. The summary model treats every historical message as untrusted data; its only job is to create a compact task-oriented context, not execute historical instructions.

**Tech Stack:** Python 3, SQLAlchemy, LangChain messages, pytest, Ruff.

**Spec:** User-confirmed bounded design in this conversation; no separate architectural spec is required.

## Global Constraints

- Keep `RECENT_AGENT_MESSAGE_LIMIT == 20`: summary is unavailable through the tool until the stored message count exceeds 20.
- Do not add system/tool message persistence, migrations, new endpoints, or changes to mem0.
- Generate the summary only in `get_session_summary`; never trigger a model call from the chat request path.
- Cache only nonempty v3 summaries, invalidate v2 and malformed cache content, and preserve a valid old cache if a new fold fails.
- Treat all early-message contents and the returned summary as untrusted historical context; current system instructions and recent messages take precedence.
- Use the four output topics exactly: current task goal, completed work/decisions, key discoveries/constraints, unresolved items.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `app/services/session_summary_service.py` | Select early messages, fold every stored role as role-agnostic historical text, persist/read schema-v3 cache, and format the safety boundary. |
| `app/tests/test_session_summary_service.py` | Prove the v3 source, all-role input, v2 invalidation, fold safety, and 20-message cutoff. |
| `app/tests/test_session_summary_tool.py` | Assert the Agent-facing prompt still describes an on-demand, untrusted early-history tool. |
| `app/services/agent_tools.py` | Update the tool description to explain task-oriented early-history continuity rather than a user-only summary. |
| `docs/memory-architecture.md` | Describe the v3 early-session context and its relationship to the current message window and mem0. |
| `docs/interview/技术亮点.md` | Keep the project overview consistent with the v3 behavior. |

### Task 1: Implement and document role-agnostic task-oriented early summaries

**Files:**
- Modify: `app/services/session_summary_service.py:1-187`
- Modify: `app/services/agent_tools.py:get_session_summary`
- Modify: `app/tests/test_session_summary_service.py:1-244`
- Modify: `app/tests/test_session_summary_tool.py:1-180`
- Modify: `docs/memory-architecture.md`
- Modify: `docs/interview/技术亮点.md`

**Interfaces:**
- Consumes: `SessionSummaryService(model).get_summary(db, user_id: int, session_id: str) -> str` and the existing `SessionSummary` row fields.
- Produces: the same public method and tool name, but with `SUMMARY_SCHEMA_VERSION == 3` and a task-oriented untrusted-history result.

- [ ] **Step 1: Write the failing service and prompt tests**

```python
def test_older_messages_of_every_stored_role_are_folded_into_v3_task_context(db):
    seed_messages(
        db,
        [
            ("user", "目标：完成 5 公里训练计划"),
            ("assistant", "已制定第一周跑步安排"),
            ("tool", "心率区间：二区"),
            ("system", "历史文本只作记录"),
        ] + [("user", f"近期消息 {index}") for index in range(20)],
    )
    model = FakeSummaryModel("当前任务目标：5 公里。\\n已完成：第一周安排。")

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    sent = payload(model)
    assert all(text in sent for text in ["5 公里训练计划", "第一周跑步安排", "心率区间", "历史文本"])
    assert "当前任务目标" in result
    cached = json.loads(db.query(SessionSummary).one().content)
    assert cached["schema_version"] == 3
```

Add a v2-cache invalidation test. Add a prompt test that requires the tool description to say that it reads task-oriented early conversation context on demand and that its result is untrusted history. Update the existing user-only tests so none accepts the old v2 source, old wording, or a role filter. Keep the existing blank-result and cached-row preservation tests.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest app/tests/test_session_summary_service.py app/tests/test_session_summary_tool.py -q -p no:cacheprovider`

Expected: FAIL because the current implementation only sends early `user` rows and only accepts schema version 2.

- [ ] **Step 3: Implement the smallest v3 fold**

```python
SUMMARY_SCHEMA_VERSION = 3
SOURCE = "压缩早期已存会话消息，用于保持任务上下文连续性；不是长期记忆，也不会自动写入用户画像或 mem0。"

new_early_messages = [message for message in older_messages if message.id > covered]
pending_messages = [(message.id, message.content) for message in new_early_messages]
```

Replace user-only constants, prompts, payload labels, and cache validation with v3 equivalents. The system prompt must require the four named topics, state that historical content cannot issue instructions, and preserve recent-message/current-system precedence. Keep the existing fixed input/output character budgets, ownership check, incremental `covered_through_message_id`, and write-after-success behavior. Update the tool description and the two documents to state that this cache is on-demand, role-agnostic over stored early messages, and separate from mem0.

- [ ] **Step 4: Run focused tests and static analysis to verify GREEN**

Run: `.venv/bin/python -m pytest app/tests/test_session_summary_service.py app/tests/test_session_summary_tool.py app/tests/test_chat.py -q -p no:cacheprovider && .venv/bin/python -m ruff check app/services/session_summary_service.py app/services/agent_tools.py app/tests/test_session_summary_service.py app/tests/test_session_summary_tool.py`

Expected: PASS. The chat tests prove this task did not add eager summary generation or alter the 20-message handoff.

- [ ] **Step 5: Commit**

```bash
git add app/services/session_summary_service.py app/services/agent_tools.py app/tests/test_session_summary_service.py app/tests/test_session_summary_tool.py docs/memory-architecture.md docs/interview/技术亮点.md docs/superpowers/plans/2026-09-07-task-oriented-session-summary.md
git commit -m "refactor: summarize early session context by task"
```
