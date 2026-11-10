import shutil
from datetime import date
from pathlib import Path

import pytest
from builders import make_statement
from sqlalchemy import func, select

from pennywise import db, importer, layers
from pennywise.adapters.base import AdapterError
from pennywise.engine.reconcile import Status, reconcile
from pennywise.importer import LedgerError

FIXTURES = Path(__file__).parent / "fixtures" / "amex"
AUG_FILE, SEP_FILE = FIXTURES / "2026-08-24.csv", FIXTURES / "2026-09-24.csv"


@pytest.fixture
def amex(conn):
    importer.add_account(conn, "amex", issuer="amex", adapter="amex", display_name="Amex")
    return "amex"


def count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar()


def test_import_creates_the_statement_and_keeps_every_row(conn, amex):
    result = importer.import_statement_csv(conn, amex, AUG_FILE)
    assert (result.rows, result.already_imported) == (8, False)
    statement = layers.get_statement(conn, result.statement_id)
    assert (statement.start, statement.end) == (date(2026, 7, 25), date(2026, 8, 24))
    assert (statement.opening_pence, statement.closing_pence) == (None, None)
    data = layers.load_statement(conn, result.statement_id)
    assert len(data.transactions) == 8
    assert {t.card_member for t in data.transactions} == {"MR A EXAMPLE", "MS B EXAMPLE"}
    assert count(conn, db.source_rows) == 8


def test_boundary_day_charges_stay_on_the_statement_that_listed_them(conn, amex):
    aug = importer.import_statement_csv(conn, amex, AUG_FILE).statement_id
    sep = importer.import_statement_csv(conn, amex, SEP_FILE).statement_id
    sep_statement = layers.get_statement(conn, sep)
    assert (sep_statement.start, sep_statement.end) == (date(2026, 8, 25), date(2026, 9, 24))
    deliveroo = [
        t for t in layers.load_statement(conn, sep).transactions if t.merchant == "DELIVEROO"
    ]
    assert [t.date for t in deliveroo] == [date(2026, 8, 24)]  # the August statement day
    assert "DELIVEROO" not in {t.merchant for t in layers.load_statement(conn, aug).transactions}


def test_statements_reconcile_once_balances_are_entered(conn, amex):
    aug = importer.import_statement_csv(conn, amex, AUG_FILE).statement_id
    sep = importer.import_statement_csv(conn, amex, SEP_FILE).statement_id
    assert reconcile(layers.load_statement(conn, sep)).status is Status.BALANCES_MISSING
    importer.set_balances(conn, aug, opening_pence=120_000, closing_pence=56_943)
    importer.set_balances(conn, sep, opening_pence=56_943, closing_pence=169_004)
    for statement in (aug, sep):
        result = reconcile(layers.load_statement(conn, statement))
        assert (result.status, result.residual_pence) == (Status.BALANCES_RECONCILE, 0)


def test_reimporting_the_same_file_does_nothing(conn, amex):
    first = importer.import_statement_csv(conn, amex, AUG_FILE)
    again = importer.import_statement_csv(conn, amex, AUG_FILE)
    assert again.already_imported
    assert (again.statement_id, again.source_file_id) == (first.statement_id, first.source_file_id)
    assert count(conn, db.transactions) == 8


def test_the_same_file_cant_go_under_another_account(conn, amex):
    importer.add_account(conn, "amex-2", issuer="amex", adapter="amex", display_name="Other")
    importer.import_statement_csv(conn, amex, AUG_FILE)
    with pytest.raises(LedgerError, match="another account"):
        importer.import_statement_csv(conn, "amex-2", AUG_FILE)


def test_a_statement_takes_one_csv(conn, amex, tmp_path):
    importer.import_statement_csv(conn, amex, AUG_FILE)
    changed = tmp_path / "2026-08-24.csv"
    changed.write_text(AUG_FILE.read_text().replace("45.20", "45.21"))
    with pytest.raises(LedgerError, match="already has its export"):
        importer.import_statement_csv(conn, amex, changed)


def test_a_file_that_doesnt_parse_writes_nothing(conn, amex, tmp_path):
    broken = tmp_path / "2026-08-24.csv"
    broken.write_text(AUG_FILE.read_text().replace("61.80", "61.8.0"))
    with pytest.raises(AdapterError, match="not an amount"):
        importer.import_statement_csv(conn, amex, broken)
    assert count(conn, db.transactions) == count(conn, db.source_files) == 0
    assert count(conn, db.statements) == 0


