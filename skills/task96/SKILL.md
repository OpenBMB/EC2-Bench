---
name: task96
description: "月末全面对账. Use when: 检查对账/发票自动化任务状态，验证支付和发票系统集成配置，核对财务流水与CRM客户应收款，比对采购记录与库存变动，发现重复扣款、短收、金额不符等异常并输出完整对账报告."
---

# 月末全面对账 Skill

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看财务交易记录 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看客户列表 | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| 获取客户详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看所有系统集成配置 | `POST /config/integrations` | `{ "status": <string> }` |
| 获取集成配置详情 | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Inventory API

**Base URL:** `http://localhost:9108`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看库存产品列表 | `POST /inventory/products` | `{ "category": <string> }` |
| 获取库存产品详情 | `POST /inventory/products/get` | `{ "product_id": <string> (required) }` |

---

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出所有定时任务 | `POST /scheduler/jobs` | `{ "status": <string>, "tag": <string> }` |
| 获取定时任务详情（含执行历史） | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |
| 查看定时任务执行历史 | `POST /scheduler/jobs/history` | `{ "job_id": <string> (required), "limit": <integer> }` |

---
