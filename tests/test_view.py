"""The effective view: what the engine sees once every layer is applied."""

import time
from datetime import date, timedelta

import pytest
from builders import AUG, SEP, make_account, make_statement, make_txn
from sqlalchemy import event

from pennywise import db, layers
from pennywise.layers import LayerError
from pennywise.model import Kind


def ids(txns):
    return [t.id for t in txns]


@pytest.fixture
def card(conn):
    account = make_account(conn)
    aug = make_statement(conn, account, *AUG)
    sep = make_statement(conn, account, *SEP)
    layers.load_category_seeds(conn)
    return account, aug, sep


def test_membership_comes_from_the_export_not_the_date(conn, card):
    account, aug, sep = card
    in_aug = make_txn(conn, account, date(2026, 8, 20), 1_000, statement_id=aug)
    # Dated on August's statement day, but exported on September's statement.
    late = make_txn(conn, account, date(2026, 9, 13), 2_000, statement_id=sep)
    in_sep = make_txn(conn, account, date(2026, 9, 20), 3_000, statement_id=sep)
    assert ids(layers.load_statement(conn, aug).transactions) == [in_aug]
    assert ids(layers.load_statement(conn, sep).transactions) == [late, in_sep]


def test_membership_corrections_move_a_transaction(conn, card):
    account, aug, sep = card
    moved = make_txn(conn, account, date(2026, 9, 13), 2_000, statement_id=sep)
    layers.move_to_statement(conn, moved, aug, reason="the PDF shows it on August's statement")
    assert ids(layers.load_statement(conn, sep).transactions) == []
    aug_data = layers.load_statement(conn, aug)
    assert ids(aug_data.transactions) == [moved]
    assert [(c.kind, c.transaction_id) for c in aug_data.corrections] == [("membership", moved)]


def test_other_accounts_never_leak_in(conn, card):
    account, _aug, sep = card
    other = make_account(conn, "barclaycard")
    other_sep = make_statement(conn, other, *SEP)
    mine = make_txn(conn, account, date(2026, 9, 20), 1_000, statement_id=sep)
    make_txn(conn, other, date(2026, 9, 20), 9_999, statement_id=other_sep)
    assert ids(layers.load_statement(conn, sep).transactions) == [mine]


def test_overrides_and_aliases_shape_each_transaction(conn, card):
    account, _aug, sep = card
    refund = make_txn(
        conn, account, date(2026, 9, 15), -2_500, "CURRYS 0042 LONDON", statement_id=sep
    )
    amazon = make_txn(
        conn, account, date(2026, 9, 16), 8_499, "AMZN MKTP UK*AB12CD34", statement_id=sep
    )
    layers.set_override(conn, refund, "kind", "refund", reason="CSV called it a purchase")
    layers.set_override(conn, refund, "merchant", "Currys PC World", reason="tidier name")
    layers.add_alias(conn, "AMZN", "Amazon", reason="group Amazon")

    by_id = {t.id: t for t in layers.load_statement(conn, sep).transactions}
    assert (by_id[refund].kind, by_id[refund].merchant) == (Kind.REFUND, "Currys PC World")
    assert (by_id[amazon].kind, by_id[amazon].merchant) == (Kind.PURCHASE, "Amazon")


def test_card_member_comes_through(conn, card):
    account, _aug, sep = card
    make_txn(conn, account, date(2026, 9, 16), 100, statement_id=sep, card_member="MS N EXAMPLE")
    assert [t.card_member for t in layers.load_statement(conn, sep).transactions] == [
        "MS N EXAMPLE"
    ]


def test_category_precedence(conn, card):
    account, _aug, sep = card
    tesco = make_txn(conn, account, date(2026, 9, 15), 6_000, "TESCO STORES 2345", statement_id=sep)
    gift = make_txn(conn, account, date(2026, 9, 16), 2_500, "TESCO STORES 2345", statement_id=sep)
    other = make_txn(conn, account, date(2026, 9, 17), 900, "CORNER SHOP", statement_id=sep)
    issuer = make_txn(conn, account, date(2026, 9, 18), 700, "NEWSAGENT", statement_id=sep)
    conn.execute(
        db.transactions.update()
        .where(db.transactions.c.id == issuer)
        .values(issuer_category="Other")
    )
    layers.set_category_rule(conn, "TESCO STORES", "groceries", reason="supermarket")
    layers.set_override(conn, gift, "category", "Gifts & charity", reason="a birthday present")

    by_id = {t.id: t.category for t in layers.load_statement(conn, sep).transactions}
    assert by_id == {tesco: "Groceries", gift: "Gifts & charity", other: None, issuer: "Other"}


