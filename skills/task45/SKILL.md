---
name: task45
description: "Ambiguous Contact Email. Use when: need to check inbox and send email notifications based on contact lookups, involving contact search, disambiguation and email operations."
---

# Ambiguous Contact Email Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get the list of emails in the inbox | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get detailed email content by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Send an email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |
| Save an email as a draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search for contacts in the address book | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get detailed contact information | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---
