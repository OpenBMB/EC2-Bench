---
name: task51
description: "Pinbench Config Change Plan. Use when: reviewing integration configurations to assess risk levels and propose production hardening changes."
---

# Pinbench Config Change Plan Skill

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List integrations | `POST /config/integrations` | `{ "status": <string> (optional) }` |
| Get one integration | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---
