import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

from app.api.routers import chat as chat_router
from app.services.chat_routing_graph import IntentDecision, build_chat_routing_graph
from app.services.react_agent import ReactAgent
from app.services.session_summary_service import RECENT_AGENT_MESSAGE_LIMIT
from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler


async def _stream_events(events):
    """将测试事件转换为可重复消费的异步生成器。"""

    for event in events:
        yield event


class TestChat:
    def test_sse_resets_preamble_before_persisting_tool_answer(self, monkeypatch):
        """撤回事件必须穿过 SSE，并让持久化答案只保留工具后的正式回复。"""

        class FakeAgent:
            @staticmethod
            async def execute_stream(_messages, **_kwargs):
                yield {"type": "text", "content": "I'll check the weather first."}
                yield {"type": "text_reset"}
                yield {"type": "tool", "id": "weather-call", "name": "查询天气"}
                yield {"type": "tool_completed", "id": "weather-call"}
                yield {"type": "text", "content": "建议傍晚慢跑 30 分钟。"}

        class FakeDb:
            @staticmethod
            def add(_message):
                """忽略测试中的待保存消息。"""

            @staticmethod
            def query(_model):
                """返回不会命中会话的查询对象。"""
                return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

            @staticmethod
            def commit():
                """避免测试访问真实数据库。"""

        @contextmanager
        def fake_trace_db():
            """提供仓储保存所需的独立会话。"""
            yield object()

        saved = {}
        monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
        monkeypatch.setattr(
            chat_router.AgentTraceRepository,
            "save",
            lambda _db, _collector, **kwargs: saved.update(kwargs),
        )

        async def collect_sse():
            """收集真实 sse_generator 的全部响应块。"""
            return [
                chunk
                async for chunk in chat_router.sse_generator(
                    FakeAgent(),
                    [{"role": "user", "content": "广州怎么训练？"}],
                    FakeDb(),
                    "session-reset",
                    "广州怎么训练？",
                    SimpleNamespace(id=7),
                )
            ]

        chunks = asyncio.run(collect_sse())

        assert [json.loads(chunk[6:]) for chunk in chunks[:-1]] == [
            {"type": "text", "content": "I'll check the weather first."},
            {"type": "text_reset"},
            {"type": "tool", "id": "weather-call", "name": "查询天气"},
            {"type": "tool_completed", "id": "weather-call"},
            {"type": "text", "content": "建议傍晚慢跑 30 分钟。"},
        ]
        assert chunks[-1] == "data: [DONE]\n\n"
        assert saved["assistant_answer"] == "建议傍晚慢跑 30 分钟。"

    def test_sse_saves_collected_question_answer_and_status(self, monkeypatch):
        """成功流结束后应将问题、回答和 Collector 交给仓储。"""

        class FakeAgent:
            """提供带回调配置断言的最小流式 Agent。"""

            captured_config = None

            @classmethod
            async def execute_stream(cls, _messages, **kwargs):
                """通过本地 Runnable 消费回调配置并输出固定文本事件。"""
                cls.captured_config = kwargs["config"]
                RunnableLambda(lambda _input: "已执行").invoke({}, config=cls.captured_config)
                yield {"type": "text", "content": "膝盖跟随脚尖。"}

        class FakeDb:
            """提供 SSE 收尾所需的最小数据库接口。"""

            @staticmethod
            def add(_message):
                """忽略测试中的待保存消息。"""

            @staticmethod
            def query(_model):
                """返回不会命中会话的查询对象。"""
                return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

            @staticmethod
            def commit():
                """避免测试访问真实数据库。"""

        @contextmanager
        def fake_trace_db():
            """提供仓储保存所需的独立会话。"""
            yield object()

        saved = {}
        monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
        monkeypatch.setattr(
            chat_router.AgentTraceRepository,
            "save",
            lambda _db, collector, **kwargs: saved.update(collector=collector, **kwargs),
        )

        async def collect_sse():
            """收集真实 sse_generator 的全部响应块。"""
            return [
                chunk
                async for chunk in chat_router.sse_generator(
                    FakeAgent(),
                    [{"role": "user", "content": "深蹲怎么做？"}],
                    FakeDb(),
                    "session-7",
                    "深蹲怎么做？",
                    SimpleNamespace(id=7),
                )
            ]

        request_id_token = chat_router.request_id_var.set("request-sse-success")
        try:
            chunks = asyncio.run(collect_sse())
        finally:
            chat_router.request_id_var.reset(request_id_token)

        assert chunks[-1] == "data: [DONE]\n\n"
        assert saved["user_question"] == "深蹲怎么做？"
        assert saved["assistant_answer"] == "膝盖跟随脚尖。"
        assert saved["status"] == "succeeded"
        assert saved["request_id"] == "request-sse-success"
        assert isinstance(saved["collector"], RunCollectorCallbackHandler)
        assert saved["collector"].traced_runs
        assert FakeAgent.captured_config == {"callbacks": [saved["collector"]]}

    def test_sse_keeps_text_and_done_when_run_record_save_fails(self, monkeypatch):
        """运行记录保存失败时，已生成的文本和结束事件仍必须发送。"""

        class FakeAgent:
            """提供固定文本事件的最小流式 Agent。"""

            @staticmethod
            async def execute_stream(_messages, **_kwargs):
                """输出一段成功的文本事件。"""
                yield {"type": "text", "content": "保持呼吸。"}

        class FakeDb:
            """提供 SSE 收尾所需的最小数据库接口。"""

            @staticmethod
            def add(_message):
                """忽略测试中的待保存消息。"""

            @staticmethod
            def query(_model):
                """返回不会命中会话的查询对象。"""
                return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

            @staticmethod
            def commit():
                """避免测试访问真实数据库。"""

        @contextmanager
        def fake_trace_db():
            """提供仓储保存所需的独立会话。"""
            yield object()

        def raise_save_error(_db, _collector, **_kwargs):
            """模拟独立运行记录写入失败。"""
            raise RuntimeError("trace storage unavailable")

        monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
        monkeypatch.setattr(chat_router.AgentTraceRepository, "save", raise_save_error)

        async def collect_sse():
            """收集真实 sse_generator 的全部响应块。"""
            return [
                chunk
                async for chunk in chat_router.sse_generator(
                    FakeAgent(),
                    [{"role": "user", "content": "深蹲怎么做？"}],
                    FakeDb(),
                    "session-8",
                    "深蹲怎么做？",
                    SimpleNamespace(id=8),
                )
            ]

        assert asyncio.run(collect_sse()) == [
            'data: {"type": "text", "content": "保持呼吸。"}\n\n',
            "data: [DONE]\n\n",
        ]

    def test_sse_turns_invalid_internal_event_into_standard_error(self, monkeypatch):
        """内部事件不合法时必须走既有 SSE 错误契约，而非作为文本透传。"""

        class FakeAgent:
            @staticmethod
            async def execute_stream(_messages, **_kwargs):
                yield "invalid internal event"

        class FakeDb:
            @staticmethod
            def add(_message):
                """忽略测试中的待保存消息。"""

            @staticmethod
            def query(_model):
                """返回不会命中会话的查询对象。"""
                return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

            @staticmethod
            def commit():
                """避免测试访问真实数据库。"""

        @contextmanager
        def fake_trace_db():
            """提供轨迹保存所需的独立会话。"""
            yield object()

        monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
        monkeypatch.setattr(
            chat_router.AgentTraceRepository, "save", lambda *_args, **_kwargs: None
        )

        async def collect_sse():
            """收集真实 sse_generator 的全部响应块。"""
            return [
                chunk
                async for chunk in chat_router.sse_generator(
                    FakeAgent(),
                    [{"role": "user", "content": "深蹲怎么做？"}],
                    FakeDb(),
                    "session-invalid-event",
                    "深蹲怎么做？",
                    SimpleNamespace(id=9),
                )
            ]

        chunks = asyncio.run(collect_sse())

        assert json.loads(chunks[0][6:]) == {
            "type": "error",
            "content": "服务暂时不可用，请稍后重试",
        }
        assert chunks[1] == "data: [DONE]\n\n"

    def test_chat_creates_session(self, auth_client, agent_mock):
        """无 session_id → 自动创建会话，响应头返回 X-Session-Id"""
        resp = auth_client.post("/api/chat", json={"message": "你好"})
        assert resp.status_code == 200
        assert resp.headers.get("X-Session-Id")  # 非空

    def test_chat_stream_format(self, auth_client, agent_mock):
        """SSE 流式格式：有 data: 前缀 + text 事件 + [DONE] 结束标记"""
        with auth_client.stream("POST", "/api/chat", json={"message": "你好"}) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(line[6:])
            assert events  # 至少有事件
            assert events[-1] == "[DONE]"  # 最后是 [DONE]
            # 中间应有 text 类型事件
            text_events = [e for e in events if e != "[DONE]" and '"type": "text"' in e]
            assert text_events

    def test_chat_passes_stable_session_id_without_city_routing_data(self, auth_client, agent_mock):
        """聊天路由只把稳定会话标识交给服务层，不参与业务路由。"""
        response = auth_client.post("/api/chat", json={"message": "我在成都，怎么训练？"})

        assert response.status_code == 200
        assert (
            agent_mock.execute_stream.call_args.kwargs["session_id"]
            == response.headers["X-Session-Id"]
        )
        assert "city" not in agent_mock.execute_stream.call_args.kwargs

    def test_sse_preserves_tool_event_before_graph_execution_error(self, monkeypatch):
        """图执行后续失败时，已流出的工具事件必须先于 error 和 DONE。"""

        class DirectClassifier:
            """固定选择直接 RAG 分支以覆盖真实图执行。"""

            @staticmethod
            def classify(_prompt, config=None):
                """返回固定的直接检索路由。"""
                del config
                return IntentDecision(route="direct_rag")

        class FailingDirectExecutor:
            """先产生工具事件，再模拟检索阶段失败。"""

            @staticmethod
            async def astream(**_kwargs):
                """生成可消费的首个事件后抛出异常。"""
                yield {"type": "text", "content": "我先查询资料。"}
                yield {"type": "text_reset"}
                yield {"type": "tool", "name": "检索知识库"}
                raise RuntimeError("retrieval failed")

        class FakeDb:
            """提供 SSE 收尾所需的最小数据库接口。"""

            @staticmethod
            def add(_message):
                """忽略测试中的待保存消息。"""

            @staticmethod
            def query(_model):
                """返回不会命中会话的查询对象。"""
                return SimpleNamespace(filter=lambda *_args: SimpleNamespace(first=lambda: None))

            @staticmethod
            def commit():
                """避免测试访问真实数据库。"""

        @contextmanager
        def fake_trace_db():
            """提供轨迹保存使用的临时上下文。"""
            yield object()

        agent = object.__new__(ReactAgent)
        agent.direct_rag_executor = FailingDirectExecutor()
        agent.agent = None
        agent.max_steps = 8
        agent.max_tool_calls = 4
        agent.routing_graph = build_chat_routing_graph(classifier=DirectClassifier())
        monkeypatch.setattr(chat_router, "get_db_session", fake_trace_db)
        saved = {}
        monkeypatch.setattr(
            chat_router.AgentTraceRepository,
            "save",
            lambda _db, collector, **kwargs: saved.update(collector=collector, **kwargs),
        )

        async def collect_sse():
            """收集真实 sse_generator 的全部响应块。"""
            return [
                chunk
                async for chunk in chat_router.sse_generator(
                    agent,
                    [{"role": "user", "content": "深蹲怎么做？"}],
                    FakeDb(),
                    "session-7",
                    "深蹲怎么做？",
                    SimpleNamespace(id=7),
                )
            ]

        chunks = asyncio.run(collect_sse())

        assert json.loads(chunks[0][6:]) == {"type": "text", "content": "我先查询资料。"}
        assert json.loads(chunks[1][6:]) == {"type": "text_reset"}
        assert json.loads(chunks[2][6:]) == {"type": "tool", "name": "检索知识库"}
        assert json.loads(chunks[3][6:])["type"] == "error"
        assert chunks[4] == "data: [DONE]\n\n"
        assert saved["status"] == "failed"
        assert saved["user_question"] == "深蹲怎么做？"
        assert saved["assistant_answer"] == "服务暂时不可用，请稍后重试"
        assert isinstance(saved["collector"], RunCollectorCallbackHandler)

    def test_chat_forwards_rag_evidence_cards(self, auth_client, agent_mock):
        """RAG 证据事件必须穿过聊天路由，前端才能渲染来源卡片。"""
        agent_mock.execute_stream.side_effect = lambda *_args, **_kwargs: _stream_events(
            [
                {"type": "tool", "name": "检索知识库"},
                {
                    "type": "evidence",
                    "items": [
                        {
                            "rank": 1,
                            "evidence_id": "动作指南.md#squat",
                            "source_id": "动作指南.md",
                            "snippet": "膝盖与脚尖方向一致。",
                            "score": 0.03,
                        }
                    ],
                },
                {"type": "text", "content": "膝盖跟随脚尖。[证据:1]"},
            ]
        )

        with auth_client.stream("POST", "/api/chat", json={"message": "深蹲怎么做？"}) as response:
            events = [
                json.loads(line[6:])
                for line in response.iter_lines()
                if line.startswith("data: ") and line[6:] != "[DONE]"
            ]

        evidence = next(event for event in events if event["type"] == "evidence")
        assert evidence["items"] == [
            {
                "rank": 1,
                "evidence_id": "动作指南.md#squat",
                "source_id": "动作指南.md",
                "snippet": "膝盖与脚尖方向一致。",
                "score": 0.03,
            }
        ]

    def test_chat_invalid_session(self, auth_client, agent_mock):
        """传不存在的 session_id → 返回统一的 404 JSON 错误。"""
        resp = auth_client.post(
            "/api/chat",
            json={
                "session_id": "nonexistent-session-id",
                "message": "你好",
            },
        )
        assert resp.status_code == 404
        data = resp.json()
        assert set(data) == {"code", "messages", "data"}
        assert data["code"] == resp.status_code
        assert "不存在" in data["messages"][0]

    def test_chat_rate_limit(self, auth_client, agent_mock):
        """同一用户超过 20/分钟 → 触发限流返回 429。
        每个 auth_client 是独立新用户（独立限流桶），不影响其他测试。"""
        # execute_stream 会被多次调用，每次返回新的空异步迭代器，避免迭代器耗尽
        agent_mock.execute_stream.side_effect = lambda *a, **k: _stream_events([])
        responses = [auth_client.post("/api/chat", json={"message": f"msg{i}"}) for i in range(21)]
        statuses = [response.status_code for response in responses]
        assert statuses[:20] == [200] * 20  # 前 20 次放行
        assert statuses[20] == 429  # 第 21 次被限流
        assert responses[20].json() == {
            "code": 429,
            "messages": ["请求过于频繁，请稍后重试。"],
            "data": None,
        }

    def test_chat_only_forwards_twenty_recent_messages_without_eager_summary(
        self, auth_client, agent_mock
    ):
        """第 11 次请求前已有 21 条消息时只把最近 20 条原文交给 Agent。"""

        agent_mock.execute_stream.side_effect = lambda *args, **kwargs: _stream_events(
            [{"type": "text", "content": "ok"}]
        )
        session_id = ""
        for index in range(11):
            payload = {"message": "我在成都，目标是减脂" if index == 0 else f"消息 {index}"}
            if session_id:
                payload["session_id"] = session_id
            response = auth_client.post("/api/chat", json=payload)
            assert response.status_code == 200
            session_id = response.headers["X-Session-Id"]

        recent_messages = agent_mock.execute_stream.call_args.args[0]
        assert len(recent_messages) == RECENT_AGENT_MESSAGE_LIMIT == 20
        assert recent_messages[0]["role"] == "assistant"
        assert "session_summary" not in agent_mock.execute_stream.call_args.kwargs

    def test_chat_extracts_memories_after_stream_completion(
        self, auth_client, agent_mock, monkeypatch
    ):
        """长期记忆提取必须在回答流结束后执行，不能阻塞模型首字。"""
        call_order = []

        async def stream_response(*_args, **_kwargs):
            call_order.append("model")
            yield {"type": "text", "content": "已生成回答"}
            call_order.append("model_completed")

        class BackgroundMemoryService:
            """记录后台提取时机，不触发外部 mem0 调用。"""

            @staticmethod
            def extract_candidates(_message, *, user_id):
                assert user_id
                call_order.append("memory")
                return []

        agent_mock.execute_stream.side_effect = stream_response
        monkeypatch.setattr(chat_router, "MemoryService", BackgroundMemoryService)

        response = auth_client.post("/api/chat", json={"message": "我习惯晚上训练"})

        assert response.status_code == 200
        assert call_order == ["model", "model_completed", "memory"]
