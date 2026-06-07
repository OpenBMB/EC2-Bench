---
name: task46
description: "Cross-Service Meeting Coordination. Use when: 需要协调跨部门会议安排, 涉及邮件阅读、联系人查询、日程检查和会议创建."
---

# Cross-Service Meeting Coordination Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱中的邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详细内容 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 将邮件保存为草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索通讯录中的联系人 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详细信息 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取指定日期范围内的日历事件 | `POST /calendar/events` | `{ "date": <string> (required), "days": <integer> }` |
| 获取指定用户在指定日期的日程 | `POST /calendar/user_events` | `{ "user": <string> (required), "date": <string> (required) }` |
| 创建新的日历事件 | `POST /calendar/events/create` | `{ "title": <string> (required), "start_time": <string> (required), "end_time": <string> (required), "attendees": <array> (required), "location": <string> }` |
| 删除日历事件 | `POST /calendar/events/delete` | `{ "event_id": <string> (required) }` |

---
