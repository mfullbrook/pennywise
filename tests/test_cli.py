"""The command line, end to end, against the synthetic Amex fixtures."""

import json
from pathlib import Path

import pytest

from pennywise import cli

FIXTURES = Path(__file__).parent / "fixtures" / "amex"


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PENNYWISE_HOME", str(tmp_path / "home"))

    def run(*argv, expect=0):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        assert code == expect, captured.err
        return captured.out, captured.err

    return run


@pytest.fixture
def imported(run):
    run("init")
    run("account", "add", "amex-test", "--issuer", "amex", "--name", "Test Amex")
    run("import", "amex-test", str(FIXTURES / "2026-08-24.csv"), str(FIXTURES / "2026-09-24.csv"))
    return run


def test_commands_need_an_initialised_database(run):
    _, err = run("accounts", expect=2)
    assert "run `pennywise init`" in err


def test_init_loads_the_seeds_once(run):
    out, _ = run("init")
    assert "Added 36 merchant aliases and 15 categories" in out
    out, _ = run("init")
    assert "Added 0 merchant aliases and 0 categories" in out


def test_import_then_reimport(imported):
    out, _ = imported("import", "amex-test", str(FIXTURES / "2026-09-24.csv"))
    assert "already imported" in out
    out, _ = imported("accounts")
    assert "amex-test" in out and "16" in out


def test_a_bad_file_is_reported_and_others_still_import(run, tmp_path):
    run("init")
    run("account", "add", "amex-test", "--issuer", "amex")
    bad = tmp_path / "2026-08-24.csv"
    bad.write_text("Date,Amount\n01/08/2026,1.00\n")
    out, err = run("import", "amex-test", str(bad), str(FIXTURES / "2026-09-24.csv"), expect=1)
    assert "unexpected columns" in err and "nothing imported" in err
    assert "2026-09-24.csv: statement" in out


def test_balances_and_reconcile(imported):
    out, _ = imported("statements")
    assert out.count("balances not entered") == 2
    out, _ = imported("balances", "1", "--opening", "1,200.00", "--closing", "569.43")
    assert "reconcile to the penny" in out
    out, _ = imported("balances", "2", "--opening", "569.43", "--closing", "1,690.05")
    assert "off by £0.01" in out
    out, _ = imported("reconcile", "1")
    assert "Balances reconcile." in out


def test_categorising_until_nothing_is_left(imported):
    out, _ = imported("uncategorised", "--json")
    merchants = [m["key"] for m in json.loads(out)["merchants"]]
    assert merchants[0] == "BRITISH AIRWAYS"  # biggest spend first
    assert "Tesco" in merchants and "Deliveroo" in merchants  # seeded aliases

    categories = {
        "BRITISH AIRWAYS": "Travel & holidays",
        "Tesco": "Groceries",
        "CURRYS": "Shopping",
        "SHELL": "Transport",
        "Deliveroo": "Takeaway & delivery",
        "Amazon": "Shopping",
        "Netflix": "Subscriptions",
        "PRET A MANGER": "Eating out",
    }
    for merchant, category in categories.items():
        imported("rule", "set", merchant, category, "--reason", "test")
    out, _ = imported("uncategorised")
    assert "Everything is categorised." in out

    report = json.loads(imported("report", "--json")[0])
    lines = {line["key"]: line["pence"] for line in report["lines"]}
    assert lines["Shopping"] == 34_900 + 2_399 - 4_900  # Currys refund nets off
    assert lines["Travel & holidays"] == 158_000
    assert report["uncategorised_pence"] == 0
    assert report["payments_pence"] == -(120_000 + 56_943)


def test_report_by_person_and_transactions_drilldown(imported):
    out, _ = imported("report", "--by", "person")
    assert "MR A EXAMPLE" in out and "MS B EXAMPLE" in out
    out, _ = imported("transactions", "--merchant", "tesco", "--json")
    rows = json.loads(out)
    assert len(rows) == 4 and {r["merchant"] for r in rows} == {"Tesco"}


def test_override_one_transaction(imported):
    imported("rule", "set", "Tesco", "Groceries", "--reason", "supermarket")
    tesco = json.loads(imported("transactions", "--merchant", "Tesco", "--json")[0])[0]["id"]
    imported("override", str(tesco), "--category", "Gifts & charity", "--reason", "present")
    rows = json.loads(imported("transactions", "--category", "Gifts & charity", "--json")[0])
    assert [r["id"] for r in rows] == [tesco]
    _, err = imported("override", str(tesco), "--category", "Nope", "--reason", "r", expect=1)
    assert "no category 'Nope'" in err


def test_rules_need_a_merchant_that_exists(imported):
    out, _ = imported("rule", "set", "tesco", "Groceries", "--reason", "case differs")
    assert out.strip() == "Tesco -> Groceries (4 transactions)"
    _, err = imported("rule", "set", "TESCOO", "Groceries", "--reason", "typo", expect=1)
    assert "no transactions at merchant 'TESCOO'; did you mean 'Tesco'?" in err
    out, _ = imported("rule", "set", "NEW SHOP", "Groceries", "--reason", "future", "--new")
    assert out.strip() == "NEW SHOP -> Groceries (0 transactions)"
    out, _ = imported("rule", "remove", "new shop", "--reason", "not needed")
    assert "Removed the rule for new shop" in out


def test_report_by_calendar_month(imported):
    out, _ = imported("report", "--months", "--json")
    data = json.loads(out)
    assert data["grouping"] == "month"
    labels = [c["label"] for c in data["columns"]]
    assert labels == sorted(labels) and all(len(label) == 7 for label in labels)
    out, _ = imported("transactions", "--json")
    first = json.loads(out)[0]
    assert first["account"] == "amex-test" and first["month"] == first["date"][:7]
