# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- MIT licence, contributing guide, security policy, issue and pull request templates,
  Dependabot configuration and a GitHub Actions CI workflow (Ruff and pytest).
- Package metadata for publishing: readme, licence, authors, classifiers, keywords and
  project URLs.

## [0.1.0] - 2026-11-10

First public version.

### Added

- **Ledger:** SQLite storage under `~/.pennywise` with owner-only permissions, WAL mode,
  and Alembic migrations usable from Python and the `alembic` CLI. Money is integer pence.
- **Append-only layers:** corrections, overrides, triage marks, merchant aliases and
  category rules, each with a reason; the latest row wins, revoking restores the previous
  value, and `as_of` replays history.
- **Engine:** merchant normalisation, statement reconciliation, balance and spending
  comparisons across statements and cards, and five anomaly rules that state their
  baselines.
- **Import:** American Express CSV statements and date-range batch exports split by
  statement day; Halifax CSV and PDF statements; Tesco Bank PDF statements. PDFs import
  only if their rows explain the printed balances. Re-importing a file is a no-op, and a
  file that doesn't parse imports nothing.
- **Categories:** 15 starter categories, merchant rules, per-transaction overrides, bundled
  UK merchant aliases, and `alias suggest` for town-suffix descriptor variants.
- **Reports:** spending by category, merchant or card member, per statement or calendar
  month and in total, with uncategorised merchants ranked by spend; `--json` output.
- **CLI:** `pennywise` with `init`, `account`, `import`, `statements`, `balances`,
  `reconcile`, `report`, `uncategorised`, `transactions`, `categories`, `rules`,
  `aliases`, `override` and `endute` commands.
- **Endute Connect sync:** read-only client (key from the macOS keychain,
  `ENDUTE_API_KEY` or `.env`), idempotent sync with an overlapping window, statement
  periods by statement day, known dates or calendar month, and cutover from CSV history.
- **Dashboard:** an offline, self-contained HTML spending dashboard built from read-only
  CLI exports.
- **Agent skill:** `.agents/skills/pennywise`, teaching agents to drive the CLI without
  doing arithmetic and to propose changes for the user to approve, with a UK merchant
  categorisation guide.

[Unreleased]: https://github.com/mfullbrook/pennywise/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mfullbrook/pennywise/releases/tag/v0.1.0
