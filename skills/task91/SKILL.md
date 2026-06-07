---
name: task91
description: "Parallel Project Progress Report Generation. Use when: synthesizing calendar events, meeting notes, to-do items, and team contacts to produce a multi-project progress report covering milestones, risks, and action items."
---

# Parallel Project Progress Report Generation Skill

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View calendar events | `POST /calendar/events` | `{ "start_date": <string>, "end_date": <string> }` |
| Get event details | `POST /calendar/events/get` | `{ "event_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List meeting notes | `POST /notes/list` | `{ "tag": <string> }` |
| Get meeting note details | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Todo API

**Base URL:** `http://localhost:9102`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| View to-do list | `POST /todo/tasks` | `{ "status": <string> }` |
| Get to-do item details | `POST /todo/tasks/get` | `{ "task_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search internal directory | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get contact details | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
