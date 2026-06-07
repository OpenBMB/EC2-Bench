---
name: task97
description: "Month-End Comprehensive Reconciliation. Use when: checking reconciliation and invoice job statuses, verifying payment and invoice integration configs, reconciling financial transactions against CRM customer receivables, comparing procurement records with inventory changes, and producing a full anomaly reconciliation report."
---

# Month-End Comprehensive Reconciliation Skill

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View financial transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View customer list | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View all system integration configurations | `POST /config/integrations` | `{ "status": <string> }` |
| Get integration configuration details | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View inventory product list | `POST /inventory/products` | `{ "category": <string> }` |
| Get inventory product details | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List all scheduled tasks | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| Get scheduled task details (with execution history) | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |
| View scheduled task execution history | `POST /scheduler/jobs/history` | `{ "job_id": <string> (required), "limit": <integer> }` |

---
