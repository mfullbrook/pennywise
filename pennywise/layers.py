"""Append-only layers over the immutable ledger, and the effective view they produce.

Five layers record judgements about transactions without ever editing the original
rows:

- corrections: statement membership, exclusion, rows added by hand from a PDF
- overrides: merchant, kind or category for one transaction
- annotations: triage marks (recognised, expected_one_off, needs_checking, unmarked)
- aliases: descriptor pattern -> merchant name
- category_rules: merchant -> category

Every layer row carries provenance (source, reason, created_at) and is never updated or
deleted. The current value of a slot is its newest unrevoked row. Revoking a row (a row in
layer_revocations) brings back whatever was current before it. Passing ``as_of`` replays
the layers as they stood at that moment.
"""

from __future__ import annotations

import csv
import difflib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Row, Select, Table, func, select, true
from sqlalchemy.engine import Connection
from sqlalchemy.sql import ColumnElement

from pennywise import db
from pennywise.engine import merchants
from pennywise.engine.merchants import Alias
from pennywise.model import UNCATEGORISED, AppliedCorrection, Kind, Statement, StatementData, Txn

SEED_ALIASES = Path(__file__).parent / "seeds" / "merchant_aliases.csv"
SEED_CATEGORIES = Path(__file__).parent / "seeds" / "categories.csv"


class LayerError(ValueError):
    """A layer write was refused: bad input, or a rule such as the needs-checking guard."""


class NeedsCheckingGuard(LayerError):
    """Refused because the transaction is marked needs_checking (eng-review D9)."""


@dataclass(frozen=True)
class Layer:
    name: str
    table: Table
    target: str  # the column naming what each row applies to
    slot: tuple[str, ...] = ()  # further columns that, with target, identify one value

    def key(self, row: Row) -> tuple[Any, ...]:
        mapping = row._mapping
        return (mapping[self.target], *(mapping[column] for column in self.slot))


CORRECTIONS = Layer("corrections", db.corrections, "transaction_id", ("kind",))
OVERRIDES = Layer("overrides", db.overrides, "transaction_id", ("field",))
ANNOTATIONS = Layer("annotations", db.annotations, "transaction_id")
ALIASES = Layer("aliases", db.aliases, "pattern", ("match_type",))
CATEGORY_RULES = Layer("category_rules", db.category_rules, "merchant")


@dataclass(frozen=True)
class HistoryEntry:
    row: Row
    revoked_by: Row | None  # the layer_revocations row, if this row was revoked


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


# --- The shared helper -------------------------------------------------------------------


def _checked_reason(source: str, reason: str) -> str:
    if source not in db.SOURCES:
        raise LayerError(f"source must be one of {', '.join(db.SOURCES)}, not {source!r}")
    if not reason or not reason.strip():
        raise LayerError("a reason is required")
    return reason.strip()


def append(
    conn: Connection, layer: Layer, values: Mapping[str, Any], *, source: str, reason: str
) -> int:
    """Add a row to a layer. It becomes the current value of its slot."""
    row = {
        **values,
        "source": source,
        "reason": _checked_reason(source, reason),
        "created_at": now(),
    }
    return conn.execute(layer.table.insert().values(row)).inserted_primary_key[0]


def revoke(conn: Connection, layer: Layer, row_id: int, *, source: str, reason: str) -> int:
    """Withdraw a layer row. Its slot falls back to the row that was current before it."""
    reason = _checked_reason(source, reason)
    if conn.execute(select(layer.table.c.id).where(layer.table.c.id == row_id)).first() is None:
        raise LayerError(f"no {layer.name} row {row_id}")
    revocations = db.layer_revocations
    already = select(revocations.c.id).where(
        revocations.c.layer == layer.name, revocations.c.row_id == row_id
    )
    if conn.execute(already).first() is not None:
        raise LayerError(f"{layer.name} row {row_id} is already revoked")
    insert = revocations.insert().values(
        layer=layer.name, row_id=row_id, source=source, reason=reason, created_at=now()
    )
    return conn.execute(insert).inserted_primary_key[0]


