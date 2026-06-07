---
name: task87
description: "Read follow-up emails, correlate them with to-do items and meeting notes to determine task status and blocking reasons, then draft targeted progress replies for each follow-up."
---

# Follow-up Email Backtracking and Progress Reply Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get inbox email list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get email details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save an email draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| Send an email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---

## Todo API

**Base URL:** `http://localhost:9102`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List to-do items | `POST /todo/tasks` | `{ "status": <string> }` |
| Get to-do item details | `POST /todo/tasks/get` | `{ "task_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List meeting notes | `POST /notes/list` | `{ "days_back": <integer> }` |
| Get meeting note details | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---
