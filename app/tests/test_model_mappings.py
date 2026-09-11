"""验证 ORM 类型映射升级不改变既有数据库契约。"""

from app.models import Message, SessionSummary, TrainingFeedback, TrainingPlan, UserProfile


def test_typed_mappings_keep_existing_column_contracts():
    """关键模型的列类型、可空性和默认值应保持既有约定。"""
    assert UserProfile.__table__.c.age.nullable is True
    assert UserProfile.__table__.c.weekly_days.default.arg == 3
    assert Message.__table__.c.content.nullable is False
    assert SessionSummary.__table__.c.covered_through_message_id.default.arg == 0
    assert TrainingPlan.__table__.c.status.default.arg == "draft"
    assert TrainingFeedback.__table__.c.pain_score.nullable is True
