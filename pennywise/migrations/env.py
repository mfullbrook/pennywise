"""Migrations share Pennywise's SQLite settings through both Python and the CLI."""

from alembic import context

from pennywise import db

if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported")


def run_migrations(connection):
    # render_as_batch: SQLite can't ALTER most things, so Alembic rebuilds tables instead.
    # The connection has foreign keys off for those rebuilds (db.migration_connection), so
    # check them explicitly before committing.
    context.configure(connection=connection, target_metadata=db.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"migration left foreign key violations: {violations[:5]}")


connection = context.config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:
    engine = db.open_engine()
    try:
        with db.migration_connection(engine) as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
