# Project Brief: Personal Finance Intelligence

**Project type:** Personal finance analytics application  
**Architecture:** Endute Connect → SQLite → Analytics Engine → REST API + MCP Server → React SPA / AI Agents  
**Primary users:** Personal / household use  
**Status:** New project — greenfield development

## 1. Project Overview

Build a personal finance intelligence application that automatically collects credit card transactions from multiple UK financial institutions, stores them in a local database, and provides spending analysis through a web dashboard and AI agents.

The system should combine **deterministic financial calculations with AI-powered analysis**, ensuring that financial figures are accurate, reproducible and auditable.

The primary objective is to understand household spending, identify unusual transactions, monitor trends and enable natural-language questions about financial activity.

The application should be lightweight, self-hostable and designed for future extensibility.

## 2. Core Architecture

```text
                    ENDUTE CONNECT
                    Open Banking API
                          |
                          |
                   Data Ingestion
                  (Automated Sync)
                          |
                          v
                    SQLITE DATABASE
                          |
                          |
                   Analytics Engine
                 (Deterministic SQL)
                          |
                +---------+---------+
                |                   |
                v                   v
             REST API           MCP SERVER
                |                   |
                v                   v
             React SPA          AI Agents
                |                   |
                v                   v
             Dashboard          Natural Language
             & Reports          Financial Analysis
```

Both the REST API and MCP server must use the same analytics engine to ensure consistent results.

## 3. Technology Stack

Recommended technologies:

| Component | Technology |
|---|---|
| Backend | Python / FastAPI |
| Database | SQLite |
| ORM | SQLAlchemy |
| Data ingestion | Endute Connect REST API |
| Scheduled tasks | APScheduler or equivalent |
| MCP server | Python MCP SDK |
| Frontend | React + TypeScript + Vite |
| Styling | Tailwind CSS |
| Charts | Recharts |
| API specification | OpenAPI |
| Deployment | Docker Compose |

Keep the architecture simple. This is a personal application, not a multi-tenant SaaS platform.

SQLite should remain the primary database unless there is a compelling reason to change.

## 4. Data Ingestion

### Endute Connect integration

