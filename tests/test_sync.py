"""Endute Connect: the client's paging, statement periods, linking and sync, against a fake
Endute that serves rows shaped like the published OpenAPI contract."""

import io
import json
import subprocess
import urllib.error
import urllib.request
from datetime import date

import pytest
from sqlalchemy import select

from pennywise import cli, db, endute, importer, layers, sync
from pennywise.endute import Client, EnduteError, _cursor
from pennywise.importer import LedgerError

CARD = {
    "id": "3f2a9c1e-0000-4000-8000-000000000001",
    "institution": "Barclaycard",
    "name": "Barclaycard Platinum",
    "masked_identifier": "1234",
    "account_type": "credit_card",
    "currency": "GBP",
    "status": "enabled",
    "sandbox": True,
    "last_synced_at": "2026-10-12T08:00:00Z",
    "oldest_booking_date": "2026-07-14",
    "available_history_days": 730,
    "consent_renewal_due": "2027-01-10T00:00:00Z",
}


def row(n, booked, amount, description="TESCO STORES 2345", **extra):
    return {
        "id": f"00000000-0000-4000-8000-{n:012d}",
        "booking_date": booked,
        "value_date": booked,
        "amount": amount,
        "currency": "GBP",
        "description": description,
        "counterparty": None,
        "enrichment": {
            "merchant_name": None,
            "category": None,
            "brand_domain": None,
            "confidence": None,
            "source": None,
        },
        "sandbox": True,
        "upstream_transaction_id": None,
        "merchant_category_code": None,
        "bank_transaction_code": None,
        "proprietary_bank_transaction_code": None,
        "end_to_end_id": None,
        "mandate_id": None,
        "entry_reference": None,
        **extra,
    }


class FakeEndute:
    """Serves `rows` newest first, `page_size` per page, honouring from/to/cursor the way
    the API does. Records every request."""

    def __init__(self, rows=(), accounts=(CARD,), page_size=2):
        self.rows, self.accounts, self.page_size = list(rows), list(accounts), page_size
        self.requests = []
        self.fail = None

    def __call__(self, path, params):
        self.requests.append((path, dict(params)))
        if self.fail:
            raise EnduteError(self.fail)
        if path == "/accounts":
            return self.accounts
        assert path == f"/accounts/{CARD['id']}/transactions"
        rows = sorted(self.rows, key=lambda r: r["booking_date"], reverse=True)
        if "from" in params:
            rows = [r for r in rows if r["booking_date"] >= params["from"]]
        offset = int(params.get("cursor", 0))
        page = rows[offset : offset + self.page_size]
        more = offset + self.page_size < len(rows)
        next_url = f"https://api.endute.com/v1{path}?cursor={offset + self.page_size}"
        return {"next": next_url if more else None, "previous": None, "results": page}


class FakeIgnoringDates(FakeEndute):
    """A bank feed that ignores the date filter."""

    def __call__(self, path, params):
        return super().__call__(path, {k: v for k, v in params.items() if k != "from"})


# --- Client -------------------------------------------------------------------------------


def test_transactions_follow_the_cursor_to_the_last_page():
    fake = FakeEndute([row(i, f"2026-09-{i:02d}", "-1.00") for i in range(1, 6)])
    got = list(Client(fake).transactions(CARD["id"], since=date(2026, 9, 2)))
    assert [r["booking_date"] for r in got] == [f"2026-09-0{i}" for i in (5, 4, 3, 2)]
    assert [p.get("cursor") for _, p in fake.requests] == [None, "2"]
    assert all(p["from"] == "2026-09-02" for _, p in fake.requests)


def test_only_the_cursor_is_taken_from_a_next_link():
    # The key goes to the configured host whatever the link says.
    assert _cursor("https://evil.example/steal?cursor=abc&x=1") == "abc"
    assert _cursor(None) is None
    with pytest.raises(EnduteError):
        _cursor("https://api.endute.com/v1/accounts/x/transactions?page=2")


def test_account_ids_are_checked_before_going_into_a_path():
    with pytest.raises(EnduteError):
        list(Client(FakeEndute()).transactions("../connections"))


