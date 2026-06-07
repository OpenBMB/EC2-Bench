---
name: task92
description: "供应链异常追查与问题链还原. Use when: 需要跨系统追查供应链中断根因并还原完整问题链, 涉及工单分析、库存排查、采购财务核查和供应商CRM状态诊断."
---

# 供应链异常追查与问题链还原 Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出工单 | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 更新工单（优先级、标签、分类） | `POST /helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array>, "category": <string> }` |
| 关闭工单 | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看库存列表 | `POST /inventory/products` | `{ "category": <string> }` |
| 获取库存品详情 | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看交易记录 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出客户和供应商 | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| 获取客户或供应商详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---
