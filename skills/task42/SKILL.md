---
name: task42
description: "Pinbench Project Alpha Summary. Use when: List inbox messages, Get one message."
---

# Pinbench Project Alpha Summary Skill


## API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List inbox messages | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get one message | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |
