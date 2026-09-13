import asyncio
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

from app.api.routers import chat as chat_router


def _latency_events(caplog) -> list[dict[str, object]]:
    """提取测试期间输出的结构化聊天耗时日志。"""
    return [
        json.loads(record.message.removeprefix("CHAT_LATENCY "))
        for record in caplog.records
        if record.message.startswith("CHAT_LATENCY ")
    ]


def test_sse_logs_first_text_once_without_exposing_message(caplog, monkeypatch):
    """首个文本分片必须只记录一次，且日志不能包含用户原文。"""

    class FakeAgent:
        @staticmethod
        async def execute_stream(_messages, **_kwargs):
            yield {"type": "text", "content": "第一段回答"}
            yield {"type": "text", "content": "第二段回答"}

    class FakeDb:
        @staticmethod
        def add(_message):
            pass

        @staticmethod
        def query(_model):
            return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

        @staticmethod
        def commit():
            pass

    @contextmanager
    def fake_trace_db():
        yield object()

    monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
    monkeypatch.setattr(chat_router.AgentTraceRepository, "save", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO, logger="agent")

    async def collect_sse():
        return [
            chunk
            async for chunk in chat_router.sse_generator(
                FakeAgent(),
                [{"role": "user", "content": "我的秘密问题"}],
                FakeDb(),
                "session-latency",
                "我的秘密问题",
                SimpleNamespace(id=7),
            )
        ]

    asyncio.run(collect_sse())
    events = _latency_events(caplog)

    assert sum(event["stage"] == "sse.first_text" for event in events) == 1
    assert any(event["stage"] == "sse.completed" for event in events)
    assert "我的秘密问题" not in caplog.text