# --- Statement periods --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("booked", "day", "start", "end"),
    [
        (date(2026, 10, 3), 24, date(2026, 9, 25), date(2026, 10, 24)),
        (date(2026, 10, 24), 24, date(2026, 9, 25), date(2026, 10, 24)),
        (date(2026, 10, 25), 24, date(2026, 10, 25), date(2026, 11, 24)),
        (date(2026, 12, 30), 24, date(2026, 12, 25), date(2027, 1, 24)),
        # A 31st statement day closes on the last day of shorter months.
        (date(2026, 2, 10), 31, date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), 31, date(2026, 3, 1), date(2026, 3, 31)),
    ],
)
def test_period_for_a_booking_date(booked, day, start, end):
    assert sync.period_end(booked, day) == end
    assert sync.period_start(end, day) == start


# --- Linking ------------------------------------------------------------------------------


def test_linking_creates_the_account_with_its_statement_day(conn):
    account_id = sync.link_account(conn, CARD, "barclaycard", statement_day=3)
    account = importer.account_row(conn, "barclaycard")
    assert (account.id, account.adapter, account.default_close_day) == (account_id, "endute", 3)
    assert account.display_name == "Barclaycard Platinum"
    with pytest.raises(LedgerError, match="already linked"):
        sync.link_account(conn, CARD, "other", statement_day=3)


def test_a_new_account_needs_a_statement_day(conn):
    with pytest.raises(LedgerError, match="statement day"):
        sync.link_account(conn, CARD, "barclaycard")


def test_only_gbp_cards_and_spending_accounts_link(conn):
    with pytest.raises(LedgerError, match="cards and spending accounts"):
        sync.link_account(conn, {**CARD, "account_type": "savings"}, "x", statement_day=1)
    with pytest.raises(LedgerError, match="GBP"):
        sync.link_account(conn, {**CARD, "currency": "EUR"}, "x", statement_day=1)


def _csv_account(conn):
    account_id = importer.add_account(
        conn, "amex", issuer="amex", adapter="amex", display_name="Amex", default_close_day=24
    )
    statement = importer.add_statement(
        conn, account_id, start=date(2026, 8, 25), end=date(2026, 9, 24)
    )
    # Pretend its CSV was imported.
    file_id = conn.execute(
        db.source_files.insert().values(
            account_id=account_id,
            sha256="x",
            filename="2026-09-24.csv",
            file_type="csv",
            row_count=0,
            imported_at="x",
        )
    ).inserted_primary_key[0]
    conn.execute(
        db.statements.update().where(db.statements.c.id == statement).values(csv_file_id=file_id)
    )
    return account_id


def test_a_csv_account_cuts_over_after_its_latest_statement(conn):
    _csv_account(conn)
    sync.link_account(conn, CARD, "amex")
    assert sync.linked_accounts(conn)[0].cutover_date == date(2026, 9, 25)


def test_a_cutover_inside_the_csv_history_is_refused(conn):
    _csv_account(conn)
    with pytest.raises(LedgerError, match="after it"):
        sync.link_account(conn, CARD, "amex", cutover=date(2026, 9, 1))


# --- Syncing ------------------------------------------------------------------------------


@pytest.fixture
def linked(engine):
    with db.write(engine) as conn:
        layers.load_category_seeds(conn)
        sync.link_account(conn, CARD, "barclaycard", statement_day=24)
    return engine


def _txns(engine):
    with db.read(engine) as conn:
        return list(conn.execute(select(db.transactions).order_by(db.transactions.c.date)))


def test_first_sync_takes_all_history_into_statement_periods(linked):
    fake = FakeEndute(
        [
            row(1, "2026-09-20", "-12.50"),
            row(2, "2026-09-26", "-30.00", "SHELL #1234"),
            row(3, "2026-10-02", "200.00", "PAYMENT RECEIVED - THANK YOU"),
            row(4, "2026-10-03", "4.99", "AMAZON REFUND"),
        ]
    )
    [result] = sync.sync(linked, Client(fake))
    assert result.window_from is None and "from" not in fake.requests[0][1]
    assert (result.fetched, result.inserted, result.statements_created) == (4, 4, 2)

    txns = _txns(linked)
    assert [(t.amount_pence, t.kind) for t in txns] == [
        (1250, "purchase"),  # money out on a card is a charge
        (3000, "purchase"),
        (-20000, "payment"),
        (-499, "refund"),
    ]
    assert {t.source for t in txns} == {"endute"} and {t.date_basis for t in txns} == {"posting"}
    with db.read(linked) as conn:
        statements = list(conn.execute(select(db.statements).order_by(db.statements.c.end_date)))
        assert [(s.start_date, s.end_date) for s in statements] == [
            (date(2026, 8, 25), date(2026, 9, 24)),
            (date(2026, 9, 25), date(2026, 10, 24)),
        ]
        by_statement = {s.id: [t.id for t in txns if t.statement_id == s.id] for s in statements}
        assert [len(ids) for ids in by_statement.values()] == [1, 3]
        # The engine sees them like any other statement.
        data = layers.load_statement(conn, statements[1].id)
        assert [t.merchant for t in data.transactions][0] == "SHELL"
        raw = json.loads(conn.execute(select(db.endute_rows.c.raw)).first().raw)
        assert raw["description"] == "TESCO STORES 2345"  # the original, verbatim


