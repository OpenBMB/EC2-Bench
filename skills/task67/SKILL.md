---
name: task67
description: "AI Industry Briefing Compilation. Use when: 需要从RSS源筛选AI/大模型相关文章, 按主题分类整理并以邮件草稿形式保存简报."
---

# 行业简报整理 Skill

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取RSS文章列表 | `POST /rss/articles` | `{ "feed_name": <string>, "category": <string>, "max_results": <integer> }` |
| 获取文章详情 | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详情 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| 保存邮件草稿 | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| 发送邮件 | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---
