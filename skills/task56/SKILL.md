---
name: task56
description: "Customer Ticket Intelligent Routing and Escalation. Use when: need to evaluate ticket priority based on customer tier and contract status for intelligent routing, involving ticket management, knowledge base search, and CRM customer lookup."
---

# Customer Ticket Intelligent Routing and Escalation Skill

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

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List customers | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| Get customer details | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---
