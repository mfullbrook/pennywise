from datetime import date
from pathlib import Path

import pytest

from pennywise import adapters
from pennywise.adapters import amex
from pennywise.adapters.base import AdapterError, decode, parse_amount
from pennywise.model import Kind

FIXTURES = Path(__file__).parent / "fixtures" / "amex"


@pytest.mark.parametrize(
    ("text", "pence"),
    [
        ("12.30", 1_230),
        ("-12.30", -1_230),
        ("1,234.56", 123_456),
        ("£5", 500),
        ("-£5.5", -550),
        ("(12.30)", -1_230),
        ("12.30 CR", -1_230),
        ("12.30DR", 1_230),
        ("0.01", 1),
        ("  7 ", 700),
    ],
)
def test_amounts_parse_to_exact_pence(text, pence):
    assert parse_amount(text) == pence


@pytest.mark.parametrize("text", ["", "12.345", "1,23.00", "12,30", "abc", "£", "1.2.3", "--5"])
def test_anything_else_is_not_an_amount(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_decoding_handles_a_bom_and_windows_1252():
    assert decode("£5".encode("utf-8-sig")) == "£5"
    assert decode("£5".encode("cp1252")) == "£5"


def test_amex_export_parses():
    parsed = amex.parse((FIXTURES / "2026-09-24.csv").read_text())
    assert parsed.date_basis == "transaction"
    assert len(parsed.rows) == 8
    first = parsed.rows[0]
    assert (first.date, first.description, first.amount_pence, first.card_member) == (
        date(2026, 8, 24),
        "DELIVEROO",
        2_850,
        "MS B EXAMPLE",
    )
    kinds = {row.description: row.kind for row in parsed.rows}
    assert kinds["PAYMENT RECEIVED - THANK YOU"] is Kind.PAYMENT
    assert kinds["CURRYS 0042 LONDON"] is Kind.REFUND
    assert kinds["BRITISH AIRWAYS 1251234567"] is Kind.PURCHASE


@pytest.mark.parametrize(
    ("description", "pence", "kind"),
    [
        ("INTEREST CHARGE", 1_200, Kind.INTEREST),
        ("LATE PAYMENT FEE", 1_200, Kind.FEE),
        ("COFFEE SHOP", 350, Kind.PURCHASE),  # FEE inside a word isn't a fee
        ("CASH ADVANCE ATM", 10_000, Kind.CASH_ADVANCE),
        ("DIRECT DEBIT PAYMENT", -50_000, Kind.PAYMENT),
        ("ASOS REFUND", -2_000, Kind.REFUND),
    ],
)
def test_amex_kinds(description, pence, kind):
    assert amex.kind_for(description, pence) is kind


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Date,Description,Amount\n01/09/2026,X,1.00\n", "unexpected columns"),
        ("Date,Description,Card Member,Account #,Amount\n", "no transactions"),
        (
            "Date,Description,Card Member,Account #,Amount\n2026-09-01,X,M,-1,1.00\n",
            "line 2: date '2026-09-01' is not dd/mm/yyyy",
        ),
        (
            "Date,Description,Card Member,Account #,Amount\n01/09/2026,X,M,-1,1.00\n"
            "02/09/2026,Y,M,-1,1.0.0\n",
            "line 3: '1.0.0' is not an amount",
        ),
        ("Date,Description,Card Member,Account #,Amount\n01/09/2026,X,M,1.00\n", "found 4"),
        ("Date,Description,Card Member,Account #,Amount\n01/09/2026, ,M,-1,1.00\n", "empty"),
    ],
)
def test_amex_errors_name_the_problem(text, message):
    with pytest.raises(AdapterError, match=message):
        amex.parse(text)


def test_unknown_adapter():
    with pytest.raises(AdapterError, match="no adapter 'monzo'; available: amex"):
        adapters.get("monzo")
