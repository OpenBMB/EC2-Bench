---
name: task53
description: "Pinbench Second Brain Recall. Use when: retrieving stored memory notes to answer factual recall questions from a personal knowledge base."
---

# Pinbench Second Brain Recall Skill

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List notes | `POST /notes/list` | `{ "max_results": <integer> }` |
| Get one note | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---
