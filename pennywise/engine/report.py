"""Where the money went: spending grouped by category, merchant or card member.

Spending is the signed sum of purchases, cash advances and refunds, so a refund reduces the
group it lands in. Payments are never spending. Fees and interest are reported separately
as cost of credit. Every line carries the transaction ids behind it, and its per-statement
figures add up to its total.

Reports are by statement, or by calendar month (by_month): the transactions of any set of
statements regrouped by their own date, across accounts, whatever statement they're on.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pennywise.model import (
    COST_OF_CREDIT_KINDS,
    SPENDING_KINDS,
    UNCATEGORISED,
    Kind,
    Statement,
    StatementData,
    Txn,
)

UNKNOWN_PERSON = "Unknown card member"


class By(StrEnum):
    CATEGORY = "category"
    MERCHANT = "merchant"
    PERSON = "person"


KEYS: dict[By, Callable[[Txn], str]] = {
    By.CATEGORY: lambda txn: txn.category or UNCATEGORISED,
    By.MERCHANT: lambda txn: txn.merchant,
    By.PERSON: lambda txn: txn.card_member or UNKNOWN_PERSON,
}


@dataclass(frozen=True)
class Line:
    key: str
    pence: int  # signed spending across all the report's statements
    per_statement: tuple[int, ...]  # in the report's statement order
    count: int
    transaction_ids: tuple[int, ...]


@dataclass(frozen=True)
class SpendingReport:
    by: By
    statement_ids: tuple[int, ...]
    lines: tuple[Line, ...]  # biggest spending first
    spending_pence: int
    per_statement_spending: tuple[int, ...]
    per_statement_cost_of_credit: tuple[int, ...]
    uncategorised_pence: int
    uncategorised_count: int
    cost_of_credit_pence: int
    payments_pence: int  # signed: payments reduce the balance, so this is negative

    def line(self, key: str) -> Line | None:
        return next((line for line in self.lines if line.key == key), None)


def _spending(txns: Iterable[Txn]) -> list[Txn]:
    return [txn for txn in txns if txn.kind in SPENDING_KINDS]


def _lines(statements: Sequence[StatementData], key: Callable[[Txn], str]) -> tuple[Line, ...]:
    columns = len(statements)
    totals: dict[str, list[int]] = {}
    ids: dict[str, list[int]] = {}
    for column, data in enumerate(statements):
        for txn in _spending(data.transactions):
            group = key(txn)
            totals.setdefault(group, [0] * columns)[column] += txn.amount_pence
            ids.setdefault(group, []).append(txn.id)
    lines = (
        Line(group, sum(per), tuple(per), len(ids[group]), tuple(ids[group]))
        for group, per in totals.items()
    )
    return tuple(sorted(lines, key=lambda line: (-line.pence, line.key)))


def spending_report(statements: Sequence[StatementData], by: By = By.CATEGORY) -> SpendingReport:
    every = [txn for data in statements for txn in data.transactions]
    spending = _spending(every)
    uncategorised = [txn for txn in spending if txn.category is None]
    return SpendingReport(
        by=by,
        statement_ids=tuple(data.statement.id for data in statements),
        lines=_lines(statements, KEYS[by]),
        spending_pence=sum(txn.amount_pence for txn in spending),
        per_statement_spending=tuple(
            sum(txn.amount_pence for txn in _spending(data.transactions)) for data in statements
        ),
        per_statement_cost_of_credit=tuple(
            sum(t.amount_pence for t in data.transactions if t.kind in COST_OF_CREDIT_KINDS)
            for data in statements
        ),
        uncategorised_pence=sum(txn.amount_pence for txn in uncategorised),
        uncategorised_count=len(uncategorised),
        cost_of_credit_pence=sum(t.amount_pence for t in every if t.kind in COST_OF_CREDIT_KINDS),
        payments_pence=sum(txn.amount_pence for txn in every if txn.kind is Kind.PAYMENT),
    )


def uncategorised_merchants(statements: Sequence[StatementData]) -> tuple[Line, ...]:
    """Merchants with uncategorised spending, biggest first: what to write rules for next."""
    only_uncategorised = [
        StatementData(data.statement, tuple(t for t in data.transactions if t.category is None))
        for data in statements
    ]
    return _lines(only_uncategorised, KEYS[By.MERCHANT])


def month_label(statement: Statement) -> str:
    return statement.start.strftime("%Y-%m")


def by_month(statements: Sequence[StatementData]) -> list[StatementData]:
    """The statements' transactions regrouped into calendar months by transaction date,
    oldest first. Each month is a pseudo-statement (id 0, no account, no balances) so the
    report functions work on it unchanged; label it with month_label."""
    months: dict[tuple[int, int], list[Txn]] = {}
    for data in statements:
        for txn in data.transactions:
            months.setdefault((txn.date.year, txn.date.month), []).append(txn)
    result = []
    for (year, month), txns in sorted(months.items()):
        last = calendar.monthrange(year, month)[1]
        period = Statement(0, 0, date(year, month, 1), date(year, month, last), None, None)
        result.append(StatementData(period, tuple(sorted(txns, key=lambda t: (t.date, t.id)))))
    return result
