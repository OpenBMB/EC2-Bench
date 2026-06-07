---
name: task99
description: "多自动化任务连锁故障排查与恢复. Use when: 排查定时任务连锁失败，诊断API集成配置根因，关联工单影响，并制定恢复方案."
---

# 多自动化任务连锁故障排查与恢复 Skill

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出定时任务 | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| 获取定时任务详情（含执行历史和依赖关系） | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出API集成配置 | `POST /config/integrations` | `{ "status": <string> }` |
| 获取API集成配置详情 | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出库存产品 | `POST /inventory/products` | `{ "category": <string> }` |
| 获取产品库存详情 | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出工单 | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 更新工单（优先级、标签、分类） | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array>, "category": <string> }` |
| 关闭工单 | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章全文 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---
