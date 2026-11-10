from datetime import date

import pytest
from builders import make_account, make_statement

from pennywise import importer
from pennywise.importer import LedgerError


def test_account_slugs_are_unique(conn):
    make_account(conn, "amex")
    with pytest.raises(LedgerError, match="already exists"):
        make_account(conn, "amex")


def test_close_day_must_be_a_day_of_month(conn):
    with pytest.raises(LedgerError):
        importer.add_account(
            conn, "barclaycard", issuer="x", adapter="x", display_name="x", default_close_day=32
        )


def test_one_statement_per_statement_date(conn):
    account = make_account(conn)
    make_statement(conn, account, date(2026, 8, 14), date(2026, 9, 13))
    with pytest.raises(LedgerError, match="already a statement dated 2026-09-13"):
        make_statement(conn, account, date(2026, 8, 20), date(2026, 9, 13))
    make_statement(conn, account, date(2026, 9, 14), date(2026, 10, 13))


def test_statement_dates_are_per_account(conn):
    amex, barclaycard = make_account(conn, "amex"), make_account(conn, "barclaycard")
    make_statement(conn, amex, date(2026, 8, 14), date(2026, 9, 13))
    make_statement(conn, barclaycard, date(2026, 8, 14), date(2026, 9, 13))


def test_statement_must_start_before_it_ends(conn):
    account = make_account(conn)
    with pytest.raises(LedgerError, match="after it ends"):
        make_statement(conn, account, date(2026, 9, 13), date(2026, 8, 14))
