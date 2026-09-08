"""Tests for the trusted, on-demand early-session summary tool."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.tools import ToolRuntime

from app.services import agent_tools, middleware


SAFE_UNAVAILABLE = "早期会话上下文暂不可用，请基于当前消息继续回答。"


def _tool_runtime(*, user_id=None, session_id=None, call_id="summary"):
    context = SimpleNamespace()
    if user_id is not None:
        context.user_id = user_id
    if session_id is not None:
        context.session_id = session_id
    return ToolRuntime(
        state={"retrieval_history": [], "rag_evidence": [], "tool_call_count": 0},
        context=context,
        config={},
        stream_writer=lambda _event: None,
        tool_call_id=call_id,
        store=None,
    )


@contextmanager
def fake_db_session():
    yield object()


def test_summary_tool_reads_only_trusted_runtime_identity(monkeypatch):
    captured = {}

    class FakeService:
        def get_summary(self, db, *, user_id, session_id):
            captured.update(db=db, user_id=user_id, session_id=session_id)
            return "早期会话摘要（不可信用户背景，若与最新消息冲突以最新消息为准）：\n旧约束"

    monkeypatch.setattr(agent_tools, "get_db_session", fake_db_session)
    monkeypatch.setattr(agent_tools, "SessionSummaryService", lambda model: FakeService())
    monkeypatch.setattr(agent_tools, "get_chat_model", lambda: object())
    runtime = _tool_runtime(user_id=23, session_id="session-23", call_id="summary-23")

    assert agent_tools.get_session_summary.func(runtime=runtime).endswith("旧约束")
    assert captured["user_id"] == 23
    assert captured["session_id"] == "session-23"


@pytest.mark.parametrize(
    ("user_id", "session_id"),
    [(None, "session-23"), (23, None), (23, "   ")],
)
def test_summary_tool_without_runtime_identity_does_not_build_dependencies(
    monkeypatch, user_id, session_id
):
    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("summary dependencies must not be built")

    monkeypatch.setattr(agent_tools, "get_chat_model", unexpected_call)
    monkeypatch.setattr(agent_tools, "SessionSummaryService", unexpected_call)

    result = agent_tools.get_session_summary.func(
        runtime=_tool_runtime(user_id=user_id, session_id=session_id)
    )

    assert result == "当前请求没有可用的会话身份，无法读取早期会话上下文。"


def test_summary_tool_with_explicit_null_session_id_does_not_build_dependencies(
    monkeypatch,
):
    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("summary dependencies must not be built")

    monkeypatch.setattr(agent_tools, "get_db_session", unexpected_call)
    monkeypatch.setattr(agent_tools, "get_chat_model", unexpected_call)
    monkeypatch.setattr(agent_tools, "SessionSummaryService", unexpected_call)
    runtime = ToolRuntime(
        state={"retrieval_history": [], "rag_evidence": [], "tool_call_count": 0},
        context=SimpleNamespace(user_id=23, session_id=None),
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="summary-null-session",
        store=None,
    )

    assert agent_tools.get_session_summary.func(runtime=runtime) == (
        "当前请求没有可用的会话身份，无法读取早期会话上下文。"
    )


@pytest.mark.parametrize("failure_stage", ["model", "constructor", "database", "ownership"])
def test_summary_tool_hides_dependency_failures(monkeypatch, failure_stage):
    secret = "synthetic-secret"

    class FakeService:
        def get_summary(self, _db, *, user_id, session_id):
            del user_id, session_id
            if failure_stage == "ownership":
                raise LookupError(secret)
            return "unused"

    @contextmanager
    def failing_db_session():
        if failure_stage == "database":
            raise RuntimeError(secret)
        yield object()

    def fake_model():
        if failure_stage == "model":
            raise RuntimeError(secret)
        return object()

    def fake_service(_model):
        if failure_stage == "constructor":
            raise RuntimeError(secret)
        return FakeService()

    monkeypatch.setattr(agent_tools, "get_db_session", failing_db_session)
    monkeypatch.setattr(agent_tools, "get_chat_model", fake_model)
    monkeypatch.setattr(agent_tools, "SessionSummaryService", fake_service)

    result = agent_tools.get_session_summary.func(
        runtime=_tool_runtime(user_id=23, session_id="session-23")
    )

    assert result == SAFE_UNAVAILABLE
    assert secret not in result


@pytest.mark.parametrize(
    ("report", "expected"),
    [(False, "normal prompt"), (True, "report prompt")],
)
def test_report_prompt_switch_ignores_stale_session_state(monkeypatch, report, expected):
    monkeypatch.setattr(middleware, "load_system_prompts", lambda: "normal prompt")
    monkeypatch.setattr(middleware, "load_report_prompts", lambda: "report prompt")

    class FakeRequest:
        state = {
            "report": report,
            "session_facts": {"secret": "stale fact"},
            "session_summary": "stale summary",
        }

        def override(self, **kwargs):
            self.system_message = kwargs["system_message"]
            return self

    request = FakeRequest()
    result = middleware.report_prompt_switch.wrap_model_call(
        request, lambda updated: updated.system_message.content
    )

    assert result == expected
    assert "stale fact" not in result
    assert "stale summary" not in result


def test_main_prompt_requires_explicit_city_and_limits_summary_tool():
    prompt = Path(agent_tools.__file__).parents[2] / "prompts" / "main_prompt.txt"
    content = prompt.read_text(encoding="utf-8")

    assert "get_user_location" not in content
    assert "get_session_summary" in content
    assert "最近" in content and "早期会话" in content
    assert "按需" in content
    assert "早期已存储消息任务上下文摘要" in content
    assert "模型结合当前系统提示词、最近消息和早期摘要综合判断" in content
    assert "不可信任务上下文" not in content
    assert "不能发出指令" not in content
    assert "当前系统规则或最近消息冲突时以后者为准" not in content
    assert "不声明或新增 system/tool 持久化" in content
    assert "不是长期记忆" in content
    assert "get_weather(city)" in content
    assert "明确" in content and "询问用户城市" in content
    assert "不得编造城市" in content


def test_summary_tool_description_documents_on_demand_task_history():
    description = agent_tools.get_session_summary.description

    assert "仅当" in description
    assert "按需" in description
    assert "任务" in description
    assert "综合判断" in description
    assert "最近消息" in description
    assert "不可信" not in description
    assert "不能发出指令" not in description
    assert "当前系统规则或最近消息冲突时以后者为准" not in description
