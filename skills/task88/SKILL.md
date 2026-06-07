---
name: task88
description: "月度系统健康巡检. Use when: 需要检查API集成状态、定时任务运行情况和未解决工单, 关联故障链并生成健康报告."
---

# 月度系统健康巡检 Skill

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

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章全文 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---
