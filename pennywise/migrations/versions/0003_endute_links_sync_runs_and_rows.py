"""Endute Connect: linked accounts, sync runs and served rows

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13

- endute_links: a pennywise account fed by an Endute account, with an optional cutover
  date before which rows are skipped (that history comes from CSV statements).
- sync_runs: one row per account per sync, ok or error, with counts.
- endute_rows: each Endute transaction as first served (raw JSON), keyed by Endute's id.
- transactions: a third source, 'endute', pointing at its endute_rows row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "endute_links",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("endute_account_id", sa.Text(), nullable=False),
        sa.Column("institution", sa.Text(), nullable=False),
        sa.Column("cutover_date", sa.Date(), nullable=True),
        sa.Column("linked_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_endute_links_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_endute_links")),
        sa.UniqueConstraint("endute_account_id", name=op.f("uq_endute_links_endute_account_id")),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("window_from", sa.Date(), nullable=True),
        sa.Column("fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("changed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("before_cutover", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(status = 'error') = (error IS NOT NULL)", name=op.f("ck_sync_runs_error")
        ),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_sync_runs_status")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_sync_runs_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
    )
    op.create_index("ix_sync_runs_account", "sync_runs", ["account_id", "id"])
    op.create_table(
        "endute_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("endute_id", sa.Text(), nullable=False),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_endute_rows_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"], ["sync_runs.id"], name=op.f("fk_endute_rows_sync_run_id_sync_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_endute_rows")),
        sa.UniqueConstraint("endute_id", name=op.f("uq_endute_rows_endute_id")),
    )
    with op.batch_alter_table("transactions", recreate="always") as batch:
        batch.add_column(sa.Column("endute_row_id", sa.Integer(), nullable=True))
        batch.create_unique_constraint(op.f("uq_transactions_endute_row_id"), ["endute_row_id"])
        batch.create_foreign_key(
            op.f("fk_transactions_endute_row_id_endute_rows"),
            "endute_rows",
            ["endute_row_id"],
            ["id"],
        )
        batch.drop_constraint(op.f("ck_transactions_source"), type_="check")
        batch.create_check_constraint(
            op.f("ck_transactions_source"), "source IN ('csv', 'manual', 'endute')"
        )
        batch.create_check_constraint(
            op.f("ck_transactions_endute_row"),
            "(source = 'endute') = (endute_row_id IS NOT NULL)",
        )


def downgrade() -> None:
    # Endute transactions can't exist under the old schema. Layer rows that point at them
    # would be orphaned, so refuse rather than silently drop them.
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM transactions WHERE source = 'endute'").first():
        raise RuntimeError("Endute transactions exist; downgrading would lose them")
    with op.batch_alter_table("transactions", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_transactions_endute_row"), type_="check")
        batch.drop_constraint(op.f("ck_transactions_source"), type_="check")
        batch.create_check_constraint(op.f("ck_transactions_source"), "source IN ('csv', 'manual')")
        batch.drop_constraint(op.f("fk_transactions_endute_row_id_endute_rows"), type_="foreignkey")
        batch.drop_constraint(op.f("uq_transactions_endute_row_id"), type_="unique")
        batch.drop_column("endute_row_id")
    op.drop_table("endute_rows")
    op.drop_index("ix_sync_runs_account", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_table("endute_links")
