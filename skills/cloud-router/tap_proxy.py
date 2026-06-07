"""Tap proxy — fully transparent OpenAI-compatible relay used to inspect how
OpenClaw assembles `messages[]` across an agent run.

It listens on TAP_PORT (default 8888), forwards every request to TAP_UPSTREAM_URL
(default https://yeysai.com/v1) using TAP_UPSTREAM_KEY, and dumps a structured
record of every incoming request to TAP_LOG_FILE (default /tmp/tap_log.jsonl).

Streaming requests are streamed through unchanged. The proxy intentionally does
*not* mutate request or response bodies; the only side-effect is the JSONL log.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse


UPSTREAM_URL = os.environ.get("TAP_UPSTREAM_URL", "https://yeysai.com/v1").rstrip("/")
UPSTREAM_KEY = os.environ.get("TAP_UPSTREAM_KEY", "")
LOG_FILE = os.environ.get("TAP_LOG_FILE", "/tmp/tap_log.jsonl")
TAP_PORT = int(os.environ.get("TAP_PORT", "8888"))

app = FastAPI(title="OpenClaw Tap Proxy")

_call_counter = 0
_lock = asyncio.Lock()


def _summarize_message(msg: Any) -> dict[str, Any]:
    """Compact a single message into a logging-friendly dict (preserve full content)."""
    if not isinstance(msg, dict):
        return {"raw": str(msg)[:500]}
    role = msg.get("role")
    content = msg.get("content")
    if isinstance(content, list):
        content_kinds = [
            (p.get("type") if isinstance(p, dict) else type(p).__name__) for p in content
        ]
    else:
        content_kinds = [type(content).__name__]
    out: dict[str, Any] = {
        "role": role,
        "content_kinds": content_kinds,
        "content_len": len(content) if isinstance(content, str) else None,
        "content": content,
    }
    if "tool_calls" in msg:
        out["tool_calls"] = msg["tool_calls"]
    if "tool_call_id" in msg:
        out["tool_call_id"] = msg["tool_call_id"]
    if "name" in msg:
        out["name"] = msg["name"]
    return out


async def _log_request(method: str, path: str, body: bytes) -> int:
    global _call_counter
    async with _lock:
        _call_counter += 1
        idx = _call_counter

    body_obj: Any = None
    try:
        body_obj = json.loads(body) if body else None
    except Exception:
        body_obj = {"_raw_body_first_500": body[:500].decode("utf-8", "replace")}

    record: dict[str, Any] = {
        "idx": idx,
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
    }
    if isinstance(body_obj, dict):
        msgs = body_obj.get("messages") or []
        record["model"] = body_obj.get("model")
        record["stream"] = body_obj.get("stream")
        record["n_messages"] = len(msgs) if isinstance(msgs, list) else None
        record["messages"] = [
            _summarize_message(m) for m in msgs if isinstance(msgs, list)
        ] if isinstance(msgs, list) else None
        record["tools_count"] = len(body_obj.get("tools") or [])
        record["tool_choice"] = body_obj.get("tool_choice")
        record["temperature"] = body_obj.get("temperature")
        record["max_tokens"] = body_obj.get("max_tokens") or body_obj.get("max_completion_tokens")
        record["other_keys"] = sorted(
            k for k in body_obj.keys()
            if k not in {"model", "stream", "messages", "tools", "tool_choice",
                         "temperature", "max_tokens", "max_completion_tokens"}
        )
    else:
        record["raw_body"] = body_obj

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return idx


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "upstream": UPSTREAM_URL, "log": LOG_FILE, "calls": _call_counter}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def relay(path: str, request: Request) -> Response:
    body = await request.body()

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "authorization", "content-length", "content-encoding"}
    }
    if UPSTREAM_KEY:
        headers["Authorization"] = f"Bearer {UPSTREAM_KEY}"

    await _log_request(request.method, path, body)

    is_stream = False
    try:
        b = json.loads(body) if body else {}
        is_stream = bool(b.get("stream"))
    except Exception:
        pass

    upstream = f"{UPSTREAM_URL}/{path}"
    timeout = httpx.Timeout(120.0, connect=10.0)

    if is_stream:
        async def _gen():
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    request.method, upstream, headers=headers, content=body,
                    params=dict(request.query_params),
                ) as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk
        return StreamingResponse(_gen(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            request.method, upstream, headers=headers, content=body,
            params=dict(request.query_params),
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=TAP_PORT, log_level="warning")
