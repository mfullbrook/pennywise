"""Plain data the engine works on. No database access or I/O in this module.

Money is always integer pence. ``amount_pence`` is positive when it increases
what you owe (a debit: purchases, fees, interest) and negative for credits
(refunds, payments).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Kind(StrEnum):
    PURCHASE = "purchase"
    REFUND = "refund"
    PAYMENT = "payment"
    FEE = "fee"
    INTEREST = "interest"
    CASH_ADVANCE = "cash_advance"
    ADJUSTMENT = "adjustment"
    OTHER = "other"


KINDS: tuple[str, ...] = tuple(kind.value for kind in Kind)

# Kinds whose positive amounts are new borrowing that anomaly rules and triage examine.
DEBIT_KINDS = frozenset(
    {Kind.PURCHASE, Kind.CASH_ADVANCE, Kind.FEE, Kind.INTEREST, Kind.ADJUSTMENT, Kind.OTHER}
)
# Spending is the signed sum of these kinds; refunds are negative, so they reduce it.
SPENDING_KINDS = frozenset({Kind.PURCHASE, Kind.CASH_ADVANCE, Kind.REFUND})
# Cost of credit is the signed sum of these kinds, reported apart from spending.
COST_OF_CREDIT_KINDS = frozenset({Kind.FEE, Kind.INTEREST})

# The group label for spending with no category. Not a category: it can't be assigned.
UNCATEGORISED = "Uncategorised"


@dataclass(frozen=True, slots=True)
class Txn:
    """A transaction as the engine sees it: overrides applied, merchant normalised."""

    id: int
    account_id: int
    date: date
    amount_pence: int
    kind: Kind
    description: str
    merchant: str
    category: str | None = None  # None means uncategorised
    card_member: str | None = None  # whose card, when the export says
    # The source's own category as served (e.g. Endute's "Food & Dining > Groceries"). A
    # hint for categorising; it only becomes the category if it names one of ours.
    source_category: str | None = None


@dataclass(frozen=True, slots=True)
class Statement:
    """A statement's printed facts. ``end`` is the statement date.

    The balances are typed in from the PDF and may be missing until then; nothing that
    needs them (reconciliation, bridges) runs without them.
    """

    id: int
    account_id: int
    start: date
    end: date
    opening_pence: int | None
    closing_pence: int | None
    due: date | None = None

    @property
    def has_balances(self) -> bool:
        return self.opening_pence is not None and self.closing_pence is not None


@dataclass(frozen=True, slots=True)
class AppliedCorrection:
    """A correction that shaped a statement's figures, listed in reconcile output."""

    id: int
    kind: str  # membership | exclude | add_row
    transaction_id: int
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class StatementData:
    """Everything the engine needs about one statement."""

    statement: Statement
    transactions: tuple[Txn, ...]  # in the figures
    excluded: tuple[Txn, ...] = ()  # still visible as evidence, but out of every total
    corrections: tuple[AppliedCorrection, ...] = ()


def is_eligible_debit(txn: Txn) -> bool:
    """Whether anomaly rules and triage look at this transaction (eligibility is by sign too)."""
    return txn.amount_pence > 0 and txn.kind in DEBIT_KINDS


def gbp(pence: int) -> str:
    """Format pence for people, e.g. -123456 -> '-£1,234.56'. Display only."""
    sign = "-" if pence < 0 else ""
    pounds, pennies = divmod(abs(pence), 100)
    return f"{sign}£{pounds:,}.{pennies:02d}"
