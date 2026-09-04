"""保存本地 Agent 运行的问答与工具明细。

修订编号：20260904_04
依赖版本：20260817_03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_04"
down_revision: str | None = "20260817_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """扩展运行摘要，并将工具输入输出列改为完整文本。"""
    op.add_column(
        "agent_runs", sa.Column("user_question", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "agent_runs", sa.Column("assistant_answer", sa.Text(), nullable=False, server_default="")
    )
    op.alter_column(
        "agent_tool_calls", "argument_shape", new_column_name="tool_input",
        existing_type=sa.Text(), existing_nullable=False,
    )
    op.alter_column(
        "agent_tool_calls", "detail", new_column_name="tool_output",
        existing_type=sa.String(length=120), type_=sa.Text(), existing_nullable=False,
    )


def downgrade() -> None:
    """还原旧工具列名称和摘要表字段。"""
    op.alter_column(
        "agent_tool_calls", "tool_output", new_column_name="detail",
        existing_type=sa.Text(), type_=sa.String(length=120), existing_nullable=False,
    )
    op.alter_column(
        "agent_tool_calls", "tool_input", new_column_name="argument_shape",
        existing_type=sa.Text(), existing_nullable=False,
    )
    op.drop_column("agent_runs", "assistant_answer")
    op.drop_column("agent_runs", "user_question")
