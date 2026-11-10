"""Endute Connect sync: link a pennywise account to an Endute account and pull its
booked transactions.

Endute has no statements, so a linked account is grouped into statement periods that end
on its statement day (accounts.default_close_day): with day 24, a row booked on 3 October
belongs to the period 25 September to 24 October. Periods are created as rows arrive. The
current period is open until its statement date passes. Balances can still be typed in
from the PDF and reconciled, though a period's edges can differ from the real statement by
a day or two.

Each sync:

- asks for rows booked since the latest one already held, minus an overlap (Endute
  recommends overlapping windows: rows can arrive late);
- keeps Endute's id as the key, so a row served twice is stored once;
- keeps every row as first served (the ledger never changes an original), and counts rows
  that came back different;
- skips rows booked before the link's cutover date, which the CSV statements cover;
- fetches everything before writing, then writes in one transaction, and records the run
  (or its error) in sync_runs.
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Row, func, select
from sqlalchemy.engine import Connection, Engine

from pennywise import db
from pennywise.adapters.base import kind_for, parse_amount
from pennywise.endute import Client, EnduteError
from pennywise.engine.merchants import desc_key
from pennywise.importer import LedgerError, account_row, add_account, add_statement
from pennywise.layers import now
from pennywise.model import Kind

OVERLAP_DAYS = 14
ADAPTER = "endute"
# The fields that make a row what it is. Enrichment can be redone at Endute without the
# transaction changing, so it doesn't count.
MATERIAL_FIELDS = ("booking_date", "value_date", "amount", "currency", "description")


# Barclays card descriptions read "Merchant, Town[, foreign amount ... COUNTRY]". The town
# and currency details would split one merchant into many, so a Barclays row's description
# is its first part; the full text stays in endute_rows.raw. Other banks' text is kept as
# served: Amex's matches its CSV export, so merchant names and rules carry across.
_FIRST_PART = ("Barclays", "Barclaycard")


# Transfers and top-ups are named after the other party, not the free-text reference the
# sender typed ("To Alex Example Birthday"), so each person is one merchant.
_PARTY_CODES = ("TRANSFER", "TOPUP")

# Tesco Bank appends the transaction date to every description ("TESCO GROCERY WELWYN
# GARDENENG, Transaction Date: 2026-07-27"), which would make each charge its own merchant.
# Without it the text matches Tesco's PDF statements, so rules carry across the join.
TRANSACTION_DATE_SUFFIX = re.compile(r",\s*Transaction Date:\s*\d{4}-\d{2}-\d{2}\s*$", re.I)


def descriptor(row: dict, institution: str) -> str:
    text = str(row.get("description") or "").strip()
    party = str(row.get("counterparty") or "").strip()
    if party and row.get("proprietary_bank_transaction_code") in _PARTY_CODES:
        return f"{'From' if not str(row.get('amount', '')).startswith('-') else 'To'} {party}"
    if not text:
        return str(row.get("counterparty") or "").strip() or "(no description)"
    if institution.startswith(_FIRST_PART):
        return text.split(", ")[0].strip() or text
    return TRANSACTION_DATE_SUFFIX.sub("", text).strip() or text


# Kinds from the bank's own transaction codes (Revolut supplies them):
# money out on a card or by transfer is spending; top-ups fund the account
# like a card repayment; money in from other people reduces spending, like a refund. Rows
# without a known code fall back to the wording rules every card uses.
def kind_of(row: dict, description: str, amount_pence: int) -> Kind:
    code = row.get("proprietary_bank_transaction_code")
    money_in = amount_pence < 0
    if code in ("CARD_PAYMENT", "REV_PAYMENT", "CARD_REFUND", "CARD_CREDIT"):
        return Kind.REFUND if money_in else Kind.PURCHASE
    if code == "TRANSFER":
        if str(row.get("description", "")).casefold().startswith("balance migration"):
            return Kind.ADJUSTMENT  # Revolut moving the account between its own entities
        if str(row.get("description", "")).startswith("Junior account withdrawal"):
            # Money back from a child's Revolut junior account repays things bought on their
            # behalf: funding, not a refund.
            return Kind.PAYMENT
        return Kind.REFUND if money_in else Kind.PURCHASE
    if code == "TOPUP":
        # "Payment from <someone>" arrives as a top-up too: someone paying you back.
        paid_by_someone = str(row.get("description", "")).startswith("Payment from")
        return Kind.REFUND if money_in and paid_by_someone else Kind.PAYMENT
    if code == "ATM":
        return Kind.CASH_ADVANCE
    if code in ("CHARGE", "FEE"):
        return Kind.FEE
    return kind_for(description, amount_pence)


# --- Statement periods ---------------------------------------------------------------------


def _on_day(year: int, month: int, day: int) -> date:
    """The statement day in a month, or the month's last day if it's shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def period_end(booked: date, day: int) -> date:
    """The statement date of the period a row booked on `booked` falls in."""
    end = _on_day(booked.year, booked.month, day)
    if end >= booked:
        return end
    year, month = (booked.year + 1, 1) if booked.month == 12 else (booked.year, booked.month + 1)
    return _on_day(year, month, day)


