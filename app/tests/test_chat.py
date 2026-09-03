import json

from app.core.database import SessionLocal
from app.models import SessionSummary
from app.services.memory_service import RECENT_MESSAGE_LIMIT


class TestChat:
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

    def test_chat_forwards_rag_evidence_cards(self, auth_client, agent_mock):
        """RAG 证据事件必须穿过聊天路由，前端才能渲染来源卡片。"""
        agent_mock.execute_stream.return_value = iter(
            [
                '{"type": "tool", "name": "检索知识库"}',
                (
                    '{"type": "evidence", "items": [{"rank": 1, '
                    '"evidence_id": "动作指南.md#squat", "source_id": "动作指南.md", '
                    '"snippet": "膝盖与脚尖方向一致。", "tags": "动作"}]}'
                ),
                '{"type": "text", "content": "膝盖跟随脚尖。[证据:1]"}',
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
                "tags": "动作",
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
        # execute_stream 会被多次调用，每次返回新的空迭代器，避免迭代器耗尽
        agent_mock.execute_stream.side_effect = lambda *a, **k: iter([])
        responses = [auth_client.post("/api/chat", json={"message": f"msg{i}"}) for i in range(21)]
        statuses = [response.status_code for response in responses]
        assert statuses[:20] == [200] * 20  # 前 20 次放行
        assert statuses[20] == 429  # 第 21 次被限流
        assert responses[20].json() == {
            "code": 429,
            "messages": ["请求过于频繁，请稍后重试。"],
            "data": None,
        }

    def test_chat_keeps_twenty_recent_messages_and_summarizes_older_history(
        self, auth_client, agent_mock
    ):
        """第 11 次请求前已有 21 条消息：最近 20 条进 Agent，首条转入会话暂存状态。"""

        agent_mock.execute_stream.side_effect = lambda *args, **kwargs: iter(
            ['{"type": "text", "content": "ok"}']
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
        assert len(recent_messages) == RECENT_MESSAGE_LIMIT == 20
        assert recent_messages[0]["role"] == "assistant"

        db = SessionLocal()
        try:
            summary = db.query(SessionSummary).filter(SessionSummary.session_id == session_id).one()
            assert json.loads(summary.content)["facts"] == {
                "city": "成都",
                "training_goal": "减脂",
            }
        finally:
            db.close()
