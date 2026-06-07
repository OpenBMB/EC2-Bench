---
name: task93
description: "Supply Chain Disruption Investigation and Problem Chain Reconstruction. Use when: need to investigate cross-system supply chain disruptions and reconstruct the complete problem chain, involving ticket analysis, inventory checks, procurement/finance verification, and supplier CRM status diagnosis."
---

# Supply Chain Disruption Investigation and Problem Chain Reconstruction Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List tickets | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| Get ticket details | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| Update ticket (priority, tags, category) | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array>, "category": <string> }` |
| Close a ticket | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inventory items | `POST /inventory/products` | `{ "category": <string> }` |
| Get inventory item details | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List financial transactions | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers and suppliers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer or supplier details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---
