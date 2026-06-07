---
name: task79
description: "Weekly Meeting Action Item Tracking. Use when: extracting action items from calendar meetings and notes, cross-referencing todo completion status, and generating a grouped weekly progress report."
---

# Weekly Meeting Action Item Tracking Skill

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List calendar events | `POST /calendar/events` | `{ "start_date": <string>, "end_date": <string> }` |
| Get event details | `POST /calendar/events/get` | `{ "event_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List meeting minutes | `POST /notes/list` | `{ "tag": <string> }` |
| Get meeting minutes details | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Todo API

**Base URL:** `http://localhost:9102`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List to-do items | `POST /todo/tasks` | `{ "status": <string> }` |
| Get to-do item details | `POST /todo/tasks/get` | `{ "task_id": <string> (required) }` |

---
