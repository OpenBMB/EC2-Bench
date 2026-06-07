---
name: task80
description: "补货链路检查. Use when: 需要排查自动补货系统故障链路, 涉及定时任务状态检查、API集成配置诊断和库存水位分析."
---

# 补货链路检查 Skill

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出定时任务 | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| 获取定时任务详情 | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看集成配置列表 | `POST /config/integrations` | `{ "status": <string> }` |
| 获取集成配置详情 | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看库存列表 | `POST /inventory/products` | `{ "category": <string> }` |
| 获取库存品详情 | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---
