"""Data written under an older schema survives later migrations."""

import re
from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from pennywise import db, layers


def test_0002_keeps_data_and_assigns_existing_rows_by_date(tmp_path):
    engine = db.open_engine(tmp_path / "pennywise.db")
    db.migrate(engine, "0001")
    with db.write(engine) as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (id, slug, issuer, adapter, display_name, created_at) "
                "VALUES (1, 'amex', 'amex', 'amex', 'Amex', 'x')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO statements (id, account_id, start_date, end_date, opening_pence, "
                "closing_pence, created_at) VALUES (1, 1, '2026-08-25', '2026-09-24', 0, 500, 'x')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, account_id, source, date, date_basis, amount_pence, "
                "kind, description, desc_key, created_at) VALUES "
                "(1, 1, 'manual', '2026-09-01', 'posting', 500, 'purchase', 'X', 'X', 'x'), "
                "(2, 1, 'manual', '2026-07-01', 'posting', 100, 'purchase', 'Y', 'Y', 'x')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO corrections (transaction_id, kind, source, reason, created_at) "
                "VALUES (1, 'add_row', 'cli', 'typed', 'x'), (2, 'add_row', 'cli', 'typed', 'x')"
            )
        )

    db.migrate(engine)

    with db.read(engine) as conn:
        assert compare_metadata(MigrationContext.configure(conn), db.metadata) == []
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        memberships = dict(conn.execute(text("SELECT id, statement_id FROM transactions")).all())
        assert memberships == {1: 1, 2: None}  # dated in the period, and not
        data = layers.load_statement(conn, 1)
        assert [t.id for t in data.transactions] == [1]
        assert data.statement.end == date(2026, 9, 24)
    engine.dispose()


def test_every_named_check_constraint_survives_the_table_rebuilds(engine):
    expected = {
        table.name: {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        for table in db.metadata.tables.values()
    }
    with db.read(engine) as conn:
        for table, names in expected.items():
            sql = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
                {"name": table},
            ).scalar()
            assert set(re.findall(r"CONSTRAINT (ck_\w+) CHECK", sql)) == names, table


def test_0003_refuses_to_downgrade_over_endute_rows(engine):
    from alembic import command
    from alembic.config import Config

    with db.write(engine) as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (id, slug, issuer, adapter, display_name, created_at) "
                "VALUES (1, 'card', 'x', 'endute', 'Card', 'x')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sync_runs (id, account_id, started_at, finished_at, status) "
                "VALUES (1, 1, 'x', 'x', 'ok')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO endute_rows (id, account_id, endute_id, sync_run_id, raw) "
                "VALUES (1, 1, 'e1', 1, '{}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (account_id, source, endute_row_id, date, date_basis, "
                "amount_pence, kind, description, desc_key, created_at) VALUES "
                "(1, 'endute', 1, '2026-10-01', 'posting', 100, 'purchase', 'X', 'X', 'x')"
            )
        )
    config = Config()
    config.set_main_option("script_location", str(db.MIGRATIONS_DIR))
    with db.migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="Endute transactions exist"):
            command.downgrade(config, "0002")
    with db.read(engine) as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM transactions").scalar() == 1


def test_0006_drops_the_transaction_date_from_synced_descriptions(tmp_path):
    engine = db.open_engine(tmp_path / "pennywise.db")
    db.migrate(engine, "0005")
    with db.write(engine) as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (id, slug, issuer, adapter, display_name, created_at) "
                "VALUES (1, 'card', 'x', 'endute', 'Card', 'x')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sync_runs (id, account_id, started_at, finished_at, status) "
                "VALUES (1, 1, 'x', 'x', 'ok')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO endute_rows (id, account_id, endute_id, sync_run_id, raw) "
                "VALUES (1, 1, 'e1', 1, '{}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, account_id, source, endute_row_id, date, "
                "date_basis, amount_pence, kind, description, desc_key, created_at) VALUES "
                "(1, 1, 'endute', 1, '2026-10-01', 'posting', 100, 'purchase', "
                "'Shop  Exampleton, Transaction Date: 2026-09-30', "
                "'SHOP EXAMPLETON, TRANSACTION DATE: 2026-09-30', 'x'), "
                "(2, 1, 'manual', NULL, '2026-10-01', 'posting', 100, 'purchase', "
                "'Kept, Transaction Date: 2026-09-30', 'KEPT, TRANSACTION DATE: 2026-09-30', 'x')"
            )
        )

    db.migrate(engine)

    with db.read(engine) as conn:
        rows = conn.execute(text("SELECT id, description, desc_key FROM transactions")).all()
        assert [tuple(r) for r in rows] == [
            (1, "Shop  Exampleton", "SHOP EXAMPLETON"),
            (2, "Kept, Transaction Date: 2026-09-30", "KEPT, TRANSACTION DATE: 2026-09-30"),
        ]
    engine.dispose()
