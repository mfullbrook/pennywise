---
name: pennywise
description: Use the pennywise CLI to work with the household's credit card spending. It imports statement CSV exports, syncs cards connected through Endute Connect, records statement balances and reconciles them, categorises merchants with rules, and reports spending by category, merchant or card member. Use this skill whenever the user asks where money went, wants transactions or merchants categorised or classified (a "categorisation pass"), asks about their Amex or card statements, spending by category, person or month, wants to import a statement, sync or connect a card through Endute, enter balances, or check that a statement adds up, or is working in the pennywise repo or with ~/.pennywise, even if they don't say "pennywise".
---

# pennywise

pennywise is a local ledger of the household's credit card statements. It explains
where the money goes, using deterministic figures you can trace back to transactions.
You drive it entirely through its command line. This skill covers how to use it well,
and the handful of rules that keep its figures trustworthy.

Run every command from the repository root as `uv run pennywise <command>`. Every
command takes `--help`.

## Rules that matter

These come from what the project promises its user, so here is why each one exists.

1. **Every money figure you state comes from pennywise output.** The project exists
   because a model's arithmetic isn't trustworthy for money. Don't add, subtract,
   average or take percentages of amounts yourself. Quote the totals and shares the
   commands print. If a question needs a figure no command produces, such as two
   categories combined, give the separate figures and say pennywise doesn't combine
   them yet; don't do the sum in your head. Turning integer pence from `--json` into
   pounds for display is fine: 158000 is £1,580.00.
2. **You propose; the user decides.** Rules, overrides, new categories, balances and
   imports all change reported figures. Reading is free: `statements`, `report`,
   `uncategorised`, `transactions`, `categories` and `rules` change nothing. For
   anything that writes, show the user what you intend and apply only what they
   approve. Write the approval into `--reason`, because that's the audit trail.
3. **Use the CLI, never the database.** Don't open `~/.pennywise/pennywise.db` with
   sqlite3 or edit anything under `~/.pennywise`. Corrections, rules and overrides are
   append-only records with provenance, and only the CLI keeps them consistent.
   Nothing is ever deleted: a removed rule stays in the history.
4. **Keep financial detail local where you can.** Work from merchant totals
   (`uncategorised --json`) rather than listing every transaction. Look at individual
   transactions only for merchants you can't place from their name. Never copy
   statement files, CSVs or the database anywhere else.
5. **At the start of a session, run `uv run pennywise init`.** It's safe to repeat: it
   applies any pending database upgrade and adds new starter aliases and categories.
   It never overrides the user's own changes and never brings back anything they
   removed. If any other command says the database "isn't set up or is out of date",
   that's what `init` fixes.

## How pennywise sees the data

- **Accounts:** one per card, for example `amex-joint`. Each card can have several
  **card members** (named cardholders), and every transaction records whose card was
  used.
- **Statements:** one CSV export per statement, named by statement date
  (`2026-09-24.csv`). A transaction belongs to the statement whose CSV listed it,
  whatever its date. A charge dated on the 24th can legitimately appear on the next
  statement. `pennywise statements` lists statement IDs; other commands take those
  IDs.
- **Kinds:** `purchase`, `refund`, `payment`, `fee`, `interest`, `cash_advance`,
  `adjustment`, `other`. **Spending** is purchases + cash advances + refunds, where
  refunds are negative and so reduce it. Payments are never spending. Fees and
  interest are reported separately as cost of credit. Only spending gets categorised.
- **Merchant:** the raw card descriptor, cleaned up and grouped by aliases. For
  example `TESCO STORES 2345` becomes `Tesco`, and `TESCO PFS 4021` becomes
  `Tesco Petrol`. Rules and reports use this merchant name, exactly as the commands
  print it.
- **Category:** decided per transaction in this order:
  1. an override on that transaction
  2. the rule for its merchant
  3. the card issuer's own category, if it names one of ours (Amex provides none)
  4. otherwise **Uncategorised**

  Refunds follow their merchant's category, so a refund at Currys reduces Shopping.
- **Balances:** each statement's previous balance and new balance, typed in from its
  PDF. When they're entered, `reconcile` checks that the transactions explain the
  difference to the penny.

## Commands