def period_start(end: date, day: int) -> date:
    year, month = (end.year - 1, 12) if end.month == 1 else (end.year, end.month - 1)
    return _on_day(year, month, day) + timedelta(days=1)


def known_dates(conn: Connection, account_id: int) -> list[date]:
    table = db.statement_dates
    query = select(table.c.end_date).where(table.c.account_id == account_id)
    return sorted(conn.execute(query).scalars())


class _Periods:
    """The account's statements, extended with new periods as rows need them. A period
    ends on the first known statement date on or after the row's booking date; past the
    last known date, on the account's usual statement day (an estimate)."""

    def __init__(self, conn: Connection, account_id: int, day: int):
        self.conn, self.account_id, self.day = conn, account_id, day
        table = db.statements
        self.rows = list(conn.execute(select(table).where(table.c.account_id == account_id)))
        self.known = known_dates(conn, account_id)
        self.created = 0

    def _period(self, booked: date) -> tuple[date, date]:
        later = [k for k in self.known if k >= booked]
        if later:
            end = later[0]
            start = period_start(end, end.day)
        else:
            end = period_end(booked, self.day)
            start = period_start(end, self.day)
        earlier = [k for k in self.known if k < end] + [
            r.end_date for r in self.rows if r.end_date < end
        ]
        if earlier:
            start = (
                max(earlier) + timedelta(days=1)
                if later
                else max(start, max(earlier) + timedelta(days=1))
            )
        return start, end

    def statement_for(self, booked: date) -> int:
        for row in self.rows:
            if row.start_date <= booked <= row.end_date:
                if row.csv_file_id is not None:
                    raise LedgerError(
                        f"an Endute row booked {booked} falls in the CSV statement dated "
                        f"{row.end_date}; the link's cutover should be after it"
                    )
                return row.id
        start, end = self._period(booked)
        if start > booked or any(start <= r.end_date and r.start_date <= end for r in self.rows):
            raise LedgerError(
                f"can't fit a statement period around {booked} between the account's "
                "existing statements"
            )
        statement_id = add_statement(self.conn, self.account_id, start=start, end=end)
        self.rows.append(
            self.conn.execute(select(db.statements).where(db.statements.c.id == statement_id)).one()
        )
        self.created += 1
        return statement_id


def add_statement_dates(conn: Connection, account_id: int, dates: Iterable[date]) -> int:
    """Record statement dates read off real statements, then rebuild the account's Endute
    periods around them. Returns how many dates were new."""
    table = db.statement_dates
    held = set(known_dates(conn, account_id))
    new = sorted(set(dates) - held)
    for end in new:
        conn.execute(table.insert().values(account_id=account_id, end_date=end, created_at=now()))
    if new:
        reperiod(conn, account_id)
    return len(new)


def reperiod(conn: Connection, account_id: int) -> int:
    """Rebuild the periods holding Endute rows from the known dates and statement day.
    Refused once a period has balances or a membership correction, since rebuilding would
    move rows out from under them. Returns the number of periods."""
    statements, txns = db.statements, db.transactions
    derived = list(
        conn.execute(
            select(statements).where(
                statements.c.account_id == account_id, statements.c.csv_file_id.is_(None)
            )
        )
    )
    ids = [s.id for s in derived]
    for s in derived:
        if s.opening_pence is not None or s.closing_pence is not None:
            raise LedgerError(
                f"statement {s.id} (dated {s.end_date}) has balances entered, so its period "
                "can't be rebuilt; add statement dates before entering balances"
            )
    corrected = conn.execute(
        select(db.corrections.c.statement_id).where(db.corrections.c.statement_id.in_(ids))
    ).first()
    if corrected is not None:
        raise LedgerError(f"statement {corrected[0]} has a membership correction; can't rebuild")
    others = conn.execute(
        select(txns.c.id).where(txns.c.statement_id.in_(ids), txns.c.source != "endute")
    ).first()
    if others is not None:
        raise LedgerError(f"transaction {others[0]} was added by hand to a derived period")
    conn.execute(txns.update().where(txns.c.statement_id.in_(ids)).values(statement_id=None))
    conn.execute(statements.delete().where(statements.c.id.in_(ids)))
    day = account_row(conn, account_id).default_close_day
    periods = _Periods(conn, account_id, day)
    rows = conn.execute(
        select(txns.c.id, txns.c.date)
        .where(txns.c.account_id == account_id, txns.c.source == "endute")
        .order_by(txns.c.date, txns.c.id)
    )
    for txn_id, booked in list(rows):
        conn.execute(
            txns.update()
            .where(txns.c.id == txn_id)
            .values(statement_id=periods.statement_for(booked))
        )
    return periods.created


