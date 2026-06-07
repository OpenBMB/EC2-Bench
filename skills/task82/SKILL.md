---
name: task82
description: "SLA合规审查与自动化诊断. Use when: 需要审查工单SLA合规性并诊断自动化告警故障, 涉及工单响应时间分析、定时任务状态检查和集成配置诊断."
---

# SLA合规审查与自动化诊断 Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出工单 | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 更新工单 | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array> }` |
| 关闭工单 | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

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
| 列出系统集成配置 | `POST /config/integrations` | `{ "type": <string>, "status": <string> }` |
| 获取集成配置详情 | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---
