---
name: task89
description: "Monthly System Health Check. Use when: inspecting API integration status, scheduled job health, and open tickets to correlate fault chains and generate a health report."
---

# Monthly System Health Check Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List tickets | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| Get ticket details | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| Update a ticket | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array> }` |
| Close a ticket | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List scheduled jobs | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| Get scheduled job details | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List system integration configurations | `POST /config/integrations` | `{ "type": <string>, "status": <string> }` |
| Get integration configuration details | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search knowledge base articles | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| Get full knowledge base article | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---
