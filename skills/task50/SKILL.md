---
name: task50
description: "Pinbench Config Workflow Notes. Use when: need to inspect service integrations and write Python client workflow documentation, involving integration listing and detailed configuration review."
---

# Pinbench Config Workflow Notes Skill

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List integrations | `POST /config/integrations` | `{ "status": <string> (optional, filter by status) }` |
| Get one integration | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---
