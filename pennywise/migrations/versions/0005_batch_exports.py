"""Batch exports: a source file can be a date-range download covering many statements

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_files", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_source_files_file_type"), type_="check")
        batch.create_check_constraint(
            op.f("ck_source_files_file_type"), "file_type IN ('csv', 'pdf', 'csv_batch')"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT 1 FROM source_files WHERE file_type = 'csv_batch'").first():
        raise RuntimeError("batch exports exist; downgrading would orphan them")
    with op.batch_alter_table("source_files", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_source_files_file_type"), type_="check")
        batch.create_check_constraint(
            op.f("ck_source_files_file_type"), "file_type IN ('csv', 'pdf')"
        )
