"""Tesco Bank credit card PDF statements. Tesco offers no CSV beyond what Endute serves.

A transaction line reads "<dd MON> <dd MON> <reference> <description> £<amount>": the
transaction date, the posting date, an eight-digit reference (missing on interest lines),
and the amount, with "-£" or " CR" for a credit. A foreign-currency purchase spreads over
three lines: the merchant line with no amount, then "<amount> <CCY> EXCHANGE RATE <rate>
£<sterling amount>", then "<rate>% FOREIGN EXCHANGE FEE ON THIS TRANSACTION £<fee>". The
fee becomes its own fee row, so it counts as cost of credit rather than spending.

The statement date and the previous and new balances come from the first page. Years come
from the statement date (a December row on a January statement is the year before). A
statement imports only if its rows take the previous balance to the new balance to the
penny. Dates are transaction dates.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime

from pennywise.adapters.base import AdapterError, ParsedFile, ParsedRow, kind_for, parse_amount

NAME = "tesco"
DATE_BASIS = "transaction"

_STATEMENT_DATE = re.compile(r"Statement date (\d{1,2} [A-Za-z]+ \d{4})")
_PREVIOUS = re.compile(r"Previous balance £ ?([\d,]+\.\d{2})( CR)?")
_NEW = re.compile(r"New balance £ ?([\d,]+\.\d{2})( CR)?")
_DATES = r"^(?P<day>\d{2}) (?P<month>[A-Z]{3}) \d{2} [A-Z]{3} (?:(?P<ref>\d{6,}) )?"
_ROW = re.compile(
    _DATES + r"(?P<description>.+?) (?P<minus>-?)£(?P<amount>[\d,]+\.\d{2})(?P<cr> CR)?$"
)
_FOREIGN_HEAD = re.compile(_DATES + r"(?P<description>.+?)$")
_EXCHANGE = re.compile(
    r"^[\d,]+\.\d{2} [A-Z]{3} EXCHANGE RATE [\d.]+ (?P<minus>-?)£(?P<amount>[\d,]+\.\d{2})( CR)?$"
)
_FX_FEE = re.compile(
    r"^[\d.]+% FOREIGN EXCHANGE FEE ON THIS TRANSACTION (?P<minus>-?)£(?P<amount>[\d,]+\.\d{2})$"
)


def parse(text: str) -> ParsedFile:
    raise AdapterError("Tesco Bank has no CSV statement export here; import the PDF statements")


def _balance(match: re.Match | None, what: str) -> int:
    if match is None:
        raise AdapterError(f"the statement has no {what}")
    pence = parse_amount(match.group(1))
    return -pence if match.group(2) else pence


def _when(match: re.Match, statement_date: date) -> date:
    month = datetime.strptime(match["month"].title(), "%b").month
    year = statement_date.year - (1 if month > statement_date.month else 0)
    return date(year, month, int(match["day"]))


def parse_text(text: str) -> ParsedFile:
    found = _STATEMENT_DATE.search(text)
    if found is None:
        raise AdapterError("can't find the statement date; is this a Tesco Bank statement?")
    statement_date = datetime.strptime(found.group(1), "%d %B %Y").date()
    opening = _balance(_PREVIOUS.search(text), "previous balance")
    closing = _balance(_NEW.search(text), "new balance")
    rows: list[ParsedRow] = []

    def add(when: date, ref: str | None, description: str, pence: int, line: str) -> None:
        rows.append(
            ParsedRow(
                row_index=len(rows),
                raw=json.dumps([when.isoformat(), ref, description, line]),
                date=when,
                description=description,
                amount_pence=pence,
                kind=kind_for(description, pence),
            )
        )

    foreign: re.Match | None = None  # a merchant line waiting for its sterling amount
    last: tuple[date, str | None, str] | None = None  # the row an FX fee belongs to
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if foreign is not None:
            exchange = _EXCHANGE.match(line)
            if exchange is None:
                raise AdapterError(f"a foreign transaction has no sterling amount: {line!r}")
            when = _when(foreign, statement_date)
            pence = parse_amount(exchange["amount"]) * (
                -1 if exchange["minus"] or exchange[3] else 1
            )
            description = foreign["description"].strip()
            add(when, foreign["ref"], description, pence, line)
            last, foreign = (when, foreign["ref"], description), None
            continue
        fee = _FX_FEE.match(line)
        if fee is not None:
            if last is None:
                raise AdapterError(f"a foreign exchange fee with no transaction: {line!r}")
            when, ref, description = last
            pence = parse_amount(fee["amount"]) * (-1 if fee["minus"] else 1)
            add(when, ref, f"FOREIGN EXCHANGE FEE {description}", pence, line)
            continue
        row = _ROW.match(line)
        if row is not None:
            try:
                when = _when(row, statement_date)
            except ValueError:
                continue
            sign = -1 if row["minus"] or row["cr"] else 1
            description = row["description"].strip()
            add(when, row["ref"], description, sign * parse_amount(row["amount"]), line)
            last = (when, row["ref"], description)
            continue
        head = _FOREIGN_HEAD.match(line)
        if head is not None and head["ref"] and "£" not in line:
            foreign = head
    if foreign is not None:
        raise AdapterError("the statement ends in the middle of a foreign transaction")
    if opening + sum(row.amount_pence for row in rows) != closing:
        raise AdapterError(
            f"the {len(rows)} transactions read from the statement dated {statement_date} don't "
            "take its previous balance to its new balance; a line wasn't read"
        )
    return ParsedFile(tuple(rows), DATE_BASIS, statement_date, opening, closing)


def parse_pdf(data: bytes) -> ParsedFile:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # Tesco's fonts trigger noisy warnings
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except PdfReadError as exc:
        raise AdapterError(f"can't read the PDF: {exc}") from exc
    return parse_text(text)
