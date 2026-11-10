# Security policy

Pennywise handles financial data (card statements and transactions) and an API key for
Endute Connect, so security reports are welcome and taken seriously.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's security advisories:
[Report a vulnerability](https://github.com/mfullbrook/pennywise/security/advisories/new)
(the repository's **Security** tab, then **Report a vulnerability**).

Do not open a public issue for a security problem. Include what you found, how to
reproduce it, and the impact you expect. Use synthetic data in any example; never send
real statements, transactions or keys.

You can expect an acknowledgement within a week. Fixes go into the latest release; there
are no separately maintained older versions.

## Supported versions

Only the latest commit on `main` is supported.

## Keeping your own data safe

- **Keep `~/.pennywise` private.** It holds the database and your statements. Pennywise
  creates it owner-only (`0700`, database `0600`); don't loosen those permissions, sync
  it to shared storage, or copy it into the repository.
- **Keep `.env` private.** It may hold `ENDUTE_API_KEY`. It is git-ignored; never commit
  it or paste it into issues, logs or chats. Prefer `pennywise endute key`, which stores
  the key in the macOS keychain.
- **The dashboard export is private.** `dashboard/spending.html` contains your
  transactions and is git-ignored. Share it only with people who may see them.
- **Agents read what pennywise prints.** When driving pennywise with an AI agent, its
  output goes to that agent's provider. Choose an agent and settings you're comfortable
  with for financial data.

## Scope

In scope: anything that could leak ledger data or the API key, write outside the data
directory, let an imported file run code or corrupt the ledger, or make the Endute client
talk to a host other than the API.
