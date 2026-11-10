import sqlite3
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from builders import make_account, make_txn
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pennywise import db


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_home_is_owner_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PENNYWISE_HOME", str(home))
    engine = db.open_engine()
    db.migrate(engine)
    engine.dispose()
    assert mode(home) == 0o700
    assert mode(home / db.DB_FILENAME) == 0o600


def test_wal_file_inherits_owner_only_permissions(engine, tmp_path):
    with db.write(engine) as conn:
        make_account(conn)
    wal = tmp_path / "pennywise.db-wal"
    assert wal.exists()
    assert mode(wal) == 0o600


def test_every_connection_is_configured(engine):
    with db.read(engine) as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == db.BUSY_TIMEOUT_MS
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_write_takes_the_lock_up_front(engine, tmp_path):
    other = sqlite3.connect(tmp_path / "pennywise.db", timeout=0.1, isolation_level=None)
    with db.write(engine):
        # Nothing written yet, but BEGIN IMMEDIATE already holds the write lock.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("BEGIN IMMEDIATE")
    other.execute("BEGIN IMMEDIATE")
    other.execute("ROLLBACK")
    other.close()


def test_cli_write_succeeds_while_mcp_holds_a_read(engine, tmp_path):
    mcp = db.open_engine(tmp_path / "pennywise.db")
    with db.read(mcp) as reader:
        assert reader.execute(select(func.count()).select_from(db.accounts)).scalar() == 0
        with db.write(engine) as writer:  # the CLI, in another connection
            make_account(writer)
        # The open read keeps its snapshot.
        assert reader.execute(select(func.count()).select_from(db.accounts)).scalar() == 0
    with db.read(mcp) as reader:  # the next tool call sees the write
        assert reader.execute(select(func.count()).select_from(db.accounts)).scalar() == 1
    mcp.dispose()


def test_migrations_match_the_schema(engine):
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), db.metadata) == []


def test_migrate_twice_is_harmless(engine):
    db.migrate(engine)


def test_alembic_cli_migrates_configured_home(tmp_path, monkeypatch):
    home = tmp_path / "cli-home"
    monkeypatch.setenv("PENNYWISE_HOME", str(home))
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    engine = db.open_engine(home / db.DB_FILENAME)
    try:
        with db.read(engine) as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.metadata) == []
            assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert mode(home) == 0o700
        assert mode(home / db.DB_FILENAME) == 0o600
    finally:
        engine.dispose()


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(IntegrityError):
        make_txn(conn, account_id=999, when=date(2026, 9, 1), amount_pence=100)


def test_unknown_kind_is_rejected(conn):
    account = make_account(conn)
    with pytest.raises(IntegrityError):
        make_txn(conn, account, date(2026, 9, 1), 100, kind="gift")


def test_layer_rows_need_a_reason(conn):
    account = make_account(conn)
    txn = make_txn(conn, account, date(2026, 9, 1), 100)
    with pytest.raises(IntegrityError):
        conn.execute(
            db.annotations.insert().values(
                transaction_id=txn, status="recognised", source="cli", reason="  ", created_at="x"
            )
        )
