"""Known statement dates for cards whose statement day varies

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "statement_dates",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_statement_dates_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("account_id", "end_date", name=op.f("pk_statement_dates")),
    )


def downgrade() -> None:
    op.drop_table("statement_dates")
