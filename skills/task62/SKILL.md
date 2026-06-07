---
name: task62
description: "Meeting Preparation Materials. Use when: compiling attendee details and schedules for upcoming meetings by cross-referencing calendar events with the contacts directory."
---

# Meeting Preparation Materials Skill

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
