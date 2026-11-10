"""Shared parsing for card issuer exports: decoding, money and dates.

Strict on purpose. Anything unexpected is an AdapterError naming the row, never a guess,
and the importer writes nothing for a file that doesn't parse completely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pennywise.model import Kind


class AdapterError(ValueError):
    """An export didn't match the format its adapter expects."""


@dataclass(frozen=True, slots=True)
class ParsedRow:
    row_index: int  # 0-based position among the file's transactions
    raw: str  # the row's original fields, as JSON
    date: date
    description: str
    amount_pence: int  # positive increases what you owe
    kind: Kind
    card_member: str | None = None
    issuer_category: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedFile:
    rows: tuple[ParsedRow, ...]
    date_basis: str  # posting | transaction
    # A PDF statement prints these; the adapter has already checked the rows explain them.
    statement_date: date | None = None
    opening_pence: int | None = None
    closing_pence: int | None = None


def decode(data: bytes) -> str:
    """UTF-8 (with or without a byte-order mark), falling back to Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


_NUMBER = re.compile(r"\d{1,3}(,\d{3})+(\.\d{1,2})?|\d+(\.\d{1,2})?")


def parse_amount(text: str) -> int:
    """Exact pence from an amount like '1,234.56', '-12.30', '(12.30)', '£5', '12.30 CR'.

    A minus sign, parentheses or a CR suffix make it negative (a credit). Never uses floats.
    """
    value = text.strip().replace(" ", "")
    negative = False
    if value[-2:].upper() in ("CR", "DR"):
        negative = value[-2:].upper() == "CR"
        value = value[:-2]
    if value.startswith("(") and value.endswith(")"):
        negative = not negative
        value = value[1:-1]
    if value.startswith("-"):
        negative = not negative
        value = value[1:]
    elif value.startswith("+"):
        value = value[1:]
    value = value.removeprefix("£")
    if not _NUMBER.fullmatch(value):
        raise ValueError(f"{text!r} is not an amount")
    pence = int(Decimal(value.replace(",", "")) * 100)
    return -pence if negative else pence


def parse_date(text: str, date_format: str) -> date:
    return datetime.strptime(text.strip(), date_format).date()


_PAYMENT = re.compile(r"\bPAYMENT RECEIVED\b|\bTHANK YOU\b|\bDIRECT DEBIT\b", re.IGNORECASE)
_INTEREST = re.compile(r"\bINTEREST\b", re.IGNORECASE)
_FEE = re.compile(r"\bFEE\b", re.IGNORECASE)
_CASH = re.compile(r"\bCASH (ADVANCE|WITHDRAWAL)\b", re.IGNORECASE)


def kind_for(description: str, amount_pence: int) -> Kind:
    """Credits are payments or refunds; charges are purchases unless the wording says
    interest, a fee or a cash advance. Overrides fix the occasional wrong guess.
    Shared by every source: issuers word these the same way."""
    if amount_pence < 0:
        return Kind.PAYMENT if _PAYMENT.search(description) else Kind.REFUND
    if _INTEREST.search(description):
        return Kind.INTEREST
    if _FEE.search(description):
        return Kind.FEE
    if _CASH.search(description):
        return Kind.CASH_ADVANCE
    return Kind.PURCHASE
