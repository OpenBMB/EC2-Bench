---
name: task52
description: "Email Inbox Triage Report. Use when: triaging an overflowing inbox by reading messages, assigning priority levels, categorizing items, and producing a sorted daily action plan."
---

# Pinbench Email Triage Report Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inbox messages | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get one message | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---
