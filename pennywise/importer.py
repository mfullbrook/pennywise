"""Ledger entry points: accounts, statements and statement-scoped CSV import.

Each CSV export is one statement's transactions, and every row belongs to that statement
whatever its date (eng-review D15, decided 2026-10-09 after inspecting the Amex exports:
charges dated on a statement day appear on either statement). So:

- importing the same file again does nothing;
- the same file can't be imported under another account;
- a statement that already has its export refuses a different one;
- a file that doesn't parse completely writes nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import Row, func, select
from sqlalchemy.engine import Connection

from pennywise import adapters, db
from pennywise.engine.merchants import desc_key
from pennywise.layers import LayerError, now


class LedgerError(LayerError):
    """A ledger write was refused because of bad or conflicting input."""


@dataclass(frozen=True)
class ImportResult:
    statement_id: int
    source_file_id: int
    rows: int
    already_imported: bool


def add_account(
    conn: Connection,
    slug: str,
    *,
    issuer: str,
    adapter: str,
    display_name: str,
    default_close_day: int | None = None,
) -> int:
    slug = slug.strip()
    if not slug:
        raise LedgerError("an account slug is required")
    if conn.execute(select(db.accounts.c.id).where(db.accounts.c.slug == slug)).first():
        raise LedgerError(f"account {slug!r} already exists")
    if default_close_day is not None and not 1 <= default_close_day <= 31:
        raise LedgerError("default_close_day must be between 1 and 31")
    insert = db.accounts.insert().values(
        slug=slug,
        issuer=issuer,
        adapter=adapter,
        display_name=display_name,
        default_close_day=default_close_day,
        created_at=now(),
    )
    return conn.execute(insert).inserted_primary_key[0]


def account_row(conn: Connection, account: int | str) -> Row:
    """An account by id or slug."""
    table = db.accounts
    column = table.c.id if isinstance(account, int) else table.c.slug
    row = conn.execute(select(table).where(column == account)).first()
    if row is None:
        raise LedgerError(f"no account {account!r}")
    return row


def add_statement(
    conn: Connection,
    account_id: int,
    *,
    start: date,
    end: date,
    opening_pence: int | None = None,
    closing_pence: int | None = None,
    due: date | None = None,
) -> int:
    """Record a statement. `end` is the statement date, unique per account. Balances are
    optional here and can be entered later with set_balances."""
    if start > end:
        raise LedgerError(f"statement starts ({start}) after it ends ({end})")
    account_row(conn, account_id)
    table = db.statements
    clash = select(table.c.id).where(table.c.account_id == account_id, table.c.end_date == end)
    if conn.execute(clash).first() is not None:
        raise LedgerError(f"there is already a statement dated {end} on this account")
    insert = table.insert().values(
        account_id=account_id,
        start_date=start,
        end_date=end,
        opening_pence=opening_pence,
        closing_pence=closing_pence,
        due_date=due,
        created_at=now(),
    )
    return conn.execute(insert).inserted_primary_key[0]


def set_balances(
    conn: Connection, statement_id: int, *, opening_pence: int, closing_pence: int
) -> None:
    """Enter (or correct) a statement's printed balances, typed in from its PDF."""
    table = db.statements
    updated = conn.execute(
        table.update()
        .where(table.c.id == statement_id)
        .values(opening_pence=opening_pence, closing_pence=closing_pence)
    )
    if updated.rowcount != 1:
        raise LedgerError(f"no statement {statement_id}")


