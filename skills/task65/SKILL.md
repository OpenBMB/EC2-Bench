---
name: task65
description: "Customer Follow-up Reminders. Use when: identifying active customers overdue for contact and drafting tier-based follow-up emails using CRM and inbox records."
---

# Customer Follow-up Reminders Skill

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
| Get inbox message list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get message details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save email draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| Send email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---
