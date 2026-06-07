---
name: task100
description: "Multi-Automation Cascade Failure Investigation and Recovery. Use when: investigating cascading failures across scheduled jobs, diagnosing API integration root causes, correlating helpdesk tickets, and producing a recovery plan."
---

# Multi-Automation Cascade Failure Investigation and Recovery Skill

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List scheduled jobs | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| Get scheduled job details (including execution history and dependencies) | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List API integration configurations | `POST /config/integrations` | `{ "status": <string> }` |
| Get API integration configuration details | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inventory products | `POST /inventory/products` | `{ "category": <string> }` |
| Get product inventory details | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List tickets | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| Get ticket details | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| Update ticket (priority, tags, category) | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array>, "category": <string> }` |
| Close ticket | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search knowledge base articles | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| Get full knowledge base article | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---
