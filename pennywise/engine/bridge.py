"""Bill bridges: why one statement differs from the previous one, to the penny.

balance_bridge splits the change in closing balance into bars:

    carried balance    curr.opening − prev.opening
    one bar per group  Σ curr − Σ prev for the kinds in that group (BAR_FOR_KIND)

Every transaction of both statements lands in exactly one bar, so

    unexplained = change − Σ bars = curr.residual − prev.residual

which is zero whenever both statements reconcile. Purchases are broken down further by
merchant, and those sub-bars sum to the purchases bar.

spending_bridge splits the change in spending (the signed sum of purchases, cash advances
and refunds) by merchant and by category.

A bridge needs two consecutive reconciled statements on the same account. "Consecutive"
means no statement on that account has a statement date between them (callers get the
previous statement from layers.previous_statement) and the current opening equals the
previous closing.
"""

# gstack-shortcut(dec-4eb938b0): no target-vs-closing gap check, upgrade when the bill figure is a collection amount  # noqa: E501

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pennywise.engine.reconcile import residual
from pennywise.model import SPENDING_KINDS, UNCATEGORISED, Kind, StatementData, Txn, gbp


class Bar(StrEnum):
    CARRIED_BALANCE = "carried_balance"
    PURCHASES = "purchases"
    CASH_ADVANCES = "cash_advances"
    REFUNDS = "refunds"
    PAYMENTS = "payments"
    FEES = "fees"
    INTEREST = "interest"
    ADJUSTMENTS_OTHER = "adjustments_other"  # adjustments, balance transfers, anything else


BAR_FOR_KIND: dict[Kind, Bar] = {
    Kind.PURCHASE: Bar.PURCHASES,
    Kind.CASH_ADVANCE: Bar.CASH_ADVANCES,
    Kind.REFUND: Bar.REFUNDS,
    Kind.PAYMENT: Bar.PAYMENTS,
    Kind.FEE: Bar.FEES,
    Kind.INTEREST: Bar.INTEREST,
    Kind.ADJUSTMENT: Bar.ADJUSTMENTS_OTHER,
    Kind.OTHER: Bar.ADJUSTMENTS_OTHER,
}
assert set(BAR_FOR_KIND) == set(Kind), "every kind needs exactly one bar"


@dataclass(frozen=True)
class BarValue:
    bar: Bar
    prev_pence: int
    curr_pence: int
    transaction_ids: tuple[int, ...] = ()

    @property
    def delta_pence(self) -> int:
        return self.curr_pence - self.prev_pence


@dataclass(frozen=True)
class GroupDelta:
    """The change for one merchant or one category."""

    key: str
    prev_pence: int
    curr_pence: int
    not_in_previous: bool  # nothing in this group in the previous statement(s)
    transaction_ids: tuple[int, ...]

    @property
    def delta_pence(self) -> int:
        return self.curr_pence - self.prev_pence


@dataclass(frozen=True)
class BalanceBridge:
    prev_statement_ids: tuple[int, ...]
    curr_statement_ids: tuple[int, ...]
    prev_closing_pence: int
    curr_closing_pence: int
    bars: tuple[BarValue, ...]  # in Bar order, carried balance first
    purchases_by_merchant: tuple[GroupDelta, ...]

    @property
    def change_pence(self) -> int:
        return self.curr_closing_pence - self.prev_closing_pence

    @property
    def unexplained_pence(self) -> int:
        return self.change_pence - sum(bar.delta_pence for bar in self.bars)

    def bar(self, bar: Bar) -> BarValue:
        return next(value for value in self.bars if value.bar is bar)


@dataclass(frozen=True)
class SpendingBridge:
    prev_statement_ids: tuple[int, ...]
    curr_statement_ids: tuple[int, ...]
    prev_spending_pence: int
    curr_spending_pence: int
    by_merchant: tuple[GroupDelta, ...]
    by_category: tuple[GroupDelta, ...]

    @property
    def change_pence(self) -> int:
        return self.curr_spending_pence - self.prev_spending_pence


@dataclass(frozen=True)
class BillExplanation:
    balance: BalanceBridge
    spending: SpendingBridge


