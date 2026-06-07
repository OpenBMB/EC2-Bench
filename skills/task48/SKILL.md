---
name: task48
description: "Incident Postmortem — Root Cause Analysis. Use when: 需要对生产环境事故进行复盘分析, 涉及工单收集、系统集成状态、邮件技术细节、知识库历史、会议记录和定时任务检查."
---

# Incident Postmortem — Root Cause Analysis Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取工单列表 | `POST /helpdesk/tickets` | `{ "status": <string>, "priority": <string>, "date_from": <string>, "date_to": <string> }` |
| 获取单个工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |

---

## Config API

**Base URL:** `http://localhost:9111`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取所有第三方集成的列表（概览信息） | `POST /config/integrations` | `{ "status": <string> }` |
| 获取单个集成的完整配置信息 | `POST /config/integrations/get` | `{ "integration_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取收件箱中的邮件列表 | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| 根据邮件ID获取邮件详细内容 | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章详情 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---

## Notes API

**Base URL:** `http://localhost:9105`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 获取笔记/会议记录列表 | `POST /notes/list` | `{ "date_from": <string>, "date_to": <string>, "tag": <string> }` |
| 获取笔记/会议记录详情 | `POST /notes/get` | `{ "note_id": <string> (required) }` |

---

## Scheduler API

**Base URL:** `http://localhost:9112`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出所有定时任务 | `POST /scheduler/jobs` | `{ "status": <string>, "enabled": <boolean>, "tag": <string> }` |
| 获取定时任务详情 | `POST /scheduler/jobs/get` | `{ "job_id": <string> (required) }` |
| 查看定时任务执行历史 | `POST /scheduler/jobs/history` | `{ "job_id": <string> (required), "limit": <integer> }` |

---
