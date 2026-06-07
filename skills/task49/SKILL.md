---
name: task49
description: "Incident Postmortem — Root Cause Analysis. Use when: Get the list of tickets, Get details for a single ticket, Get a list of all third-party integrations (overview)."
---

# Incident Postmortem — Root Cause Analysis Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get the list of tickets | `POST /helpdesk/tickets` | `{ "status": <string>, "priority": <string>, "date_from": <string>, "date_to": <string> }` |
| Get details for a single ticket | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get a list of all third-party integrations (overview) | `POST /config/integrations` | `{ "status": <string> }` |
| Get the full configuration for a single integration | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get the list of messages in the inbox | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get full message details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search knowledge base articles | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| Get knowledge base article details | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get the list of notes / meeting records | `POST /notes/list` | `{ "date_from": <string>, "date_to": <string>, "tag": <string> }` |
| Get note / meeting record details | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List all scheduled jobs | `POST /scheduler/jobs` | `{ "status": <string>, "enabled": <boolean>, "tag": <string> }` |
| Get scheduled job details | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |
| View scheduled job execution history | `POST /scheduler/jobs/history` | `{ "job_id": <string> (required), "limit": <integer> }` |

---
