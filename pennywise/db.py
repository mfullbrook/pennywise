"""SQLite storage: schema, connections and migrations.

All data lives in one SQLite file under the pennywise home directory
(``~/.pennywise`` unless ``PENNYWISE_HOME`` is set). The CLI and the MCP server
are separate processes sharing that file, so every connection runs in WAL mode
with a busy timeout, writes take the write lock up front (``BEGIN IMMEDIATE``),
and readers keep transactions short instead of holding one open while idle.

Dates are stored as ISO text through SQLAlchemy's ``Date`` type. Timestamps
(``created_at``) are UTC ISO strings with microseconds, so they sort as text.
"""

# gstack-shortcut(dec-4cacbfc9): live-file Time Machine backup may drop -wal writes, upgrade when restore attempted or Endute sync lands  # noqa: E501

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Connection, Engine

from pennywise.model import KINDS

BUSY_TIMEOUT_MS = 5000
DB_FILENAME = "pennywise.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

SOURCES = ("cli", "mcp", "seed")
MARK_STATUSES = ("recognised", "expected_one_off", "needs_checking", "unmarked")
CORRECTION_KINDS = ("membership", "exclude", "add_row")
OVERRIDE_FIELDS = ("merchant", "kind", "category")
LAYER_NAMES = ("corrections", "overrides", "annotations", "aliases", "category_rules")


def _one_of(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(f"'{value}'" for value in values)})"


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", Text, nullable=False, unique=True),
    Column("issuer", Text, nullable=False),
    Column("adapter", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("default_close_day", Integer),
    Column("created_at", Text, nullable=False),
    CheckConstraint("default_close_day BETWEEN 1 AND 31", name="close_day"),
)

source_files = Table(
    "source_files",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    # Unique across accounts: the same bytes can never be imported under a second account.
    Column("sha256", Text, nullable=False, unique=True),
    Column("filename", Text, nullable=False),
    Column("file_type", Text, nullable=False),
    Column("min_date", Date),
    Column("max_date", Date),
    Column("row_count", Integer, nullable=False),
    Column("imported_at", Text, nullable=False),
    # csv_batch: a date-range download covering many statements, split by date on import.
    CheckConstraint(_one_of("file_type", ("csv", "pdf", "csv_batch")), name="file_type"),
)

source_rows = Table(
    "source_rows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file_id", Integer, ForeignKey("source_files.id"), nullable=False),
    Column("row_index", Integer, nullable=False),
    Column("raw", Text, nullable=False),
    UniqueConstraint("source_file_id", "row_index"),
)

# A transaction belongs to the statement whose export it came from (statement_id), unless a
# membership correction moves it. Dates can't decide membership: a charge dated on the
# statement day can appear on either statement.
transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("source_row_id", Integer, ForeignKey("source_rows.id")),
    Column("date", Date, nullable=False),
    Column("date_basis", Text, nullable=False),
    Column("amount_pence", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("desc_key", Text, nullable=False),
    Column("issuer_category", Text),
    Column("created_at", Text, nullable=False),
    Column("statement_id", Integer, ForeignKey("statements.id")),
    Column("card_member", Text),  # whose card, when the export says
    Column("endute_row_id", Integer, ForeignKey("endute_rows.id"), unique=True),
    CheckConstraint(_one_of("source", ("csv", "manual", "endute")), name="source"),
    CheckConstraint("(source = 'csv') = (source_row_id IS NOT NULL)", name="source_row"),
    CheckConstraint("(source = 'endute') = (endute_row_id IS NOT NULL)", name="endute_row"),
    CheckConstraint(_one_of("date_basis", ("posting", "transaction")), name="date_basis"),
    CheckConstraint(_one_of("kind", KINDS), name="kind"),
    Index("ix_transactions_account_date", "account_id", "date"),
    Index("ix_transactions_statement", "statement_id"),
)