def _live(layer: Layer, as_of: str | None) -> Select:
    """Layer rows that existed and were not revoked as of `as_of` (now, if None)."""
    table, revocations = layer.table, db.layer_revocations
    revoked = select(revocations.c.row_id).where(revocations.c.layer == layer.name)
    query = select(table)
    if as_of is not None:
        revoked = revoked.where(revocations.c.created_at <= as_of)
        query = query.where(table.c.created_at <= as_of)
    return query.where(table.c.id.not_in(revoked))


def current(
    conn: Connection,
    layer: Layer,
    target: Any,
    slot: tuple[Any, ...] = (),
    *,
    as_of: str | None = None,
) -> Row | None:
    """The current row of one slot, or None. For single lookups only: loops use current_all."""
    table = layer.table
    query = _live(layer, as_of).where(table.c[layer.target] == target)
    for column, value in zip(layer.slot, slot, strict=True):
        query = query.where(table.c[column] == value)
    return conn.execute(query.order_by(table.c.id.desc()).limit(1)).first()


def current_all(
    conn: Connection,
    layer: Layer,
    *,
    as_of: str | None = None,
    targets: Collection[Any] | None = None,
) -> dict[tuple[Any, ...], Row]:
    """The current row of every slot, keyed by (target, *slot), in one window query."""
    live = _live(layer, as_of)
    if targets is not None:
        live = live.where(layer.table.c[layer.target].in_(list(targets)))
    live = live.subquery()
    rank = (
        func.row_number()
        .over(
            partition_by=[live.c[layer.target], *(live.c[column] for column in layer.slot)],
            order_by=live.c.id.desc(),
        )
        .label("slot_rank")
    )
    ranked = select(*live.c, rank).subquery()
    columns = [ranked.c[column.name] for column in live.c]
    rows = conn.execute(select(*columns).where(ranked.c.slot_rank == 1)).all()
    return {layer.key(row): row for row in rows}


def history(conn: Connection, layer: Layer, target: Any) -> list[HistoryEntry]:
    """Every row ever written for a target, oldest first, with its revocation if any."""
    table, revocations = layer.table, db.layer_revocations
    rows = conn.execute(
        select(table).where(table.c[layer.target] == target).order_by(table.c.id)
    ).all()
    revoked = {
        revocation.row_id: revocation
        for revocation in conn.execute(
            select(revocations).where(
                revocations.c.layer == layer.name,
                revocations.c.row_id.in_([row.id for row in rows]),
            )
        )
    }
    return [HistoryEntry(row, revoked.get(row.id)) for row in rows]


# --- Ledger lookups ----------------------------------------------------------------------


def _transaction_row(conn: Connection, txn_id: int) -> Row:
    row = conn.execute(select(db.transactions).where(db.transactions.c.id == txn_id)).first()
    if row is None:
        raise LayerError(f"no transaction {txn_id}")
    return row


def _statement(row: Row) -> Statement:
    return Statement(
        id=row.id,
        account_id=row.account_id,
        start=row.start_date,
        end=row.end_date,
        opening_pence=row.opening_pence,
        closing_pence=row.closing_pence,
        due=row.due_date,
    )


def get_statement(conn: Connection, statement_id: int) -> Statement:
    row = conn.execute(select(db.statements).where(db.statements.c.id == statement_id)).first()
    if row is None:
        raise LayerError(f"no statement {statement_id}")
    return _statement(row)


def previous_statement(conn: Connection, statement: Statement) -> Statement | None:
    """The statement on the same account with the latest statement date before this one's."""
    table = db.statements
    row = conn.execute(
        select(table)
        .where(table.c.account_id == statement.account_id, table.c.end_date < statement.end)
        .order_by(table.c.end_date.desc())
        .limit(1)
    ).first()
    return None if row is None else _statement(row)


# --- Corrections -------------------------------------------------------------------------


def mark_status(conn: Connection, txn_id: int, *, as_of: str | None = None) -> str | None:
    """A transaction's current triage status, or None if it has no mark (or was cleared)."""
    row = current(conn, ANNOTATIONS, txn_id, as_of=as_of)
    return None if row is None or row.status == "unmarked" else row.status


