---
name: task58
description: "Customer Complaint Multi-Source Investigation and Recommendations. Use when: investigating customer complaint emails, cross-referencing CRM and finance records, and drafting reply emails."
---

# Customer Complaint Multi-Source Investigation and Recommendations Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get inbox email list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get email details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save email draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| Send email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
