from datetime import date

from builders import AUG, SEP, statement_data, txn

from pennywise.engine.report import UNKNOWN_PERSON, By, spending_report, uncategorised_merchants
from pennywise.model import UNCATEGORISED, Kind

A, S = date(2026, 8, 20), date(2026, 9, 20)


def statements():
    aug = statement_data(
        1,
        *AUG,
        [
            txn(1, A, 6_000, merchant="Tesco", category="Groceries", member="A"),
            txn(2, A, 1_500, merchant="Pret", member="B"),
            txn(3, A, -120_000, Kind.PAYMENT, "Payment", member="A"),
        ],
    )
    sep = statement_data(
        2,
        *SEP,
        [
            txn(4, S, 9_000, merchant="Tesco", category="Groceries", member="B"),
            txn(5, S, 158_000, merchant="BA", category="Travel & holidays", member="A"),
            txn(6, S, -1_000, Kind.REFUND, "Tesco", category="Groceries", member="B"),
            txn(7, S, 700, merchant="Pret", member="B"),
            txn(8, S, 1_800, Kind.INTEREST, "Interest"),
            txn(9, S, 999, merchant="Netflix"),
        ],
    )
    return [aug, sep]


def test_spending_by_category_with_refunds_netted():
    report = spending_report(statements())
    assert [(line.key, line.pence) for line in report.lines] == [
        ("Travel & holidays", 158_000),
        ("Groceries", 14_000),
        (UNCATEGORISED, 3_199),
    ]
    groceries = report.line("Groceries")
    assert groceries.per_statement == (6_000, 8_000)
    assert groceries.transaction_ids == (1, 4, 6)
    assert report.spending_pence == sum(line.pence for line in report.lines) == 175_199
    assert report.per_statement_spending == (7_500, 167_699)


def test_payments_and_cost_of_credit_are_kept_apart():
    report = spending_report(statements())
    assert (report.payments_pence, report.cost_of_credit_pence) == (-120_000, 1_800)
    assert 8 not in {i for line in report.lines for i in line.transaction_ids}


def test_uncategorised_totals():
    report = spending_report(statements())
    assert (report.uncategorised_pence, report.uncategorised_count) == (3_199, 3)


def test_spending_by_person_and_by_merchant():
    by_person = spending_report(statements(), By.PERSON)
    assert [(line.key, line.pence) for line in by_person.lines] == [
        ("A", 164_000),
        ("B", 10_200),
        (UNKNOWN_PERSON, 999),
    ]
    by_merchant = spending_report(statements(), By.MERCHANT)
    assert by_merchant.line("Tesco").pence == 14_000


def test_uncategorised_merchants_biggest_first():
    lines = uncategorised_merchants(statements())
    assert [(line.key, line.pence, line.count) for line in lines] == [
        ("Pret", 2_200, 2),
        ("Netflix", 999, 1),
    ]


def test_by_month_regroups_transactions_by_their_own_date_across_accounts():
    from pennywise.engine.report import by_month, month_label

    # A statement spanning two months, and another account's statement.
    first = statement_data(1, *SEP, [txn(1, date(2026, 8, 30), 500), txn(2, date(2026, 9, 2), 700)])
    other = statement_data(2, *SEP, [txn(3, date(2026, 9, 10), 1_000, account=2)], account=2)
    months = by_month([first, other])
    assert [month_label(m.statement) for m in months] == ["2026-08", "2026-09"]
    assert [[t.id for t in m.transactions] for m in months] == [[1], [2, 3]]
    assert months[1].statement.end == date(2026, 9, 30)
    report = spending_report(months)
    assert report.per_statement_spending == (500, 1_700)
    assert report.spending_pence == 2_200