| Command | What it does |
|---|---|
| `init` | Set up or upgrade the database; add new starter aliases and categories. Safe to repeat. |
| `accounts` | List accounts with statement and transaction counts. |
| `statements [--account A]` | List statement IDs, dates, periods, and whether each reconciles. |
| `report [--by category\|merchant\|person] [--months] [--account A] [--statement ID]... [--json]` | Spending grouped, per statement (or per calendar month with `--months`) and in total, with shares. |
| `uncategorised [--limit N] [--account A] [--statement ID]... [--json]` | Merchants with uncategorised spending, biggest first. |
| `transactions [--merchant M] [--category C \| --uncategorised] [--statement ID]... [--json]` | The transactions behind a figure (`--merchant` ignores case). |
| `categories` | The category list, with descriptions and rule counts. |
| `rules` | Current merchant → category rules, with who made them and why. |
| `rule set "MERCHANT" "Category" --reason "..."` | Put every transaction at a merchant into a category. |
| `rule remove "MERCHANT" --reason "..."` | Withdraw a rule; the merchant goes back to Uncategorised. |
| `override ID --category C --reason "..."` | Change one transaction (also `--merchant`, `--kind`). |
| `category add "Name" "description"` | Add a category. |
| `balances ID --opening 1,234.56 --closing 2,468.10` | Enter a statement's balances from its PDF, then check them. |
| `reconcile ID` | Show how a statement's transactions add up against its balances. |
| `account add SLUG --issuer amex\|halifax --name "..." [--statement-day N]` | Add a card account fed by statement files. |
| `import ACCOUNT FILE... [--batch]` | Import statement exports (Amex CSV; Halifax CSV or PDF; Tesco Bank PDF). `--batch` takes a date-range download and splits it into statement periods. The same file twice does nothing; a bad file imports nothing. |
| `endute accounts` | The cards Endute Connect can see, how far back their history goes, and which pennywise account each feeds. |
| `endute link ID SLUG --statement-day N [--name "..."] [--from DATE]` | Feed a pennywise account from an Endute card (creates the account if SLUG is new). |
| `endute sync [SLUG...]` | Fetch new transactions for linked cards. Safe to repeat. |
| `endute statement-dates SLUG DATE...` | Record real statement dates for a card whose statement day varies; periods are rebuilt around them. |
| `endute status` | Linked cards, their latest transaction and last sync result (no network). |

`--json` output gives amounts as integer pence. `uncategorised --json` looks like:

```json
{"uncategorised_pence": 225947,
 "merchants": [{"key": "BRITISH AIRWAYS", "pence": 158000,
                "per_statement": {"2026-08-24": 0, "2026-09-24": 158000}, "count": 1}],
 "more": 6}
```

`key` is the merchant name to use in `rule set`. `more` counts merchants beyond
`--limit`.

## Categorisation pass

The goal is to get uncategorised spending down to zero, or down to a tail the user is
happy to leave, with as few rules as possible. Work biggest spend first: a few dozen
merchants usually cover most of the money.

1. **Orient.** Run `init`, `categories` (learn the categories and their descriptions),
   `rules` (what's already decided) and `report` (the current uncategorised total).
2. **Get the worklist:** `uncategorised --json --limit 40`.
3. **Decide each merchant from its name,** using the category descriptions and
   `references/categorising.md`, which covers UK merchants and the tricky cases. Read
   it before your first batch. When a name doesn't tell you enough (a person's name, a
   payment processor like `SQ *` or `SUMUP *`, initials, an unfamiliar business), look
   at its transactions with `transactions --merchant "NAME" --json`. The dates,
   amounts and descriptions often settle it. If you still can't tell, ask. A wrong
   rule quietly misfiles every past and future charge at that merchant, which is
   worse than leaving it uncategorised for a moment.
