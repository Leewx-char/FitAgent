class TestChat:

    def test_chat_creates_session(self, auth_client, agent_mock):
        """无 session_id → 自动创建会话，响应头返回 X-Session-Id"""
        resp = auth_client.post('/api/chat', json={'message': '你好'})
        assert resp.status_code == 200
        assert resp.headers.get('X-Session-Id')  # 非空

    def test_chat_stream_format(self, auth_client, agent_mock):
        """SSE 流式格式：有 data: 前缀 + text 事件 + [DONE] 结束标记"""
        with auth_client.stream('POST', '/api/chat', json={'message': '你好'}) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith('data: '):
                    events.append(line[6:])
            assert events  # 至少有事件
            assert events[-1] == '[DONE]'  # 最后是 [DONE]
            # 中间应有 text 类型事件
            text_events = [e for e in events if e != '[DONE]' and '"type": "text"' in e]
            assert text_events

    def test_chat_invalid_session(self, auth_client, agent_mock):
        """传不存在的 session_id → 返回 code=404 提示"""
        resp = auth_client.post('/api/chat', json={
            'session_id': 'nonexistent-session-id',
            'message': '你好',
        })
        # chat 端点对 404 返回 200 + body.code=404（项目响应格式约定）
        assert resp.status_code == 200
        data = resp.json()
        assert data['code'] == 404
        assert '不存在' in data['message']

    def test_chat_rate_limit(self, auth_client, agent_mock):
        """同一用户超过 20/分钟 → 触发限流返回 429。
        每个 auth_client 是独立新用户（独立限流桶），不影响其他测试。"""
        # execute_stream 会被多次调用，每次返回新的空迭代器，避免迭代器耗尽
        agent_mock.execute_stream.side_effect = lambda *a, **k: iter([])
        statuses = [
            auth_client.post('/api/chat', json={'message': f'msg{i}'}).status_code
            for i in range(21)
        ]
        assert statuses[:20] == [200] * 20  # 前 20 次放行
        assert statuses[20] == 429          # 第 21 次被限流

