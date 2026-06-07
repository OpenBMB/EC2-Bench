---
name: task35
description: "Meeting Notes Action Items. Use when: 获取会议记录列表, 根据ID获取会议记录详细内容, 将会议记录分享给其他人."
---

# Meeting Notes Action Items Skill


## API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取会议记录列表 | `POST /notes/list` | `{ "max_results": <integer> }` |
| 根据ID获取会议记录详细内容 | `POST /notes/get` | `{ "note_id": <string> (required) }` |
| 将会议记录分享给其他人 | `POST /notes/share` | `{ "note_id": <string> (required), "recipients": <array> (required) }` |
