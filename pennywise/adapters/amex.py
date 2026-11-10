"""American Express UK statement export (CSV).

Columns: Date, Description, Card Member, Account #, Amount. Dates are dd/mm/yyyy. Amounts
are plain numbers: positive is a charge, negative a credit. There is no category column.

The date is treated as the transaction date: a charge made on a statement day can appear on
the next statement (eng-review D15 investigation, 2026-10-09). That's why a statement's
transactions come from its export, not from their dates.
"""

from __future__ import annotations

import csv
import io
import json

from pennywise.adapters.base import (
    AdapterError,
    ParsedFile,
    ParsedRow,
    kind_for,
    parse_amount,
    parse_date,
)

NAME = "amex"
COLUMNS = ("Date", "Description", "Card Member", "Account #", "Amount")
DATE_FORMAT = "%d/%m/%Y"
DATE_BASIS = "transaction"


def parse(text: str) -> ParsedFile:
    reader = csv.reader(io.StringIO(text))
    header = [column.strip() for column in next(reader, [])]
    if tuple(header) != COLUMNS:
        raise AdapterError(f"unexpected columns {header}; an Amex export has {list(COLUMNS)}")
    rows: list[ParsedRow] = []
    for line, fields in enumerate(reader, start=2):
        if not any(field.strip() for field in fields):
            continue
        if len(fields) != len(COLUMNS):
            raise AdapterError(f"line {line}: expected {len(COLUMNS)} fields, found {len(fields)}")
        date_text, description, card_member, _account, amount_text = (f.strip() for f in fields)
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
                card_member=card_member or None,
            )
        )
    if not rows:
        raise AdapterError("the export has no transactions")
    return ParsedFile(tuple(rows), DATE_BASIS)
