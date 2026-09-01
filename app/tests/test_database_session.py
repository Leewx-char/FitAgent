from app.core import database


class FakeSession:
    def __init__(self):
        """初始化用于记录事务与关闭状态的模拟会话。"""
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        """记录模拟会话已提交。"""
        self.committed = True

    def rollback(self):
        """记录模拟会话已回滚。"""
        self.rolled_back = True

    def close(self):
        """记录模拟会话已关闭。"""
        self.closed = True


def test_db_session_commits_and_closes_on_success(monkeypatch):
    """验证数据库会话上下文正常结束时提交并关闭会话。"""
    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)

    with database.get_db_session() as actual:
        assert actual is session

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


def test_db_session_rolls_back_and_closes_on_error(monkeypatch):
    """验证数据库会话上下文异常结束时回滚并关闭会话。"""
    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)

    try:
        with database.get_db_session():
            raise RuntimeError("database operation failed")
    except RuntimeError:
        pass

    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True
