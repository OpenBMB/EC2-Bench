---
name: task34
description: "Expense Report. Use when: 获取费用交易记录列表, 获取单笔交易详情, 提交费用报告."
---

# Expense Report Skill


## API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取费用交易记录列表 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取单笔交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |
| 提交费用报告 | `POST /finance/report/submit` | `{ "title": <string> (required), "transactions": <array> (required), "total_amount": <number> (required) }` |
