# Password Vault API

Base URL: **http://localhost:9130**

## Endpoints

### GET /vault/schema

Returns available vault categories and their field requirements.

**Response 200:**

```json
{
  "categories": {
    "login": {
      "description": "Website or app login credentials",
      "required_fields": ["title", "username", "password"],
      "optional_fields": ["url", "totp_secret", "mfa_enabled", "notes", "tags", "priority", "last_rotated"]
    },
    "credit_card": {
      "description": "Credit or debit card",
      "required_fields": ["title", "cardholder", "card_number", "expiry", "cvv"],
      "optional_fields": ["pin", "bank", "notes", "tags", "priority", "last_rotated"]
    },
    "secure_note": {
      "description": "Encrypted free-form text for sensitive info",
      "required_fields": ["title", "content"],
      "optional_fields": ["notes", "tags", "priority", "last_rotated"]
    },
    "api_credential": {
      "description": "API keys, tokens, or service-account secrets",
      "required_fields": ["title", "credential_value"],
      "optional_fields": ["service_name", "environment", "notes", "tags", "priority", "last_rotated"]
    }
  },
  "policy_notes": "..."
}
```

### POST /vault/items

Create a new vault item.

**Request body (JSON):**

```json
{
  "category": "login",
  "fields": {
    "title": "GitHub Personal",
    "username": "alexgreen-dev",
    "password": "...",
    "url": "https://github.com",
    "tags": ["development"],
    "mfa_enabled": true,
    "totp_secret": "..."
  }
}
```

**Response 201:**

```json
{
  "status": "created",
  "item_id": "vault_001",
  "category": "login"
}
```

**Error 400:** unknown category.
**Error 422:** missing required fields.

### GET /vault/items

List all vault items (summary only — no secret fields).

**Response 200:**

```json
{
  "items": [
    {"id": "vault_001", "category": "login", "title": "GitHub Personal", "created_at": "..."}
  ],
  "total": 1
}
```

### GET /vault/audit

Full audit log (for automated grading only — do not call during migration).
