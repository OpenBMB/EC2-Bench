---
name: task69
description: "Meeting Action Item Extraction and Deduplication. Use when: need to extract action items from meeting notes and deduplicate against existing to-dos, involving meeting note review and task management."
---

# Meeting Action Item Extraction and Deduplication Skill

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List meeting notes | `POST /notes/list` | `{ "tag": <string> }` |
| Get note details | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Todo API

**Base URL:** `http://localhost:9102`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List to-do items | `POST /todo/tasks` | `{ "status": <string> }` |
| Get to-do item details | `POST /todo/tasks/get` | `{ "task_id": <string> (required) }` |

---
