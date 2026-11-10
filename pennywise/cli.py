"""The pennywise command line.

    pennywise init
    pennywise account add amex-joint --issuer amex --name "Amex (joint account)"
    pennywise import amex-joint ~/.pennywise/statements/amex-joint/*.csv
    pennywise statements
    pennywise balances 6 --opening 1234.56 --closing 2468.10
    pennywise reconcile 6
    pennywise report [--by category|merchant|person] [--account SLUG] [--statement ID ...]
    pennywise uncategorised
    pennywise rule set "TESCO STORES" Groceries --reason "supermarket"
    pennywise alias suggest
    pennywise alias add "THE RED LION BRISTOL" "THE RED LION" --reason "same pub"
    pennywise transactions --merchant "TESCO STORES"
    pennywise override 123 --category "Gifts & charity" --reason "birthday present"
    pennywise endute key
    pennywise endute accounts
    pennywise endute link 3f2a amex-main --statement-day 24 --name "Amex (main card)"
    pennywise endute sync

Every figure comes from the deterministic engine. --json prints machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from sqlalchemy import Row, func, select
from sqlalchemy.engine import Connection, Engine

from pennywise import db, endute, importer, layers, sync
from pennywise.adapters import ADAPTERS, AdapterError
from pennywise.adapters.base import parse_amount
from pennywise.endute import EnduteError
from pennywise.engine.merchants import suffix_variants
from pennywise.engine.reconcile import Status, reconcile
from pennywise.engine.report import (
    By,
    Line,
    by_month,
    month_label,
    spending_report,
    uncategorised_merchants,
)
from pennywise.layers import LayerError
from pennywise.model import UNCATEGORISED, StatementData, gbp


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(f"pennywise: {text}\n")


def _json(value: object) -> None:
    _out(json.dumps(value, indent=2, default=str))


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], right: set[int]) -> str:
    widths = [max([len(h), *(len(row[i]) for row in rows)]) for i, h in enumerate(headers)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            cell.rjust(width) if i in right else cell.ljust(width)
            for i, (cell, width) in enumerate(zip(cells, widths, strict=True))
        ).rstrip()

    return "\n".join([line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def _share(part: int, whole: int) -> str:
    """A whole-number percentage for display."""
    if whole <= 0:
        return ""
    percent = (abs(part) * 200 + whole) // (2 * whole)
    return f"{'-' if part < 0 else ''}{percent}%"


# --- Selecting statements ----------------------------------------------------------------


def _statement_rows(conn: Connection, account: str | None, ids: Sequence[int] | None) -> list[Row]:
    statements, accounts = db.statements, db.accounts
    query = select(statements, accounts.c.slug).join(
        accounts, accounts.c.id == statements.c.account_id
    )
    if account:
        query = query.where(accounts.c.id == importer.account_row(conn, account).id)
    if ids:
        query = query.where(statements.c.id.in_(ids))
    rows = list(conn.execute(query.order_by(accounts.c.slug, statements.c.end_date)))
    if ids and len(rows) != len(set(ids)):
        missing = sorted(set(ids) - {row.id for row in rows})
        raise LayerError(f"no statement {', '.join(map(str, missing))}")
    return rows


def _load(conn: Connection, rows: Sequence[Row]) -> list[StatementData]:
    return [layers.load_statement(conn, row.id) for row in rows]


def _labels(rows: Sequence[Row]) -> list[str]:
    several = len({row.slug for row in rows}) > 1
    return [f"{row.slug} {row.end_date}" if several else str(row.end_date) for row in rows]


# --- Commands ----------------------------------------------------------------------------


def cmd_init(engine: Engine, args: argparse.Namespace) -> int:
    db.migrate(engine)
    with db.write(engine) as conn:
        aliases = layers.load_alias_seeds(conn)
        categories = layers.load_category_seeds(conn)
    _out(f"Database ready: {engine.url.database}")
    _out(f"Added {aliases} merchant aliases and {categories} categories")
    return 0


def cmd_account_add(engine: Engine, args: argparse.Namespace) -> int:
    adapter = args.adapter or args.issuer
    if adapter not in ADAPTERS:
        raise AdapterError(f"no adapter {adapter!r}; available: {', '.join(sorted(ADAPTERS))}")
    with db.write(engine) as conn:
        importer.add_account(
            conn,
            args.slug,
            issuer=args.issuer,
            adapter=adapter,
            display_name=args.name or args.slug,
            default_close_day=args.statement_day,
        )
    _out(f"Added account {args.slug}")
    return 0


def cmd_accounts(engine: Engine, args: argparse.Namespace) -> int:
    accounts, statements, txns = db.accounts, db.statements, db.transactions
    with db.read(engine) as conn:
        rows = [
            [
                row.slug,
                row.display_name,
                row.adapter,
                str(conn.scalar(select(func.count()).where(statements.c.account_id == row.id))),
                str(conn.scalar(select(func.count()).where(txns.c.account_id == row.id))),
            ]
            for row in conn.execute(select(accounts).order_by(accounts.c.slug))
        ]
    _out(_table(["Account", "Name", "Format", "Statements", "Transactions"], rows, {3, 4}))
    return 0


def cmd_import(engine: Engine, args: argparse.Namespace) -> int:
    if args.statement_date and len(args.files) > 1:
        raise LayerError("--statement-date only works with a single file")
    failures = 0
    for name in args.files:
        path = Path(name).expanduser()
        try:
            with db.write(engine) as conn:
                if args.batch:
                    batch = importer.import_batch_csv(
                        conn, args.account, path, csv_format=args.format
                    )
                else:
                    result = importer.import_statement_csv(
                        conn,
                        args.account,
                        path,
                        statement_date=args.statement_date,
                        csv_format=args.format,
                    )
                    statement = layers.get_statement(conn, result.statement_id)
        except (LayerError, AdapterError, OSError) as exc:
            failures += 1
            _err(f"{path.name}: {exc} (nothing imported from this file)")
            continue
        if args.batch:
            what = (
                "already imported"
                if batch.already_imported
                else f"{batch.rows} transactions in {len(batch.statement_ids)} statement periods"
            )
            _out(f"{path.name}: {what}")
            continue
        what = "already imported" if result.already_imported else f"{result.rows} transactions"
        _out(f"{path.name}: statement {statement.id} dated {statement.end}: {what}")
    return 1 if failures else 0


def _estimated_statements(conn: Connection) -> set[int]:
    """Periods on a card whose statement day varies (it has known statement dates) that
    end on an estimated day rather than a known date. A card with a fixed day has none."""
    statements, dates = db.statements, db.statement_dates
    known = select(dates.c.end_date).where(dates.c.account_id == statements.c.account_id)
    query = select(statements.c.id).where(
        statements.c.csv_file_id.is_(None),
        statements.c.account_id.in_(select(dates.c.account_id)),
        statements.c.end_date.not_in(known),
    )
    return set(conn.execute(query).scalars())


def _statement_label(row: Row, estimated: set[int]) -> str:
    notes = [
        n
        for n, on in (("open", row.end_date >= date.today()), ("estimated", row.id in estimated))
        if on
    ]
    return f"{row.end_date} ({', '.join(notes)})" if notes else str(row.end_date)


def cmd_statements(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rows = _statement_rows(conn, args.account, None)
        estimated = _estimated_statements(conn)
        table = []
        for row, data in zip(rows, _load(conn, rows), strict=True):
            result = reconcile(data)
            status = {
                Status.BALANCES_RECONCILE: "reconciles",
                Status.INCOMPLETE: f"off by {gbp(result.residual_pence or 0)}",
                Status.BALANCES_MISSING: "balances not entered",
            }[result.status]
            table.append(
                [
                    str(row.id),
                    row.slug,
                    _statement_label(row, estimated),
                    f"{row.start_date} to {row.end_date}",
                    str(len(data.transactions)),
                    gbp(row.closing_pence) if row.closing_pence is not None else "",
                    status,
                ]
            )
    headers = ["Id", "Account", "Statement date", "Period", "Transactions", "Balance", "Check"]
    _out(_table(headers, table, {0, 4, 5}))
    return 0


def cmd_balances(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        importer.set_balances(
            conn,
            args.statement,
            opening_pence=parse_amount(args.opening),
            closing_pence=parse_amount(args.closing),
        )
        result = reconcile(layers.load_statement(conn, args.statement))
    if result.status is Status.BALANCES_RECONCILE:
        _out(f"Statement {args.statement}: balances reconcile to the penny")
    else:
        _out(
            f"Statement {args.statement}: transactions add up to "
            f"{gbp(result.computed_closing_pence or 0)}, the statement says "
            f"{gbp(result.printed_closing_pence or 0)} (off by {gbp(result.residual_pence or 0)})"
        )
    return 0


def cmd_reconcile(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        data = layers.load_statement(conn, args.statement)
    result = reconcile(data)
    if result.status is Status.BALANCES_MISSING:
        _out(f"Statement {args.statement} has no balances yet: run `pennywise balances`")
        return 0
    lines = [["Opening balance", gbp(result.opening_pence or 0)]]
    lines += [[f"+ {kind.value}", gbp(pence)] for kind, pence in result.debits_by_kind.items()]
    lines += [[f"- {kind.value}", gbp(-pence)] for kind, pence in result.credits_by_kind.items()]
    lines += [
        ["= Computed closing", gbp(result.computed_closing_pence or 0)],
        ["Statement says", gbp(result.printed_closing_pence or 0)],
        ["Difference", gbp(result.residual_pence or 0)],
    ]
    _out(_table(["", "Amount"], lines, {1}))
    verdict = (
        "Balances reconcile."
        if result.status is Status.BALANCES_RECONCILE
        else "Doesn't reconcile: a transaction is missing, extra, or the balances are mistyped."
    )
    counts = f"{len(result.transaction_ids)} transactions, {len(result.excluded_ids)} excluded"
    _out(f"\n{verdict} {counts}.")
    for correction in result.corrections:
        _out(
            f"  correction {correction.id}: {correction.kind} {correction.transaction_id} "
            f"({correction.reason})"
        )
    return 0


def _line_json(line: Line, labels: Sequence[str]) -> dict[str, object]:
    return {
        "key": line.key,
        "pence": line.pence,
        "per_statement": dict(zip(labels, line.per_statement, strict=True)),
        "count": line.count,
    }


def cmd_report(engine: Engine, args: argparse.Namespace) -> int:
    by = By(args.by)
    with db.read(engine) as conn:
        rows = _statement_rows(conn, args.account, args.statement)
        loaded = _load(conn, rows)
    if args.months:
        loaded = by_month(loaded)
        labels = [month_label(data.statement) for data in loaded]
    else:
        labels = _labels(rows)
    report = spending_report(loaded, by)
    if args.json:
        _json(
            {
                "by": by.value,
                "grouping": "month" if args.months else "statement",
                "statements": [{"id": r.id, "account": r.slug, "date": r.end_date} for r in rows],
                "columns": [
                    {"label": label, "spending_pence": spent, "cost_of_credit_pence": cost}
                    for label, spent, cost in zip(
                        labels,
                        report.per_statement_spending,
                        report.per_statement_cost_of_credit,
                        strict=True,
                    )
                ],
                "lines": [_line_json(line, labels) for line in report.lines],
                "spending_pence": report.spending_pence,
                "uncategorised_pence": report.uncategorised_pence,
                "cost_of_credit_pence": report.cost_of_credit_pence,
                "payments_pence": report.payments_pence,
            }
        )
        return 0
    if not rows:
        _out("No statements yet: run `pennywise import`")
        return 0
    per_statement = len(labels) > 1
    headers = [by.value.capitalize(), *(labels if per_statement else []), "Total", "Share"]
    body = [
        [
            line.key,
            *([gbp(p) for p in line.per_statement] if per_statement else []),
            gbp(line.pence),
            _share(line.pence, report.spending_pence),
        ]
        for line in report.lines
    ]
    body.append(
        [
            "Spending",
            *([gbp(p) for p in report.per_statement_spending] if per_statement else []),
            gbp(report.spending_pence),
            "",
        ]
    )
    _out(_table(headers, body, set(range(1, len(headers)))))
    _out(
        f"\nNot counted as spending: fees and interest {gbp(report.cost_of_credit_pence)}, "
        f"payments {gbp(report.payments_pence)}."
    )
    if report.uncategorised_pence:
        _out(
            f"{gbp(report.uncategorised_pence)} across {report.uncategorised_count} transactions "
            "is uncategorised: see `pennywise uncategorised`."
        )
    return 0


def _source_categories(statements: Sequence[StatementData]) -> dict[str, list[str]]:
    """Per merchant, the categories its source gave its uncategorised transactions (such
    as Endute's), most frequent first."""
    counts: dict[str, Counter[str]] = {}
    for data in statements:
        for txn in data.transactions:
            if txn.category is None and txn.source_category:
                counts.setdefault(txn.merchant, Counter())[txn.source_category] += 1
    return {merchant: [name for name, _ in c.most_common()] for merchant, c in counts.items()}


def cmd_uncategorised(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rows = _statement_rows(conn, args.account, args.statement)
        statements = _load(conn, rows)
        lines = uncategorised_merchants(statements)
    labels = _labels(rows)
    total = sum(line.pence for line in lines)
    if args.json:
        hints = _source_categories(statements)
        _json(
            {
                "uncategorised_pence": total,
                "merchants": [
                    {**_line_json(line, labels), "source_categories": hints.get(line.key, [])}
                    for line in lines[: args.limit]
                ],
                "more": max(len(lines) - args.limit, 0),
            }
        )
        return 0
    if not lines:
        _out("Everything is categorised.")
        return 0
    shown = lines[: args.limit]
    body = [
        [line.key, gbp(line.pence), str(line.count), _share(line.pence, total)] for line in shown
    ]
    _out(_table(["Merchant", "Spending", "Transactions", "Share"], body, {1, 2, 3}))
    more = (
        f" ({len(lines) - len(shown)} more merchants not shown)" if len(lines) > len(shown) else ""
    )
    _out(f"\n{gbp(total)} uncategorised across {len(lines)} merchants{more}.")
    _out('Categorise a merchant: pennywise rule set "MERCHANT" CATEGORY --reason "..."')
    return 0


def cmd_transactions(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rows = _statement_rows(conn, args.account, args.statement)
        statements = _load(conn, rows)
    picked = []
    for row, data in zip(rows, statements, strict=True):
        for txn in data.transactions:
            if args.merchant and txn.merchant.casefold() != args.merchant.casefold():
                continue
            if args.uncategorised and txn.category is not None:
                continue
            if args.category and (txn.category or "").casefold() != args.category.casefold():
                continue
            picked.append((row, txn))
    if args.json:
        _json(
            [
                {
                    "id": txn.id,
                    "account": row.slug,
                    "statement_id": row.id,
                    "statement": row.end_date,
                    "month": txn.date.strftime("%Y-%m"),
                    "date": txn.date,
                    "card_member": txn.card_member,
                    "description": txn.description,
                    "merchant": txn.merchant,
                    "kind": txn.kind.value,
                    "pence": txn.amount_pence,
                    "category": txn.category,
                    "source_category": txn.source_category,
                }
                for row, txn in picked
            ]
        )
        return 0
    body = [
        [
            str(txn.id),
            str(txn.date),
            str(row.end_date),
            txn.card_member or "",
            txn.description,
            txn.merchant,
            txn.kind.value,
            gbp(txn.amount_pence),
            txn.category or UNCATEGORISED,
        ]
        for row, txn in picked
    ]
    headers = [
        "Id",
        "Date",
        "Statement",
        "Card member",
        "Description",
        "Merchant",
        "Kind",
        "Amount",
        "Category",
    ]
    _out(_table(headers, body, {0, 7}))
    _out(
        f"\n{len(picked)} transactions, {gbp(sum(txn.amount_pence for _, txn in picked))} in total."
    )
    return 0


def cmd_categories(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rules = layers.category_rules(conn)
        rows = list(conn.execute(select(db.categories).order_by(db.categories.c.name)))
    counts = {row.name: sum(1 for c in rules.values() if c == row.name) for row in rows}
    body = [[row.name, str(counts[row.name]), row.description] for row in rows]
    _out(_table(["Category", "Rules", "Description"], body, {1}))
    return 0


def cmd_category_add(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        name = layers.add_category(conn, args.name, args.description)
    _out(f"Added category {name}")
    return 0


def cmd_rules(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rows = layers.current_all(conn, layers.CATEGORY_RULES).values()
    body = [
        [r.merchant, r.category, r.source, r.reason] for r in sorted(rows, key=lambda r: r.merchant)
    ]
    _out(_table(["Merchant", "Category", "Source", "Reason"], body, set()))
    return 0


def cmd_rule_set(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        if args.new:
            merchant = args.merchant.strip()
        else:
            try:
                merchant = layers.resolve_merchant(conn, args.merchant)
            except LayerError as exc:
                raise LayerError(
                    f"{exc} Copy the name from `pennywise uncategorised`, or pass --new to make "
                    "a rule for a merchant that isn't imported yet."
                ) from None
        layers.set_category_rule(conn, merchant, args.category, reason=args.reason)
        category = layers.category_rules(conn)[merchant]
        count = layers.merchant_names(conn).get(merchant, 0)
    _out(f"{merchant} -> {category} ({count} transactions)")
    return 0


def cmd_rule_remove(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        layers.remove_category_rule(conn, args.merchant, reason=args.reason)
    _out(f"Removed the rule for {args.merchant.strip()}")
    return 0


def cmd_aliases(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        rows = layers.current_all(conn, layers.ALIASES).values()
    body = [
        [r.pattern, r.match_type, r.merchant, r.source, r.reason]
        for r in sorted(rows, key=lambda r: r.pattern)
        if args.all or r.source != "seed"
    ]
    _out(_table(["Pattern", "Match", "Merchant", "Source", "Reason"], body, set()))
    return 0


def cmd_alias_suggest(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        lines = uncategorised_merchants(_load(conn, _statement_rows(conn, None, None)))
        rules = layers.category_rules(conn)
        alias_outputs = {alias.merchant for alias in layers.alias_table(conn)}
    # A name an alias already produces can't be re-aliased by its own text.
    names = [line.key for line in lines if line.key not in alias_outputs]
    variants = suffix_variants(names, rules)
    found = [line for line in lines if line.key in variants]
    if args.json:
        _json(
            [
                {
                    "variant": line.key,
                    "merchant": variants[line.key],
                    "category": rules[variants[line.key]],
                    "pence": line.pence,
                    "count": line.count,
                }
                for line in found
            ]
        )
        return 0
    if not found:
        _out("No uncategorised merchant looks like a categorised one with extra words.")
        return 0
    body = [
        [line.key, variants[line.key], rules[variants[line.key]], gbp(line.pence), str(line.count)]
        for line in found
    ]
    _out(_table(["Variant", "Same as", "Category", "Spending", "Transactions"], body, {3, 4}))
    total = sum(line.pence for line in found)
    _out(f"\n{len(found)} variants, {gbp(total)} of uncategorised spending.")
    _out('Fold one in: pennywise alias add "VARIANT" "MERCHANT" --reason "..."')
    return 0


def cmd_alias_add(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        before = layers.merchant_names(conn)
        merchant = (
            args.merchant.strip() if args.new else layers.resolve_merchant(conn, args.merchant)
        )
        layers.add_alias(conn, args.pattern, merchant, reason=args.reason)
        after = layers.merchant_names(conn)
        category = layers.category_rules(conn).get(merchant, UNCATEGORISED)
    moved = after.get(merchant, 0) - before.get(merchant, 0)
    _out(f"{args.pattern.strip()} -> {merchant} ({moved} transactions moved; {category})")
    if moved == 0:
        _out("No transaction changed merchant: check the pattern against `pennywise transactions`.")
    return 0


def cmd_alias_remove(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        layers.remove_alias(conn, args.pattern, reason=args.reason)
    _out(f"Removed the alias for {args.pattern.strip()}")
    return 0


def cmd_override(engine: Engine, args: argparse.Namespace) -> int:
    changes = {"category": args.category, "merchant": args.merchant, "kind": args.kind}
    changes = {field: value for field, value in changes.items() if value is not None}
    if not changes:
        raise LayerError("say what to override: --category, --merchant or --kind")
    with db.write(engine) as conn:
        for field, value in changes.items():
            layers.set_override(conn, args.transaction, field, value, reason=args.reason)
    _out(f"Transaction {args.transaction}: " + ", ".join(f"{f} = {v}" for f, v in changes.items()))
    return 0


# --- Arguments ---------------------------------------------------------------------------


# --- Endute Connect ---------------------------------------------------------------------


def cmd_endute_key(engine: Engine, args: argparse.Namespace) -> int:
    _out("Paste your Endute API key (edk_...) when asked; it goes into the login keychain.")
    if subprocess.run(endute.keychain_command(), check=False).returncode != 0:
        _err("the keychain didn't store the key")
        return 1
    accounts = endute.Client.connect().accounts()
    _out(f"Key stored. Endute can see {len(accounts)} accounts: run `pennywise endute accounts`")
    return 0


def _endute_accounts(client: endute.Client) -> list[dict]:
    return sorted(client.accounts(), key=lambda a: (a["institution"], a["name"]))


def cmd_endute_accounts(engine: Engine, args: argparse.Namespace) -> int:
    accounts = _endute_accounts(endute.Client.connect())
    with db.read(engine) as conn:
        linked = {r.endute_account_id: r.slug for r in sync.linked_accounts(conn)}
    if args.json:
        _json([{**a, "linked_to": linked.get(a["id"])} for a in accounts])
        return 0
    rows = [
        [
            a["id"][:8],
            a["institution"],
            a["name"] + (f" ...{a['masked_identifier']}" if a.get("masked_identifier") else ""),
            a.get("account_type") or "?",
            str(a.get("oldest_booking_date") or "none yet"),
            str(a.get("available_history_days") or "?"),
            str(a.get("consent_renewal_due") or "")[:10],
            linked.get(a["id"], ""),
        ]
        for a in accounts
    ]
    headers = ["Id", "Bank", "Name", "Type", "History from", "Days", "Renew by", "Linked to"]
    _out(_table(headers, rows, {5}))
    return 0


def cmd_endute_link(engine: Engine, args: argparse.Namespace) -> int:
    accounts = _endute_accounts(endute.Client.connect())
    matches = [a for a in accounts if a["id"].startswith(args.endute_id)]
    if len(matches) != 1:
        found = "no" if not matches else "more than one"
        raise LayerError(f"{found} Endute account id starts with {args.endute_id!r}")
    with db.write(engine) as conn:
        sync.link_account(
            conn,
            matches[0],
            args.account,
            statement_day=args.statement_day,
            display_name=args.name,
            cutover=args.cutover,
        )
        link = sync.linked_accounts(conn, [args.account])[0]
    since = f", from {link.cutover_date}" if link.cutover_date else ""
    _out(
        f"Linked {matches[0]['name']} to {args.account} (statement day "
        f"{link.default_close_day}{since}). Run `pennywise endute sync {args.account}`"
    )
    return 0


def cmd_endute_sync(engine: Engine, args: argparse.Namespace) -> int:
    with db.read(engine) as conn:
        if not sync.linked_accounts(conn, args.accounts):
            raise LayerError("no accounts are linked to Endute; see `pennywise endute link`")
    results = sync.sync(engine, endute.Client.connect(), args.accounts)
    for r in results:
        if r.error:
            _err(f"{r.slug}: {r.error} (nothing synced for this account)")
            continue
        window = f"booked since {r.window_from}" if r.window_from else "all history"
        parts = [f"{r.inserted} new", f"{r.unchanged} already held"]
        if r.changed:
            parts.append(f"{r.changed} changed at Endute (originals kept)")
        if r.before_cutover:
            parts.append(f"{r.before_cutover} before the cutover (skipped)")
        if r.statements_created:
            parts.append(f"{r.statements_created} new statement periods")
        _out(f"{r.slug}: {r.fetched} fetched ({window}): {', '.join(parts)}")
    return 1 if any(r.error for r in results) else 0


def cmd_endute_dates(engine: Engine, args: argparse.Namespace) -> int:
    with db.write(engine) as conn:
        account = importer.account_row(conn, args.account)
        added = sync.add_statement_dates(conn, account.id, args.dates)
        known = sync.known_dates(conn, account.id)
    _out(f"{args.account}: {added} new statement dates; {len(known)} known, latest {known[-1]}")
    _out("Periods rebuilt around them: see `pennywise statements`")
    return 0


def cmd_endute_status(engine: Engine, args: argparse.Namespace) -> int:
    txns = db.transactions
    with db.read(engine) as conn:
        links = sync.linked_accounts(conn)
        runs = sync.last_runs(conn)
        rows = []
        for link in links:
            run = runs.get(link.id)
            latest = conn.scalar(
                select(func.max(txns.c.date)).where(
                    txns.c.account_id == link.id, txns.c.source == "endute"
                )
            )
            last = "never" if run is None else f"{run.finished_at[:16].replace('T', ' ')} UTC"
            result = "" if run is None else ("ok" if run.status == "ok" else f"error: {run.error}")
            rows.append(
                [
                    link.slug,
                    link.endute_account_id[:8],
                    str(link.default_close_day),
                    str(link.cutover_date or ""),
                    str(latest or ""),
                    last,
                    result,
                ]
            )
    if not rows:
        _out("No accounts are linked to Endute. See `pennywise endute accounts`.")
        return 0
    headers = ["Account", "Endute id", "Day", "Cutover", "Latest row", "Last sync", "Result"]
    _out(_table(headers, rows, {2}))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pennywise", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler: Callable, help: str, **kwargs) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, description=help, **kwargs)
        sub.set_defaults(handler=handler)
        return sub

    def selection(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--account", help="only this account (slug)")
        sub.add_argument("--statement", type=int, action="append", help="statement id (repeatable)")

    command("init", cmd_init, "create or upgrade the database and load the starter seeds")

    account = commands.add_parser("account", help="manage card accounts")
    account_commands = account.add_subparsers(dest="action", required=True)
    add = account_commands.add_parser("add", help="add a card account")
    add.set_defaults(handler=cmd_account_add)
    add.add_argument("slug", help="a short name, e.g. amex-joint")
    add.add_argument("--issuer", required=True, help="e.g. amex")
    add.add_argument("--adapter", help="export format (defaults to the issuer)")
    add.add_argument("--name", help="a display name")
    add.add_argument(
        "--statement-day", type=int, help="usual day of the month statements close (1-31)"
    )
    command("accounts", cmd_accounts, "list accounts")

    imp = command("import", cmd_import, "import statement CSV exports (one file per statement)")
    imp.add_argument("account", help="account slug")
    imp.add_argument(
        "files", nargs="+", help="statement exports (CSV, or PDF where supported), by date"
    )
    imp.add_argument("--statement-date", type=date.fromisoformat, help="for a single, unnamed file")
    imp.add_argument(
        "--batch",
        action="store_true",
        help="a date-range export covering many statements: split by the statement day",
    )
    imp.add_argument(
        "--format",
        choices=sorted(ADAPTERS),
        help="CSV format, for an account fed by Endute (default: from its bank)",
    )

    statements = command("statements", cmd_statements, "list statements and whether they reconcile")
    statements.add_argument("--account", help="only this account (slug)")

    balances = command("balances", cmd_balances, "enter a statement's balances from its PDF")
    balances.add_argument("statement", type=int, help="statement id")
    balances.add_argument("--opening", required=True, help="previous balance, e.g. 1234.56")
    balances.add_argument("--closing", required=True, help="new balance, e.g. 2468.10")

    rec = command(
        "reconcile", cmd_reconcile, "check a statement's transactions against its balances"
    )
    rec.add_argument("statement", type=int, help="statement id")

    report = command("report", cmd_report, "spending by category, merchant or person")
    report.add_argument("--by", choices=[b.value for b in By], default=By.CATEGORY.value)
    report.add_argument(
        "--months",
        action="store_true",
        help="columns are calendar months (by transaction date) instead of statements",
    )
    selection(report)
    report.add_argument("--json", action="store_true")

    unc = command("uncategorised", cmd_uncategorised, "merchants with uncategorised spending")
    selection(unc)
    unc.add_argument("--limit", type=int, default=30)
    unc.add_argument("--json", action="store_true")

    txns = command("transactions", cmd_transactions, "list transactions behind a figure")
    selection(txns)
    txns.add_argument("--merchant", help="exact merchant name, as reports show it")
    group = txns.add_mutually_exclusive_group()
    group.add_argument("--category")
    group.add_argument("--uncategorised", action="store_true")
    txns.add_argument("--json", action="store_true")

    command("categories", cmd_categories, "list categories")
    category = commands.add_parser("category", help="manage categories")
    category_commands = category.add_subparsers(dest="action", required=True)
    cat_add = category_commands.add_parser("add", help="add a category")
    cat_add.set_defaults(handler=cmd_category_add)
    cat_add.add_argument("name")
    cat_add.add_argument("description")

    command("rules", cmd_rules, "list merchant -> category rules")
    rule = commands.add_parser("rule", help="manage merchant -> category rules")
    rule_commands = rule.add_subparsers(dest="action", required=True)
    rule_set = rule_commands.add_parser("set", help="put a merchant in a category")
    rule_set.set_defaults(handler=cmd_rule_set)
    rule_set.add_argument("merchant", help="merchant name, as reports show it")
    rule_set.add_argument("category")
    rule_set.add_argument("--reason", required=True)
    rule_set.add_argument(
        "--new", action="store_true", help="allow a merchant no imported transaction has yet"
    )
    rule_remove = rule_commands.add_parser("remove", help="remove a merchant's rule")
    rule_remove.set_defaults(handler=cmd_rule_remove)
    rule_remove.add_argument("merchant")
    rule_remove.add_argument("--reason", required=True)

    command("aliases", cmd_aliases, "list merchant aliases you added").add_argument(
        "--all", action="store_true", help="include the starter aliases"
    )
    alias = commands.add_parser("alias", help="group descriptor variants under one merchant")
    alias_commands = alias.add_subparsers(dest="action", required=True)
    suggest = alias_commands.add_parser(
        "suggest", help="uncategorised merchants that are a categorised one plus extra words"
    )
    suggest.set_defaults(handler=cmd_alias_suggest)
    suggest.add_argument("--json", action="store_true")
    alias_add = alias_commands.add_parser(
        "add", help="map descriptors starting PATTERN to MERCHANT"
    )
    alias_add.set_defaults(handler=cmd_alias_add)
    alias_add.add_argument("pattern", help="descriptor prefix, as the variant merchant name shows")
    alias_add.add_argument("merchant", help="the merchant to fold it into")
    alias_add.add_argument("--reason", required=True)
    alias_add.add_argument(
        "--new", action="store_true", help="allow a merchant no imported transaction has yet"
    )
    alias_remove = alias_commands.add_parser("remove", help="withdraw an alias")
    alias_remove.set_defaults(handler=cmd_alias_remove)
    alias_remove.add_argument("pattern")
    alias_remove.add_argument("--reason", required=True)

    override = command(
        "override", cmd_override, "change one transaction's category, merchant or kind"
    )
    override.add_argument("transaction", type=int, help="transaction id")
    override.add_argument("--category")
    override.add_argument("--merchant")
    override.add_argument("--kind")
    override.add_argument("--reason", required=True)

    end = commands.add_parser("endute", help="connect cards through Endute Connect")
    end_commands = end.add_subparsers(dest="action", required=True)
    key = end_commands.add_parser("key", help="store the Endute API key in the keychain")
    key.set_defaults(handler=cmd_endute_key)
    end_accounts = end_commands.add_parser("accounts", help="list the accounts Endute can see")
    end_accounts.set_defaults(handler=cmd_endute_accounts)
    end_accounts.add_argument("--json", action="store_true")
    link = end_commands.add_parser("link", help="feed a pennywise account from Endute")
    link.set_defaults(handler=cmd_endute_link)
    link.add_argument("endute_id", help="Endute account id (or its first few characters)")
    link.add_argument("account", help="pennywise account slug; created if new")
    link.add_argument("--statement-day", type=int, help="day of the month statements close")
    link.add_argument("--name", help="display name for a new account")
    link.add_argument(
        "--from",
        dest="cutover",
        type=date.fromisoformat,
        help="skip rows booked before this date (default for a CSV account: after its "
        "latest statement)",
    )
    end_sync = end_commands.add_parser("sync", help="fetch new transactions from Endute")
    end_sync.set_defaults(handler=cmd_endute_sync)
    end_sync.add_argument("accounts", nargs="*", help="account slugs (default: all linked)")
    dates = end_commands.add_parser(
        "statement-dates", help="record real statement dates for a card whose day varies"
    )
    dates.set_defaults(handler=cmd_endute_dates)
    dates.add_argument("account", help="account slug")
    dates.add_argument("dates", nargs="+", type=date.fromisoformat, help="YYYY-MM-DD ...")
    end_status = end_commands.add_parser("status", help="linked accounts and their last sync")
    end_status.set_defaults(handler=cmd_endute_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine = db.open_engine()
    try:
        needs_schema = args.command != "init" and args.handler is not cmd_endute_key
        if needs_schema and not db.schema_is_current(engine):
            _err("the database isn't set up or is out of date: run `pennywise init`")
            return 2
        return args.handler(engine, args)
    except (LayerError, AdapterError, EnduteError, OSError) as exc:
        _err(str(exc))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
