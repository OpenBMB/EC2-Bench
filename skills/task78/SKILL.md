---
name: task78
description: "周会行动项跟踪. Use when: 从日历和会议纪要中提取行动项，与待办系统交叉比对状态，生成按会议分组的周进度报告."
---

# 周会行动项跟踪 Skill

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
