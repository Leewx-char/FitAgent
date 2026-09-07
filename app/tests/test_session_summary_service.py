import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Message, Session, SessionSummary, User
from app.services.session_summary_service import (
    MAX_SUMMARY_CHARS,
    SessionSummaryNotFoundError,
    SessionSummaryService,
    WINDOW_COVERED_MESSAGE,
)


class FakeSummaryModel:
    def __init__(self, *responses):
        self.calls = []
        self.responses = iter(responses)

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=next(self.responses))


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=7, username="summary-user", password_hash="hash"))
    session.add(Session(id="s-7", user_id=7, title="summary session"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def seed_messages(db, rows):
    base = datetime(2026, 1, 1)
    for offset, (role, content) in enumerate(rows):
        db.add(
            Message(
                session_id="s-7",
                role=role,
                content=content,
                created_at=base + timedelta(seconds=offset),
            )
        )
    db.commit()


def payload(model):
    return "\n".join(str(message.content) for message in model.calls[0])


def test_visible_window_needs_no_summary_or_model_call(db):
    seed_messages(db, [("user", f"消息 {index}") for index in range(20)])
    model = FakeSummaryModel()

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert result == WINDOW_COVERED_MESSAGE
    assert model.calls == []
    assert db.query(SessionSummary).count() == 0


def test_older_user_messages_are_summarized_and_cached_as_v2(db):
    seed_messages(
        db,
        [
            ("user", "早期用户消息 1"),
            ("assistant", "assistant 的旧回答"),
            ("tool", "tool 的旧回包"),
        ]
        + [("user", f"近期消息 {index}") for index in range(19)],
    )
    model = FakeSummaryModel("用户曾说膝盖不适。")

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert "用户曾说膝盖不适。" in result
    assert "早期用户消息 1" in payload(model)
    assert "assistant 的旧回答" not in payload(model)
    assert "tool 的旧回包" not in payload(model)
    row = db.query(SessionSummary).one()
    assert json.loads(row.content) == {
        "schema_version": 2,
        "source": "仅压缩早期用户消息；不是长期记忆，也不会自动写入用户画像或 mem0。",
        "summary": "用户曾说膝盖不适。",
    }


def test_valid_cache_only_folds_newer_early_user_messages(db):
    seed_messages(
        db,
        [
            ("user", "原有用户约束"),
            ("assistant", "assistant 的旧回答"),
            ("user", "新增的旧用户约束"),
        ]
        + [("user", f"近期消息 {index}") for index in range(20)],
    )
    first = db.query(Message).order_by(Message.id).first()
    db.add(
        SessionSummary(
            id="a" * 32,
            session_id="s-7",
            content=json.dumps({"schema_version": 2, "summary": "此前摘要"}, ensure_ascii=False),
            covered_through_message_id=first.id,
        )
    )
    db.commit()
    model = FakeSummaryModel("合并后的摘要")

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert "合并后的摘要" in result
    assert "此前摘要" in payload(model)
    assert "新增的旧用户约束" in payload(model)
    assert "assistant 的旧回答" not in payload(model)


def test_legacy_cache_rebuilds_and_owner_is_required(db):
    seed_messages(db, [("user", "早期用户消息") for _ in range(21)])
    db.add(
        SessionSummary(
            id="b" * 32,
            session_id="s-7",
            content='{"facts": {}}',
            covered_through_message_id=1,
        )
    )
    db.commit()
    model = FakeSummaryModel("重新生成的摘要")

    SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert json.loads(db.query(SessionSummary).one().content)["schema_version"] == 2
    with pytest.raises(SessionSummaryNotFoundError):
        SessionSummaryService(model).get_summary(db, user_id=8, session_id="s-7")


def test_early_window_without_user_text_does_not_create_invalid_cache(db):
    seed_messages(db, [("assistant", "早期助手回答")] + [("user", "近期消息")] * 20)
    model = FakeSummaryModel()
    service = SessionSummaryService(model)

    first_result = service.get_summary(db, user_id=7, session_id="s-7")
    second_result = service.get_summary(db, user_id=7, session_id="s-7")

    assert first_result == second_result
    assert model.calls == []
    assert db.query(SessionSummary).count() == 0


def test_human_payload_never_exceeds_fixed_character_budget(db):
    seed_messages(db, [("user", "很长的用户消息" * 500)] + [("user", "近期消息")] * 20)
    model = FakeSummaryModel("第一段摘要", "第二段摘要")

    SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert model.calls
    assert all(len(call[1].content) <= MAX_SUMMARY_CHARS for call in model.calls)


def test_matching_boundary_cache_is_reused_without_model_or_cache_mutation(db):
    seed_messages(db, [("user", "早期用户消息")] + [("user", "近期消息")] * 20)
    boundary = db.query(Message).order_by(Message.id).first().id
    original_content = json.dumps(
        {"schema_version": 2, "source": "cache", "summary": "已有摘要"}, ensure_ascii=False
    )
    db.add(
        SessionSummary(
            id="c" * 32,
            session_id="s-7",
            content=original_content,
            covered_through_message_id=boundary,
        )
    )
    db.commit()
    model = FakeSummaryModel()

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    row = db.query(SessionSummary).one()
    assert "已有摘要" in result
    assert model.calls == []
    assert row.content == original_content
    assert row.covered_through_message_id == boundary