statements = Table(
    "statements",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    # Typed in from the PDF; may be entered after the transactions are imported.
    Column("opening_pence", Integer),
    Column("closing_pence", Integer),
    Column("due_date", Date),
    Column("pdf_file_id", Integer, ForeignKey("source_files.id")),
    # The statement's CSV export: one per statement, and its rows belong to this statement.
    Column("csv_file_id", Integer, ForeignKey("source_files.id")),
    Column("created_at", Text, nullable=False),
    CheckConstraint("start_date <= end_date", name="period"),
    # A statement is identified by its account and statement date.
    UniqueConstraint("account_id", "end_date"),
)

# Endute Connect (pennywise.sync). A linked account takes its transactions from Endute;
# Endute has no statements, so they're grouped into periods ending on the account's
# statement day (accounts.default_close_day).
endute_links = Table(
    "endute_links",
    metadata,
    Column("account_id", Integer, ForeignKey("accounts.id"), primary_key=True),
    Column("endute_account_id", Text, nullable=False, unique=True),
    Column("institution", Text, nullable=False),
    # Rows booked before this date are skipped: earlier spending comes from CSV statements.
    Column("cutover_date", Date),
    Column("linked_at", Text, nullable=False),
)

# Statement dates read off real statements, for a card whose statement day varies. Periods
# end on these; after the last one they're estimated from accounts.default_close_day.
statement_dates = Table(
    "statement_dates",
    metadata,
    Column("account_id", Integer, ForeignKey("accounts.id"), primary_key=True),
    Column("end_date", Date, primary_key=True),
    Column("created_at", Text, nullable=False),
)

