"""Halifax credit card statements: the CSV export and the PDF statement.

CSV columns: Transaction Date, Transaction Cleared Date, Transaction Type, Transaction
Description, Transaction Amount. Dates are dd/mm/yyyy; positive is a charge, negative a
credit. Files are named <card last 4>_<ddmmyyyy statement date>.csv.

The PDF lists "<card> <dd MONTH> <dd MONTH> <description> <amount>[ CR]" lines (the card
number is missing on older statements, and a foreign-currency line follows some rows),
with the statement date and the previous and new balances on the first page. The year of
each row comes from the statement date: a December row on a January statement is from the
year before. A PDF imports only if its rows take the previous balance to the new balance
to the penny, so a line the parser missed can't slip through.

Dates are transaction dates, as on Amex.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime

from pennywise.adapters.base import (
    AdapterError,
    ParsedFile,
    ParsedRow,
    kind_for,
    parse_amount,
    parse_date,
)

NAME = "halifax"
COLUMNS = (
    "Transaction Date",
    "Transaction Cleared Date",
    "Transaction Type",
    "Transaction Description",
    "Transaction Amount",
)
DATE_FORMAT = "%d/%m/%Y"
DATE_BASIS = "transaction"

_FILE_NAME = re.compile(r"\d{4}_(\d{2})(\d{2})(\d{4})")
_STATEMENT_DATE = re.compile(r"Your credit card statement\s*\n\s*(\d{1,2} [A-Za-z]+ \d{4})")
_PREVIOUS = re.compile(r"Previous balance\s+£([\d,]+\.\d{2})( CR)?")
_NEW = re.compile(r"New balance\s+£([\d,]+\.\d{2})( CR)?")
_ROW = re.compile(
    r"^(?:(?P<card>\d{4}) )?(?P<day>\d{2}) (?P<month>[A-Z]+) \d{2} [A-Z]+ "
    r"(?P<description>.+?)\s+(?P<amount>[\d,]+\.\d{2})(?P<cr> CR)?$"
)


def statement_date_from_name(stem: str) -> date | None:
    """2025-11-05 from 1234_05112025."""
    match = _FILE_NAME.fullmatch(stem)
    if match is None:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse(text: str) -> ParsedFile:
    reader = csv.reader(io.StringIO(text))
    header = [column.strip() for column in next(reader, [])]
    if tuple(header) != COLUMNS:
        raise AdapterError(f"unexpected columns {header}; a Halifax export has {list(COLUMNS)}")
    rows: list[ParsedRow] = []
    for line, fields in enumerate(reader, start=2):
        if not any(field.strip() for field in fields):
            continue
        if len(fields) != len(COLUMNS):
            raise AdapterError(f"line {line}: expected {len(COLUMNS)} fields, found {len(fields)}")
        date_text, _cleared, _type, description, amount_text = (f.strip() for f in fields)
        try:
            when = parse_date(date_text, DATE_FORMAT)
        except ValueError as exc:
            raise AdapterError(f"line {line}: date {date_text!r} is not dd/mm/yyyy") from exc
        try:
            amount = parse_amount(amount_text)
        except ValueError as exc:
            raise AdapterError(f"line {line}: {exc}") from exc
        if not description:
            raise AdapterError(f"line {line}: the description is empty")
        rows.append(
            ParsedRow(
                row_index=len(rows),
                raw=json.dumps(fields),
                date=when,
                description=description,
                amount_pence=amount,
                kind=kind_for(description, amount),
            )
        )
    if not rows:
        raise AdapterError("the export has no transactions")
    return ParsedFile(tuple(rows), DATE_BASIS)


def _balance(match: re.Match | None, what: str) -> int:
    if match is None:
        raise AdapterError(f"the statement has no {what}")
    pence = parse_amount(match.group(1))
    return -pence if match.group(2) else pence  # a CR balance is in your favour


def parse_text(text: str) -> ParsedFile:
    """A PDF statement's extracted text."""
    found = _STATEMENT_DATE.search(text)
    if found is None:
        raise AdapterError("can't find the statement date; is this a Halifax statement?")
    statement_date = datetime.strptime(found.group(1), "%d %B %Y").date()
    opening = _balance(_PREVIOUS.search(text), "previous balance")
    closing = _balance(_NEW.search(text), "new balance")
    rows: list[ParsedRow] = []
    for line in text.splitlines():
        match = _ROW.match(line.strip())
        if match is None:
            continue
        try:
            month = datetime.strptime(match["month"].title(), "%B").month
        except ValueError:
            continue  # not a transaction line after all
        year = statement_date.year - (1 if month > statement_date.month else 0)
        try:
            when = date(year, month, int(match["day"]))
        except ValueError as exc:
            raise AdapterError(f"bad date on line {line.strip()!r}") from exc
        amount = parse_amount(match["amount"]) * (-1 if match["cr"] else 1)
        description = match["description"].strip()
        rows.append(
            ParsedRow(
                row_index=len(rows),
                raw=json.dumps(
                    [
                        match["card"],
                        when.isoformat(),
                        description,
                        match["amount"],
                        bool(match["cr"]),
                    ]
                ),
                date=when,
                description=description,
                amount_pence=amount,
                kind=kind_for(description, amount),
            )
        )
    if opening + sum(row.amount_pence for row in rows) != closing:
        raise AdapterError(
            f"the {len(rows)} transactions read from the statement dated {statement_date} don't "
            "take its previous balance to its new balance; a line wasn't read"
        )
    return ParsedFile(tuple(rows), DATE_BASIS, statement_date, opening, closing)


def parse_pdf(data: bytes) -> ParsedFile:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        pages = PdfReader(io.BytesIO(data)).pages
        text = "\n".join(page.extract_text() or "" for page in pages)
    except PdfReadError as exc:
        raise AdapterError(f"can't read the PDF: {exc}") from exc
    return parse_text(text)