def _guard_needs_checking(conn: Connection, txn_id: int, action: str, force: bool) -> None:
    if not force and mark_status(conn, txn_id) == "needs_checking":
        raise NeedsCheckingGuard(
            f"transaction {txn_id} is marked needs_checking, and {action} would remove it from "
            "this statement's dispute pack; use --force if that's what you mean"
        )


def exclude(
    conn: Connection, txn_id: int, *, reason: str, source: str = "cli", force: bool = False
) -> int:
    """Take a transaction out of every figure. It stays visible as evidence."""
    _transaction_row(conn, txn_id)
    _guard_needs_checking(conn, txn_id, "excluding it", force)
    values = {"transaction_id": txn_id, "kind": "exclude"}
    return append(conn, CORRECTIONS, values, source=source, reason=reason)


def move_to_statement(
    conn: Connection,
    txn_id: int,
    statement_id: int,
    *,
    reason: str,
    source: str = "cli",
    force: bool = False,
) -> int:
    """Assign a transaction to a statement regardless of its date (same account only)."""
    txn = _transaction_row(conn, txn_id)
    statement = get_statement(conn, statement_id)
    if txn.account_id != statement.account_id:
        raise LayerError(
            f"transaction {txn_id} and statement {statement_id} are on different accounts"
        )
    _guard_needs_checking(conn, txn_id, "moving it to another statement", force)
    values = {"transaction_id": txn_id, "kind": "membership", "statement_id": statement_id}
    return append(conn, CORRECTIONS, values, source=source, reason=reason)


def add_row(
    conn: Connection,
    statement_id: int,
    *,
    txn_date: date,
    amount_pence: int,
    kind: str,
    description: str,
    reason: str,
    source: str = "cli",
    pdf_file_id: int | None = None,
    pdf_page: int | None = None,
) -> int:
    """Add a transaction that is on the statement PDF but missing from the CSV.

    Returns the new transaction's id. It belongs to the given statement, and counts in the
    figures only while its add_row correction is current, so reverting that correction takes
    it back out. The date may be the previous statement date: charges made on a statement
    day often appear on the next statement.
    """
    statement = get_statement(conn, statement_id)
    if not statement.start - timedelta(days=1) <= txn_date <= statement.end:
        raise LayerError(f"{txn_date} is outside statement {statement_id}'s period")
    try:
        kind = Kind(kind)
    except ValueError as exc:
        raise LayerError(f"unknown kind {kind!r}") from exc
    description = description.strip()
    if not description:
        raise LayerError("a description is required")
    _checked_reason(source, reason)
    txn_id = conn.execute(
        db.transactions.insert().values(
            account_id=statement.account_id,
            source="manual",
            source_row_id=None,
            date=txn_date,
            date_basis="posting",
            amount_pence=amount_pence,
            kind=kind.value,
            description=description,
            desc_key=merchants.desc_key(description),
            created_at=now(),
            statement_id=statement.id,
        )
    ).inserted_primary_key[0]
    values = {
        "transaction_id": txn_id,
        "kind": "add_row",
        "pdf_file_id": pdf_file_id,
        "pdf_page": pdf_page,
    }
    append(conn, CORRECTIONS, values, source=source, reason=reason)
    return txn_id


def revert(conn: Connection, correction_id: int, *, reason: str, source: str = "cli") -> int:
    """Undo a correction by revoking it; the correction itself stays in the history."""
    return revoke(conn, CORRECTIONS, correction_id, source=source, reason=reason)


# --- Overrides and marks -----------------------------------------------------------------


def set_override(
    conn: Connection, txn_id: int, field: str, value: str, *, reason: str, source: str = "cli"
) -> int:
    _transaction_row(conn, txn_id)
    if field not in db.OVERRIDE_FIELDS:
        raise LayerError(f"field must be one of {', '.join(db.OVERRIDE_FIELDS)}")
    value = value.strip()
    if not value:
        raise LayerError("an override value is required")
    if field == "kind":
        try:
            value = Kind(value).value
        except ValueError as exc:
            raise LayerError(f"unknown kind {value!r}") from exc
    if field == "category":
        value = _known_category(conn, value)
    values = {"transaction_id": txn_id, "field": field, "value": value}
    return append(conn, OVERRIDES, values, source=source, reason=reason)