@dataclass(frozen=True)
class Unavailable:
    reason: str


def pair_problem(prev: StatementData | None, curr: StatementData) -> str | None:
    """Why prev -> curr can't be bridged, or None if it can."""
    current = curr.statement
    if prev is None:
        return f"statement {current.id} has no previous statement on its account"
    previous = prev.statement
    if previous.account_id != current.account_id:
        return f"statements {previous.id} and {current.id} are on different accounts"
    if previous.end >= current.end:
        return f"statement {previous.id} does not end before statement {current.id}"
    for data, label in ((prev, "previous statement"), (curr, "statement")):
        if not data.statement.has_balances:
            return f"{label} {data.statement.id} has no balances entered"
    if (difference := residual(prev)) != 0:
        return f"previous statement {previous.id} does not reconcile (residual {gbp(difference)})"
    if (difference := residual(curr)) != 0:
        return f"statement {current.id} does not reconcile (residual {gbp(difference)})"
    if current.opening_pence != previous.closing_pence:
        return (
            f"statement {current.id} opens at {gbp(current.opening_pence)} but statement "
            f"{previous.id} closed at {gbp(previous.closing_pence)}"
        )
    return None


@dataclass
class _Tally:
    prev: int = 0
    curr: int = 0
    prev_count: int = 0
    ids: list[int] = field(default_factory=list)


def by_merchant(txn: Txn) -> str:
    return txn.merchant


def by_category(txn: Txn) -> str:
    return txn.category or UNCATEGORISED


def _sorted(deltas: Iterable[GroupDelta]) -> tuple[GroupDelta, ...]:
    return tuple(sorted(deltas, key=lambda delta: (-delta.delta_pence, delta.key)))


def _group_deltas(
    prev: Iterable[Txn], curr: Iterable[Txn], kinds: frozenset[Kind], key: Callable[[Txn], str]
) -> tuple[GroupDelta, ...]:
    tallies: dict[str, _Tally] = {}
    for txn in prev:
        if txn.kind in kinds:
            tally = tallies.setdefault(key(txn), _Tally())
            tally.prev += txn.amount_pence
            tally.prev_count += 1
            tally.ids.append(txn.id)
    for txn in curr:
        if txn.kind in kinds:
            tally = tallies.setdefault(key(txn), _Tally())
            tally.curr += txn.amount_pence
            tally.ids.append(txn.id)
    return _sorted(
        GroupDelta(group, t.prev, t.curr, t.prev_count == 0, tuple(t.ids))
        for group, t in tallies.items()
    )


def compute_balance_bridge(prev: StatementData, curr: StatementData) -> BalanceBridge:
    """The bars for prev -> curr, without checking that the pair can be bridged.

    Both statements need their balances; pair_problem says so when they don't.
    """
    sums = {bar: [0, 0] for bar in Bar}
    ids: dict[Bar, list[int]] = {bar: [] for bar in Bar}
    for side, data in enumerate((prev, curr)):
        for txn in data.transactions:
            bar = BAR_FOR_KIND[txn.kind]
            sums[bar][side] += txn.amount_pence
            ids[bar].append(txn.id)
    sums[Bar.CARRIED_BALANCE] = [prev.statement.opening_pence, curr.statement.opening_pence]
    bars = tuple(BarValue(bar, sums[bar][0], sums[bar][1], tuple(ids[bar])) for bar in Bar)
    return BalanceBridge(
        prev_statement_ids=(prev.statement.id,),
        curr_statement_ids=(curr.statement.id,),
        prev_closing_pence=prev.statement.closing_pence,
        curr_closing_pence=curr.statement.closing_pence,
        bars=bars,
        purchases_by_merchant=_group_deltas(
            prev.transactions, curr.transactions, frozenset({Kind.PURCHASE}), by_merchant
        ),
    )


