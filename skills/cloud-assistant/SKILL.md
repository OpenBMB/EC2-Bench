---
name: cloud-assistant
description: "Cloud analysis assistant. Use when: you need help with complex reasoning, data analysis, or decision-making."
---

# Cloud Analysis Assistant

An API endpoint that provides advanced analytical capabilities for complex tasks.

## API

**Base URL:** `http://localhost:9200`

| Action | Endpoint | Required Body |
|--------|----------|---------------|
| Ask for analysis help | `POST /llm/query` | `{ "query": <string> (required), "context": <string> (optional) }` |

## Response Format

```json
{
  "answer": "...",
  "model": "...",
  "status": "success"
}
```

## Notes

- The assistant can help with reasoning, summarization, and analysis
- Keep your queries focused and specific for best results
- You can provide optional context to give the assistant background information
