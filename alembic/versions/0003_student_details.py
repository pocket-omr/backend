"""add first/last name, group and registration number to exam_students

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exam_students",
        sa.Column("first_name", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "exam_students",
        sa.Column("last_name", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "exam_students",
        sa.Column("group_name", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "exam_students",
        sa.Column(
            "registration_number", sa.String(100), nullable=False, server_default=""
        ),
    )
    # Backfill the new name parts from the legacy single `name` column: everything
    # before the first space becomes first_name, the remainder last_name.
    op.execute(
        """
        UPDATE exam_students
        SET first_name = split_part(name, ' ', 1),
            last_name = TRIM(SUBSTRING(name FROM POSITION(' ' IN name) + 1))
        WHERE name <> '' AND POSITION(' ' IN name) > 0
        """
    )
    op.execute(
        "UPDATE exam_students SET first_name = name "
        "WHERE name <> '' AND POSITION(' ' IN name) = 0"
    )
    # `name` was previously NOT NULL with no default; relax it so new structured
    # inserts can omit it.
    op.alter_column(
        "exam_students", "name", existing_type=sa.String(255), server_default=""
    )


def downgrade() -> None:
    op.drop_column("exam_students", "registration_number")
    op.drop_column("exam_students", "group_name")
    op.drop_column("exam_students", "last_name")
    op.drop_column("exam_students", "first_name")
