---
name: task90
description: "并行项目进度汇报生成. Use when: 需要为多个并行项目生成进度报告, 涉及会议记录整理、待办事项状态对比和项目负责人联系方式查找."
---

# 并行项目进度汇报生成 Skill

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看日历事件 | `POST /calendar/events` | `{ "start_date": <string>, "end_date": <string> }` |
| 获取事件详情 | `POST /calendar/events/get` | `{ "event_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出会议纪要 | `POST /notes/list` | `{ "tag": <string> }` |
| 获取纪要详情 | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Todo API

**Base URL:** `http://localhost:9102`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 查看待办事项列表 | `POST /todo/tasks` | `{ "status": <string> }` |
| 获取待办详情 | `POST /todo/tasks/get` | `{ "task_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索内部通讯录 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详情 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