Use [Endute Connect](https://endute.com/connect) to collect transactions automatically.

Initially support:

- American Express
- Barclaycard
- Tesco Bank credit cards

The system should discover accounts dynamically rather than hardcoding financial institutions.

**Important:** Before implementing live ingestion, verify that Endute can access each of these specific accounts. Individual account support has not yet been confirmed.

Endute provides a versioned REST API with account information, transaction history, balances and merchant enrichment. It also supports signed webhooks and a free sandbox environment. :chatgpt-content-reference{index="0"}

### Ingestion requirements

The ingestion service must:

1. Discover connected accounts.
2. Import available historical transactions.
3. Automatically synchronise new transactions.
4. Prevent duplicate transaction ingestion.
5. Preserve original bank transaction information.
6. Store merchant names and category enrichment.
7. Track synchronisation history and errors.
8. Support manual synchronisation.
9. Handle connection failures and expired consent.

Prefer Endute webhooks for triggering incremental synchronisation, with periodic reconciliation as a fallback.

Endute supports cursor pagination, booking-date filtering and stable transaction identifiers. Its documentation recommends overlapping incremental fetch windows to capture late-arriving transactions. :chatgpt-content-reference{index="1"}

### Historical data

Import the maximum history available from each account.

Endute can capture up to 730 days of history, but many banks initially expose only around 90 days. Display the actual available history for each account rather than assuming full coverage. :chatgpt-content-reference{index="2"}

Design the ingestion layer so additional data sources can be supported later.

## 5. Database Design

SQLite is the single source of truth for transaction records and financial calculations.

Suggested entities:

| Entity | Purpose |
|---|---|
| Accounts | Connected financial accounts |
| Transactions | Original transaction records |
| Merchants | Normalised merchant identities |
| Categories | Spending categories |
| Category Rules | User-defined categorisation |
| Transaction Overrides | Manual corrections and exclusions |
| Sync History | Import status, timestamps and errors |
| Anomalies | Detected unusual spending |

### Transaction information

Preserve at least:

- Source transaction ID
- Account ID
- Booking date and value date
- Original description
- Original signed amount and currency
- Normalised merchant
- Category and categorisation source
- Transaction classification
- Original Endute payload
- Import and update timestamps

Use exact decimal arithmetic or integer minor units for financial calculations. Never use binary floating-point arithmetic for money.

Maintain the original data independently of any subsequent corrections or categorisation changes.

### Normalisation

Normalise transactions around **calendar months**, regardless of each card's statement period.

Use the transaction booking date as the default reporting date.

Implement merchant normalisation so variations such as:

`AMZN MKTP`, `AMAZON UK` and `Amazon Marketplace`

can be grouped under a common merchant, where appropriate.

Support user-defined merchant aliases and category overrides.

Initially use Endute's enrichment, with custom rules taking precedence.

### Spending calculation rules

The system must distinguish between purchases, refunds, card repayments, transfers, fees and other transaction types.

Credit card repayments must not be counted as new household expenditure.

Define total spending consistently, including how refunds reduce expenditure.

Retain original transaction signs while exposing clearly defined reporting measures.

## 6. Analytics Engine

All financial calculations must be deterministic.

**The LLM must never be responsible for calculating totals or performing financial arithmetic.**

The analytics engine should expose reusable functions for:

### Spending analysis

- Total spending by calendar month
- Monthly spending by category
- Monthly spending by merchant
- Spending by card/account
- Month-on-month comparisons
- Rolling three-, six- and twelve-month trends
- Largest individual transactions
- Largest merchants by expenditure

Every aggregate should be traceable to its underlying transactions.

### Unusual transaction detection

Build an anomaly detection system capable of identifying:

**Unusually large transactions**

Transactions significantly above the customer's typical spending.

**Unexpected merchant expenditure**

A transaction considerably larger than historical spending at the same merchant.

**Duplicate or suspicious transactions**

Potential duplicate charges within a short period.

**Changes in spending patterns**

For example, grocery spending increasing significantly compared with previous months.

**New merchants or unexpected recurring charges**

Potentially interesting new expenditure or changes in subscription amounts.

Start with deterministic rules and statistical techniques.

AI can subsequently interpret anomalies, explain their significance and help prioritise them.

Anomalies should not automatically be labelled as fraudulent.

## 7. REST API

Expose the analytics engine through a REST API.

Suggested endpoints:

```text
GET /api/accounts
GET /api/transactions
GET /api/merchants
GET /api/categories

GET /api/analytics/summary
GET /api/analytics/monthly
GET /api/analytics/categories
GET /api/analytics/merchants
GET /api/analytics/trends
GET /api/analytics/anomalies

GET /api/reports/monthly

GET  /api/sync/status
POST /api/sync/trigger
```

Support filtering by:

- Date range
- Calendar month
- Account
- Merchant
- Category

Use consistent response formats so the data can be consumed by both the SPA and other applications.

## 8. MCP Server

Provide an MCP server that allows AI agents to interact with the financial data.

The MCP server should share the same service layer as the REST API rather than reimplementing analysis.

### Suggested MCP tools

| Tool | Description |
|---|---|
| `get_spending_summary` | Overall spending for a period |
| `get_monthly_spending` | Spending by month |
| `get_category_breakdown` | Spending grouped by category |
| `get_merchant_spending` | Spending associated with merchants |
| `search_transactions` | Find matching transactions |
| `get_spending_trends` | Historical trends |
| `get_anomalies` | Unusual transactions |
| `compare_periods` | Compare spending between periods |
| `get_account_status` | Account connection and data freshness |

### Example natural-language questions

The MCP integration should enable questions such as:

- "How much did we spend in September?"
- "What are our top ten merchants this year?"
- "How much have we spent at Amazon over the last six months?"
- "Why was our spending higher in August?"
- "Have we got any unusual transactions this month?"
- "How much are we spending on subscriptions?"
- "Compare our grocery expenditure over the last twelve months."

The agent should select appropriate tools, retrieve calculated results and explain them.

Where useful, MCP tools should return transaction-level evidence supporting the calculations.

**Avoid unrestricted database access in the initial implementation.** Prefer defined, read-only analytical tools.

## 9. Single Page Application

Build a modern, responsive personal finance dashboard.

The interface should prioritise clarity and exploration of financial data.

### Dashboard

Display:

- Current month's spending
- Previous month's spending
- Month-on-month change
- Spending by category
- Top merchants
- Historical spending chart
- Unusual transaction alerts

### Transaction explorer

Provide a searchable, filterable transaction table.

Allow users to inspect individual transactions and correct merchant names or categories.

### Spending analysis

Interactive visualisations showing:

- Monthly expenditure
- Category breakdowns
- Merchant rankings
- Spending trends over time

Clicking a chart segment should reveal the underlying transactions.

### Monthly reports

Generate a consolidated monthly spending report.

Reports should include:

- Total expenditure
- Comparison with previous months
- Category breakdown
- Top merchants
- Notable changes
- Unusual transactions
- Optional AI-generated narrative summary

Reports should be accessible within the SPA and downloadable as CSV, with PDF export as a later enhancement.

### Account management

Display connected accounts, last successful data refresh, historical coverage and any required reconnection actions.

## 10. AI Integration

AI should operate as an interpretation layer over structured financial data.

Its responsibilities include:

- Answering natural-language questions
- Explaining changes in spending
- Summarising unusual transactions
- Suggesting categorisation improvements
- Generating readable financial narratives
- Identifying potentially interesting spending patterns

AI-generated observations must be clearly distinguished from verified calculations.

Where AI suggests category changes, the user should be able to review them before they affect reporting.

The initial implementation does not need a built-in AI chat interface. MCP access through an existing agent is sufficient.

## 11. Security and Privacy

This application will contain sensitive household financial information.

Requirements:

- Endute API credentials must remain server-side.
- Never expose financial data through unauthenticated endpoints.
- Use authenticated, encrypted connections.
- Make the MCP interface read-only initially.
- Keep financial data out of application logs.
- Protect database files and backups.
- Never send complete transaction histories to an LLM unnecessarily.
- Implement configurable retention for AI analysis requests.

Support local development and secure self-hosted deployment.

SQLite must reside on persistent storage with automated backups.

If the frontend is deployed to Vercel, host the backend and SQLite separately on persistent infrastructure rather than Vercel's ephemeral functions.

## 12. Implementation Phases

### Phase 1 — Data foundation

Implement the Endute integration, database schema, historical import, transaction normalisation and synchronisation.

Validate ingestion against Endute's sandbox and then real accounts.

**Deliverable:** A reliable SQLite database populated automatically with financial transactions.

### Phase 2 — Analytics and REST API

Implement deterministic spending calculations, merchant normalisation, categories, reporting endpoints and anomaly detection.

Add automated tests for financial calculations.

**Deliverable:** A complete financial analytics API.

### Phase 3 — Web application

Build the React SPA, including the dashboard, transaction explorer, interactive charts and monthly reports.

**Deliverable:** A functional personal finance dashboard.

### Phase 4 — MCP integration

Expose the analytics engine through MCP.

Test natural-language questions against known financial data and verify the correctness of answers.

**Deliverable:** AI agents capable of querying and analysing household expenditure.

### Phase 5 — Enhancements

Potential future capabilities include:

- Subscription and recurring-payment detection
- Spending budgets and targets
- AI-assisted categorisation
- Automated monthly report generation
- Email delivery of reports
- Spending alerts and notifications
- Support for current accounts and additional financial institutions
- Historical data import from CSV

These should not delay the initial implementation.

## 13. Acceptance Criteria

The initial project is complete when:

1. Transactions are automatically synchronised from connected credit cards without manual downloads.
2. Repeated imports do not create duplicate records.
3. Financial data is stored persistently in SQLite.
4. Transactions are normalised into calendar months.
5. Merchant and category reporting works across all accounts.
6. Credit card repayments and refunds are handled correctly.
7. Financial totals are calculated deterministically and verified by automated tests.
8. The dashboard displays accurate monthly expenditure and historical trends.
9. Unusual transactions are identified and visible in the SPA.
10. An MCP-connected agent can answer financial questions using the same analytics engine.
11. Every reported total can be reconciled against its underlying transactions.
12. Connection failures and stale data are clearly visible.

---

## 14. Engineering Guidance

Keep this as a relatively small, well-structured application.

Avoid unnecessary microservices, message brokers, distributed infrastructure or complex orchestration.

Use a modular monolith with clear separation between:

- Data ingestion
- Persistence
- Transaction normalisation
- Financial analytics
- REST API
- MCP interface
- Frontend

The most important architectural principle is:

**One database. One analytics engine. Multiple interfaces.**

Financial calculations must never diverge between the dashboard, reports and AI agent responses.

Build incrementally, keeping the application functional after each phase.

### Reference Documentation

- [Endute Connect overview](https://endute.com/connect)
- [Endute Connect quickstart](https://endute.com/connect/docs/quickstart)
- [Endute API endpoint reference](https://endute.com/connect/docs/endpoints)
- [Endute OpenAPI specification](https://api.endute.com/v1/openapi.json)
- [Endute webhooks](https://endute.com/connect/docs/webhooks)
- [Endute data freshness](https://endute.com/connect/docs/data-freshness)

---

### One architectural recommendation

I'd make **the deterministic analytics engine the centre of this project**, rather than the dashboard or MCP server.

That gives you the freedom to change the frontend, connect different AI agents, and eventually introduce more sophisticated spending intelligence without rewriting the financial logic.

I'd also ask the development agent to establish a set of financial reconciliation tests early. Credit card spending, repayments, refunds and merchant corrections are precisely where an apparently polished finance dashboard can produce misleading figures.
