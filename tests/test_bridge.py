from collections import Counter
from datetime import date

import pytest
from builders import AUG, SEP, statement_data, txn

from pennywise.engine.bridge import (
    BAR_FOR_KIND,
    BalanceBridge,
    Bar,
    BillExplanation,
    Unavailable,
    balance_bridge,
    compute_balance_bridge,
    explain_bill,
    spending_bridge,
)
from pennywise.model import Kind, Statement, StatementData

A, S = date(2026, 8, 20), date(2026, 9, 20)


def one_of_every_kind(first_id, when, scale):
    return [txn(first_id + i, when, scale * (i + 1), kind) for i, kind in enumerate(Kind)]


def pair(prev_txns, curr_txns, prev_opening=100_000, account=1):
    prev = statement_data(1, *AUG, prev_txns, opening=prev_opening, account=account)
    curr = statement_data(2, *SEP, curr_txns, opening=prev.statement.closing_pence, account=account)
    return prev, curr


def test_every_transaction_lands_in_exactly_one_bar():
    prev, curr = pair(one_of_every_kind(1, A, 100), one_of_every_kind(100, S, 300))
    bridge = balance_bridge(prev, curr)
    assert isinstance(bridge, BalanceBridge)
    placed = Counter(i for bar in bridge.bars for i in bar.transaction_ids)
    every = [t.id for t in prev.transactions + curr.transactions]
    assert placed == Counter(every)
    assert bridge.unexplained_pence == 0
    assert [bar.bar for bar in bridge.bars] == list(Bar)


def test_adjustments_and_other_kinds_share_a_bar():
    assert BAR_FOR_KIND[Kind.ADJUSTMENT] is BAR_FOR_KIND[Kind.OTHER] is Bar.ADJUSTMENTS_OTHER


def test_carried_balance_is_the_change_in_opening_balance():
    prev, curr = pair([txn(1, A, 50_000)], [txn(2, S, 50_000)], prev_opening=100_000)
    bridge = balance_bridge(prev, curr)
    assert bridge.bar(Bar.CARRIED_BALANCE).delta_pence == 50_000
    assert bridge.change_pence == 50_000
    assert bridge.bar(Bar.PURCHASES).delta_pence == 0


def test_unexplained_is_the_difference_of_residuals():
    prev = statement_data(1, *AUG, [txn(1, A, 1_000)], opening=0, closing=1_300)  # residual 300
    curr = statement_data(2, *SEP, [txn(2, S, 2_000)], opening=1_300, closing=3_250)  # residual -50
    assert compute_balance_bridge(prev, curr).unexplained_pence == -50 - 300


def test_merchant_sub_bars_sum_to_the_purchases_bar():
    prev, curr = pair(
        [txn(1, A, 6_000, merchant="Tesco"), txn(2, A, 1_500, merchant="Uber")],
        [
            txn(3, S, 9_000, merchant="Tesco"),
            txn(4, S, 89_900, merchant="Currys"),
            txn(5, S, -1_000, Kind.REFUND, "Tesco"),  # refunds are not purchases
        ],
    )
    bridge = balance_bridge(prev, curr)
    merchants = {m.key: m for m in bridge.purchases_by_merchant}
    assert sum(m.delta_pence for m in merchants.values()) == bridge.bar(Bar.PURCHASES).delta_pence
    assert [m.key for m in bridge.purchases_by_merchant] == ["Currys", "Tesco", "Uber"]
    assert merchants["Currys"].not_in_previous
    assert not merchants["Tesco"].not_in_previous
    assert merchants["Uber"].delta_pence == -1_500


def test_spending_counts_purchases_cash_advances_and_refunds_only():
    prev, curr = pair(
        [txn(1, A, 10_000)],
        [
            txn(2, S, 10_000),
            txn(3, S, 5_000, Kind.CASH_ADVANCE, "ATM"),
            txn(4, S, -2_000, Kind.REFUND),
            txn(5, S, -50_000, Kind.PAYMENT, "Payment"),
            txn(6, S, 1_800, Kind.INTEREST, "Interest"),
        ],
    )
    spending = spending_bridge(prev, curr)
    assert (spending.prev_spending_pence, spending.curr_spending_pence) == (10_000, 13_000)
    assert sum(m.delta_pence for m in spending.by_merchant) == spending.change_pence


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda: (None, statement_data(2, *SEP, [])), "no previous statement"),
        (
            lambda: (
                statement_data(1, *AUG, [], account=1),
                statement_data(2, *SEP, [], account=2),
            ),
            "different accounts",
        ),
        (lambda: (statement_data(1, *SEP, []), statement_data(2, *SEP, [])), "does not end before"),
        (
            lambda: (
                statement_data(1, *AUG, [], closing=5),
                statement_data(2, *SEP, [], opening=5),
            ),
            "previous statement 1 does not reconcile",
        ),
        (
            lambda: (statement_data(1, *AUG, []), statement_data(2, *SEP, [], closing=5)),
            "statement 2 does not reconcile",
        ),
        (
            lambda: (
                statement_data(1, *AUG, [], opening=10),
                statement_data(2, *SEP, [], opening=0),
            ),
            "opens at £0.00 but statement 1 closed at £0.10",
        ),
        (
            lambda: (
                statement_data(1, *AUG, []),
                StatementData(Statement(2, 1, *SEP, None, None), ()),
            ),
            "statement 2 has no balances entered",
        ),
    ],
)
def test_bridges_say_why_they_are_unavailable(make, reason):
    prev, curr = make()
    for result in (balance_bridge(prev, curr), spending_bridge(prev, curr)):
        assert isinstance(result, Unavailable)
        assert reason in result.reason


def test_a_bill_across_cards_is_the_exact_sum_of_each_card():
    amex = pair([txn(1, A, 30_000, merchant="Tesco")], [txn(2, S, 90_000, merchant="Tesco")])
    barclaycard = (
        statement_data(3, *AUG, [txn(4, A, 10_000, merchant="Uber", account=2)], account=2),
        statement_data(
            5,
            *SEP,
            [txn(6, S, 25_000, merchant="Tesco", account=2)],
            opening=10_000,
            account=2,
        ),
    )
    bill = explain_bill([amex, barclaycard])
    assert isinstance(bill, BillExplanation)
    each = [balance_bridge(*amex), balance_bridge(*barclaycard)]
    for bar in Bar:
        assert bill.balance.bar(bar).delta_pence == sum(b.bar(bar).delta_pence for b in each)
    assert bill.balance.change_pence == sum(b.change_pence for b in each)
    assert bill.balance.unexplained_pence == 0
    tesco = next(m for m in bill.balance.purchases_by_merchant if m.key == "Tesco")
    assert (tesco.prev_pence, tesco.curr_pence, tesco.not_in_previous) == (30_000, 115_000, False)
    assert bill.spending.change_pence == (90_000 - 30_000) + (25_000 - 10_000)


def test_a_bill_is_unavailable_if_any_card_is():
    good = pair([txn(1, A, 100)], [txn(2, S, 100)])
    broken = (None, statement_data(9, *SEP, [], account=2))
    result = explain_bill([good, broken])
    assert isinstance(result, Unavailable)
    assert "statement 9 has no previous statement" in result.reason