def test_resyncing_overlaps_the_window_and_stores_nothing_twice(linked):
    fake = FakeEndute([row(1, "2026-09-20", "-12.50"), row(2, "2026-10-01", "-3.00")])
    sync.sync(linked, Client(fake))
    fake.rows.append(row(3, "2026-10-05", "-7.00"))
    fake.rows.append(row(1, "2026-09-20", "-12.50"))  # Endute serving a duplicate
    [result] = sync.sync(linked, Client(fake))
    assert result.window_from == date(2026, 9, 17)  # latest held (1 Oct) minus 14 days
    assert fake.requests[-1][1]["from"] == "2026-09-17"
    assert (result.fetched, result.inserted, result.unchanged) == (3, 1, 2)
    assert len(_txns(linked)) == 3


def test_a_row_that_comes_back_different_keeps_its_original(linked):
    fake = FakeEndute([row(1, "2026-10-01", "-12.50")])
    sync.sync(linked, Client(fake))
    fake.rows = [row(1, "2026-10-01", "-13.50")]
    [result] = sync.sync(linked, Client(fake))
    assert (result.changed, result.inserted) == (1, 0)
    assert [t.amount_pence for t in _txns(linked)] == [1250]


def test_enrichment_changes_dont_count_as_changes(linked):
    fake = FakeEndute([row(1, "2026-10-01", "-12.50")])
    sync.sync(linked, Client(fake))
    enriched = row(1, "2026-10-01", "-12.50")
    enriched["enrichment"] = {**enriched["enrichment"], "merchant_name": "Tesco"}
    fake.rows = [enriched]
    [result] = sync.sync(linked, Client(fake))
    assert (result.unchanged, result.changed) == (1, 0)


def test_rows_before_the_cutover_are_skipped(engine):
    with db.write(engine) as conn:
        _csv_account(conn)
        sync.link_account(conn, CARD, "amex")
    fake = FakeEndute([row(1, "2026-09-20", "-12.50"), row(2, "2026-09-25", "-3.00")])
    [result] = sync.sync(engine, Client(fake))
    assert fake.requests[0][1]["from"] == "2026-09-25"
    # The fake honours `from`, so only the later row arrives; a bank that ignored it would
    # still be filtered.
    assert (result.inserted, result.before_cutover) == (1, 0)
    [result] = sync.sync(engine, Client(FakeIgnoringDates([row(1, "2026-09-20", "-12.50")])))
    assert (result.inserted, result.before_cutover) == (0, 1)
    [txn] = [t for t in _txns(engine) if t.source == "endute"]
    with db.read(engine) as conn:
        statement = layers.get_statement(conn, txn.statement_id)
    assert (statement.start, statement.end) == (date(2026, 9, 25), date(2026, 10, 24))


def test_a_failed_sync_writes_nothing_and_records_the_error(linked):
    fake = FakeEndute(
        [row(1, "2026-10-01", "-12.50"), row(2, "2026-10-02", "-1.00", currency="USD")]
    )
    [result] = sync.sync(linked, Client(fake))
    assert "USD" in result.error
    assert _txns(linked) == []
    with db.read(linked) as conn:
        [run] = sync.last_runs(conn).values()
    assert (run.status, run.error) == ("error", result.error)

    fake.fail = "Endute returned 503"
    [result] = sync.sync(linked, Client(fake))
    assert result.error == "Endute returned 503"


def test_rules_apply_to_endute_rows_like_csv_rows(linked):
    sync.sync(linked, Client(FakeEndute([row(1, "2026-10-01", "-12.50", "SHELL #1234")])))
    with db.write(linked) as conn:
        layers.set_category_rule(conn, "SHELL", "Transport", reason="fuel")
        [txn] = _txns(linked)
        [data] = [layers.load_statement(conn, txn.statement_id)]
    assert data.transactions[0].category == "Transport"