def statement_date_from(path: Path) -> date | None:
    """A statement date from a file named like 2026-09-24.csv, or None."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", path.stem)
    if match is None:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


# Endute-fed accounts can take CSV statements for the history Endute can't reach (Amex
# serves only 90 days). The CSV's format follows the bank.
ENDUTE = "endute"
ENDUTE_CSV_FORMATS = {"American Express": "amex", "Tesco Bank": "tesco"}


def _csv_adapter(conn: Connection, account: Row, csv_format: str | None) -> str:
    if csv_format:
        return csv_format
    if account.adapter != ENDUTE:
        return account.adapter
    institution = conn.execute(
        select(db.endute_links.c.institution).where(db.endute_links.c.account_id == account.id)
    ).scalar()
    if institution not in ENDUTE_CSV_FORMATS:
        raise LedgerError(
            f"{account.slug} is fed by Endute from {institution}; pass the CSV format "
            f"(--format), one of: {', '.join(sorted(adapters.ADAPTERS))}"
        )
    return ENDUTE_CSV_FORMATS[institution]


def _check_before_endute(conn: Connection, account: Row, statement_date: date) -> None:
    """A CSV statement on an Endute-fed account must end before Endute's first row, or
    the same transactions would be counted twice."""
    first = conn.execute(
        select(func.min(db.transactions.c.date)).where(
            db.transactions.c.account_id == account.id, db.transactions.c.source == ENDUTE
        )
    ).scalar()
    if first is not None and statement_date >= first:
        raise LedgerError(
            f"{account.slug} has Endute transactions from {first}; a CSV statement dated "
            f"{statement_date} would overlap them. Only statements dated before {first} "
            "can be imported"
        )


def _move_cutover_past(conn: Connection, account_id: int, statement_date: date) -> None:
    """Endute rows on or before an imported CSV statement are skipped from now on."""
    links = db.endute_links
    current = conn.execute(
        select(links.c.cutover_date).where(links.c.account_id == account_id)
    ).scalar()
    after = statement_date + timedelta(days=1)
    if current is None or current < after:
        conn.execute(
            links.update().where(links.c.account_id == account_id).values(cutover_date=after)
        )


def _already_imported(
    conn: Connection, account: Row, path: Path, sha256: str
) -> ImportResult | None:
    files = db.source_files
    existing = conn.execute(select(files).where(files.c.sha256 == sha256)).first()
    if existing is None:
        return None
    if existing.account_id != account.id:
        raise LedgerError(f"{path.name} was already imported under another account")
    statement_id = conn.execute(
        select(db.statements.c.id).where(db.statements.c.csv_file_id == existing.id)
    ).scalar()
    return ImportResult(statement_id, existing.id, existing.row_count, True)


def _parse(conn: Connection, account: Row, path: Path, data: bytes, csv_format: str | None):
    adapter = adapters.get(_csv_adapter(conn, account, csv_format))
    if path.suffix.lower() == ".pdf":
        if not hasattr(adapter, "parse_pdf"):
            raise LedgerError(f"the {adapter.NAME} format has no PDF statement reader")
        return adapter, adapter.parse_pdf(data), "pdf"
    return adapter, adapter.parse(adapters.base.decode(data)), "csv"


def _previous_end(conn: Connection, account_id: int, before: date) -> date | None:
    statements = db.statements
    return conn.execute(
        select(statements.c.end_date)
        .where(statements.c.account_id == account_id, statements.c.end_date < before)
        .order_by(statements.c.end_date.desc())
        .limit(1)
    ).scalar()


def _store_rows(
    conn: Connection,
    account: Row,
    path: Path,
    sha256: str,
    file_type: str,
    parsed: adapters.base.ParsedFile,
    statement_for: dict[date, int] | int,
) -> int:
    """Record the file and its rows; `statement_for` is one statement id, or a statement id
    per row date. Returns the file id."""
    imported_at = now()
    dates = [row.date for row in parsed.rows]
    file_id = conn.execute(
        db.source_files.insert().values(
            account_id=account.id,
            sha256=sha256,
            filename=path.name,
            file_type=file_type,
            min_date=min(dates, default=None),
            max_date=max(dates, default=None),
            row_count=len(parsed.rows),
            imported_at=imported_at,
        )
    ).inserted_primary_key[0]
    for row in parsed.rows:
        source_row_id = conn.execute(
            db.source_rows.insert().values(
                source_file_id=file_id, row_index=row.row_index, raw=row.raw
            )
        ).inserted_primary_key[0]
        statement_id = statement_for if isinstance(statement_for, int) else statement_for[row.date]
        conn.execute(
            db.transactions.insert().values(
                account_id=account.id,
                source="csv",
                source_row_id=source_row_id,
                date=row.date,
                date_basis=parsed.date_basis,
                amount_pence=row.amount_pence,
                kind=row.kind.value,
                description=row.description,
                desc_key=desc_key(row.description),
                issuer_category=row.issuer_category,
                card_member=row.card_member,
                statement_id=statement_id,
                created_at=imported_at,
            )
        )
    return file_id


def import_statement_csv(
    conn: Connection,
    account: int | str,
    path: Path,
    *,
    statement_date: date | None = None,
    csv_format: str | None = None,
) -> ImportResult:
    """Import one statement's export: a CSV, or a PDF statement where the format has a
    reader. The statement is found by its date (from the argument, the PDF, or the file
    name) and created if it doesn't exist yet. A PDF also brings the statement's printed
    balances, which its reader has already checked the rows explain."""
    account_record = account_row(conn, account)
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    done = _already_imported(conn, account_record, path, sha256)
    if done is not None:
        return done

    adapter, parsed, file_type = _parse(conn, account_record, path, data, csv_format)
    named = getattr(adapter, "statement_date_from_name", lambda stem: None)(path.stem)
    statement_date = statement_date or parsed.statement_date or named or statement_date_from(path)
    if statement_date is None:
        raise LedgerError(
            f"can't tell the statement date of {path.name}; name the file YYYY-MM-DD.csv "
            "or pass the statement date"
        )
    if parsed.statement_date is not None and parsed.statement_date != statement_date:
        raise LedgerError(
            f"{path.name} is the statement dated {parsed.statement_date}, not {statement_date}"
        )
    if account_record.adapter == ENDUTE:
        _check_before_endute(conn, account_record, statement_date)
    dates = [row.date for row in parsed.rows]
    if dates and max(dates) > statement_date:
        raise LedgerError(
            f"{path.name} has a transaction dated {max(dates)}, after its statement date "
            f"{statement_date}; is the statement date right?"
        )

    statements = db.statements
    statement = conn.execute(
        select(statements).where(
            statements.c.account_id == account_record.id, statements.c.end_date == statement_date
        )
    ).first()
    if statement is not None and statement.csv_file_id is not None:
        raise LedgerError(
            f"the statement dated {statement_date} already has its export; "
            f"{path.name} is a different file"
        )
    if statement is None:
        previous = _previous_end(conn, account_record.id, statement_date)
        start = (
            previous + timedelta(days=1)
            if previous is not None
            else min(dates, default=statement_date)
        )
        statement_id = add_statement(
            conn, account_record.id, start=min(start, statement_date), end=statement_date
        )
    else:
        statement_id = statement.id

    file_id = _store_rows(conn, account_record, path, sha256, file_type, parsed, statement_id)
    values: dict[str, object] = {"csv_file_id": file_id}
    if file_type == "pdf":
        values.update(
            pdf_file_id=file_id,
            opening_pence=parsed.opening_pence,
            closing_pence=parsed.closing_pence,
        )
    conn.execute(statements.update().where(statements.c.id == statement_id).values(**values))
    if account_record.adapter == ENDUTE:
        _move_cutover_past(conn, account_record.id, statement_date)
    return ImportResult(statement_id, file_id, len(parsed.rows), False)


@dataclass(frozen=True)
class BatchResult:
    source_file_id: int
    rows: int
    statement_ids: tuple[int, ...]
    already_imported: bool


def import_batch_csv(
    conn: Connection, account: int | str, path: Path, *, csv_format: str | None = None
) -> BatchResult:
    """Import an export covering many statements (a date-range download). Rows are split
    into statement periods by transaction date, ending on the account's statement day.

    Unlike a statement export, membership here is by date, so a charge made a day or two
    before a statement date may sit one period early. Overlap is refused: the file's date
    range must not overlap any other file on the account, a period can't already hold a
    statement export, and an Endute-fed account takes only periods before its Endute rows.
    """
    from pennywise.sync import period_end, period_start  # sync imports this module

    account_record = account_row(conn, account)
    day = account_record.default_close_day
    if day is None:
        raise LedgerError(f"{account_record.slug} has no statement day to split a batch by")
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    done = _already_imported(conn, account_record, path, sha256)
    if done is not None:
        return BatchResult(done.source_file_id, done.rows, (), True)
    _, parsed, file_type = _parse(conn, account_record, path, data, csv_format)
    if file_type != "csv":
        raise LedgerError("a batch export must be a CSV")
    dates = [row.date for row in parsed.rows]
    first, last = min(dates), max(dates)
    files = db.source_files
    clash = conn.execute(
        select(files.c.filename).where(
            files.c.account_id == account_record.id,
            files.c.min_date <= last,
            files.c.max_date >= first,
        )
    ).first()
    if clash is not None:
        raise LedgerError(
            f"{path.name} ({first} to {last}) overlaps {clash.filename} already imported"
        )
    ends = sorted({period_end(d, day) for d in dates})
    if account_record.adapter == ENDUTE:
        _check_before_endute(conn, account_record, ends[-1])

    statements = db.statements
    statement_for_end: dict[date, int] = {}
    batch_statements: list[int] = []
    for end in ends:
        statement = conn.execute(
            select(statements).where(
                statements.c.account_id == account_record.id, statements.c.end_date == end
            )
        ).first()
        if statement is None:
            previous = _previous_end(conn, account_record.id, end)
            start = period_start(end, day)
            if previous is not None:
                start = max(start, previous + timedelta(days=1))
            statement_for_end[end] = add_statement(conn, account_record.id, start=start, end=end)
            batch_statements.append(statement_for_end[end])
            continue
        holder = conn.execute(
            select(files.c.file_type, files.c.filename).where(files.c.id == statement.csv_file_id)
        ).first()
        if holder is None or holder.file_type != "csv_batch":
            what = f"its export {holder.filename}" if holder else "transactions from Endute"
            raise LedgerError(
                f"the statement period ending {end} already has {what}; {path.name} would "
                "count its transactions twice"
            )
        statement_for_end[end] = statement.id  # a period split across two batch files
    file_id = _store_rows(
        conn,
        account_record,
        path,
        sha256,
        "csv_batch",
        parsed,
        {d: statement_for_end[period_end(d, day)] for d in dates},
    )
    for statement_id in batch_statements:
        conn.execute(
            statements.update().where(statements.c.id == statement_id).values(csv_file_id=file_id)
        )
    if account_record.adapter == ENDUTE:
        _move_cutover_past(conn, account_record.id, ends[-1])
    return BatchResult(file_id, len(parsed.rows), tuple(statement_for_end.values()), False)