def mark(
    conn: Connection,
    txn_id: int,
    status: str,
    *,
    reason: str,
    source: str,
    bulk_merchant: str | None = None,
) -> int:
    """Record a triage mark. `unmarked` clears the transaction back to untriaged."""
    _transaction_row(conn, txn_id)
    if status not in db.MARK_STATUSES:
        raise LayerError(f"status must be one of {', '.join(db.MARK_STATUSES)}")
    values = {"transaction_id": txn_id, "status": status, "bulk_merchant": bulk_merchant}
    return append(conn, ANNOTATIONS, values, source=source, reason=reason)


# --- Aliases -----------------------------------------------------------------------------


def _alias_pattern(pattern: str, match_type: str) -> str:
    pattern = pattern.strip()
    if not pattern:
        raise LayerError("an alias pattern is required")
    if match_type == "prefix":
        return merchants.desc_key(pattern)
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            raise LayerError(f"invalid regex {pattern!r}: {exc}") from exc
        return pattern
    raise LayerError("match_type must be prefix or regex")


def add_alias(
    conn: Connection,
    pattern: str,
    merchant: str,
    *,
    match_type: str = "prefix",
    reason: str,
    source: str = "cli",
) -> int:
    merchant = merchant.strip()
    if not merchant:
        raise LayerError("a merchant name is required")
    values = {
        "pattern": _alias_pattern(pattern, match_type),
        "match_type": match_type,
        "merchant": merchant,
    }
    return append(conn, ALIASES, values, source=source, reason=reason)


def remove_alias(
    conn: Connection, pattern: str, *, match_type: str = "prefix", reason: str, source: str = "cli"
) -> int:
    pattern = _alias_pattern(pattern, match_type)
    row = current(conn, ALIASES, pattern, (match_type,))
    if row is None:
        raise LayerError(f"no current {match_type} alias {pattern!r}")
    return revoke(conn, ALIASES, row.id, source=source, reason=reason)


def alias_table(conn: Connection, *, as_of: str | None = None) -> tuple[Alias, ...]:
    rows = current_all(conn, ALIASES, as_of=as_of).values()
    return tuple(sorted(Alias(row.pattern, row.match_type, row.merchant) for row in rows))


def load_alias_seeds(conn: Connection, path: Path = SEED_ALIASES) -> int:
    """Add seed aliases for patterns that have never had an alias. Never overrides yours,
    and never brings back one you removed."""
    added = 0
    with path.open(newline="", encoding="utf-8") as seed_file:
        for record in csv.DictReader(seed_file):
            match_type = record["match_type"]
            pattern = _alias_pattern(record["pattern"], match_type)
            ever = [e for e in history(conn, ALIASES, pattern) if e.row.match_type == match_type]
            if not ever:
                add_alias(
                    conn,
                    pattern,
                    record["merchant"],
                    match_type=match_type,
                    reason=f"seed file {path.name}",
                    source="seed",
                )
                added += 1
    return added


# --- Categories --------------------------------------------------------------------------


def category_names(conn: Connection) -> tuple[str, ...]:
    return tuple(
        conn.execute(select(db.categories.c.name).order_by(db.categories.c.name)).scalars()
    )


def _known_category(conn: Connection, name: str) -> str:
    """The category's stored name (matched case-insensitively), or LayerError."""
    wanted = name.strip().casefold()
    for known in category_names(conn):
        if known.casefold() == wanted:
            return known
    raise LayerError(f"no category {name!r}; add it first or pick one of the existing ones")


def add_category(conn: Connection, name: str, description: str) -> str:
    name = name.strip()
    if not name:
        raise LayerError("a category name is required")
    if name.casefold() == UNCATEGORISED.casefold():
        raise LayerError(f"{UNCATEGORISED!r} means no category; it can't be one")
    if any(known.casefold() == name.casefold() for known in category_names(conn)):
        raise LayerError(f"category {name!r} already exists")
    conn.execute(
        db.categories.insert().values(name=name, description=description.strip(), created_at=now())
    )
    return name