AMEX_CSV = "Date,Description,Card Member,Account #,Amount\n"


def _amex_endute(engine):
    amex = {**CARD, "institution": "American Express"}
    with db.write(engine) as conn:
        sync.link_account(conn, amex, "amex-card", statement_day=19)
    sync.sync(engine, Client(FakeEndute([row(1, "2026-07-20", "-12.50")])))


def test_older_csv_statements_go_into_an_endute_account(engine, tmp_path):
    _amex_endute(engine)
    june, july = tmp_path / "2026-06-19.csv", tmp_path / "2026-07-19.csv"
    june.write_text(AMEX_CSV + "01/06/2026,SHOP,A,1,5.00\n")
    july.write_text(AMEX_CSV + "01/07/2026,SHOP,A,1,7.00\n")
    with db.write(engine) as conn:
        importer.import_statement_csv(conn, "amex-card", june)  # format from the bank
        importer.import_statement_csv(conn, "amex-card", july)
        [link] = sync.linked_accounts(conn)
    assert link.cutover_date == date(2026, 7, 20)
    assert _periods(engine)[:2] == [
        (date(2026, 6, 1), date(2026, 6, 19)),
        (date(2026, 6, 20), date(2026, 7, 19)),
    ]
    # Later syncs still add to the Endute periods only.
    [result] = sync.sync(engine, Client(FakeEndute([row(1, "2026-07-20", "-12.50")])))
    assert (result.inserted, result.unchanged) == (0, 1)


def test_a_csv_overlapping_endute_rows_is_refused(engine, tmp_path):
    _amex_endute(engine)
    august = tmp_path / "2026-08-19.csv"
    august.write_text(AMEX_CSV + "01/08/2026,SHOP,A,1,5.00\n")
    with db.write(engine) as conn, pytest.raises(LedgerError, match="before 2026-07-20"):
        importer.import_statement_csv(conn, "amex-card", august)


def test_an_endute_bank_without_a_known_csv_format_needs_one(linked, tmp_path):
    csv = tmp_path / "2026-06-24.csv"
    csv.write_text(AMEX_CSV + "01/06/2026,X,A,1,1.00\n")
    with db.write(linked) as conn, pytest.raises(LedgerError, match="--format"):
        importer.import_statement_csv(conn, "barclaycard", csv)


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PENNYWISE_HOME", str(tmp_path / "home"))
    fake = FakeEndute([row(1, "2026-09-20", "-12.50"), row(2, "2026-10-01", "-3.00")])
    monkeypatch.setattr(Client, "connect", classmethod(lambda cls, *a, **k: cls(fake)))

    def run(*argv, expect=0):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        assert code == expect, captured.err
        return captured.out, captured.err

    run.fake = fake
    return run


def test_cli_link_sync_status(run):
    run("init")
    out, _ = run("endute", "accounts")
    assert "Barclaycard Platinum ...1234" in out and "3f2a9c1e" in out
    out, _ = run("endute", "link", "3f2a", "barclaycard", "--statement-day", "24")
    assert "Linked Barclaycard Platinum to barclaycard (statement day 24)" in out
    out, _ = run("endute", "sync")
    assert "barclaycard: 2 fetched (all history): 2 new, 0 already held, 2 new statement" in out
    out, _ = run("endute", "status")
    assert "barclaycard" in out and "2026-10-01" in out and "ok" in out
    out, _ = run("statements")
    assert "2026-09-24 " in out and "2026-10-24" in out
    run.fake.fail = "Endute returned 503"
    _, err = run("endute", "sync", expect=1)
    assert "barclaycard: Endute returned 503" in err


def test_cli_link_needs_a_unique_id(run):
    run("init")
    _, err = run("endute", "link", "zz", "x", "--statement-day", "1", expect=1)
    assert "no Endute account id starts with 'zz'" in err


# --- HTTP ---------------------------------------------------------------------------------


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b""):
    url = "https://api.endute.com/v1/accounts"
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))


def _serve(monkeypatch, *outcomes):
    """urlopen returns or raises each outcome in turn; returns the requests made."""
    requests, queue = [], list(outcomes)

    def urlopen(request, timeout):
        requests.append(request)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(endute.time, "sleep", lambda seconds: None)
    return requests


