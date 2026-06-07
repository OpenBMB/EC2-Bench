---
name: task66
description: "Inventory Cost Analysis. Use when: cross-verifying inventory unit costs against procurement transaction records to identify price discrepancies and batch pricing anomalies."
---

# Inventory Cost Analysis Skill

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View inventory list | `POST /inventory/products` | `{ "category": <string> }` |
| Get inventory item details | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
