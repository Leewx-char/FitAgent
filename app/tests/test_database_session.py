from app.core import database


class FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_db_session_commits_and_closes_on_success(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)

    with database.get_db_session() as actual:
        assert actual is session

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


def test_db_session_rolls_back_and_closes_on_error(monkeypatch):
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
