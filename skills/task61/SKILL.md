---
name: task61
description: "会议准备材料整理. Use when: 需要整理明天的会议安排, 汇总参会者信息并在通讯录中匹配联系方式和职位, 标注外部参会者."
---

# 会议准备材料整理 Skill

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看日历事件 | `POST /calendar/events` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取事件详情 | `POST /calendar/events/get` | `{ "event_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索内部通讯录 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详情 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
