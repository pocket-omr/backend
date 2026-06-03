"""add needs_review + flagged_questions to student_submissions

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "student_submissions",
        sa.Column(
            "needs_review", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "student_submissions",
        sa.Column("flagged_questions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("student_submissions", "flagged_questions")
    op.drop_column("student_submissions", "needs_review")
