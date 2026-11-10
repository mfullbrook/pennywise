"""Drop the ", Transaction Date: ..." suffix Tesco Bank adds to Endute descriptions

Synced rows kept it, so every Tesco charge was its own merchant and no rule matched the
next one. The served text stays in endute_rows.raw; only the derived description changes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25
"""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of sync.TRANSACTION_DATE_SUFFIX and merchants.desc_key as of this revision.
_SUFFIX = re.compile(r",\s*Transaction Date:\s*\d{4}-\d{2}-\d{2}\s*$", re.I)
_SPACES = re.compile(r"\s+")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        "SELECT id, description FROM transactions "
        "WHERE source = 'endute' AND description LIKE '%Transaction Date:%'"
    ).fetchall()
    for txn_id, description in rows:
        cleaned = _SUFFIX.sub("", description).strip()
        if cleaned and cleaned != description:
            bind.exec_driver_sql(
                "UPDATE transactions SET description = ?, desc_key = ? WHERE id = ?",
                (cleaned, _SPACES.sub(" ", cleaned.upper()).strip(), txn_id),
            )


def downgrade() -> None:
    # The cleaned text is what the current code writes; the served text is in
    # endute_rows.raw, so nothing is lost by leaving it.
    pass
