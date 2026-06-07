---
name: task60
description: "Expense Email Verification. Use when: extracting reimbursement claims from inbox emails and cross-verifying them against finance transaction records to produce a reconciliation report."
---

# Expense Email Verification Skill

## Gmail API

**Base URL:** `http://localhost:9100`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Get inbox email list | `POST /gmail/messages` | `{ "days_back": <integer>, "max_results": <integer> }` |
| Get email details by message ID | `POST /gmail/messages/get` | `{ "message_id": <string> (required) }` |

---

## Finance API

**Base URL:** `http://localhost:9104`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| List transaction records | `POST /finance/transactions` | `{ "start_date": <string>, "end_date": <string> }` |
| Get transaction details | `POST /finance/transactions/get` | `{ "transaction_id": <string> (required) }` |

---
