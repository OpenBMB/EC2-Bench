"""Cloud LLM Proxy — audited relay to a cloud model (FastAPI on port 9200).

Accepts queries from the edge agent, forwards them to a cloud LLM via
OpenAI-compatible API, records every request to cloud_assistant_audit.jsonl
for privacy auditing and usage tracking.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Cloud LLM Proxy")

CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
CLOUD_BASE_URL = os.environ.get("CLOUD_BASE_URL") or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
CLOUD_MODEL = os.environ.get("CLOUD_MODEL", "gpt-5.4")

AUDIT_FILE = "/tmp/cloud_assistant_audit.jsonl"


class QueryRequest(BaseModel):
    query: str
    context: str | None = None


ENV_CONTEXT = os.environ.get("CLOUD_ENV_CONTEXT", "")


@app.post("/llm/query")
def llm_query(req: QueryRequest) -> dict[str, Any]:
    from openai import OpenAI

    system_parts = []
    if ENV_CONTEXT:
        system_parts.append(ENV_CONTEXT)
    if req.context:
        system_parts.append(req.context)

    messages = []
    if system_parts:
        messages.append({"role": "system", "content": "\n".join(system_parts)})
    messages.append({"role": "user", "content": req.query})

    client = OpenAI(api_key=CLOUD_API_KEY, base_url=CLOUD_BASE_URL)

    usage_info: dict[str, int] = {}
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=CLOUD_MODEL,
            messages=messages,
            max_tokens=2048,
        )
        answer = resp.choices[0].message.content or ""
        status = "success"
        if resp.usage:
            usage_info = {
                "input_tokens": resp.usage.prompt_tokens or 0,
                "output_tokens": resp.usage.completion_tokens or 0,
                "total_tokens": resp.usage.total_tokens or 0,
            }
    except Exception as e:
        answer = ""
        status = f"error: {e}"
    latency_ms = round((time.monotonic() - t0) * 1000)

    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": CLOUD_MODEL,
                "prompt_tokens": usage_info.get("input_tokens", 0),
                "completion_tokens": usage_info.get("output_tokens", 0),
                "total_tokens": usage_info.get("total_tokens", 0),
                "latency_ms": latency_ms,
                "query": req.query[:200],
                "context_messages": len(messages),
                "input": messages,
                "output": answer,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return {"answer": answer, "model": CLOUD_MODEL, "status": status}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("CLOUD_PROXY_PORT", "9200")))
