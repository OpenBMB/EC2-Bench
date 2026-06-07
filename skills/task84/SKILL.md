---
name: task84
description: "Q1季度业务洞察与趋势分析. Use when: 综合财务交易、CRM客户信息和行业RSS动态，分析Q1收入趋势并生成业务洞察报告."
---

# Q1季度业务洞察与趋势分析 Skill

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
| 列出客户 | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| 获取客户详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出RSS订阅源 | `POST /rss/feeds` | `{}` |
| 获取RSS源文章列表 | `POST /rss/feeds/get` | `{ "feed_id": <string> (required) }` |
| 获取文章详情 | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---
