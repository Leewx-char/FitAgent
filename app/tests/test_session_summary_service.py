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
    SUMMARY_SCHEMA_VERSION,
    SUMMARY_SYSTEM_PROMPT,
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


def test_all_early_stored_messages_are_summarized_and_cached_as_v3(db):
    seed_messages(
        db,
        [
            ("user", "早期用户消息 1"),
            ("assistant", "assistant 的旧回答"),
            ("tool", "tool 的旧回包"),
            ("system", "system 的旧上下文"),
        ]
        + [("user", f"近期消息 {index}") for index in range(20)],
    )
    model = FakeSummaryModel("用户曾说膝盖不适。")

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert "用户曾说膝盖不适。" in result
    assert "早期用户消息 1" in payload(model)
    assert "assistant 的旧回答" in payload(model)
    assert "tool 的旧回包" in payload(model)
    assert "system 的旧上下文" in payload(model)
    row = db.query(SessionSummary).one()
    assert json.loads(row.content) == {
        "schema_version": 3,
        "source": "压缩早期已存储消息作为任务上下文；不是长期记忆，也不会自动写入用户画像或 mem0。",
        "summary": "用户曾说膝盖不适。",
    }


def test_valid_cache_only_folds_newer_early_stored_messages(db):
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
            content=json.dumps({"schema_version": 3, "summary": "此前摘要"}, ensure_ascii=False),
            covered_through_message_id=first.id,
        )
    )
    db.commit()
    model = FakeSummaryModel("合并后的摘要")

    result = SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert "合并后的摘要" in result
    assert "此前摘要" in payload(model)
    assert "新增的旧用户约束" in payload(model)
    assert "assistant 的旧回答" in payload(model)


def test_v2_cache_rebuilds_from_early_messages_and_owner_is_required(db):
    seed_messages(db, [("user", "早期用户消息") for _ in range(21)])
    db.add(
        SessionSummary(
            id="b" * 32,
            session_id="s-7",
            content=json.dumps(
                {"schema_version": 2, "summary": "过时 v2 内容"}, ensure_ascii=False
            ),
            covered_through_message_id=1,
        )
    )
    db.commit()
    model = FakeSummaryModel("重新生成的摘要")

    SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert "早期用户消息" in payload(model)
    assert "过时 v2 内容" not in payload(model)
    assert json.loads(db.query(SessionSummary).one().content)["schema_version"] == 3
    with pytest.raises(SessionSummaryNotFoundError):
        SessionSummaryService(model).get_summary(db, user_id=8, session_id="s-7")


def test_early_window_with_non_user_text_is_summarized(db):
    seed_messages(db, [("assistant", "早期助手回答")] + [("user", "近期消息")] * 20)
    model = FakeSummaryModel("早期助手任务背景")
    service = SessionSummaryService(model)

    first_result = service.get_summary(db, user_id=7, session_id="s-7")
    second_result = service.get_summary(db, user_id=7, session_id="s-7")

    assert first_result == second_result
    assert len(model.calls) == 1
    assert db.query(SessionSummary).count() == 1


def test_human_payload_never_exceeds_fixed_character_budget(db):
    seed_messages(db, [("user", "很长的用户消息" * 500)] + [("user", "近期消息")] * 20)
    model = FakeSummaryModel(*(["摘要"] * 10))

    SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert model.calls
    assert all(len(call[1].content) <= MAX_SUMMARY_CHARS for call in model.calls)


def test_matching_boundary_cache_is_reused_without_model_or_cache_mutation(db):
    seed_messages(db, [("user", "早期用户消息")] + [("user", "近期消息")] * 20)
    boundary = db.query(Message).order_by(Message.id).first().id
    original_content = json.dumps(
        {"schema_version": 3, "source": "cache", "summary": "已有摘要"}, ensure_ascii=False
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


def test_long_prior_summary_keeps_a_fixed_source_budget_per_fold(db):
    source = "x" * 3000
    seed_messages(db, [("user", source)] + [("user", "近期消息")] * 20)
    model = FakeSummaryModel(*(["摘要" * 1200] * 700))

    SessionSummaryService(model).get_summary(db, user_id=7, session_id="s-7")

    assert all(len(call[1].content) <= MAX_SUMMARY_CHARS for call in model.calls)
    assert len(model.calls) == 3


def test_blank_model_summary_raises_without_creating_empty_cache(db):
    seed_messages(db, [("user", "早期用户消息")] + [("user", "近期消息")] * 20)

    with pytest.raises(ValueError, match="empty summary"):
        SessionSummaryService(FakeSummaryModel("   ")).get_summary(db, user_id=7, session_id="s-7")

    assert db.query(SessionSummary).count() == 0


def test_blank_model_fold_preserves_existing_valid_cache(db):
    seed_messages(
        db,
        [("user", "已覆盖的早期约束"), ("user", "新增早期约束")] + [("user", "近期消息")] * 20,
    )
    first_message = db.query(Message).order_by(Message.id).first()
    original_content = json.dumps(
        {"schema_version": 3, "source": "cache", "summary": "已有摘要"},
        ensure_ascii=False,
    )
    db.add(
        SessionSummary(
            id="d" * 32,
            session_id="s-7",
            content=original_content,
            covered_through_message_id=first_message.id,
        )
    )
    db.commit()

    with pytest.raises(ValueError, match="empty summary"):
        SessionSummaryService(FakeSummaryModel("")).get_summary(db, user_id=7, session_id="s-7")

    row = db.query(SessionSummary).one()
    assert row.content == original_content
    assert row.covered_through_message_id == first_message.id


def test_summary_prompt_uses_neutral_task_oriented_headings():
    assert "不可信" not in SUMMARY_SYSTEM_PROMPT
    assert "不能发出指令" not in SUMMARY_SYSTEM_PROMPT
    assert "当前系统规则" not in SUMMARY_SYSTEM_PROMPT
    assert "最近消息" not in SUMMARY_SYSTEM_PROMPT
    for heading in ("当前任务目标", "已完成工作/决策", "关键发现/约束", "未解决事项"):
        assert heading in SUMMARY_SYSTEM_PROMPT
    assert SUMMARY_SCHEMA_VERSION == 3