sync_runs = Table(
    "sync_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("window_from", Date),  # booking dates requested; null means all history
    Column("fetched", Integer, nullable=False, server_default="0"),
    Column("inserted", Integer, nullable=False, server_default="0"),
    Column("unchanged", Integer, nullable=False, server_default="0"),
    Column("changed", Integer, nullable=False, server_default="0"),  # kept as first seen
    Column("before_cutover", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    CheckConstraint(_one_of("status", ("ok", "error")), name="status"),
    CheckConstraint("(status = 'error') = (error IS NOT NULL)", name="error"),
    Index("ix_sync_runs_account", "account_id", "id"),
)

# Each Endute transaction exactly as served the first time it was seen. Endute ids are
# stable; a row served again with different content keeps this original.
endute_rows = Table(
    "endute_rows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("endute_id", Text, nullable=False, unique=True),
    Column("sync_run_id", Integer, ForeignKey("sync_runs.id"), nullable=False),
    Column("raw", Text, nullable=False),
)

categories = Table(
    "categories",
    metadata,
    Column("name", Text, primary_key=True),
    Column("description", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)


# Layer tables (see pennywise.layers): append-only rows with provenance. The newest
# unrevoked row per slot is the current value; revocations live in layer_revocations.


def _provenance() -> list[Column]:
    return [
        Column("source", Text, nullable=False),
        Column("reason", Text, nullable=False),
        Column("created_at", Text, nullable=False),
    ]


def _provenance_checks() -> list[CheckConstraint]:
    return [
        CheckConstraint(_one_of("source", SOURCES), name="source"),
        CheckConstraint("length(trim(reason)) > 0", name="reason"),
    ]


corrections = Table(
    "corrections",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("transaction_id", Integer, ForeignKey("transactions.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("statement_id", Integer, ForeignKey("statements.id")),
    Column("pdf_file_id", Integer, ForeignKey("source_files.id")),
    Column("pdf_page", Integer),
    *_provenance(),
    CheckConstraint(_one_of("kind", CORRECTION_KINDS), name="kind"),
    CheckConstraint("(kind = 'membership') = (statement_id IS NOT NULL)", name="membership"),
    *_provenance_checks(),
    Index("ix_corrections_slot", "transaction_id", "kind", "id"),
)

overrides = Table(
    "overrides",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("transaction_id", Integer, ForeignKey("transactions.id"), nullable=False),
    Column("field", Text, nullable=False),
    Column("value", Text, nullable=False),
    *_provenance(),
    CheckConstraint(_one_of("field", OVERRIDE_FIELDS), name="field"),
    CheckConstraint(f"field != 'kind' OR {_one_of('value', KINDS)}", name="kind_value"),
    *_provenance_checks(),
    Index("ix_overrides_slot", "transaction_id", "field", "id"),
)

annotations = Table(
    "annotations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("transaction_id", Integer, ForeignKey("transactions.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("bulk_merchant", Text),
    *_provenance(),
    CheckConstraint(_one_of("status", MARK_STATUSES), name="status"),
    *_provenance_checks(),
    Index("ix_annotations_slot", "transaction_id", "id"),
)

aliases = Table(
    "aliases",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("pattern", Text, nullable=False),
    Column("match_type", Text, nullable=False),
    Column("merchant", Text, nullable=False),
    *_provenance(),
    CheckConstraint(_one_of("match_type", ("prefix", "regex")), name="match_type"),
    *_provenance_checks(),
    Index("ix_aliases_slot", "pattern", "match_type", "id"),
)

category_rules = Table(
    "category_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("merchant", Text, nullable=False),  # a normalised merchant name
    Column("category", Text, ForeignKey("categories.name"), nullable=False),
    *_provenance(),
    *_provenance_checks(),
    Index("ix_category_rules_slot", "merchant", "id"),
)

layer_revocations = Table(
    "layer_revocations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("layer", Text, nullable=False),
    Column("row_id", Integer, nullable=False),
    *_provenance(),
    CheckConstraint(_one_of("layer", LAYER_NAMES), name="layer"),
    *_provenance_checks(),
    UniqueConstraint("layer", "row_id"),
)


def home_dir() -> Path:
    return Path(os.environ.get("PENNYWISE_HOME") or Path.home() / ".pennywise")


def ensure_home(home: Path | None = None) -> Path:
    """Create the pennywise home readable by its owner only (0700)."""
    home = home or home_dir()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    return home


def open_engine(db_path: Path | None = None) -> Engine:
    """Open the database, creating the file owner-only (0600) if it doesn't exist.

    SQLite creates the -wal and -shm files with the main file's permissions.
    """
    if db_path is None:
        db_path = ensure_home() / DB_FILENAME
    if not db_path.exists():
        os.close(os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    db_path.chmod(0o600)
    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "begin", _begin)
    return engine


def _configure_connection(dbapi_connection, _connection_record) -> None:
    # Hand BEGIN over to SQLAlchemy's "begin" event so writes can use BEGIN IMMEDIATE.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
    finally:
        cursor.close()


def _begin(connection: Connection) -> None:
    write = connection.get_execution_options().get("pennywise_write", False)
    connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")


@contextmanager
def write(engine: Engine) -> Iterator[Connection]:
    """One write transaction that holds SQLite's write lock from its first statement."""
    with engine.connect() as connection:
        connection.execution_options(pennywise_write=True)
        with connection.begin():
            yield connection


@contextmanager
def read(engine: Engine) -> Iterator[Connection]:
    """One short read transaction: a consistent snapshot, released on exit."""
    with engine.connect() as connection, connection.begin():
        yield connection


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """A connection for schema migrations.

    SQLite rebuilds a table to change it, and dropping a table that other tables reference
    fails while foreign keys are enforced. So foreign keys are off for the migration (the
    migration environment runs PRAGMA foreign_key_check before committing) and back on
    before the connection returns to the pool. See sqlite.org/lang_altertable.html.
    """
    with engine.connect() as connection:
        dbapi_connection = connection.connection.dbapi_connection
        dbapi_connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execution_options(pennywise_write=True)
            yield connection
        finally:
            if connection.in_transaction():
                connection.rollback()
            dbapi_connection.execute("PRAGMA foreign_keys = ON")


def schema_is_current(engine: Engine) -> bool:
    """Whether the database is at the latest migration."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision() == head


def migrate(engine: Engine, revision: str = "head") -> None:
    """Bring the schema up to the latest migration (or a given revision)."""
    from alembic import command
    from alembic.config import Config

    config = Config(stdout=sys.stderr)  # stdout belongs to the MCP protocol
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
