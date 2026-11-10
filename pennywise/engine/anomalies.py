"""Deterministic anomaly rules for one statement, judged against the account's history.

Only eligible debits are examined: positive amounts of a debit kind. Every flag states
the baseline it used. A rule without enough history reports that instead of flagging,
because a percentile of three purchases means nothing.

Rules (RECURRING_CHANGE is deferred to TODOS.md, eng-review D3):

- LARGE: a purchase or cash advance above the 95th percentile of earlier purchases (needs
  20 of them) or more than 5x their median (needs 5)
- DUPLICATE: same merchant and amount within 3 days (dates carry no time of day)
- NEW_MERCHANT: no earlier charge at that merchant (needs some history)
- MERCHANT_JUMP: more than 3x the median of earlier charges at that merchant (needs 3)
- COST_OF_CREDIT: any fee or interest charged
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median_low

from pennywise.model import COST_OF_CREDIT_KINDS, Kind, StatementData, Txn, gbp, is_eligible_debit


class Rule(StrEnum):
    LARGE = "large"
    DUPLICATE = "duplicate"
    NEW_MERCHANT = "new_merchant"
    MERCHANT_JUMP = "merchant_jump"
    COST_OF_CREDIT = "cost_of_credit"


LARGE_PERCENTILE = 95
LARGE_PERCENTILE_MIN_HISTORY = 20
LARGE_MEDIAN_MIN_HISTORY = 5
LARGE_MEDIAN_MULTIPLE = 5
JUMP_MULTIPLE = 3
JUMP_MIN_HISTORY = 3
DUPLICATE_WINDOW_DAYS = 3

# Kinds whose size and merchant the size-based rules judge.
_SIZED_KINDS = frozenset({Kind.PURCHASE, Kind.CASH_ADVANCE})
_RULE_ORDER = {rule: position for position, rule in enumerate(Rule)}


@dataclass(frozen=True)
class Flag:
    transaction_id: int
    rule: Rule
    baseline: str
    related_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AnomalyReport:
    statement_id: int
    flags: tuple[Flag, ...]
    insufficient_history: tuple[str, ...]  # rules that couldn't run, and why
    history_count: int
    history_start: date | None

    def flagged_ids(self) -> frozenset[int]:
        return frozenset(flag.transaction_id for flag in self.flags)


def nearest_rank(values: Sequence[int], percentile: int) -> int:
    """Nearest-rank percentile of sorted integers: no interpolation, no floats."""
    rank = -(-percentile * len(values) // 100)  # ceiling division
    return values[max(rank, 1) - 1]


def _large(candidates: list[Txn], prior: list[int], notes: list[str]) -> list[Flag]:
    count = len(prior)
    if count < LARGE_MEDIAN_MIN_HISTORY:
        notes.append(
            f"large: needs {LARGE_MEDIAN_MIN_HISTORY} earlier purchases, history has {count}"
        )
        return []
    typical = median_low(prior)
    threshold = (
        nearest_rank(prior, LARGE_PERCENTILE) if count >= LARGE_PERCENTILE_MIN_HISTORY else None
    )
    if threshold is None:
        notes.append(
            f"large: percentile check needs {LARGE_PERCENTILE_MIN_HISTORY} earlier purchases, "
            f"history has {count}; median check only"
        )
    baseline = f"{count} earlier purchases"
    flags = []
    for txn in candidates:
        reasons = []
        if threshold is not None and txn.amount_pence > threshold:
            reasons.append(
                f"above the {LARGE_PERCENTILE}th percentile ({gbp(threshold)}) of {baseline}"
            )
        if txn.amount_pence > LARGE_MEDIAN_MULTIPLE * typical:
            reasons.append(
                f"more than {LARGE_MEDIAN_MULTIPLE}x the median ({gbp(typical)}) of {baseline}"
            )
        if reasons:
            flags.append(
                Flag(txn.id, Rule.LARGE, f"{gbp(txn.amount_pence)} is " + " and ".join(reasons))
            )
    return flags


def _duplicates(eligible: list[Txn], history: Sequence[Txn]) -> list[Flag]:
    pool = eligible + [txn for txn in history if is_eligible_debit(txn)]
    flags = []
    for txn in eligible:
        twins = sorted(
            other.id
            for other in pool
            if other.id != txn.id
            and other.merchant == txn.merchant
            and other.amount_pence == txn.amount_pence
            and abs((other.date - txn.date).days) <= DUPLICATE_WINDOW_DAYS
        )
        if twins:
            flags.append(
                Flag(
                    txn.id,
                    Rule.DUPLICATE,
                    f"same merchant and amount ({gbp(txn.amount_pence)}) within "
                    f"{DUPLICATE_WINDOW_DAYS} days of transaction(s) {', '.join(map(str, twins))}",
                    tuple(twins),
                )
            )
    return flags


def _new_merchants(candidates: list[Txn], history: Sequence[Txn], notes: list[str]) -> list[Flag]:
    if not history:
        notes.append("new_merchant: no history before this statement")
        return []
    known = {txn.merchant for txn in history}
    since = min(txn.date for txn in history)
    return [
        Flag(
            txn.id,
            Rule.NEW_MERCHANT,
            f"no earlier charge at {txn.merchant} in {len(history)} transactions since {since}",
        )
        for txn in candidates
        if txn.merchant not in known
    ]


def _merchant_jumps(candidates: list[Txn], history: Sequence[Txn], notes: list[str]) -> list[Flag]:
    earlier: dict[str, list[int]] = {}
    for txn in history:
        if txn.kind in _SIZED_KINDS and txn.amount_pence > 0:
            earlier.setdefault(txn.merchant, []).append(txn.amount_pence)
    flags = []
    thin = set()
    for txn in candidates:
        prior = earlier.get(txn.merchant, [])
        if len(prior) < JUMP_MIN_HISTORY:
            if prior:
                thin.add(txn.merchant)
            continue
        typical = median_low(prior)
        if txn.amount_pence > JUMP_MULTIPLE * typical:
            flags.append(
                Flag(
                    txn.id,
                    Rule.MERCHANT_JUMP,
                    f"{gbp(txn.amount_pence)} is more than {JUMP_MULTIPLE}x the median "
                    f"({gbp(typical)}) of {len(prior)} earlier charges at {txn.merchant}",
                )
            )
    if thin:
        notes.append(
            f"merchant_jump: skipped {len(thin)} merchant(s) with fewer than "
            f"{JUMP_MIN_HISTORY} earlier charges"
        )
    return flags


def anomalies(curr: StatementData, history: Sequence[Txn]) -> AnomalyReport:
    """Flag the statement's eligible debits. `history` is the account's transactions dated
    before the statement starts (layers.history_before)."""
    eligible = [txn for txn in curr.transactions if is_eligible_debit(txn)]
    sized = [txn for txn in eligible if txn.kind in _SIZED_KINDS]
    prior_purchases = sorted(
        txn.amount_pence for txn in history if txn.kind in _SIZED_KINDS and txn.amount_pence > 0
    )
    notes: list[str] = []
    flags = [
        *_large(sized, prior_purchases, notes),
        *_duplicates(eligible, history),
        *_new_merchants(sized, history, notes),
        *_merchant_jumps(sized, history, notes),
        *(
            Flag(
                txn.id, Rule.COST_OF_CREDIT, f"{txn.kind.value} of {gbp(txn.amount_pence)} charged"
            )
            for txn in eligible
            if txn.kind in COST_OF_CREDIT_KINDS
        ),
    ]
    flags.sort(key=lambda flag: (flag.transaction_id, _RULE_ORDER[flag.rule]))
    return AnomalyReport(
        statement_id=curr.statement.id,
        flags=tuple(flags),
        insufficient_history=tuple(notes),
        history_count=len(history),
        history_start=min((txn.date for txn in history), default=None),
    )
