"""Halifax statements: the CSV export and the PDF statement's text (synthetic)."""

from datetime import date

import pytest

from pennywise.adapters import halifax
from pennywise.adapters.base import AdapterError
from pennywise.model import Kind

CSV = (
    "Transaction Date,Transaction Cleared Date,Transaction Type,Transaction Description,"
    "Transaction Amount\n"
    "02/03/2026,02/03/2026,,DIRECT DEBIT PAYMENT -,-90.38\n"
    "25/02/2026,26/02/2026,,Disney Plus,12.99\n"
)


def test_csv_rows_and_signs():
    parsed = halifax.parse(CSV)
    assert [(r.date, r.amount_pence, r.kind) for r in parsed.rows] == [
        (date(2026, 3, 2), -9038, Kind.PAYMENT),
        (date(2026, 2, 25), 1299, Kind.PURCHASE),
    ]
    assert parsed.date_basis == "transaction"


def test_the_statement_date_comes_from_the_file_name():
    assert halifax.statement_date_from_name("1234_05112025") == date(2025, 11, 5)
    assert halifax.statement_date_from_name("statement") is None


STATEMENT = """Your credit card statement
05 January 2026
Previous balance        £314.92
Payments received        £314.92 CR
BALANCE FROM PREVIOUS STATEMENT               314.92
1234 07 DECEMBER 08 DECEMBER EXAMPLE BOOKS BERLIN DEU        59.99
9.88  USD @ 1.3315
12 DECEMBER 12 DECEMBER EXAMPLE FOOD Exampleton         9.98
1234 03 JANUARY 03 JANUARY EXAMPLE REFUND        5.00 CR
02 JANUARY 02 JANUARY DIRECT DEBIT PAYMENT - THANK YOU       314.92 CR
New balance               £64.97
"""


def test_pdf_text_rows_years_and_balances():
    parsed = halifax.parse_text(STATEMENT)
    assert parsed.statement_date == date(2026, 1, 5)
    assert (parsed.opening_pence, parsed.closing_pence) == (31492, 6497)
    assert [(r.date, r.amount_pence, r.kind) for r in parsed.rows] == [
        (date(2025, 12, 7), 5999, Kind.PURCHASE),  # December on a January statement
        (date(2025, 12, 12), 998, Kind.PURCHASE),
        (date(2026, 1, 3), -500, Kind.REFUND),
        (date(2026, 1, 2), -31492, Kind.PAYMENT),
    ]


def test_a_pdf_whose_rows_dont_explain_its_balances_is_refused():
    missing_a_line = STATEMENT.replace(
        "12 DECEMBER 12 DECEMBER EXAMPLE FOOD Exampleton         9.98\n", ""
    )
    with pytest.raises(AdapterError, match="a line wasn't read"):
        halifax.parse_text(missing_a_line)


def test_text_that_isnt_a_halifax_statement_is_refused():
    with pytest.raises(AdapterError, match="statement date"):
        halifax.parse_text("hello")
