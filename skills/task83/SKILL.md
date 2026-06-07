---
name: task83
description: "SLA Compliance Audit and Automation Diagnosis. Use when: need to audit ticket SLA compliance and diagnose automation alert failures, involving response time analysis, scheduled job status checks, and integration configuration diagnosis."
---

# SLA Compliance Audit and Automation Diagnosis Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List tickets | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| Get ticket details | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| Update ticket | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array> }` |
| Close ticket | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

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
