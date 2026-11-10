"""Statement reconciliation: do the imported transactions account for the printed balances?

    computed closing = opening + Σ amount_pence   (debits positive, credits negative)
    residual         = printed closing − computed closing

A statement whose residual isn't zero is incomplete, and nothing that compares statements
runs on it. A zero residual means the balances reconcile. It does not prove the contents
are complete: a missing purchase and a missing payment of the same amount cancel out.
A statement whose balances haven't been typed in yet can't be reconciled at all.
"""

# gstack-shortcut(dec-2c28b6b1): residual-only check, offsetting omissions pass, upgrade when an explanation looks wrong or Endute lands  # noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pennywise.model import AppliedCorrection, Kind, StatementData


class Status(StrEnum):
    BALANCES_RECONCILE = "balances_reconcile"
    INCOMPLETE = "incomplete"
    BALANCES_MISSING = "balances_missing"


@dataclass(frozen=True)
class Reconciliation:
    statement_id: int
    opening_pence: int | None
    debits_by_kind: dict[Kind, int]  # sums of positive amounts
    credits_by_kind: dict[Kind, int]  # sums of negative amounts, as positive magnitudes
    computed_closing_pence: int | None
    printed_closing_pence: int | None
    residual_pence: int | None
    status: Status
    transaction_ids: tuple[int, ...]
    excluded_ids: tuple[int, ...]
    corrections: tuple[AppliedCorrection, ...]


def computed_closing(data: StatementData) -> int | None:
    if data.statement.opening_pence is None:
        return None
    return data.statement.opening_pence + sum(txn.amount_pence for txn in data.transactions)


def residual(data: StatementData) -> int | None:
    """Printed closing minus computed closing, or None until both balances are entered."""
    computed = computed_closing(data)
    if computed is None or data.statement.closing_pence is None:
        return None
    return data.statement.closing_pence - computed


def is_reconciled(data: StatementData) -> bool:
    return residual(data) == 0


def reconcile(data: StatementData) -> Reconciliation:
    debits: dict[Kind, int] = {}
    credits: dict[Kind, int] = {}
    for txn in data.transactions:
        if txn.amount_pence >= 0:
            debits[txn.kind] = debits.get(txn.kind, 0) + txn.amount_pence
        else:
            credits[txn.kind] = credits.get(txn.kind, 0) - txn.amount_pence
    difference = residual(data)
    if difference is None:
        status = Status.BALANCES_MISSING
    elif difference == 0:
        status = Status.BALANCES_RECONCILE
    else:
        status = Status.INCOMPLETE
    return Reconciliation(
        statement_id=data.statement.id,
        opening_pence=data.statement.opening_pence,
        debits_by_kind={kind: debits[kind] for kind in Kind if kind in debits},
        credits_by_kind={kind: credits[kind] for kind in Kind if kind in credits},
        computed_closing_pence=computed_closing(data),
        printed_closing_pence=data.statement.closing_pence,
        residual_pence=difference,
        status=status,
        transaction_ids=tuple(txn.id for txn in data.transactions),
        excluded_ids=tuple(txn.id for txn in data.excluded),
        corrections=data.corrections,
    )
