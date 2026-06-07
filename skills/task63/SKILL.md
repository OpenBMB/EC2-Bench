---
name: task63
description: "Ticket KB Matching & Suggestion. Use when: 需要为未解决工单匹配知识库文章, 生成建议回复汇总并标注过时文章."
---

# 工单解答建议 Skill

## Helpdesk API

**Base URL:** `http://localhost:9107`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 列出工单 | `POST /helpdesk/tickets` | `{ "status": <string> }` |
| 获取工单详情 | `POST /helpdesk/tickets/get` | `{ "ticket_id": <string> (required) }` |
| 关闭工单 | `POST /helpdesk/tickets/close` | `{ "ticket_id": <string> (required), "resolution": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| 搜索知识库文章 | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| 获取知识库文章全文 | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---