4. **Propose a batch** of about 15-25 merchants, as a table the user can approve at a
   glance:

   | Merchant | Spend | Txns | Proposed category | Why |
   |---|---|---|---|---|
   | BRITISH AIRWAYS | £1,580.00 | 1 | Travel & holidays | airline |

   Then list any merchants you're unsure about under **Need your call**, each with a
   short question ("`J SMITH`, £450 over 3 payments: a tradesperson (Household) or a
   person you paid back (Other)?"). Spend and transaction counts come straight from the
   JSON.
5. **Wait for the user's answer.** They may approve everything, change a few ("Shell
   is Transport, Costa is Eating out"), or skip some.
6. **Apply only what was approved,** one command per merchant:

   ```bash
   uv run pennywise rule set "BRITISH AIRWAYS" "Travel & holidays" --reason "airline; approved by user in categorisation pass"
   ```

   Copy the merchant exactly from `key`. The command confirms with
   `BRITISH AIRWAYS -> Travel & holidays (1 transactions)`. If it says "no
   transactions at merchant", recheck the name; it suggests close matches. Don't
   reach for `--new`, which exists for merchants that haven't been imported yet.
7. **Repeat from step 2** until the user wants to stop. Report progress using the
   uncategorised total the command prints.
8. **Finish** with `report` (and `report --by person` if they're interested) and a
   short summary that quotes its figures.

Things that come up:
- **Mixed merchants** (Amazon, eBay, PayPal, Apple, supermarket clothing): set the
  rule for what they mostly are, and suggest overrides for clear exceptions only when
  the user points them out ("the £300 Amazon order was a present").
- **A one-off big charge at a merchant** (a flight, a sofa): a normal rule is right.
  Use an override only when one transaction differs from the merchant's usual
  category.
- **No good category:** use Other for genuinely miscellaneous spending. If several
  merchants share a theme no category covers, propose a new category to the user
  rather than overloading Other.
- **Changing your mind:** setting a new rule for the same merchant replaces the old
  one (the latest wins), and `rule remove` takes it back to Uncategorised. Both stay
  in the history.
- **One shop under two names** (two descriptors that aren't grouped): give each its
  own rule. The CLI can't add aliases yet, so mention it to the user if it happens
  often.

## Answering questions about spending

- "Where did the money go?" → `report`, then `report --by merchant` for the detail.
- "What did we spend each month?" → `report --months`: calendar months by transaction
  date, across all accounts. The user thinks in months, not statements; use statements
  only for reconciling against a statement PDF.
- "How much did each of us spend?" → `report --by person`.
- "What was in the September statement?" → find its ID with `statements`, then
  `report --statement ID`.
- "Since June": pass each statement ID from June onwards (`--statement 3 --statement
  4 ...`). The report prints per-statement columns and their total.
- "Show me the Amazon charges" → `transactions --merchant "Amazon"`, which prints the
  count and total.

Quote the figures as printed. When a figure would need arithmetic the CLI doesn't do,
say so.

## Balances and reconciliation

The user reads two numbers off each statement PDF: the previous balance (opening) and
the new balance (closing). Enter them with `balances ID --opening ... --closing ...`
once the user has given you the values; never guess them or work them out. The
command says either "balances reconcile to the penny" or "off by £X".

When it's off, check the typed numbers first (the commonest cause). Then use
`reconcile ID` to see the breakdown by kind, and tell the user a transaction may be
missing from, or extra in, the CSV. Fixing that needs corrections the CLI doesn't
expose yet, so stop there.

## Cards connected through Endute

Endute Connect pulls transactions straight from the bank for cards the user connected in
Endute's portal. pennywise only reads from Endute; the API key lives in the macOS
keychain (`endute key` stores it; never ask the user to paste it into the conversation).

- **Syncing is safe to repeat** and is a read from the user's point of view: it only
  adds transactions the bank reported. Run `endute sync` before answering questions
  about recent spending on linked cards. It says how many rows were new.
- **Statement periods.** Endute has no statements, so each linked card has a statement
  day and transactions fall into periods ending on that day. The current period shows as
  `(open)` in `statements`. Periods can differ from the real statement by a day or two
  at the edges, so a period may not reconcile exactly against the PDF.
- **Linking changes the ledger**, so it follows rule 2: show the user the card (from
  `endute accounts`), the slug and the statement day you intend, and link once they
  agree. The statement day is on the card's statement; ask rather than guess.
- **Older history by CSV.** Endute often has only 90 days (Amex). Statements from before
  Endute's first transaction can still be imported as CSVs into the linked account
  (`import amex-mark ~/.pennywise/statements/amex-mark/*.csv`); the format comes from the
  bank. A CSV dated on or after Endute's first transaction is refused, since it would
  count the same spending twice.
- **A card with CSV history** gets a cutover: Endute rows booked before the day after
  its latest CSV statement are skipped, so nothing is counted twice.
- **Varying statement days** (Barclaycard): periods end on the dates recorded with
  `endute statement-dates`; later ones are estimated from the usual day and show as
  `(estimated)` until the user adds the real date from the next statement.
- **Revolut is a spending account**, not a card, grouped by calendar month. Its bank
  codes decide the kind: card payments and transfers out are spending; top-ups are
  funding (like a card repayment); money in from people ("From ...", "Payment from ...")
  is a refund that reduces spending. Transfers are named after the other party
  (`To ALEX EXAMPLE`). Revolut doesn't say which card was used, so spending on a card
  kept for one purpose (a business card, say) is categorised by merchant rules.
- **Endute's own category** (e.g. `Food & Dining > Groceries`) comes with each row as
  `source_categories` in `uncategorised --json` and `source_category` in
  `transactions --json`. Use it as a hint when proposing a category, never as the
  answer: it doesn't use the household's categories.
- **Card members aren't known** for Endute rows, so `report --by person` shows them as
  unknown.
- **Errors.** A sync error names the account and writes nothing for it. "Rejected the
  API key" means the key needs storing again (`endute key`, which the user runs
  themselves). An expired consent is renewed in Endute's portal; `endute accounts` shows
  each card's renewal date.
- **"changed at Endute"** in the sync output means the bank re-reported a transaction
  differently. pennywise keeps the original; mention it to the user.

## Importing statements

Formats: American Express CSV; Halifax CSV (`<card>_<ddmmyyyy>.csv`) or PDF statement;
Tesco Bank PDF statement (no CSV exists). A PDF brings its printed balances and only
imports if its rows explain them to the penny; Tesco's foreign-exchange fees become fee
rows, outside spending. A date-range download that spans many statements (an Amex "batch"
export) goes in with `--batch`: rows are split into periods by the account's statement
day, and a file overlapping another file's dates is refused. Import files oldest first.

Export one CSV per statement from the card website, named by statement date
(`YYYY-MM-DD.csv`), and keep it outside the repository, for example in
`~/.pennywise/statements/<account>/`. Then run `import ACCOUNT FILE...`. A new card
needs `account add` first. For a card with no supported format, tell the user it needs a new adapter.
