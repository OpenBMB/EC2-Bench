---
name: task77
description: "Collect latest competitor updates from RSS feeds, knowledge base historical analyses, and inbox emails; synthesize a structured competitive intelligence report covering Competitor A and Competitor B, and prepare an email draft for management."
---

# Competitive Intelligence Report Skill

## RSS API

**Base URL:** `http://localhost:9109`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get RSS article list | `POST /rss/articles` | `{ "feed_name": <string>, "category": <string>, "max_results": <integer> }` |
| Get article details | `POST /rss/articles/get` | `{ "article_id": <string> (required) }` |

---

## Knowledge Base API

**Base URL:** `http://localhost:9106`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search knowledge base articles | `POST /kb/search` | `{ "query": <string> (required), "category": <string>, "max_results": <integer> }` |
| Get full knowledge base article | `POST /kb/articles/get` | `{ "article_id": <string> (required) }` |

---

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get inbox email list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get email details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save email draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| Send email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---
