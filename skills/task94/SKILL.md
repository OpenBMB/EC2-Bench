---
name: task94
description: "综合市场研究报告. Use when: 从邮件获取报告要求，综合RSS行业动态、知识库内部分析和CRM客户数据，撰写Q1市场研究报告并起草管理层邮件草稿."
---

# 综合市场研究报告 Skill

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

## CRM API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出客户 | `POST /crm/customers` | `{ "status": <string>, "tier": <string>, "industry": <string> }` |
| 获取客户详情 | `POST /crm/customers/get` | `{ "customer_id": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索内部通讯录 | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| 获取联系人详情 | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

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
