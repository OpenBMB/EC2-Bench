---
name: task74
description: "订单利润分析. Use when: 需要分析客户订单利润率, 结合CRM订单、库存成本和财务收款数据进行交叉核算."
---

# 订单利润分析 Skill

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出客户 | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| 获取客户详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看库存/产品列表 | `POST /inventory/products` | `{ "category": <string> }` |
| 获取产品详情 | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看交易记录 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
