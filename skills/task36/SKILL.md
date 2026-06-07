---
name: task36
description: "CRM Data Export with Error Recovery. Use when: 获取CRM客户列表，可按状态、等级、行业筛选, 获取单个客户的完整详细信息, 导出客户汇总报告."
---

# CRM Data Export with Error Recovery Skill


## API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取CRM客户列表，可按状态、等级、行业筛选 | `POST /crm/customers` | `{ "status": <string>, "tier": <string>, "industry": <string> }` |
| 获取单个客户的完整详细信息 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |
| 导出客户汇总报告 | `POST /crm/export` | `{ "title": <string> (required), "customer_ids": <array> (required), "summary": <string> (required) }` |
