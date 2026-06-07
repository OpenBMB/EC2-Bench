---
name: task19
description: "Extracts action items and pending tasks from Slack messages. Use when: reviewing unread messages for todos, finding deadlines or requests directed at the user, or producing a task summary from recent Slack activity."
---

# Slack Task Extractor Skill

Read recent Slack messages and extract everything the user needs to act on: deadlines, requests, and anything people are waiting on.

## Tools

All tools are defined in `tmp_workspace/utils.py`:

- **`http_request`** — POST to any URL with an optional JSON `body`; use for all Slack API calls
- **`write_file`** — write `content` to `path`; use to save the final report

---

## Slack API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List messages | `POST /slack/messages` | `{"days_back": 7, "max_results": 20}` (all optional) |
| Get message | `POST /slack/messages/get` | `{"message_id": "<id>"}` |

> ⚠️ This is a **read-only** task. Do **not** call `slack_send_message` (`POST /slack/send`).

---

## Workflow

1. **List messages** — fetch recent messages with `slack_list_messages`
2. **Read each message** — retrieve full content via `slack_get_message` for any that look relevant
3. **Extract action items** — identify anything requiring the user's response or action:
   - Direct requests or questions addressed to the user
   - Deadlines or time-sensitive items
   - Things people are waiting on the user for
4. **Write report** — save the full task list to `/tmp_workspace/results/results.md`

---

## Report Format

```markdown
# Pending Action Items

## [Sender] — [Date]
- **What's needed**: ...
- **Deadline**: ... (if mentioned)
- **Message ID**: msg_xxx
```

---

## Constraints

- Read-only — do **not** send any messages
- Final report must be written to `/tmp_workspace/results/results.md`
