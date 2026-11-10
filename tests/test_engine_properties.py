"""Property tests for the four money invariants (eng-review D10), over random statements
with every kind, both signs, unreconciled residuals and more than one card."""

from collections import Counter
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from pennywise.engine.bridge import (
    Bar,
    combine_balance,
    compute_balance_bridge,
    compute_spending_bridge,
)
from pennywise.engine.reconcile import Status, reconcile, residual
from pennywise.model import Kind, Statement, StatementData, Txn

AUG_START, SEP_START = date(2026, 8, 14), date(2026, 9, 14)
PERIOD = timedelta(days=30)


@st.composite
def statements(draw, statement_id, account_id, start, first_txn_id):
    count = draw(st.integers(0, 25))
    txns = tuple(
        Txn(
            id=first_txn_id + i,
            account_id=account_id,
            date=start + timedelta(days=draw(st.integers(0, 30))),
            amount_pence=draw(st.integers(-500_000, 500_000)),
            kind=draw(st.sampled_from(list(Kind))),
            description="X",
            merchant=draw(st.sampled_from(["Tesco", "Amazon", "Uber", "Netflix"])),
        )
        for i in range(count)
    )
    opening = draw(st.integers(-1_000_000, 5_000_000))
    off_by = draw(st.one_of(st.just(0), st.integers(-10_000, 10_000)))
    closing = opening + sum(t.amount_pence for t in txns) + off_by
    return StatementData(
        Statement(statement_id, account_id, start, start + PERIOD, opening, closing), txns
    )


def card(account_id):
    base = account_id * 100_000
    return st.tuples(
        statements(base + 1, account_id, AUG_START, base + 1_000),
        statements(base + 2, account_id, SEP_START, base + 50_000),
    )


@given(statements(1, 1, SEP_START, 1))
def test_reconcile_identity(data):
    result = reconcile(data)
    computed = (
        result.opening_pence
        + sum(result.debits_by_kind.values())
        - sum(result.credits_by_kind.values())
    )
    assert result.computed_closing_pence == computed
    assert result.residual_pence == result.printed_closing_pence - computed
    assert (result.status is Status.BALANCES_RECONCILE) == (result.residual_pence == 0)


@given(card(1))
def test_every_transaction_lands_in_exactly_one_bar(pair):
    prev, curr = pair
    bridge = compute_balance_bridge(prev, curr)
    placed = Counter(i for bar in bridge.bars for i in bar.transaction_ids)
    assert placed == Counter(t.id for t in prev.transactions + curr.transactions)
    assert bridge.bar(Bar.CARRIED_BALANCE).transaction_ids == ()


@given(card(1))
def test_unexplained_is_the_difference_of_residuals(pair):
    prev, curr = pair
    bridge = compute_balance_bridge(prev, curr)
    assert bridge.unexplained_pence == residual(curr) - residual(prev)
    merchant_total = sum(m.delta_pence for m in bridge.purchases_by_merchant)
    assert merchant_total == bridge.bar(Bar.PURCHASES).delta_pence
    spending = compute_spending_bridge(prev, curr)
    assert sum(m.delta_pence for m in spending.by_merchant) == spending.change_pence


@given(card(1), card(2))
def test_a_combined_bill_is_the_sum_of_its_cards(first, second):
    bridges = [compute_balance_bridge(*first), compute_balance_bridge(*second)]
    combined = combine_balance(bridges)
    for bar in Bar:
        assert combined.bar(bar).delta_pence == sum(b.bar(bar).delta_pence for b in bridges)
    assert combined.change_pence == sum(b.change_pence for b in bridges)
    assert combined.unexplained_pence == sum(b.unexplained_pence for b in bridges)
