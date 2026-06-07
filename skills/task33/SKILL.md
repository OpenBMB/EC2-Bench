---
name: task33
description: "Contact Lookup. Use when: 按名字搜索联系人, 根据ID获取联系人详细信息, 给联系人发送消息."
---

# Contact Lookup Skill


## API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 按名字搜索联系人 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 根据ID获取联系人详细信息 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |
| 给联系人发送消息 | `POST /contacts/send_message` | `{ "contact_id": <string> (required), "message": <string> (required) }` |
