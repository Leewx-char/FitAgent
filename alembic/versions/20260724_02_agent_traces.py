"""新增 Agent 执行轨迹表。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260724_02"
down_revision: str | None = "20260723_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.CHAR(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_request_id", "agent_runs", ["request_id"], unique=False)

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.CHAR(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("argument_shape", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("detail", sa.String(length=120), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_tool_calls_run_sequence",
        "agent_tool_calls",
        ["agent_run_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tool_calls_run_sequence", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_runs_request_id", table_name="agent_runs")
    op.drop_table("agent_runs")
