"""Tesco Bank PDF statements, from synthetic statement text."""

from datetime import date

import pytest

from pennywise.adapters import tesco
from pennywise.adapters.base import AdapterError
from pennywise.model import Kind

STATEMENT = """Statement date 20 January 2026
Previous balance £ 100.00
New balance £ 141.25
21 December - 20 January 2026
BALANCE FROM PREVIOUS STATEMENT £ 100.00
19 DEC 22 DEC 11113333 EXAMPLE GROCERY EXAMPLETON £40.00
23 DEC 24 DEC 22224444 EXAMPLE GROCERY EXAMPLETON -£1.35
05 JAN 05 JAN 11112222 EXAMPLE CAFE PARIS FRA
10.00 EUR EXCHANGE RATE 1.150 £8.70
2.75% FOREIGN EXCHANGE FEE ON THIS TRANSACTION £0.24
10 JAN 10 JAN 33334444 PAYMENT RECEIVED - THANK YOU -£100.00
20 JAN 20 JAN INTEREST - SEE SUMMARY £1.66
02 JAN 03 JAN 55556666 EXAMPLE SHOP EXAMPLETON £92.00
"""


def test_rows_foreign_purchases_fees_and_years():
    parsed = tesco.parse_text(STATEMENT)
    assert parsed.statement_date == date(2026, 1, 20)
    assert (parsed.opening_pence, parsed.closing_pence) == (10000, 14125)
    assert [(r.date, r.description, r.amount_pence, r.kind) for r in parsed.rows] == [
        (date(2025, 12, 19), "EXAMPLE GROCERY EXAMPLETON", 4000, Kind.PURCHASE),
        (date(2025, 12, 23), "EXAMPLE GROCERY EXAMPLETON", -135, Kind.REFUND),
        (date(2026, 1, 5), "EXAMPLE CAFE PARIS FRA", 870, Kind.PURCHASE),
        (date(2026, 1, 5), "FOREIGN EXCHANGE FEE EXAMPLE CAFE PARIS FRA", 24, Kind.FEE),
        (date(2026, 1, 10), "PAYMENT RECEIVED - THANK YOU", -10000, Kind.PAYMENT),
        (date(2026, 1, 20), "INTEREST - SEE SUMMARY", 166, Kind.INTEREST),
        (date(2026, 1, 2), "EXAMPLE SHOP EXAMPLETON", 9200, Kind.PURCHASE),
    ]


def test_a_statement_whose_rows_dont_explain_its_balances_is_refused():
    with pytest.raises(AdapterError, match="a line wasn't read"):
        tesco.parse_text(
            STATEMENT.replace("02 JAN 03 JAN 55556666 EXAMPLE SHOP EXAMPLETON £92.00\n", "")
        )


def test_a_foreign_line_without_its_sterling_amount_is_refused():
    broken = STATEMENT.replace("10.00 EUR EXCHANGE RATE 1.150 £8.70\n", "")
    with pytest.raises(AdapterError, match="sterling amount"):
        tesco.parse_text(broken)


def test_there_is_no_tesco_csv():
    with pytest.raises(AdapterError, match="PDF"):
        tesco.parse("anything")
