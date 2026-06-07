---
name: task70
description: "工单归属分配. Use when: 根据工单类型和部门匹配处理人，结合通讯录生成工单分配建议表."
---

# 工单归属分配 Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出工单 | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索内部通讯录 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详情 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
