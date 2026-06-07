---
name: task102
description: "Q1 Key Customer Quarterly Review Report. Use when: preparing a quarterly customer review report that synthesizes financial transactions, CRM data, email communications, industry news, and account manager contacts."
---

# Q1 Key Customer Quarterly Review Report Skill

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inbox messages | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get message details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List RSS feed sources | `POST /rss/feeds` | `{}` |
| Get articles from an RSS feed | `POST /rss/feeds/get` | `{ "feed_id": <string> (required) }` |
| Get article details | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
