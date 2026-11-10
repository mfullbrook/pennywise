# TODOS

## Import

### Full overlapping-import matcher

**What:** Replace the "refuse overlapping files" import rule with a matcher. It would add only genuinely new rows from an overlapping CSV (surplus, equal, deficit and near-match cases), with import issues and resolve commands.

**Why:** Re-downloads and rolling-window exports would then just work, without trimming files by hand.

**Context:**
- Cut in /plan-eng-review D1 (2026-10-08) to protect the first-build deadline. The first build refuses any file whose date range overlaps existing rows on the account, unless it's byte-identical.
- The spec review found 8 edge-case holes in the original matcher design (R3-1, 2, 31-33, 35, 36: manual rows reopening deficits, `--link` not sticking, excluded rows counted, resolutions not revertible).
- Start from that list. Match against the `desc_key` of every source row linked to a transaction. Leave manual and excluded rows out of the stored count.
- The D15 investigation (export granularity, date basis, boundary overlap) may change what's needed.

**Effort:** M
**Priority:** P3
**Depends on:** the D15 import-strategy follow-up decision; real exports showing that refusal is too strict.

## Engine

### compare_statements (any two statement sets)

**What:** An MCP and CLI tool comparing arbitrary statement sets, such as this September against last September, paired by account.

**Why:** It answers period-versus-period questions beyond the adjacent statement that `explain_bill` covers.

**Context:**
- Deferred in /plan-eng-review D2 (2026-10-08). It's a thin wrapper over the existing bridge functions.
- Rules from the design: pair statements by account; each account in one set must have exactly one statement in the other; refuse unpaired or cross-account input.
- Still open (R3-42): which set is "before". Use `a_ids` as prev and `b_ids` as curr, and refuse reversed or identical pairs.

**Effort:** S
**Priority:** P3
**Depends on:** `balance_bridge` and `spending_bridge` shipped.

### RECURRING_CHANGE anomaly rule

**What:** Flag recurring charges (subscriptions) whose amount changed.

**Why:** Subscription price creep is a classic slow leak the other five rules surface only indirectly.

**Context:**
- Deferred in /plan-eng-review D3 (2026-10-08). There's no recurrence definition yet, and only 2-3 statements of history would make it noisy.
- Define recurrence as, for example, the same normalised merchant in at least 3 consecutive statements, with a minimum of 6 statements of history.
- Below the minimum, output `insufficient_history`, following the A7 pattern.
- The spec puts subscription detection in Phase 5.

**Effort:** S
**Priority:** P3
**Depends on:** 6+ statements of imported history per account.

### explain_bill target comparison

**What:** Optional `target_pence`, `target_kind` (`statement_balance` | `collection`) and `target_source` on `explain_bill`. The output reports Σ closing balances, the target and the gap. If the gap is non-zero, the header says which number is being explained.

**Why:** If the bill figure is a direct-debit amount (full, minimum, fixed or adjusted) or spans cards, the explanation can otherwise describe a different number with no warning.

**Context:**
- Deferred in /plan-eng-review D16 (2026-10-08). Decision `4eb938b0` records the ceiling.
- Raised by the Codex outside voice (O5).
- Upgrade trigger: the Assignment shows the bill figure being explained is a collection amount, or any explained total differs from the figure the user asked about.
- At implementation, remove the `gstack-shortcut(dec-4eb938b0)` marker in `engine/bridge.py`.

**Effort:** S
**Priority:** P2
**Depends on:** None.

## Endute

### Move a second card member's Amex from CSV to Endute without losing categories

**What:** Once a second card member's Amex is connected in Endute, compare the history Endute holds (`oldest_booking_date`) with the CSV statements, and compare Endute's descriptions with the CSV descriptors for the same months. If the merchant names match, rules carry over unchanged. Then decide: keep CSV up to a cutover (the default `endute link` gives), or replace CSV rows with Endute rows.

**Why:** The user said they don't mind replacing CSV data, but the categorisation work must survive.

**Context:**
- Rules are keyed by merchant name, so they carry over if the normaliser gives the same names. Overrides are keyed by transaction id and would need re-pointing; there are few.
- Replacing needs a way to retire CSV statements (an exclude correction per row, or a statement-level flag) and to re-point overrides by matching date and amount.
- Endute often serves only 90 days on first connect, so CSV likely stays for older months.

**Effort:** M
**Priority:** P2
**Depends on:** that Amex connected in Endute.

### Scheduled sync

**What:** Run `pennywise endute sync` on a schedule (a launchd agent on the Mac), and optionally on Endute's signed webhooks.

**Why:** Freshness without remembering to sync. Endute itself refreshes from the bank 2-4 times a day.

**Context:** Webhooks need an always-on reachable host, which the Mac isn't; launchd every few hours is enough. This is also the trigger for the backup shortcut (`dec-4cacbfc9` in `db.py`).

**Effort:** S
**Priority:** P3

### Review rows Endute re-reports differently

**What:** A command listing rows whose later Endute copy differs from the stored original (the sync's "changed" count), with both versions, and a way to accept the new one as a correction.

**Why:** Originals are immutable; today a change is only counted.

**Effort:** S
**Priority:** P3
**Depends on:** seeing whether it happens in practice.

## MCP

### Household access from a second Claude (remote MCP)

**What:** Serve the pennywise MCP over streamable HTTP so a second household member can triage from their own Claude.

**Why:** The spec is household-first ("how much did *we* spend"), but stdio MCP runs on one machine.

**Context:**
- From the office-hours Open Question 5. Tracked in /plan-eng-review D19 (2026-10-08).
- Security requirements: the home network or a tailnet only, never the public internet; per-person auth tokens; per-person `source` provenance on every mark.
- Mark tools keep the per-call approval rule.
- Building this is one of the triggers for the D11 shortcut (`c396343f`, no mark-tool eval). When a second person starts triaging, add the eval at the same time.

**Effort:** M
**Priority:** P3
**Depends on:** the first build shipped; a decision to expose financial data on the local network.

## Completed