def load_category_seeds(conn: Connection, path: Path = SEED_CATEGORIES) -> int:
    """Add the seed categories that don't exist yet. Never touches yours."""
    known = {name.casefold() for name in category_names(conn)}
    added = 0
    with path.open(newline="", encoding="utf-8") as seed_file:
        for record in csv.DictReader(seed_file):
            if record["name"].casefold() not in known:
                add_category(conn, record["name"], record["description"])
                added += 1
    return added


def set_category_rule(
    conn: Connection, merchant: str, category: str, *, reason: str, source: str = "cli"
) -> int:
    """Put every transaction at a merchant (as normalised) into a category."""
    merchant = merchant.strip()
    if not merchant:
        raise LayerError("a merchant is required")
    values = {"merchant": merchant, "category": _known_category(conn, category)}
    return append(conn, CATEGORY_RULES, values, source=source, reason=reason)


def remove_category_rule(
    conn: Connection, merchant: str, *, reason: str, source: str = "cli"
) -> int:
    rules = category_rules(conn)
    name = merchant.strip()
    if name not in rules:
        matches = [m for m in rules if m.casefold() == name.casefold()]
        if len(matches) != 1:
            raise LayerError(f"no category rule for {merchant!r}")
        name = matches[0]
    row = current(conn, CATEGORY_RULES, name)
    assert row is not None
    return revoke(conn, CATEGORY_RULES, row.id, source=source, reason=reason)


def category_rules(conn: Connection, *, as_of: str | None = None) -> dict[str, str]:
    """merchant -> category for every current rule."""
    rows = current_all(conn, CATEGORY_RULES, as_of=as_of).values()
    return {row.merchant: row.category for row in rows}


# --- The effective view ------------------------------------------------------------------


def _transaction_rows(conn: Connection, where: ColumnElement[bool], as_of: str | None) -> list[Row]:
    table = db.transactions
    query = select(table).where(where)
    if as_of is not None:
        query = query.where(table.c.created_at <= as_of)
    return list(conn.execute(query.order_by(table.c.date, table.c.id)))


def _in_figures(row: Row, corrections: Mapping[tuple[Any, ...], Row]) -> bool:
    if row.source == "manual" and (row.id, "add_row") not in corrections:
        return False  # its add_row correction was reverted
    return (row.id, "exclude") not in corrections


@dataclass(frozen=True)
class _View:
    """Everything needed to turn ledger rows into what the engine sees."""

    corrections: Mapping[tuple[Any, ...], Row]
    overrides: Mapping[tuple[Any, ...], Row]
    aliases: tuple[Alias, ...]
    rules: Mapping[str, str]
    categories: frozenset[str]


def _view(conn: Connection, as_of: str | None, targets: Collection[int] | None = None) -> _View:
    return _View(
        corrections=current_all(conn, CORRECTIONS, as_of=as_of),
        overrides=current_all(conn, OVERRIDES, as_of=as_of, targets=targets),
        aliases=alias_table(conn, as_of=as_of),
        rules=category_rules(conn, as_of=as_of),
        categories=frozenset(category_names(conn)),
    )


def _to_txn(row: Row, view: _View) -> Txn:
    """Apply the layers. Category precedence: this transaction's override, then the rule
    for its merchant, then the issuer's own category if it names one of ours."""
    kind = view.overrides.get((row.id, "kind"))
    merchant_override = view.overrides.get((row.id, "merchant"))
    category_override = view.overrides.get((row.id, "category"))
    merchant = (
        merchant_override.value
        if merchant_override is not None
        else merchants.normalise(row.description, view.aliases)
    )
    if category_override is not None:
        category = category_override.value
    elif merchant in view.rules:
        category = view.rules[merchant]
    elif row.issuer_category in view.categories:
        category = row.issuer_category
    else:
        category = None
    return Txn(
        id=row.id,
        account_id=row.account_id,
        date=row.date,
        amount_pence=row.amount_pence,
        kind=Kind(kind.value if kind is not None else row.kind),
        description=row.description,
        merchant=merchant,
        category=category,
        card_member=row.card_member,
        source_category=row.issuer_category,
    )


