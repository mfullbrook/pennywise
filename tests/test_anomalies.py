from datetime import date, timedelta

import pytest
from builders import SEP, statement_data, txn

from pennywise.engine.anomalies import Rule, anomalies, nearest_rank
from pennywise.model import Kind

S = date(2026, 9, 20)


def history(count, amount=2_000, merchant="Tesco", start=date(2026, 3, 1), first_id=1000):
    return [
        txn(first_id + i, start + timedelta(days=i), amount, merchant=merchant)
        for i in range(count)
    ]


def flags(report, rule):
    return {flag.transaction_id: flag for flag in report.flags if flag.rule is rule}


def run(txns, past):
    return anomalies(statement_data(1, *SEP, txns), past)


def test_nearest_rank_percentile():
    values = list(range(1, 21))  # 1..20
    assert nearest_rank(values, 95) == 19
    assert nearest_rank(values, 100) == 20
    assert nearest_rank([7], 95) == 7


def test_large_needs_five_earlier_purchases():
    report = run([txn(1, S, 500_000)], history(4))
    assert flags(report, Rule.LARGE) == {}
    assert any(note.startswith("large: needs 5") for note in report.insufficient_history)


def test_large_uses_the_median_with_thin_history():
    report = run([txn(1, S, 10_001), txn(2, S, 9_999)], history(5, amount=2_000))
    large = flags(report, Rule.LARGE)
    assert set(large) == {1}
    assert "more than 5x the median (£20.00) of 5 earlier purchases" in large[1].baseline
    assert any("median check only" in note for note in report.insufficient_history)


def test_large_also_uses_the_95th_percentile_with_enough_history():
    past = history(19, amount=2_000) + [txn(2000, date(2026, 6, 1), 5_000)]
    report = run([txn(1, S, 5_001), txn(2, S, 2_000)], past)
    large = flags(report, Rule.LARGE)
    assert set(large) == {1}
    assert "95th percentile (£20.00) of 20 earlier purchases" in large[1].baseline


def test_duplicates_within_three_days():
    report = run(
        [
            txn(1, date(2026, 9, 20), 4_999, merchant="Netflix"),
            txn(2, date(2026, 9, 23), 4_999, merchant="Netflix"),
            txn(3, date(2026, 9, 27), 4_999, merchant="Netflix"),
        ],
        [],
    )
    duplicates = flags(report, Rule.DUPLICATE)
    assert set(duplicates) == {1, 2}
    assert duplicates[1].related_ids == (2,)


def test_duplicates_across_the_statement_boundary():
    previous = txn(500, date(2026, 9, 12), 1_299, merchant="Spotify")
    report = run([txn(1, date(2026, 9, 14), 1_299, merchant="Spotify")], [previous])
    assert flags(report, Rule.DUPLICATE)[1].related_ids == (500,)


def test_new_merchants_need_some_history():
    assert flags(run([txn(1, S, 1_000, merchant="Currys")], []), Rule.NEW_MERCHANT) == {}
    report = run([txn(1, S, 1_000, merchant="Currys"), txn(2, S, 1_000)], history(3))
    assert set(flags(report, Rule.NEW_MERCHANT)) == {1}


def test_merchant_jump_needs_three_earlier_charges():
    past = history(3, amount=1_000, merchant="Uber") + history(
        2, amount=1_000, merchant="Deliveroo", first_id=2000
    )
    report = run([txn(1, S, 3_001, merchant="Uber"), txn(2, S, 9_000, merchant="Deliveroo")], past)
    assert set(flags(report, Rule.MERCHANT_JUMP)) == {1}
    assert any("skipped 1 merchant" in note for note in report.insufficient_history)


def test_fees_and_interest_are_cost_of_credit():
    report = run(
        [
            txn(1, S, 3_210, Kind.INTEREST, "Interest"),
            txn(2, S, 1_200, Kind.FEE, "Late fee"),
            txn(3, S, -1_200, Kind.FEE, "Fee refund"),  # a credit, not eligible
        ],
        [],
    )
    assert set(flags(report, Rule.COST_OF_CREDIT)) == {1, 2}


@pytest.mark.parametrize("kind", [Kind.REFUND, Kind.PAYMENT, Kind.ADJUSTMENT])
def test_credits_are_never_flagged(kind):
    report = run([txn(1, S, -500_000, kind), txn(2, S, -500_000, kind)], history(30))
    assert report.flags == ()


def test_flags_are_in_a_stable_order_and_report_their_history():
    past = history(25)
    report = run([txn(2, S, 900_000, merchant="Currys"), txn(1, S, 4_000, Kind.INTEREST)], past)
    assert [(f.transaction_id, f.rule) for f in report.flags] == [
        (1, Rule.COST_OF_CREDIT),
        (2, Rule.LARGE),
        (2, Rule.NEW_MERCHANT),
    ]
    assert (report.history_count, report.history_start) == (25, date(2026, 3, 1))