def compute_spending_bridge(prev: StatementData, curr: StatementData) -> SpendingBridge:
    """Spending for prev -> curr by merchant and category, without checking the pair."""

    def spending(data: StatementData) -> int:
        return sum(txn.amount_pence for txn in data.transactions if txn.kind in SPENDING_KINDS)

    return SpendingBridge(
        prev_statement_ids=(prev.statement.id,),
        curr_statement_ids=(curr.statement.id,),
        prev_spending_pence=spending(prev),
        curr_spending_pence=spending(curr),
        by_merchant=_group_deltas(
            prev.transactions, curr.transactions, SPENDING_KINDS, by_merchant
        ),
        by_category=_group_deltas(
            prev.transactions, curr.transactions, SPENDING_KINDS, by_category
        ),
    )


def balance_bridge(prev: StatementData | None, curr: StatementData) -> BalanceBridge | Unavailable:
    if (problem := pair_problem(prev, curr)) is not None:
        return Unavailable(problem)
    assert prev is not None
    return compute_balance_bridge(prev, curr)


def spending_bridge(
    prev: StatementData | None, curr: StatementData
) -> SpendingBridge | Unavailable:
    if (problem := pair_problem(prev, curr)) is not None:
        return Unavailable(problem)
    assert prev is not None
    return compute_spending_bridge(prev, curr)


def _combine_groups(groups: Iterable[Iterable[GroupDelta]]) -> tuple[GroupDelta, ...]:
    merged: dict[str, GroupDelta] = {}
    for group in groups:
        for delta in group:
            seen = merged.get(delta.key)
            merged[delta.key] = (
                delta
                if seen is None
                else GroupDelta(
                    delta.key,
                    seen.prev_pence + delta.prev_pence,
                    seen.curr_pence + delta.curr_pence,
                    seen.not_in_previous and delta.not_in_previous,
                    seen.transaction_ids + delta.transaction_ids,
                )
            )
    return _sorted(merged.values())


def combine_balance(bridges: Sequence[BalanceBridge]) -> BalanceBridge:
    """One bridge for a bill spanning several cards: the exact sum of the per-card bridges."""
    if not bridges:
        raise ValueError("nothing to combine")
    return BalanceBridge(
        prev_statement_ids=tuple(i for b in bridges for i in b.prev_statement_ids),
        curr_statement_ids=tuple(i for b in bridges for i in b.curr_statement_ids),
        prev_closing_pence=sum(b.prev_closing_pence for b in bridges),
        curr_closing_pence=sum(b.curr_closing_pence for b in bridges),
        bars=tuple(
            BarValue(
                bar,
                sum(b.bar(bar).prev_pence for b in bridges),
                sum(b.bar(bar).curr_pence for b in bridges),
                tuple(i for b in bridges for i in b.bar(bar).transaction_ids),
            )
            for bar in Bar
        ),
        purchases_by_merchant=_combine_groups(b.purchases_by_merchant for b in bridges),
    )


def combine_spending(bridges: Sequence[SpendingBridge]) -> SpendingBridge:
    if not bridges:
        raise ValueError("nothing to combine")
    return SpendingBridge(
        prev_statement_ids=tuple(i for b in bridges for i in b.prev_statement_ids),
        curr_statement_ids=tuple(i for b in bridges for i in b.curr_statement_ids),
        prev_spending_pence=sum(b.prev_spending_pence for b in bridges),
        curr_spending_pence=sum(b.curr_spending_pence for b in bridges),
        by_merchant=_combine_groups(b.by_merchant for b in bridges),
        by_category=_combine_groups(b.by_category for b in bridges),
    )


def explain_bill(
    pairs: Sequence[tuple[StatementData | None, StatementData]],
) -> BillExplanation | Unavailable:
    """Both bridges for a bill of one or more cards, each card against its own previous
    statement. Unavailable, with every reason, if any card's pair can't be bridged."""
    if not pairs:
        raise ValueError("a bill needs at least one statement")
    problems = [problem for prev, curr in pairs if (problem := pair_problem(prev, curr))]
    if problems:
        return Unavailable("; ".join(problems))
    checked = [(prev, curr) for prev, curr in pairs if prev is not None]
    return BillExplanation(
        balance=combine_balance([compute_balance_bridge(p, c) for p, c in checked]),
        spending=combine_spending([compute_spending_bridge(p, c) for p, c in checked]),
    )
