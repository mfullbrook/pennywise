import time
from datetime import date
from types import SimpleNamespace

import pytest
from builders import AUG, SEP, make_account, make_statement, make_txn
from sqlalchemy import func, select

from pennywise import db, layers
from pennywise.layers import ALIASES, ANNOTATIONS, CORRECTIONS, LayerError, NeedsCheckingGuard


@pytest.fixture
def ledger(conn):
    account = make_account(conn)
    statement = make_statement(conn, account, *SEP)
    txn = make_txn(conn, account, date(2026, 9, 20), 4_999, statement_id=statement)
    return SimpleNamespace(account=account, statement=statement, txn=txn)


def status(conn, txn_id):
    row = layers.current(conn, ANNOTATIONS, txn_id)
    return None if row is None else row.status


# --- The shared helper contract ----------------------------------------------------------


def test_latest_row_wins(conn, ledger):
    layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    layers.mark(conn, ledger.txn, "needs_checking", reason="looks high", source="mcp")
    assert status(conn, ledger.txn) == "needs_checking"


def test_revoking_the_latest_restores_the_previous_value(conn, ledger):
    layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    second = layers.mark(conn, ledger.txn, "needs_checking", reason="looks high", source="mcp")
    layers.revoke(conn, ANNOTATIONS, second, source="cli", reason="it was the weekly shop")
    assert status(conn, ledger.txn) == "recognised"


def test_revoking_an_older_row_leaves_the_latest(conn, ledger):
    first = layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    layers.mark(conn, ledger.txn, "needs_checking", reason="looks high", source="mcp")
    layers.revoke(conn, ANNOTATIONS, first, source="cli", reason="tidy up")
    assert status(conn, ledger.txn) == "needs_checking"


def test_revoking_every_row_empties_the_slot(conn, ledger):
    only = layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    layers.revoke(conn, ANNOTATIONS, only, source="cli", reason="wrong transaction")
    assert layers.current(conn, ANNOTATIONS, ledger.txn) is None


def test_a_row_can_be_revoked_once(conn, ledger):
    row = layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    layers.revoke(conn, ANNOTATIONS, row, source="cli", reason="first")
    with pytest.raises(LayerError, match="already revoked"):
        layers.revoke(conn, ANNOTATIONS, row, source="cli", reason="second")
    with pytest.raises(LayerError, match="no annotations row"):
        layers.revoke(conn, ANNOTATIONS, 999, source="cli", reason="missing")


def test_history_keeps_every_row_in_order_with_its_revocation(conn, ledger):
    first = layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    second = layers.mark(conn, ledger.txn, "needs_checking", reason="looks high", source="mcp")
    layers.revoke(conn, ANNOTATIONS, second, source="cli", reason="it was fine")
    entries = layers.history(conn, ANNOTATIONS, ledger.txn)
    assert [entry.row.id for entry in entries] == [first, second]
    assert entries[0].revoked_by is None
    assert entries[1].revoked_by.reason == "it was fine"
    assert entries[1].row.source == "mcp"


@pytest.mark.parametrize(
    ("source", "reason", "message"),
    [
        ("cli", "", "reason is required"),
        ("cli", "   ", "reason is required"),
        ("web", "ok", "source"),
    ],
)
def test_every_write_needs_provenance(conn, ledger, source, reason, message):
    with pytest.raises(LayerError, match=message):
        layers.mark(conn, ledger.txn, "recognised", reason=reason, source=source)


def test_current_all_returns_the_current_row_of_every_slot(conn, ledger):
    other = make_txn(conn, ledger.account, date(2026, 9, 21), 1_200)
    layers.mark(conn, ledger.txn, "recognised", reason="a", source="cli")
    layers.mark(conn, ledger.txn, "expected_one_off", reason="b", source="cli")
    layers.mark(conn, other, "needs_checking", reason="c", source="cli")
    current = layers.current_all(conn, ANNOTATIONS)
    assert {key: row.status for key, row in current.items()} == {
        (ledger.txn,): "expected_one_off",
        (other,): "needs_checking",
    }
    assert set(layers.current_all(conn, ANNOTATIONS, targets=[other])) == {(other,)}


def test_as_of_replays_rows_and_revocations(conn):
    layers.add_alias(conn, "AMZN", "Amazon", reason="mine")
    time.sleep(0.002)
    before = layers.now()
    time.sleep(0.002)
    layers.remove_alias(conn, "AMZN", reason="too broad")
    layers.add_alias(conn, "AMZN MKTP", "Amazon Marketplace", reason="narrower")
    assert [a.merchant for a in layers.alias_table(conn, as_of=before)] == ["Amazon"]
    assert [a.merchant for a in layers.alias_table(conn)] == ["Amazon Marketplace"]


# --- Corrections -------------------------------------------------------------------------


def test_exclude_then_revert(conn, ledger):
    correction = layers.exclude(conn, ledger.txn, reason="duplicate of the pending row")
    data = layers.load_statement(conn, ledger.statement)
    assert [t.id for t in data.excluded] == [ledger.txn]
    assert data.transactions == ()
    layers.revert(conn, correction, reason="it wasn't a duplicate")
    assert [t.id for t in layers.load_statement(conn, ledger.statement).transactions] == [
        ledger.txn
    ]