# --- Linking ----------------------------------------------------------------------------


def link_account(
    conn: Connection,
    endute_account: dict,
    slug: str,
    *,
    statement_day: int | None = None,
    display_name: str | None = None,
    cutover: date | None = None,
) -> int:
    """Feed a pennywise account from an Endute account, creating the account if `slug` is
    new. An existing account with CSV statements gets a cutover the day after its latest
    statement unless one is given, so the two sources never overlap."""
    # A current account used mainly for card spending (Revolut) works too: its transaction
    # codes say what's spending. Savings, loans and investments don't fit.
    if endute_account.get("account_type") not in (None, "credit_card", "current"):
        raise LedgerError(
            f"{endute_account['name']} is a {endute_account['account_type']} account; "
            "pennywise handles cards and spending accounts"
        )
    if endute_account.get("currency") != "GBP":
        raise LedgerError(f"{endute_account['name']} isn't in GBP")
    links = db.endute_links
    taken = conn.execute(
        select(links.c.account_id).where(links.c.endute_account_id == endute_account["id"])
    ).first()
    if taken is not None:
        raise LedgerError(f"{endute_account['name']} is already linked")
    if statement_day is not None and not 1 <= statement_day <= 31:
        raise LedgerError("the statement day must be between 1 and 31")

    existing = conn.execute(select(db.accounts).where(db.accounts.c.slug == slug)).first()
    if existing is None:
        if statement_day is None:
            raise LedgerError("a new account needs its statement day (--statement-day)")
        account_id = add_account(
            conn,
            slug,
            issuer=endute_account["institution"],
            adapter=ADAPTER,
            display_name=display_name or endute_account["name"],
            default_close_day=statement_day,
        )
    else:
        account_id = existing.id
        if conn.execute(select(links).where(links.c.account_id == account_id)).first():
            raise LedgerError(f"{slug} is already linked to an Endute account")
        day = statement_day or existing.default_close_day
        if day is None:
            raise LedgerError(f"{slug} has no statement day; pass --statement-day")
        conn.execute(
            db.accounts.update().where(db.accounts.c.id == account_id).values(default_close_day=day)
        )
        latest = conn.execute(
            select(func.max(db.statements.c.end_date)).where(
                db.statements.c.account_id == account_id,
                db.statements.c.csv_file_id.is_not(None),
            )
        ).scalar()
        if latest is not None:
            if cutover is None:
                cutover = latest + timedelta(days=1)
            elif cutover <= latest:
                raise LedgerError(
                    f"{slug} has CSV statements up to {latest}; the cutover must be after it"
                )
    conn.execute(
        links.insert().values(
            account_id=account_id,
            endute_account_id=endute_account["id"],
            institution=endute_account["institution"],
            cutover_date=cutover,
            linked_at=now(),
        )
    )
    return account_id


def linked_accounts(conn: Connection, slugs: Sequence[str] = ()) -> list[Row]:
    """Linked accounts (all, or the given slugs) with their link details."""
    query = (
        select(
            db.accounts.c.id,
            db.accounts.c.slug,
            db.accounts.c.display_name,
            db.accounts.c.default_close_day,
            db.endute_links.c.endute_account_id,
            db.endute_links.c.cutover_date,
            db.endute_links.c.institution,
        )
        .join(db.endute_links, db.endute_links.c.account_id == db.accounts.c.id)
        .order_by(db.accounts.c.slug)
    )
    rows = list(conn.execute(query))
    if slugs:
        for slug in slugs:
            account_row(conn, slug)
            if slug not in {r.slug for r in rows}:
                raise LedgerError(f"{slug} isn't linked to Endute; see `pennywise endute link`")
        rows = [r for r in rows if r.slug in slugs]
    return rows


def last_runs(conn: Connection) -> dict[int, Row]:
    """The latest sync run per account."""
    runs = db.sync_runs
    latest = select(func.max(runs.c.id)).group_by(runs.c.account_id).scalar_subquery()
    return {r.account_id: r for r in conn.execute(select(runs).where(runs.c.id.in_(latest)))}


# --- Syncing ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    slug: str
    window_from: date | None
    fetched: int = 0
    inserted: int = 0
    unchanged: int = 0
    changed: int = 0
    before_cutover: int = 0
    statements_created: int = 0
    error: str | None = None


def window_start(conn: Connection, account_id: int, cutover: date | None) -> date | None:
    """Fetch from the latest booking date held minus the overlap, never before the cutover.
    The first sync takes all the history Endute has (from the cutover, if any)."""
    latest = conn.execute(
        select(func.max(db.transactions.c.date)).where(
            db.transactions.c.account_id == account_id, db.transactions.c.source == "endute"
        )
    ).scalar()
    if latest is None:
        return cutover
    start = latest - timedelta(days=OVERLAP_DAYS)
    return max(start, cutover) if cutover is not None else start