def test_requests_carry_the_key_only_in_the_header(monkeypatch):
    requests = _serve(monkeypatch, b"[]")
    assert Client.connect("edk_secret").accounts() == []
    [request] = requests
    assert request.get_header("Authorization") == "Bearer edk_secret"
    assert request.full_url == "https://api.endute.com/v1/accounts"


def test_http_errors_are_explained_without_the_key(monkeypatch):
    body = b'{"error": {"code": "consent_expired", "message": "Renew access"}}'
    _serve(monkeypatch, _http_error(403, body))
    with pytest.raises(EnduteError) as caught:
        Client.connect("edk_secret").accounts()
    assert str(caught.value) == "Endute returned 403 (consent_expired: Renew access)"

    _serve(monkeypatch, _http_error(401))
    with pytest.raises(EnduteError, match="rejected the API key"):
        Client.connect("edk_secret").accounts()


def test_busy_and_server_errors_are_retried(monkeypatch):
    requests = _serve(monkeypatch, _http_error(503), _http_error(429), b"[]")
    assert Client.connect("edk_secret").accounts() == []
    assert len(requests) == 3


def test_the_key_can_come_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv(endute.KEY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('OTHER=1\nexport ENDUTE_API_KEY="edk_fromfile"\n')
    assert endute.api_key() == "edk_fromfile"
    assert "OTHER" not in __import__("os").environ


def test_a_missing_key_says_how_to_store_one(tmp_path, monkeypatch):
    monkeypatch.delenv(endute.KEY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    failed = subprocess.CompletedProcess([], 44, "", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: failed)
    with pytest.raises(EnduteError, match="pennywise endute key"):
        endute.api_key()


@pytest.mark.parametrize(
    ("institution", "description", "expected"),
    [
        ("Barclays Personal", "Budgens, Exampleton, 12.34 null UNITED KINGDOM", "Budgens"),
        ("Barclaycard UK", "Mcdonalds 1510, Sampleford", "Mcdonalds 1510"),
        ("Barclays Personal", "Payment By Direct Debit", "Payment By Direct Debit"),
        ("American Express", "SMITH, J & SONS LONDON", "SMITH, J & SONS LONDON"),
        ("American Express", "", "(no description)"),
        (
            "Tesco Bank",
            "TESCO GROCERY EXAMPLETON ENG, Transaction Date: 2026-07-27",
            "TESCO GROCERY EXAMPLETON ENG",
        ),
    ],
)
def test_descriptors_by_bank(institution, description, expected):
    assert sync.descriptor({"description": description}, institution) == expected


def test_barclays_rows_group_by_merchant_across_towns(engine):
    card = {**CARD, "institution": "Barclays Personal"}
    with db.write(engine) as conn:
        sync.link_account(conn, card, "barclaycard", statement_day=24)
    rows = [
        row(1, "2026-10-01", "-5.00", "Mcdonalds 1510, Sampleford"),
        row(2, "2026-10-02", "-6.00", "Mcdonalds 1510, Exampleton, 6.00 null UNITED KINGDOM"),
    ]
    sync.sync(engine, Client(FakeEndute(rows)))
    with db.read(engine) as conn:
        [statement_id] = {t.statement_id for t in _txns(engine)}
        data = layers.load_statement(conn, statement_id)
        raw = [json.loads(r.raw)["description"] for r in conn.execute(select(db.endute_rows))]
    assert {t.merchant for t in data.transactions} == {"MCDONALDS"}
    assert "Mcdonalds 1510, Sampleford" in raw


# --- Known statement dates ----------------------------------------------------------------


def _periods(engine):
    with db.read(engine) as conn:
        rows = conn.execute(select(db.statements).order_by(db.statements.c.end_date))
        return [(s.start_date, s.end_date) for s in rows]


def test_periods_follow_known_statement_dates_then_the_usual_day(linked):
    rows = [
        row(1, "2026-08-27", "-1.00"),
        row(2, "2026-08-28", "-1.00"),
        row(3, "2026-09-24", "-1.00"),
        row(4, "2026-09-30", "-1.00"),
    ]
    with db.write(linked) as conn:
        sync.add_statement_dates(conn, 1, [date(2026, 8, 26), date(2026, 9, 25)])
    sync.sync(linked, Client(FakeEndute(rows)))
    assert _periods(linked) == [
        (date(2026, 8, 27), date(2026, 9, 25)),
        (date(2026, 9, 26), date(2026, 10, 24)),  # estimated from the usual day (24)
    ]


def test_adding_a_date_rebuilds_the_periods(linked):
    rows = [row(1, "2026-09-24", "-1.00"), row(2, "2026-09-26", "-2.00")]
    sync.sync(linked, Client(FakeEndute(rows)))
    assert _periods(linked) == [
        (date(2026, 8, 25), date(2026, 9, 24)),
        (date(2026, 9, 25), date(2026, 10, 24)),
    ]
    with db.write(linked) as conn:
        assert sync.add_statement_dates(conn, 1, [date(2026, 9, 26)]) == 1
        assert sync.add_statement_dates(conn, 1, [date(2026, 9, 26)]) == 0
    assert _periods(linked) == [(date(2026, 8, 27), date(2026, 9, 26))]
    assert len({t.statement_id for t in _txns(linked)}) == 1


def test_periods_with_balances_are_not_rebuilt(linked):
    sync.sync(linked, Client(FakeEndute([row(1, "2026-09-24", "-1.00")])))
    with db.write(linked) as conn:
        [txn] = _txns(linked)
        importer.set_balances(conn, txn.statement_id, opening_pence=0, closing_pence=100)
    with db.write(linked) as conn, pytest.raises(LedgerError, match="balances entered"):
        sync.add_statement_dates(conn, 1, [date(2026, 9, 26)])


# --- Spending accounts (Revolut) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "description", "amount", "kind"),
    [
        ("CARD_PAYMENT", "Openai *chatgpt Subscr", "-20.00", "purchase"),
        ("CARD_PAYMENT", "Anthropic", "18.00", "refund"),
        ("CARD_REFUND", "Shop", "5.00", "refund"),
        ("REV_PAYMENT", "LYCAMOBILE UK LIMITED*London", "-10.00", "purchase"),
        ("TRANSFER", "To Alex Example Birthday", "-50.00", "purchase"),
        ("TRANSFER", "From Sam P", "25.00", "refund"),
        ("TRANSFER", "Junior account withdrawal (Jo Example)", "15.00", "payment"),
        ("TRANSFER", "Balance migration to another region or legal entity", "100.00", "adjustment"),
        (
            "TRANSFER",
            "Balance migration to another region or legal entity",
            "-100.00",
            "adjustment",
        ),
        ("TOPUP", "OBA topup from EXAMPLE A+B OBA0REF0000000", "200.00", "payment"),
        ("TOPUP", "Payment from Sam Example DINNER", "30.00", "refund"),
        ("ATM", "Cash at Sampleford 2", "-40.00", "cash_advance"),
        ("CHARGE", "Card Delivery Fee", "-5.00", "fee"),
        ("FEE", "Something", "-1.00", "fee"),
        (None, "PAYMENT RECEIVED - THANK YOU", "100.00", "payment"),  # no code: wording rules
    ],
)
def test_kinds_from_bank_codes(code, description, amount, kind):
    r = row(1, "2026-10-01", amount, description, proprietary_bank_transaction_code=code)
    assert sync.kind_of(r, description, sync._pence(r)).value == kind


def test_transfers_are_named_after_the_other_party():
    def revolut(amount, description, party, code):
        return row(
            1,
            "2026-10-01",
            amount,
            description,
            counterparty=party,
            proprietary_bank_transaction_code=code,
        )

    out = revolut("-50.00", "To Alex Example Birthday", "ALEX EXAMPLE", "TRANSFER")
    paid = revolut("30.00", "Payment from Sam Example DINNER", "SAM EXAMPLE", "TOPUP")
    card = revolut("-3.99", "Endute", "Endute", "CARD_PAYMENT")
    assert sync.descriptor(out, "Revolut") == "To ALEX EXAMPLE"
    assert sync.descriptor(paid, "Revolut") == "From SAM EXAMPLE"
    assert sync.descriptor(card, "Revolut") == "Endute"


def test_a_spending_account_groups_by_calendar_month(engine):
    current = {**CARD, "institution": "Revolut", "account_type": "current"}
    with db.write(engine) as conn:
        sync.link_account(conn, current, "revolut", statement_day=31)
    rows = [
        row(1, "2026-02-01", "-1.00"),
        row(2, "2026-02-28", "-1.00"),
        row(3, "2026-03-31", "-1.00"),
    ]
    sync.sync(engine, Client(FakeEndute(rows)))
    assert _periods(engine) == [
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 31)),
    ]
