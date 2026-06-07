---
name: task81
description: "Customer Contract Expiry Warning and Renewal Follow-up. Use when: identifying customers with contracts expiring within 60 days, correlating CRM details with sales contacts to generate a renewal warning report."
---

# Customer Contract Expiry Warning and Renewal Follow-up Skill

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inbox messages | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get message details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save email draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| Send email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---