def load_statement(
    conn: Connection, statement_id: int, *, as_of: str | None = None
) -> StatementData:
    """A statement with its transactions, every layer applied.

    A transaction belongs to the statement whose export it came from (or that it was added
    to by hand), unless a membership correction moves it to another statement.
    """
    statement = get_statement(conn, statement_id)
    corrections = current_all(conn, CORRECTIONS, as_of=as_of)
    moved = _moves(corrections)
    table = db.transactions
    moved_in = [txn_id for txn_id, target in moved.items() if target == statement.id]
    rows = [
        row
        for row in _transaction_rows(
            conn, (table.c.statement_id == statement.id) | table.c.id.in_(moved_in), as_of
        )
        if moved.get(row.id, row.statement_id) == statement.id
    ]
    view = _view(conn, as_of, targets=[row.id for row in rows])

    included: list[Txn] = []
    excluded: list[Txn] = []
    applied: list[AppliedCorrection] = []
    for row in rows:
        target = included if _in_figures(row, corrections) else excluded
        target.append(_to_txn(row, view))
        for kind in db.CORRECTION_KINDS:
            correction = corrections.get((row.id, kind))
            if correction is not None:
                applied.append(
                    AppliedCorrection(
                        correction.id, kind, row.id, correction.reason, correction.source
                    )
                )
    return StatementData(
        statement=statement,
        transactions=tuple(included),
        excluded=tuple(excluded),
        corrections=tuple(sorted(applied, key=lambda correction: correction.id)),
    )


def _moves(corrections: Mapping[tuple[Any, ...], Row]) -> dict[int, int]:
    """transaction id -> statement id, for every current membership correction."""
    return {
        txn_id: row.statement_id
        for (txn_id, kind), row in corrections.items()
        if kind == "membership"
    }


def history_before(
    conn: Connection, statement: Statement, *, as_of: str | None = None
) -> tuple[Txn, ...]:
    """The account's transactions on earlier statements, in the figures, layers applied.

    Earlier means an earlier statement date. Transactions on no statement count as history
    when dated before this statement starts.
    """
    table, statements = db.transactions, db.statements
    earlier = set(
        conn.execute(
            select(statements.c.id).where(
                statements.c.account_id == statement.account_id,
                statements.c.end_date < statement.end,
            )
        ).scalars()
    )
    view = _view(conn, as_of)
    moved = _moves(view.corrections)
    history = []
    for row in _transaction_rows(conn, table.c.account_id == statement.account_id, as_of):
        home = moved.get(row.id, row.statement_id)
        is_earlier = home in earlier if home is not None else row.date < statement.start
        if is_earlier and _in_figures(row, view.corrections):
            history.append(_to_txn(row, view))
    return tuple(history)


def merchant_names(conn: Connection) -> dict[str, int]:
    """Every merchant name transactions in the figures carry now (aliases and overrides
    applied), with how many transactions carry it."""
    view = _view(conn, None)
    counts: dict[str, int] = {}
    for row in _transaction_rows(conn, true(), None):
        if _in_figures(row, view.corrections):
            merchant = _to_txn(row, view).merchant
            counts[merchant] = counts.get(merchant, 0) + 1
    return counts


def resolve_merchant(conn: Connection, name: str) -> str:
    """The merchant as transactions carry it: the exact name, or its only case-insensitive
    match. A rule keyed by a name no transaction has would silently do nothing, so anything
    else is a LayerError that suggests the closest names."""
    known = merchant_names(conn)
    name = name.strip()
    if name in known:
        return name
    same_but_case = [merchant for merchant in known if merchant.casefold() == name.casefold()]
    if len(same_but_case) == 1:
        return same_but_case[0]
    by_folded = {merchant.casefold(): merchant for merchant in known}
    close = [by_folded[m] for m in difflib.get_close_matches(name.casefold(), by_folded, n=3)]
    hint = f"; did you mean {' or '.join(repr(m) for m in close)}?" if close else ""
    raise LayerError(f"no transactions at merchant {name!r}{hint}")
