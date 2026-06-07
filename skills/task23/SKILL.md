---
name: task23
description: "Analyzes Slack messages to assess deal or project feasibility. Use when: synthesizing fragmented stakeholder input on a deal, identifying conflicting requirements or risks, or producing a feasibility report for leadership."
---

# Slack Deal Analyzer Skill

Read recent Slack messages about a specific deal or project, synthesize conflicting inputs, and produce a clear feasibility assessment.

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
2. **Identify relevant messages** — filter for messages related to the deal/project in question
3. **Read each in full** — retrieve complete content via `slack_get_message`
4. **Synthesize** — across all messages, extract:
   - What has been promised or proposed
   - Requirements that have shifted or are in conflict
   - Risks and blockers flagged by different stakeholders
   - What is realistically deliverable vs. what will likely fail
5. **Write report** — save findings to `/tmp_workspace/results/results.md`

---

## Report Format

```markdown
# Deal Feasibility Assessment: [Deal Name]

## What's Been Committed / Proposed
- ...

## Shifting or Conflicting Requirements
- ...

## Risks & Likely Failure Points
- ...

## Realistic Deliverability
- What we can deliver: ...
- What we cannot deliver: ...

## Recommended Stance
...
```

---

## Constraints

- Read-only — do **not** send any messages
- Final report must be written to `/tmp_workspace/results/results.md`