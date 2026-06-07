---
name: task41
description: "Onboarding Coordinator. Use when: 获取收件箱中的邮件列表, 根据邮件ID获取邮件详细内容, 发送邮件."
---

# Onboarding Coordinator Skill


## API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱中的邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详细内容 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| 将邮件保存为草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| 获取指定日期范围内的日历事件 | `POST http://localhost:9101/calendar/events` | `{ "date": <string> (required), "days": <integer> }` |
| 获取指定用户在指定日期的日程 | `POST http://localhost:9101/calendar/user_events` | `{ "user": <string> (required), "date": <string> (required) }` |
| 创建新的日历事件 | `POST http://localhost:9101/calendar/events/create` | `{ "title": <string> (required), "start_time": <string> (required), "end_time": <string> (required), "attendees": <array> (required), "location": <string> }` |
| 获取待办事项列表 | `POST http://localhost:9102/todo/tasks` | `{ "status": <string>, "priority": <string> }` |
| 创建新的待办事项 | `POST http://localhost:9102/todo/tasks/create` | `{ "title": <string> (required), "description": <string>, "priority": <string>, "due_date": <string>, "tags": <array> }` |
| 更新待办事项 | `POST http://localhost:9102/todo/tasks/update` | `{ "task_id": <string> (required), "status": <string>, "priority": <string>, "title": <string>, "description": <string> }` |
| 搜索通讯录中的联系人 | `POST http://localhost:9103/contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详细信息 | `POST http://localhost:9103/contacts/get` | `{ "contact_id": <string> (required) }` |
| 搜索知识库文章 | `POST http://localhost:9106/kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章详情 | `POST http://localhost:9106/kb/articles/get` | `{ "article_id": <string> (required) }` |
| 获取库存产品列表 | `POST http://localhost:9108/inventory/products` | `{ "category": <string>, "status": <string> }` |
| 获取产品库存详情 | `POST http://localhost:9108/inventory/products/get` | `{ "product_id": <string> (required) }` |
