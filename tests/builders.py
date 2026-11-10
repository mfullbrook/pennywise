"""Synthetic data for tests: ledger rows in a test database, and plain engine inputs."""

import hashlib
import itertools
from datetime import date

from pennywise import db, importer
from pennywise.engine import merchants
from pennywise.layers import now
from pennywise.model import Kind, Statement, StatementData, Txn

_files = itertools.count()


def make_account(conn, slug="amex"):
    return importer.add_account(conn, slug, issuer="Test", adapter="test", display_name=slug)


def make_txn(
    conn,
    account_id,
    when,
    amount_pence,
    description="TESCO STORES 2345",
    kind="purchase",
    statement_id=None,
    card_member=None,
):
    """A CSV-sourced transaction, with the source file and row it came from."""
    n = next(_files)
    file_id = conn.execute(
        db.source_files.insert().values(
            account_id=account_id,
            sha256=hashlib.sha256(f"file-{n}".encode()).hexdigest(),
            filename=f"test-{n}.csv",
            file_type="csv",
            min_date=when,
            max_date=when,
            row_count=1,
            imported_at=now(),
        )
    ).inserted_primary_key[0]
    row_id = conn.execute(
        db.source_rows.insert().values(
            source_file_id=file_id, row_index=0, raw=f"{when},{description},{amount_pence}"
        )
    ).inserted_primary_key[0]
    return conn.execute(
        db.transactions.insert().values(
            account_id=account_id,
            source="csv",
            source_row_id=row_id,
            date=when,
            date_basis="posting",
            amount_pence=amount_pence,
            kind=kind,
            description=description,
            desc_key=merchants.desc_key(description),
            created_at=now(),
            statement_id=statement_id,
            card_member=card_member,
        )
    ).inserted_primary_key[0]


def make_statement(conn, account_id, start, end, opening=0, closing=0):
    return importer.add_statement(
        conn, account_id, start=start, end=end, opening_pence=opening, closing_pence=closing
    )


def txn(
    id, when, amount, kind=Kind.PURCHASE, merchant="Tesco", account=1, category=None, member=None
):
    return Txn(
        id=id,
        account_id=account,
        date=when,
        amount_pence=amount,
        kind=kind,
        description=merchant.upper(),
        merchant=merchant,
        category=category,
        card_member=member,
    )


def statement_data(id, start, end, txns, opening=0, closing=None, account=1, excluded=()):
    """Statement data whose printed closing reconciles unless `closing` says otherwise."""
    txns = tuple(txns)
    if closing is None:
        closing = opening + sum(t.amount_pence for t in txns)
    return StatementData(
        Statement(id, account, start, end, opening, closing), txns, tuple(excluded)
    )


AUG = (date(2026, 8, 14), date(2026, 9, 13))
SEP = (date(2026, 9, 14), date(2026, 10, 13))