def _canonical(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _material(row: dict) -> tuple:
    return tuple(row.get(field) for field in MATERIAL_FIELDS)


def _booked(row: dict) -> date:
    try:
        return date.fromisoformat(row["booking_date"])
    except (KeyError, TypeError, ValueError):
        raise LedgerError(f"Endute row {row.get('id')} has no valid booking date") from None


def _pence(row: dict) -> int:
    """Endute amounts are signed as the bank reports them, negative for money out. On a
    card, money out is a charge, which pennywise records as positive (it adds to what you
    owe)."""
    if row.get("currency") != "GBP":
        raise LedgerError(f"Endute row {row.get('id')} is in {row.get('currency')}, not GBP")
    try:
        return -parse_amount(str(row["amount"]))
    except (KeyError, ValueError):
        raise LedgerError(f"Endute row {row.get('id')} has no valid amount") from None


def store(
    conn: Connection,
    link: Row,
    rows: Iterable[dict],
    *,
    window_from: date | None,
    started_at: str,
) -> SyncResult:
    """Write one account's fetched rows and record the run. All or nothing."""
    run_id = conn.execute(
        db.sync_runs.insert().values(
            account_id=link.id,
            started_at=started_at,
            finished_at=started_at,
            status="ok",
            window_from=window_from,
        )
    ).inserted_primary_key[0]
    seen: dict[str, dict] = {}
    for row in rows:
        endute_id = str(row.get("id") or "")
        if not endute_id:
            raise LedgerError("Endute served a row without an id")
        seen.setdefault(endute_id, row)  # Endute can serve the same row twice
    held = {
        r.endute_id: r.raw
        for r in conn.execute(
            select(db.endute_rows.c.endute_id, db.endute_rows.c.raw).where(
                db.endute_rows.c.endute_id.in_(list(seen))
            )
        )
    }
    periods = _Periods(conn, link.id, link.default_close_day)
    counts = dict(inserted=0, unchanged=0, changed=0, before_cutover=0)
    # Oldest first, so periods are created in date order.
    for endute_id, row in sorted(seen.items(), key=lambda item: (_booked(item[1]), item[0])):
        if endute_id in held:
            same = _material(json.loads(held[endute_id])) == _material(row)
            counts["unchanged" if same else "changed"] += 1
            continue
        booked = _booked(row)
        if link.cutover_date is not None and booked < link.cutover_date:
            counts["before_cutover"] += 1
            continue
        amount = _pence(row)
        description = descriptor(row, link.institution)
        row_id = conn.execute(
            db.endute_rows.insert().values(
                account_id=link.id, endute_id=endute_id, sync_run_id=run_id, raw=_canonical(row)
            )
        ).inserted_primary_key[0]
        enrichment = row.get("enrichment") or {}
        conn.execute(
            db.transactions.insert().values(
                account_id=link.id,
                source="endute",
                endute_row_id=row_id,
                date=booked,
                date_basis="posting",
                amount_pence=amount,
                kind=kind_of(row, description, amount).value,
                description=description,
                desc_key=desc_key(description),
                issuer_category=enrichment.get("category"),
                statement_id=periods.statement_for(booked),
                created_at=started_at,
            )
        )
        counts["inserted"] += 1
    conn.execute(
        db.sync_runs.update()
        .where(db.sync_runs.c.id == run_id)
        .values(finished_at=now(), fetched=len(seen), **counts)
    )
    return SyncResult(
        link.slug,
        window_from,
        fetched=len(seen),
        statements_created=periods.created,
        **counts,
    )


def sync(engine: Engine, client: Client, slugs: Sequence[str] = ()) -> list[SyncResult]:
    """Sync every linked account (or the given ones). One account failing doesn't stop
    the others; its error is recorded and returned."""
    with db.read(engine) as conn:
        links = linked_accounts(conn, slugs)
        windows = {link.id: window_start(conn, link.id, link.cutover_date) for link in links}
    results = []
    for link in links:
        started_at = now()
        window_from = windows[link.id]
        try:
            rows = list(client.transactions(link.endute_account_id, since=window_from))
            with db.write(engine) as conn:
                results.append(
                    store(conn, link, rows, window_from=window_from, started_at=started_at)
                )
        except (EnduteError, LedgerError) as exc:
            with db.write(engine) as conn:
                conn.execute(
                    db.sync_runs.insert().values(
                        account_id=link.id,
                        started_at=started_at,
                        finished_at=now(),
                        status="error",
                        window_from=window_from,
                        error=str(exc),
                    )
                )
            results.append(SyncResult(link.slug, window_from, error=str(exc)))
    return results
