---
name: task38
description: "Cross-Service Customer Escalation Triage with Budget Constraints. Use when: 获取收件箱中的邮件列表, 根据邮件ID获取邮件详细内容, 发送邮件."
---

# Cross-Service Customer Escalation Triage with Budget Constraints Skill


## API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱中的邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详细内容 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| 将邮件保存为草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| 搜索通讯录中的联系人 | `POST http://localhost:9103/contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详细信息 | `POST http://localhost:9103/contacts/get` | `{ "contact_id": <string> (required) }` |
| 获取工单列表 | `POST http://localhost:9107/helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详细信息 | `POST http://localhost:9107/helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 更新工单信息 | `POST http://localhost:9107/helpdesk/tickets/update` | `{ "ticket_id": <string> (required), "priority": <string>, "tags": <array>, "category": <string> }` |
| 获取客户列表 | `POST http://localhost:9110/crm/customers` | `{ "status": <string>, "tier": <string>, "industry": <string> }` |
| 获取客户详细信息 | `POST http://localhost:9110/crm/customers/get` | `{ "customer_id": <string> (required) }` |
| 获取财务交易记录列表 | `POST http://localhost:9104/finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取财务交易详情 | `POST http://localhost:9104/finance/transactions/get` | `{ "transaction_id": <string> (required) }` |
