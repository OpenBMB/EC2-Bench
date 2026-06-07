---
name: task71
description: "Ticket Assignment. Use when: routing unassigned helpdesk tickets to appropriate handlers by matching issue types against department contacts and producing an assignment recommendation table."
---

# Ticket Assignment Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List tickets | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| Get ticket details | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal contacts directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
