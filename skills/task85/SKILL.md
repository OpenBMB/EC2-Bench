---
name: task85
description: "Q1 Quarterly Business Insight and Trend Analysis. Use when: synthesizing finance transactions, CRM customer data, and industry RSS feeds to analyze Q1 revenue trends and generate a business insight report with Q2 forecasts."
---

# Q1 Quarterly Business Insight and Trend Analysis Skill

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List RSS feed sources | `POST /rss/feeds` | `{}` |
| Get RSS feed article list | `POST /rss/feeds/get` | `{ "feed_id": <string> (required) }` |
| Get article details | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---
