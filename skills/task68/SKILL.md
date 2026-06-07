---
name: task68
description: "会议行动项提取与去重. Use when: 需要从会议纪要中提取行动项并与现有待办进行去重匹配, 涉及会议纪要查阅和待办管理."
---

# 会议行动项提取与去重 Skill

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