def test_the_statement_date_comes_from_the_name_or_the_caller(conn, amex, tmp_path):
    unnamed = tmp_path / "activity.csv"
    shutil.copy(AUG_FILE, unnamed)
    with pytest.raises(LedgerError, match="can't tell the statement date"):
        importer.import_statement_csv(conn, amex, unnamed)
    result = importer.import_statement_csv(conn, amex, unnamed, statement_date=date(2026, 8, 24))
    assert layers.get_statement(conn, result.statement_id).end == date(2026, 8, 24)


def test_a_transaction_after_the_statement_date_is_refused(conn, amex):
    with pytest.raises(LedgerError, match="after its statement date 2026-08-20"):
        importer.import_statement_csv(conn, amex, AUG_FILE, statement_date=date(2026, 8, 20))


def test_import_attaches_to_a_statement_entered_first(conn, amex):
    account = importer.account_row(conn, amex).id
    statement = make_statement(
        conn, account, date(2026, 7, 25), date(2026, 8, 24), opening=120_000, closing=56_943
    )
    assert importer.import_statement_csv(conn, amex, AUG_FILE).statement_id == statement
    result = reconcile(layers.load_statement(conn, statement))
    assert result.status is Status.BALANCES_RECONCILE


# --- Batch exports ------------------------------------------------------------------------

BATCH_HEADER = "Date,Description,Card Member,Account #,Amount\n"


def _batch_account(conn):
    return importer.add_account(
        conn, "amex-batch", issuer="amex", adapter="amex", display_name="A", default_close_day=19
    )


def _write(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text(BATCH_HEADER + "".join(f"{line}\n" for line in lines))
    return path


def test_a_batch_export_is_split_into_statement_periods(conn, tmp_path):
    _batch_account(conn)
    year = _write(
        tmp_path,
        "2025.csv",
        [
            "02/01/2025,SHOP A,X,1,1.00",
            "19/01/2025,SHOP B,X,1,2.00",
            "20/01/2025,SHOP C,X,1,3.00",
            "24/12/2025,SHOP D,X,1,4.00",
        ],
    )
    result = importer.import_batch_csv(conn, "amex-batch", year)
    assert (result.rows, len(result.statement_ids)) == (4, 3)
    periods = conn.execute(
        select(db.statements.c.start_date, db.statements.c.end_date).order_by(
            db.statements.c.end_date
        )
    ).all()
    assert [tuple(p) for p in periods] == [
        (date(2024, 12, 20), date(2025, 1, 19)),
        (date(2025, 1, 20), date(2025, 2, 19)),
        (date(2025, 12, 20), date(2026, 1, 19)),  # months with no rows get no statement
    ]
    # The next year's file fills the rest of the period it shares.
    next_year = _write(tmp_path, "2026.csv", ["05/01/2026,SHOP E,X,1,5.00"])
    again = importer.import_batch_csv(conn, "amex-batch", next_year)
    assert again.statement_ids == (result.statement_ids[-1],)
    assert importer.import_batch_csv(conn, "amex-batch", year).already_imported


def test_an_overlapping_batch_is_refused(conn, tmp_path):
    _batch_account(conn)
    importer.import_batch_csv(
        conn,
        "amex-batch",
        _write(tmp_path, "a.csv", ["02/01/2025,A,X,1,1.00", "10/03/2025,B,X,1,1.00"]),
    )
    with pytest.raises(LedgerError, match="overlaps a.csv"):
        importer.import_batch_csv(
            conn, "amex-batch", _write(tmp_path, "b.csv", ["01/03/2025,C,X,1,1.00"])
        )


def test_a_batch_cant_add_to_a_statement_export(conn, tmp_path):
    _batch_account(conn)
    importer.import_statement_csv(
        conn, "amex-batch", _write(tmp_path, "2025-02-19.csv", ["25/01/2025,A,X,1,1.00"])
    )
    with pytest.raises(LedgerError, match="already has its export 2025-02-19.csv"):
        importer.import_batch_csv(
            conn, "amex-batch", _write(tmp_path, "b.csv", ["10/02/2025,B,X,1,1.00"])
        )
