from datetime import date

from builders import SEP, statement_data, txn

from pennywise.engine.reconcile import Status, reconcile
from pennywise.model import Kind, Statement, StatementData

D = date(2026, 9, 20)


def test_a_statement_whose_transactions_explain_its_balance_reconciles():
    data = statement_data(
        1,
        *SEP,
        [
            txn(1, D, 120_000),
            txn(2, D, 4_500, Kind.PURCHASE, "Amazon"),
            txn(3, D, -2_500, Kind.REFUND),
            txn(4, D, -300_000, Kind.PAYMENT, "Payment"),
            txn(5, D, 3_210, Kind.INTEREST, "Interest"),
        ],
        opening=310_000,
        closing=135_210,
    )
    result = reconcile(data)
    assert result.status is Status.BALANCES_RECONCILE
    assert result.residual_pence == 0
    assert result.debits_by_kind == {Kind.PURCHASE: 124_500, Kind.INTEREST: 3_210}
    assert result.credits_by_kind == {Kind.REFUND: 2_500, Kind.PAYMENT: 300_000}


def test_a_missing_transaction_shows_as_a_residual():
    data = statement_data(1, *SEP, [txn(1, D, 10_000)], opening=0, closing=12_500)
    result = reconcile(data)
    assert result.status is Status.INCOMPLETE
    assert result.residual_pence == 2_500
    assert result.computed_closing_pence == 10_000


def test_excluded_transactions_are_listed_but_not_counted():
    kept, phantom = txn(1, D, 1_000), txn(2, D, 1_000)
    data = statement_data(1, *SEP, [kept], excluded=[phantom], opening=0, closing=1_000)
    result = reconcile(data)
    assert result.status is Status.BALANCES_RECONCILE
    assert (result.transaction_ids, result.excluded_ids) == ((1,), (2,))


def test_a_statement_without_balances_cant_be_reconciled():
    data = StatementData(Statement(1, 1, *SEP, None, None), (txn(1, D, 1_000),))
    result = reconcile(data)
    assert result.status is Status.BALANCES_MISSING
    assert (result.residual_pence, result.computed_closing_pence) == (None, None)
