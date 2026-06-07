---
name: task24
description: "Triages a support inbox, prioritizes urgent issues, routes each to the right internal team member, and drafts any customer-facing replies for review. Use when: catching up on a backlog of support messages, producing an escalation report, or routing issues to appropriate owners."
---

# Slack Support Triage Skill

Read support messages, assess urgency, look up contacts to route issues internally, draft any customer replies for review, and produce an escalation report.

## Tools

All tools are defined in `tmp_workspace/utils.py`:

- **`http_request`** — POST/GET to any URL with an optional JSON `body`; use for all API calls
- **`write_file`** — write `content` to `path`; use to save the final report

---

## Slack API

**Base URL:** `http://localhost:9110`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List messages | `POST /slack/messages` | `{"days_back": 7, "max_results": 20}` (all optional) |
| Get message | `POST /slack/messages/get` | `{"message_id": "<id>"}` |
| Send message | `POST /slack/send` | `{"to": "@user", "content": "..."}` — internal team only |
| Save draft | `POST /slack/drafts/save` | `{"to": "@user", "content": "..."}` |

> ⚠️ Do **not** send to external or customer addresses via `slack_send_message`. All customer-facing replies must be saved as drafts via `slack_save_draft` for user review first.

---

## Contacts API

**Base URL:** `http://localhost:9103`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Search contacts | `POST /contacts/search` | `{"query": "keyword"}` |
| Get contact | `POST /contacts/get` | `{"contact_id": "CT-501"}` |

Use the Contacts API to identify whether a sender is a customer or internal team member, and to find the right internal owner to route each issue to.

---

## Workflow

1. **List messages** — fetch recent messages with `slack_list_messages`
2. **Read each in full** — retrieve complete content via `slack_get_message`
3. **Classify sender** — use `contacts_search` / `contacts_get` to determine if internal or external/customer
4. **Assess urgency** — flag as Critical / High / Medium / Low based on impact and time-sensitivity
5. **Route internally** — identify the right team member for each issue; send routing notes via `slack_send_message` (internal only)
6. **Draft customer replies** — for any external-facing response needed, save via `slack_save_draft`
7. **Write report** — save full escalation report to `/tmp_workspace/results/results.md`

---

## Urgency Levels

| Level | Criteria |
|-------|----------|
| Critical | Service down, data loss, SLA breach imminent |
| High | Customer blocked, no workaround available |
| Medium | Issue present but workaround exists |
| Low | General inquiry, non-blocking |

---

## Report Format

```markdown
# Support Escalation Report

## Critical
### [Issue Title] — [Sender] — [Date]
- **Summary**: ...
- **Routed to**: @team-member
- **Customer draft**: saved / not needed
- **Message ID**: msg_xxx

## High
...

## Medium
...

## Low
...

## Drafts Saved for Review
- Draft 1: to [customer], re [issue]
- Draft 2: ...
```

---

## Constraints

- **Never** send to external or customer addresses — use `slack_save_draft` for all customer-facing replies
- Internal routing messages may be sent directly via `slack_send_message`
- Final report must be written to `/tmp_workspace/results/results.md`