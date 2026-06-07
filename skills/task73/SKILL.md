---
name: task73
description: "Business Trip Planning. Use when: Get inbox email list, Get email details by message ID, List calendar events."
---

# Business Trip Planning Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get inbox email list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get email details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List calendar events | `POST /calendar/events` | `{ "start_date": <string>, "end_date": <string> }` |
| Get event details | `POST /calendar/events/get` | `{ "event_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal contacts directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
