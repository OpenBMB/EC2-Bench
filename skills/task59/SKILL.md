---
name: task59
description: "报销邮件核对. Use when: 需要从邮箱提取报销申请信息，与财务系统交易记录交叉核对，生成核对报告."
---

# 报销邮件核对 Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详情 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看交易记录 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
