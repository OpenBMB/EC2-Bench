---
name: task57
description: "客户投诉多源调查与处理建议. Use when: 需要调查客户投诉邮件, 结合CRM和财务系统交叉验证, 并起草回复邮件."
---

# 客户投诉多源调查与处理建议 Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详情 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 保存邮件草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索内部通讯录 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详情 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出客户 | `POST /crm/customers` | `{ "status": <string>, "tier": <string> }` |
| 获取客户详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看交易记录 | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取交易详情 | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
