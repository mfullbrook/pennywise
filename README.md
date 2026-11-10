# Pennywise

[![CI](https://github.com/mfullbrook/pennywise/actions/workflows/ci.yml/badge.svg)](https://github.com/mfullbrook/pennywise/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Pennywise is a local-first tool for understanding UK credit card spending. It keeps your
card statements in a SQLite ledger on your own machine, reconciles each statement to the
penny, groups messy descriptors into merchants, and reports where the money went by
category, merchant, card member or month. Every figure comes from a deterministic engine
working in integer pence, so the answers are the same for a person at the command line
and for an AI agent driving it: the agent asks the questions, pennywise does the sums.

**Status:** early and actively developed (version 0.1.0). The ledger, importers, reports,
Endute Connect sync and the agent skill work today; interfaces may still change. See
[CHANGELOG.md](CHANGELOG.md) and [TODOS.md](TODOS.md).

## Features

- **Statement import** for American Express (CSV statements and date-range batch
  exports), Halifax (CSV and PDF) and Tesco Bank (PDF). A PDF imports only if its rows
  take the printed previous balance to the new balance exactly. Importing the same file
  twice does nothing; a file that doesn't parse imports nothing and names the line at
  fault.
- **Card sync through [Endute Connect](https://endute.com/connect)**: read-only, safe to
  repeat, grouped into statement periods by statement day, known statement dates or
  calendar month.
- **Reconciliation**: enter balances from the statement and check the transactions add
  up.
- **Merchants and categories**: merchant normalisation with bundled aliases, suggested
  aliases for town-suffix variants, 15 starter categories, merchant rules and
  per-transaction overrides (precedence: override > rule > issuer category).
- **Reports** by category, merchant or card member, per statement, per calendar month
  and in total, with uncategorised merchants ranked by spend. `--json` output for
  agents and scripts.
- **Append-only history**: corrections, overrides, aliases and review marks are recorded
  with a reason and can be revoked without editing the original ledger rows.
- **Engine extras**: balance and spending comparisons between statements, and
  deterministic anomaly flags that state their baselines.
- **Offline dashboard**: a self-contained HTML snapshot with charts, filters and
  searchable transactions.
- **Agent skill** that teaches Claude Code, Codex and similar agents to drive the CLI
  without doing arithmetic themselves.

## Privacy model

- **Your data stays local.** The database and your statements live in `~/.pennywise`
  (or `PENNYWISE_HOME`). The directory is created owner-only (`0700`) and the database
  `0600`. Nothing is uploaded anywhere; the only network calls are the Endute sync
  commands you run yourself.
- **Nothing private is committed.** `.gitignore` excludes CSVs, PDFs, SQLite files,
  `.env` and the dashboard export. The only CSVs in the repository are synthetic test
  fixtures and the bundled merchant seeds.
- **Secrets stay out of the repository.** The Endute API key is kept in the macOS
  keychain, or read from `ENDUTE_API_KEY` or a git-ignored `.env`.
- **The LLM never does arithmetic.** Money is integer pence and every total, share and
  difference is computed by the engine. The agent skill tells agents to quote pennywise
  output rather than add up figures themselves, and to propose changes for you to
  approve.

## Requirements

- Python **3.12 or newer**.
- [`uv`](https://docs.astral.sh/uv/) for dependency management and running commands.

SQLite is used locally; no separate database service is needed. API credentials are
only needed for Endute Connect sync.

## Install

```bash
git clone https://github.com/mfullbrook/pennywise.git
cd pennywise
uv sync --locked
```

This creates `.venv` and installs the project with its development dependencies from
`uv.lock`, including pytest, Hypothesis and Ruff. Check the installation:

```bash
uv run pennywise --help
uv run pytest
```

## Quick start

Statement exports live outside the repository, for example in
`~/.pennywise/statements/<account>/`, one file per statement named by its statement
date (`2026-09-24.csv`).

```bash
uv run pennywise init                      # create or upgrade the database, load starter seeds
uv run pennywise account add amex-joint --issuer amex --name "Amex (joint account)"
uv run pennywise import amex-joint ~/.pennywise/statements/amex-joint/*.csv
uv run pennywise statements                # statement ids, periods, and whether they reconcile
uv run pennywise balances 6 --opening 1234.56 --closing 2468.10   # from the statement PDF
uv run pennywise reconcile 6
```

Other issuers take `--issuer halifax` or `--issuer tesco`. A date-range export covering
several statements imports with `import --batch`, split by the account's statement day
(`account add --statement-day 24`).

Then see where the money goes, and categorise what isn't yet:

```bash
uv run pennywise report                    # spending by category, per statement and in total
uv run pennywise report --by person        # or --by merchant; --account / --statement to narrow
uv run pennywise report --months           # calendar months instead of statements
uv run pennywise uncategorised             # merchants with uncategorised spending, biggest first
uv run pennywise rule set "TESCO STORES" Groceries --reason "supermarket"
uv run pennywise alias suggest             # descriptor variants of merchants you've categorised
uv run pennywise transactions --merchant "Tesco"   # the transactions behind a figure
uv run pennywise override 123 --category "Gifts & charity" --reason "birthday present"
uv run pennywise categories                # the category list; add your own with `category add`
```

Every command takes `--help`, and `report`, `uncategorised` and `transactions` take
`--json`.

### Connecting cards through Endute Connect

[Endute Connect](https://endute.com/connect) reads card transactions from the bank.
Connect the cards in Endute's portal and create an API key, then:

```bash
uv run pennywise endute key          # store the key in the macOS keychain (prompts for it)
uv run pennywise endute accounts     # the cards Endute can see
uv run pennywise endute link 3f2a amex-main --statement-day 24 --name "Amex (main card)"
uv run pennywise endute sync         # safe to repeat; fetches only what's new
uv run pennywise endute status
```

Endute has no statements, so a linked card's transactions are grouped into periods
ending on its statement day. For a card whose day varies, record the real dates with
`pennywise endute statement-dates SLUG 2026-08-26 2026-09-24 ...`. A spending current
account links with `--statement-day 31`, which groups it by calendar month. A card that
already has CSV statements is cut over the day after the latest one, so the two sources
never overlap. The reverse works too: when Endute's history is short (Amex serves 90
days), import the older statements as CSVs or PDFs into the linked account; only
statements dated before Endute's first transaction are accepted. The key can also come
from `ENDUTE_API_KEY`.

## Dashboard

```bash
uv run python scripts/build_dashboard.py
```

Then open `dashboard/spending.html` in your browser. The self-contained HTML works
offline, with calendar-month, account and category filters, clickable charts, and
searchable transaction details. Refunds reduce spending; payments, fees and interest are
excluded from spending.

The export contains private transaction data and is ignored by Git. It uses only
read-only CLI exports, makes no external requests, and every figure on it is picked from
a pennywise report rather than added up in the page. It is a snapshot: rerun the command
after imports or categorisation changes to refresh it.

## Agent skill

[`.agents/skills/pennywise`](.agents/skills/pennywise/SKILL.md) is an agent skill
(symlinked from `.claude/skills` so Claude Code and Codex share one copy). It teaches an
agent how to drive the CLI and the rules that keep figures trustworthy:

1. Every money figure stated comes from pennywise output; the agent never adds,
   subtracts or averages amounts itself.
2. The agent proposes; the user decides. Reading is free, but rules, overrides,
   categories, balances and imports are shown to the user before they're applied.
3. Data stays local, and the agent works through the CLI only.

It includes workflows for a categorisation pass, answering spending questions, entering
balances and importing statements, plus a
[UK merchant categorisation guide](.agents/skills/pennywise/references/categorising.md).
Open an agent session in the repository root and ask, for example, "where did the money
go last month?".

## Configuration

No environment variables are required. Those read by the code are:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PENNYWISE_HOME` | No | `~/.pennywise` | Directory used for the SQLite database. An unset or empty value uses the default. |
| `ENDUTE_API_KEY` | No | keychain | Endute Connect API key, if not stored with `pennywise endute key`. |

To customise the location, create a `.env` file in the repository root (it is
git-ignored):

```dotenv
PENNYWISE_HOME=/absolute/path/to/pennywise-data
```

Use an absolute path. A literal `~` in an environment variable is not expanded by the
application. The application does not load `PENNYWISE_HOME` from `.env` automatically;
pass the file to `uv` explicitly:

```bash
uv run --env-file .env pennywise statements
```

Alternatively, set the variable for a single invocation:

```bash
PENNYWISE_HOME="$HOME/.pennywise-dev" uv run python -c 'from pennywise.db import home_dir; print(home_dir())'
```

## Initialise or migrate the database

`uv run pennywise init` creates the database, applies all migrations and loads the
bundled categories and merchant aliases. Run it again after pulling changes that add
migrations; loading the seeds preserves existing aliases, skips patterns that already
have an active alias, and does not re-add a starter alias you removed.

From Python, the equivalent is:

```bash
uv run python - <<'PY'
from pennywise import db, layers

engine = db.open_engine()
try:
    db.migrate(engine)
    with db.write(engine) as conn:
        added = layers.load_alias_seeds(conn)
    print(f"Database ready: {db.home_dir() / db.DB_FILENAME}")
    print(f"Added {added} merchant aliases")
finally:
    engine.dispose()
PY
```

By default, this creates `~/.pennywise/pennywise.db`. The directory is set to owner-only
permissions (`0700`) and the database to `0600`. Connections use SQLite WAL mode, foreign
keys, and a five-second busy timeout.

To apply migrations without loading seeds, run from the repository root:

```bash
uv run alembic upgrade head            # or: uv run --env-file .env alembic upgrade head
```

The Alembic CLI uses the same database location, permissions, and SQLite settings as
`db.migrate(engine)`. Check the applied revision with `uv run alembic current`. Offline
SQL generation (`--sql`) is not supported.

## Using the engine from Python

This example uses synthetic data entirely in memory and leaves your database untouched.
It reconciles a statement containing a £25 purchase and £10 payment:

```bash
uv run python - <<'PY'
from datetime import date

from pennywise.engine.reconcile import reconcile
from pennywise.model import Kind, Statement, StatementData, Txn, gbp

statement = Statement(
    id=1,
    account_id=1,
    start=date(2026, 9, 1),
    end=date(2026, 9, 30),
    opening_pence=10000,
    closing_pence=11500,
)
transactions = (
    Txn(1, 1, date(2026, 9, 5), 2500, Kind.PURCHASE, "EXAMPLE SHOP", "Example Shop"),
    Txn(2, 1, date(2026, 9, 10), -1000, Kind.PAYMENT, "PAYMENT", "Payment"),
)
result = reconcile(StatementData(statement, transactions))
print(f"Status: {result.status.value}")
print(f"Computed closing balance: {gbp(result.computed_closing_pence)}")
print(f"Residual: {gbp(result.residual_pence)}")
PY
```

Expected output:

```text
Status: balances_reconcile
Computed closing balance: £115.00
Residual: £0.00
```

A zero residual means the balances reconcile; it does not prove every transaction is
present, because missing debits and credits can cancel each other out.

To work with persisted data in your own scripts, use `db.write(engine)` for writes and
`db.read(engine)` for reads. Create accounts and statement periods with
`importer.add_account()` and `importer.add_statement()`, then load the effective
statement with `layers.load_statement()`.

## Development

Run these from the repository root:

| Command | Purpose |
| --- | --- |
| `uv sync --locked` | Install runtime and development dependencies from the lockfile. |
| `uv run pytest` | Run all tests; quiet output is configured in `pyproject.toml`. |
| `uv run pytest tests/test_reconcile.py` | Run one test module. |
| `uv run pytest -k bridge` | Run tests whose names match `bridge`. |
| `uv run ruff check .` | Check lint rules and import order. |
| `uv run ruff check . --fix` | Apply available automatic lint fixes. |
| `uv run ruff format --check .` | Check formatting without changing files. |
| `uv run ruff format .` | Format the Python files. |
| `uv run alembic upgrade head` | Create the database and apply pending migrations. |
| `uv run alembic current` | Show the database's current migration revision. |
| `uv build` | Build the source distribution and wheel into `dist/`. |

CI runs `ruff check`, `ruff format --check` and `pytest` on every push and pull request.
Tests create temporary databases through pytest fixtures and never touch your
`PENNYWISE_HOME` database. Test data is synthetic; never commit real statements. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Project layout

```text
pennywise/
  cli.py                The `pennywise` command line
  db.py                 SQLite schema, connections, and migration entry point
  importer.py           Accounts, statements, and statement-scoped import
  sync.py               Endute Connect sync into statement periods
  endute.py             Read-only Endute Connect API client
  model.py              Typed transactions, statements, and money conventions
  layers.py             Corrections, overrides, marks, aliases, category rules, effective views
  adapters/             One parser per issuer export format (amex, halifax, tesco)
  engine/
    reconcile.py        Statement balance reconciliation
    bridge.py           Balance/spending comparisons and bill explanations
    report.py           Spending by category, merchant, card member and month
    anomalies.py        Deterministic transaction flags
    merchants.py        Merchant descriptor normalisation and alias suggestions
  migrations/           Alembic migrations
  seeds/                Bundled merchant aliases and categories
scripts/build_dashboard.py   Offline dashboard export
dashboard/template.html      Dashboard page template
.agents/skills/pennywise/    Agent skill
tests/                  Unit, property-based and end-to-end tests (synthetic fixtures)
specs/                  Original project vision
```

## Troubleshooting

- **`uv` is not found:** install `uv` and make sure it is on your shell's `PATH`.
- **Python version errors:** use Python 3.12 or newer; `uv python install 3.12` installs
  a compatible interpreter.
- **Your `.env` setting has no effect:** use `uv run --env-file .env ...`; the library
  only reads `PENNYWISE_HOME` from the process environment.
- **`no such table` errors:** run `uv run pennywise init` first.
- **Permission errors:** choose a writable data directory. Database setup also needs
  permission to set owner-only file and directory modes.
- **`database is locked`:** finish other write transactions and retry. Keep read
  transactions short; concurrent writes wait up to five seconds.

## Further reading

- [Original project vision](specs/initial-vision.md)
- [Outstanding work](TODOS.md)
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[MIT](LICENSE) © 2026 Mark Fullbrook
