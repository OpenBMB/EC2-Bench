---
name: task76
description: "竞品情报收集与分析. Use when: 需要从RSS源、知识库和邮箱收集竞品动态与历史分析, 整合输出结构化竞品情报报告并准备管理层邮件草稿."
---

# 竞品情报整理 Skill

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取RSS文章列表 | `POST /rss/articles` | `{ "feed_name": <string>, "category": <string>, "max_results": <integer> }` |
| 获取文章详情 | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章全文 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

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