def test_needs_checking_charges_are_guarded(conn, ledger):
    aug = make_statement(conn, ledger.account, *AUG)
    layers.mark(conn, ledger.txn, "needs_checking", reason="don't recognise it", source="cli")
    with pytest.raises(NeedsCheckingGuard, match="--force"):
        layers.exclude(conn, ledger.txn, reason="phantom")
    with pytest.raises(NeedsCheckingGuard):
        layers.move_to_statement(conn, ledger.txn, aug, reason="posted late")
    assert conn.execute(select(func.count()).select_from(db.corrections)).scalar() == 0
    layers.exclude(conn, ledger.txn, reason="phantom, really", force=True)


def test_the_guard_only_applies_to_a_current_needs_checking_mark(conn, ledger):
    layers.mark(conn, ledger.txn, "needs_checking", reason="odd", source="cli")
    layers.mark(conn, ledger.txn, "unmarked", reason="wrong charge", source="cli")
    layers.exclude(conn, ledger.txn, reason="phantom")
    other = make_txn(conn, ledger.account, date(2026, 9, 22), 700)
    layers.mark(conn, other, "recognised", reason="coffee", source="cli")
    layers.exclude(conn, other, reason="phantom")


def test_moves_stay_within_an_account(conn, ledger):
    other_account = make_account(conn, "barclaycard")
    elsewhere = make_statement(conn, other_account, *SEP)
    with pytest.raises(LayerError, match="different accounts"):
        layers.move_to_statement(conn, ledger.txn, elsewhere, reason="wrong card")


def test_added_rows_count_until_their_correction_is_reverted(conn, ledger):
    manual = layers.add_row(
        conn,
        ledger.statement,
        txn_date=date(2026, 9, 30),
        amount_pence=1_250,
        kind="fee",
        description="LATE PAYMENT FEE",
        reason="on page 2 of the PDF, missing from the CSV",
    )
    data = layers.load_statement(conn, ledger.statement)
    assert manual in [t.id for t in data.transactions]
    assert [c.kind for c in data.corrections] == ["add_row"]
    correction = layers.current(conn, CORRECTIONS, manual, ("add_row",))
    layers.revert(conn, correction.id, reason="typed the wrong amount")
    data = layers.load_statement(conn, ledger.statement)
    assert manual not in [t.id for t in data.transactions]
    assert manual in [t.id for t in data.excluded]


def test_added_rows_must_fall_inside_the_statement(conn, ledger):
    with pytest.raises(LayerError, match="outside"):
        layers.add_row(
            conn,
            ledger.statement,
            txn_date=date(2026, 8, 1),
            amount_pence=100,
            kind="purchase",
            description="X",
            reason="r",
        )


def test_add_row_checks_the_reason_before_writing_anything(conn, ledger):
    with pytest.raises(LayerError, match="reason"):
        layers.add_row(
            conn,
            ledger.statement,
            txn_date=date(2026, 9, 30),
            amount_pence=100,
            kind="purchase",
            description="X",
            reason="",
        )
    assert conn.execute(select(func.count()).select_from(db.transactions)).scalar() == 1


# --- Overrides and marks -----------------------------------------------------------------


def test_override_values_are_validated(conn, ledger):
    with pytest.raises(LayerError, match="unknown kind"):
        layers.set_override(conn, ledger.txn, "kind", "gift", reason="r")
    with pytest.raises(LayerError, match="field must be"):
        layers.set_override(conn, ledger.txn, "amount", "1", reason="r")
    layers.set_override(conn, ledger.txn, "kind", "refund", reason="it was a refund")


def test_unmarked_clears_a_mark(conn, ledger):
    layers.mark(conn, ledger.txn, "recognised", reason="weekly shop", source="cli")
    assert layers.mark_status(conn, ledger.txn) == "recognised"
    layers.mark(conn, ledger.txn, "unmarked", reason="marked the wrong one", source="mcp")
    assert layers.mark_status(conn, ledger.txn) is None


def test_unknown_mark_status_is_refused(conn, ledger):
    with pytest.raises(LayerError, match="status must be"):
        layers.mark(conn, ledger.txn, "fine", reason="r", source="cli")


# --- Aliases -----------------------------------------------------------------------------


def test_prefix_patterns_are_stored_in_matching_form(conn):
    layers.add_alias(conn, "  amzn   mktp ", "Amazon", reason="mine")
    assert layers.alias_table(conn)[0].pattern == "AMZN MKTP"


def test_invalid_regex_is_refused(conn):
    with pytest.raises(LayerError, match="invalid regex"):
        layers.add_alias(conn, "(", "Broken", match_type="regex", reason="r")


def test_removing_an_alias(conn):
    layers.add_alias(conn, "TESCO", "Tesco", reason="mine")
    layers.remove_alias(conn, "tesco", reason="not needed")
    assert layers.alias_table(conn) == ()
    with pytest.raises(LayerError, match="no current"):
        layers.remove_alias(conn, "TESCO", reason="again")


def test_seed_aliases_never_come_back_once_removed(conn):
    layers.load_alias_seeds(conn)
    layers.remove_alias(conn, "TESCO", reason="I want Tesco descriptors kept apart")
    assert layers.load_alias_seeds(conn) == 0
    assert "TESCO" not in {a.pattern for a in layers.alias_table(conn)}


def test_seed_aliases_never_override_yours(conn):
    layers.add_alias(conn, "TESCO", "Tesco Groceries", reason="my name for it")
    with layers.SEED_ALIASES.open() as seed_file:
        seeds = sum(1 for _ in seed_file) - 1
    assert layers.load_alias_seeds(conn) == seeds - 1
    assert layers.load_alias_seeds(conn) == 0
    tesco = layers.current(conn, ALIASES, "TESCO", ("prefix",))
    assert (tesco.merchant, tesco.source) == ("Tesco Groceries", "cli")
