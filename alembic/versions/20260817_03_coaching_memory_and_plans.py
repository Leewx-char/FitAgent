"""新增用户可控记忆、自适应计划存储与稳定运动记录标识。

修订编号：20260817_03
依赖版本：20260724_02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_03"
down_revision: str | None = "20260724_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展运动幂等键，并创建会话记忆、训练计划和反馈结构。"""
    op.add_column("fitness_data", sa.Column("external_id", sa.String(length=128), nullable=True))
    # 历史记录早于稳定的 Coros 活动标识；保留其原有按日幂等行为，
    # 同时让新活动记录使用真正的活动级标识。
    op.execute(
        "UPDATE fitness_data SET external_id = CONCAT(data_type, ':', DATE_FORMAT(date, '%Y%m%d')) "
        "WHERE external_id IS NULL"
    )
    op.alter_column("fitness_data", "external_id", existing_type=sa.String(length=128), nullable=False)
    op.create_index(
        "ix_fitness_user_type_external",
        "fitness_data",
        ["user_id", "data_type", "external_id"],
        unique=True,
    )
    op.drop_index("ix_fitness_user_date_type", table_name="fitness_data")

    op.create_table(
        "session_summaries",
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("session_id", sa.CHAR(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("covered_through_message_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_session_summaries_session_id"),
    )

    op.create_table(
        "memory_facts",
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("supersedes_id", sa.CHAR(length=32), nullable=True),
        sa.Column("fact_key", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("display_text", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_facts_user_status_key",
        "memory_facts",
        ["user_id", "status", "fact_key"],
        unique=False,
    )

    op.create_table(
        "training_plans",
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("plan_data", sa.Text(), nullable=False),
        sa.Column("safety_data", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_training_plans_user_week", "training_plans", ["user_id", "week_start"], unique=False
    )

    op.create_table(
        "training_feedbacks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.CHAR(length=32), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rpe", sa.Integer(), nullable=True),
        sa.Column("pain_score", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["training_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "day_of_week", name="uq_plan_feedback_day"),
    )


def downgrade() -> None:
    """删除记忆与训练计划结构，并还原运动数据的按日唯一索引。"""
    op.drop_table("training_feedbacks")
    op.drop_index("ix_training_plans_user_week", table_name="training_plans")
    op.drop_table("training_plans")
    op.drop_index("ix_memory_facts_user_status_key", table_name="memory_facts")
    op.drop_table("memory_facts")
    op.drop_table("session_summaries")
    op.create_index(
        "ix_fitness_user_date_type",
        "fitness_data",
        ["user_id", "date", "data_type"],
        unique=True,
    )
    op.drop_index("ix_fitness_user_type_external", table_name="fitness_data")
    op.drop_column("fitness_data", "external_id")
