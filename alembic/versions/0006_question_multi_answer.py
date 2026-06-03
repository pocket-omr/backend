"""add correct_answers (multiple correct choices) to questions

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("correct_answers", sa.JSON(), nullable=True))
    # Backfill from the legacy single correct_answer.
    op.execute(
        "UPDATE questions SET correct_answers = "
        "CASE WHEN correct_answer IS NULL THEN '[]'::json "
        "ELSE json_build_array(correct_answer) END"
    )


def downgrade() -> None:
    op.drop_column("questions", "correct_answers")
