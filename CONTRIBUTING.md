# Contributing

Thanks for your interest in pennywise. Issues and pull requests are welcome; for larger
changes, please open an issue first to agree the approach.

## Setup

You need Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/mfullbrook/pennywise.git
cd pennywise
uv sync --locked
```

## Tests

```bash
uv run pytest
```

Tests use temporary databases and never touch `~/.pennywise`. Add tests for new
behaviour; money invariants have Hypothesis property tests in
`tests/test_engine_properties.py`.

## Style

```bash
uv run ruff check .
uv run ruff format .
```

CI runs `ruff check`, `ruff format --check` and `pytest`, and all three must pass.

- Money is integer pence. Every figure comes from the deterministic engine; nothing is
  computed by a model, a template or the dashboard page.
- History is append-only: change effective values through the layers (corrections,
  overrides, rules, aliases, marks), never by editing original ledger rows.
- Schema changes go in a new Alembic migration under `pennywise/migrations/versions/`.
- Don't use `print()` in the package (Ruff's `T20` rule); logs must not carry financial
  data.
- If behaviour changes, update the README, the agent skill in `.agents/skills/pennywise`
  and `TODOS.md` as needed, and add a line to `CHANGELOG.md`.

## Never commit real data

**Synthetic fixtures only.** Never commit real statements, CSV or PDF exports,
databases, `.env` files, API keys, account numbers, or names and places from real
transactions, in code, tests, docs, issues or commit messages. Fixtures in
`tests/fixtures/` use made-up card members (`MR A EXAMPLE`) and generic national chains.
`.gitignore` blocks CSVs, PDFs and SQLite files outside the fixture and seed directories;
don't work around it.

For an import bug in a new export format, build a small synthetic file in the same
format that reproduces it.

## Licence

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
