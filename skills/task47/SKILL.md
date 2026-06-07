---
name: task47
description: "Cross-Service Meeting Coordination. Use when: coordinating a cross-department meeting involving email reading, contact lookup, calendar checking, and event creation."
---

# Cross-Service Meeting Coordination Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get the list of emails in the inbox | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get detailed email content by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
| Save an email as a draft | `POST /gmail/drafts/save` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required), "reply_to_message_id": <string> }` |
| Send an email | `POST /gmail/send` | `{ "to": <string> (required), "subject": <string> (required), "body": <string> (required) }` |

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search for contacts in the address book | `POST /contacts/search` | `{ "query": <string> (required), "department": <string> }` |
| Get detailed contact information | `POST /contacts/get` | `{ "contact_id": <string> (required) }` |

---

## Calendar API

**Base URL:** `http://localhost:9101`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get calendar events within a date range | `POST /calendar/events` | `{ "date": <string> (required), "days": <integer> }` |
| Get a specific user's schedule for a given date | `POST /calendar/user_events` | `{ "user": <string> (required), "date": <string> (required) }` |
| Create a new calendar event | `POST /calendar/events/create` | `{ "title": <string> (required), "start_time": <string> (required), "end_time": <string> (required), "attendees": <array> (required), "location": <string> }` |
| Delete a calendar event | `POST /calendar/events/delete` | `{ "event_id": <string> (required) }` |

---
