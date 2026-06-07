---
name: task39
description: "Ops Review Dashboard — Cross-System Anomaly Correlation. Use when: 获取所有第三方集成的列表（概览信息）, 获取单个集成的完整配置信息, 获取工单列表."
---

# Ops Review Dashboard — Cross-System Anomaly Correlation Skill


## API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取所有第三方集成的列表（概览信息） | `POST http://localhost:9111/config/integrations` | `{ "status": <string> }` |
| 获取单个集成的完整配置信息 | `POST http://localhost:9111/config/integrations/get` | `{ "integration_id": <string> (required) }` |
| 获取工单列表 | `POST http://localhost:9107/helpdesk/tickets` | `{ "status": <string>, "priority": <string>, "category": <string> }` |
| 获取单个工单的详细信息 | `POST http://localhost:9107/helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 获取商品库存列表 | `POST http://localhost:9108/inventory/products` | `{ "category": <string> }` |
| 获取单个商品的详细库存信息 | `POST http://localhost:9108/inventory/products/get` | `{ "product_id": <string> (required) }` |
| 获取客户列表 | `POST http://localhost:9110/crm/customers` | `{ "tier": <string>, "status": <string> }` |
| 获取单个客户的详细信息 | `POST http://localhost:9110/crm/customers/get` | `{ "customer_id": <string> (required) }` |
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章详情 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |
| 列出所有定时任务 | `POST http://localhost:9112/scheduler/jobs` | `{ "status": <string>, "enabled": <boolean> }` |
| 获取定时任务详情 | `POST http://localhost:9112/scheduler/jobs/get` | `{ "job_id": <string> (required) }` |
| 查看定时任务执行历史 | `POST http://localhost:9112/scheduler/jobs/history` | `{ "job_id": <string> (required), "limit": <integer> }` |
