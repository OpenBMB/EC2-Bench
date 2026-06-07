---
name: task44
description: "Ambiguous Contact Email. Use when: 需要查收邮件并根据联系人信息发送邮件通知, 涉及联系人搜索、歧义消解和邮件操作."
---

# Ambiguous Contact Email Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱中的邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详细内容 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| 将邮件保存为草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索通讯录中的联系人 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详细信息 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