def test_categories_must_exist(conn, card):
    account, _aug, sep = card
    txn = make_txn(conn, account, date(2026, 9, 15), 100, statement_id=sep)
    with pytest.raises(LayerError, match="no category 'Booze'"):
        layers.set_category_rule(conn, "TESCO STORES", "Booze", reason="r")
    with pytest.raises(LayerError, match="no category"):
        layers.set_override(conn, txn, "category", "Booze", reason="r")
    with pytest.raises(LayerError, match="means no category"):
        layers.add_category(conn, "uncategorised", "nope")
    layers.add_category(conn, "Booze", "Wine and beer")
    layers.set_category_rule(conn, "TESCO STORES", "booze", reason="it's the off-licence")
    assert layers.category_rules(conn) == {"TESCO STORES": "Booze"}


def test_removing_a_rule_uncategorises_and_replays(conn, card):
    account, _aug, sep = card
    make_txn(conn, account, date(2026, 9, 15), 6_000, "TESCO STORES 2345", statement_id=sep)
    layers.set_category_rule(conn, "TESCO STORES", "Groceries", reason="supermarket")
    time.sleep(0.002)
    before = layers.now()
    time.sleep(0.002)
    layers.remove_category_rule(conn, "TESCO STORES", reason="it's a petrol station")
    assert [t.category for t in layers.load_statement(conn, sep).transactions] == [None]
    then = layers.load_statement(conn, sep, as_of=before)
    assert [t.category for t in then.transactions] == ["Groceries"]


def test_category_seeds_never_override_yours(conn):
    layers.add_category(conn, "groceries", "my own description")
    with layers.SEED_CATEGORIES.open() as seed_file:
        seeds = sum(1 for _ in seed_file) - 1
    assert layers.load_category_seeds(conn) == seeds - 1
    assert layers.load_category_seeds(conn) == 0


def test_excluded_rows_stay_visible_but_out_of_the_figures(conn, card):
    account, _aug, sep = card
    kept = make_txn(conn, account, date(2026, 9, 20), 1_000, statement_id=sep)
    phantom = make_txn(conn, account, date(2026, 9, 20), 1_000, statement_id=sep)
    layers.exclude(conn, phantom, reason="pre-authorisation the issuer dropped")
    data = layers.load_statement(conn, sep)
    assert (ids(data.transactions), ids(data.excluded)) == ([kept], [phantom])
    assert [(c.kind, c.reason) for c in data.corrections] == [
        ("exclude", "pre-authorisation the issuer dropped")
    ]


def test_outputs_can_be_replayed_as_of_an_earlier_moment(conn, card):
    account, _aug, sep = card
    make_txn(conn, account, date(2026, 9, 16), 8_499, "AMZN MKTP UK*AB12CD34", statement_id=sep)
    time.sleep(0.002)
    before = layers.now()
    time.sleep(0.002)
    layers.add_alias(conn, "AMZN", "Amazon", reason="group Amazon")
    make_txn(conn, account, date(2026, 9, 17), 100, "LATER IMPORT", statement_id=sep)

    then = layers.load_statement(conn, sep, as_of=before)
    assert [t.merchant for t in then.transactions] == ["AMZN MKTP"]
    assert [t.merchant for t in layers.load_statement(conn, sep).transactions] == [
        "Amazon",
        "LATER IMPORT",
    ]


def test_history_is_earlier_statements_without_excluded_rows(conn, card):
    account, aug, sep = card
    early = make_txn(conn, account, date(2026, 8, 20), 1_000, statement_id=aug)
    dropped = make_txn(conn, account, date(2026, 8, 21), 1_000, statement_id=aug)
    loose = make_txn(conn, account, date(2026, 7, 1), 500)  # on no statement, dated earlier
    make_txn(conn, account, date(2026, 9, 13), 1_000, statement_id=sep)  # sep's own row
    layers.exclude(conn, dropped, reason="duplicate")
    history = layers.history_before(conn, layers.get_statement(conn, sep))
    assert ids(history) == [loose, early]


def test_previous_statement_goes_by_statement_date(conn, card):
    account, aug, sep = card
    jul = make_statement(conn, account, date(2026, 7, 14), date(2026, 8, 13))
    assert layers.previous_statement(conn, layers.get_statement(conn, sep)).id == aug
    assert layers.previous_statement(conn, layers.get_statement(conn, jul)) is None


def test_loading_a_statement_costs_the_same_queries_at_any_size(engine):
    def queries_to_load(count):
        with db.write(engine) as conn:
            account = make_account(conn, f"card-{count}")
            statement = make_statement(conn, account, *SEP)
            for day in range(count):
                txn = make_txn(
                    conn,
                    account,
                    SEP[0] + timedelta(days=day % 28),
                    100 + day,
                    statement_id=statement,
                )
                layers.set_override(conn, txn, "merchant", f"Shop {day}", reason="r")
                layers.mark(conn, txn, "recognised", reason="r", source="cli")
            executed = []
            listener = lambda *args: executed.append(args[2])  # noqa: E731
            event.listen(engine, "before_cursor_execute", listener)
            try:
                layers.load_statement(conn, statement)
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            return len(executed)

    assert queries_to_load(3) == queries_to_load(80)
