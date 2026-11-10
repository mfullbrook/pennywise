"""Statement-scoped imports, card members, optional balances, categories

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09

- transactions.statement_id: membership comes from the statement export a row came from
  (eng-review D15). Rows that existed before are assigned by date, as they were before.
- transactions.card_member: whose card, when the export says.
- statements.csv_file_id: the statement's CSV export (one per statement).
- statements: balances become optional (typed in from the PDF, possibly after import), and
  a statement is identified by its account and statement date.
- categories and category_rules (a new append-only layer: merchant -> category).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAYERS_BEFORE = "'corrections', 'overrides', 'annotations', 'aliases'"
LAYERS_AFTER = LAYERS_BEFORE + ", 'category_rules'"


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_categories")),
    )
    op.create_table(
        "category_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("merchant", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source IN ('cli', 'mcp', 'seed')", name=op.f("ck_category_rules_source")
        ),
        sa.CheckConstraint("length(trim(reason)) > 0", name=op.f("ck_category_rules_reason")),
        sa.ForeignKeyConstraint(
            ["category"], ["categories.name"], name=op.f("fk_category_rules_category_categories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_rules")),
    )
    op.create_index("ix_category_rules_slot", "category_rules", ["merchant", "id"])

    with op.batch_alter_table("statements", recreate="always") as batch:
        batch.alter_column("opening_pence", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("closing_pence", existing_type=sa.Integer(), nullable=True)
        batch.drop_constraint(op.f("uq_statements_account_id"), type_="unique")
        batch.create_unique_constraint(op.f("uq_statements_account_id"), ["account_id", "end_date"])
        batch.add_column(sa.Column("csv_file_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_statements_csv_file_id_source_files"), "source_files", ["csv_file_id"], ["id"]
        )

    with op.batch_alter_table("transactions", recreate="always") as batch:
        batch.add_column(sa.Column("statement_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("card_member", sa.Text(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_transactions_statement_id_statements"), "statements", ["statement_id"], ["id"]
        )
        batch.create_index("ix_transactions_statement", ["statement_id"])

    # Existing rows keep the membership they had: by date within the statement period.
    op.execute(
        """
        UPDATE transactions SET statement_id = (
            SELECT s.id FROM statements s
            WHERE s.account_id = transactions.account_id
              AND transactions.date BETWEEN s.start_date AND s.end_date
            ORDER BY s.end_date LIMIT 1
        )
        """
    )

    with op.batch_alter_table("layer_revocations", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_layer_revocations_layer"), type_="check")
        batch.create_check_constraint(
            op.f("ck_layer_revocations_layer"), f"layer IN ({LAYERS_AFTER})"
        )


def downgrade() -> None:
    op.execute("DELETE FROM layer_revocations WHERE layer = 'category_rules'")
    with op.batch_alter_table("layer_revocations", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_layer_revocations_layer"), type_="check")
        batch.create_check_constraint(
            op.f("ck_layer_revocations_layer"), f"layer IN ({LAYERS_BEFORE})"
        )
    with op.batch_alter_table("transactions", recreate="always") as batch:
        batch.drop_index("ix_transactions_statement")
        batch.drop_constraint(op.f("fk_transactions_statement_id_statements"), type_="foreignkey")
        batch.drop_column("card_member")
        batch.drop_column("statement_id")
    with op.batch_alter_table("statements", recreate="always") as batch:
        batch.drop_constraint(op.f("fk_statements_csv_file_id_source_files"), type_="foreignkey")
        batch.drop_column("csv_file_id")
        batch.drop_constraint(op.f("uq_statements_account_id"), type_="unique")
        batch.create_unique_constraint(
            op.f("uq_statements_account_id"), ["account_id", "start_date"]
        )
        batch.alter_column("closing_pence", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("opening_pence", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_category_rules_slot", table_name="category_rules")
    op.drop_table("category_rules")
    op.drop_table("categories")
