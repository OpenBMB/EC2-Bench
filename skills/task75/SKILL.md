---
name: task75
description: "Order Profit Analysis. Use when: analyzing customer order profitability by cross-referencing CRM orders, inventory costs, and finance payment records."
---

# Order Profit Analysis Skill

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inventory/products | `POST /inventory/products` | `{ "category": <string> }` |
| Get product details | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List transactions | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
