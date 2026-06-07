---
name: task40
description: "Vendor Procurement Evaluation. Use when: 获取商品库存列表, 获取单个商品的详细库存信息, 获取文章列表."
---

# Vendor Procurement Evaluation Skill


## API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取商品库存列表 | `POST http://localhost:9108/inventory/products` | `{ "category": <string> }` |
| 获取单个商品的详细库存信息 | `POST http://localhost:9108/inventory/products/get` | `{ "product_id": <string> (required) }` |
| 获取文章列表 | `POST http://localhost:9109/rss/articles` | `{ "source": <string>, "category": <string>, "max_results": <integer> }` |
| 获取文章完整内容 | `POST http://localhost:9109/rss/articles/get` | `{ "article_id": <string> (required) }` |
| 获取CRM客户列表，可按状态、等级、行业筛选 | `POST http://localhost:9110/crm/customers` | `{ "status": <string>, "tier": <string>, "industry": <string> }` |
| 获取单个客户的完整详细信息 | `POST http://localhost:9110/crm/customers/get` | `{ "customer_id": <string> (required) }` |
| 搜索知识库文章 | `POST http://localhost:9106/kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章详情 | `POST http://localhost:9106/kb/articles/get` | `{ "article_id": <string> (required) }` |
| 获取费用交易记录列表 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取单笔交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |
| 提交财务报告 | `POST /finance/report/submit` | `{ "title": <string> (required), "content": <string> (required), "total_amount": <number>, "report_type": <string> }` |
