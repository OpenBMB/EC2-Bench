"""Cloud Privacy Router — transparent OpenAI-compatible proxy.

Sits between OpenClaw and the actual LLM endpoints. For every chat
completions call:

  1. Match against the cached per-session state to detect incremental rounds.
  2. Ask a small *judge* LLM whether this round can stay on the edge model
     or needs to escalate to the cloud model.
  3. If staying local → forward unchanged to ``EDGE_BASE_URL``.
  4. If escalating  → catch-up redact every previously un-redacted message
     using a *redactor* LLM, forward the clean history to ``CLOUD_BASE_URL``,
     then reverse-map placeholders out of the cloud's reply before handing
     it back to OpenClaw.

Both routes always re-emit the upstream response as an SSE stream so the
OpenClaw `openai-completions` provider (which expects `stream=true`) is
happy. Cloud calls are appended to ``AUDIT_FILE`` with the **clean**
messages so ``utils/privacy_audit.py`` can score what the cloud actually
saw.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import math

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse


# ---------------------------- Config ----------------------------

ROUTER_PORT = int(os.environ.get("ROUTER_PORT", "9303"))

CLOUD_BASE_URL = os.environ.get("CLOUD_BASE_URL", "https://yeysai.com/v1").rstrip("/")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY", "")
CLOUD_MODEL = os.environ.get("CLOUD_MODEL", "gpt-5.4")

EDGE_BASE_URL = os.environ.get("EDGE_BASE_URL", "http://11.11.26.2:8117/v1").rstrip("/")
EDGE_API_KEY = os.environ.get("EDGE_API_KEY", "dummy")
EDGE_MODEL = os.environ.get("EDGE_MODEL", "Qwen/Qwen3.5-9B")

JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", EDGE_BASE_URL).rstrip("/")
JUDGE_API_KEY = os.environ.get("JUDGE_API_KEY", EDGE_API_KEY)
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", EDGE_MODEL)

REDACT_BASE_URL = os.environ.get("REDACT_BASE_URL", EDGE_BASE_URL).rstrip("/")
REDACT_API_KEY = os.environ.get("REDACT_API_KEY", EDGE_API_KEY)
REDACT_MODEL = os.environ.get("REDACT_MODEL", EDGE_MODEL)

AUDIT_FILE = os.environ.get("AUDIT_FILE", "/tmp/cloud_assistant_audit.jsonl")
ROUTER_LOG = os.environ.get("ROUTER_LOG", "/tmp/router_proxy.log")
PROMPTS_DIR = Path(os.environ.get(
    "PROMPTS_DIR", "/root/skills/cloud-router/prompts"
))

# Operator overrides — useful for ablations / debugging.
#   "judge" (default, now = glimpse), "force_local", "force_cloud"
ROUTER_MODE = os.environ.get("ROUTER_MODE", "judge").lower()

# Toggle redaction on/off. When off, cloud receives original messages
# (no PII replacement) but routing still works normally.
REDACT_ENABLED = os.environ.get("REDACT_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _default_glimpse_entropy_threshold(edge_model: str) -> str:
    """Pick a calibrated default tau from the edge model size.

    Explicit GLIMPSE_ENTROPY_THRESHOLD still wins; this only chooses the
    default for normal runs where the caller does not set a tau.
    """
    normalized = edge_model.lower()
    if re.search(r"(?<!\d)27b(?!\d)", normalized):
        return "1.3"
    if re.search(r"(?<!\d)9b(?!\d)", normalized):
        return "1.7"
    return "1.7"


GLIMPSE_ENTROPY_THRESHOLD_SOURCE = (
    "env" if "GLIMPSE_ENTROPY_THRESHOLD" in os.environ else f"auto:{EDGE_MODEL}"
)
GLIMPSE_ENTROPY_THRESHOLD = float(
    os.environ.get(
        "GLIMPSE_ENTROPY_THRESHOLD",
        _default_glimpse_entropy_threshold(EDGE_MODEL),
    )
)
GLIMPSE_TOP_K = int(os.environ.get("GLIMPSE_TOP_K", "20"))
GLIMPSE_ENABLE_THINKING = os.environ.get(
    "GLIMPSE_ENABLE_THINKING", "1",
).lower() not in ("0", "false", "no", "off")
# Optional instrumentation for diagnosing which uncertainty signal is most
# separable in agent settings. Default OFF because it asks the edge model to
# generate a short draft step with logprobs, which is much more expensive than
# the 1-token routing probe.
GLIMPSE_ANALYZE_STEP_METRICS = os.environ.get(
    "GLIMPSE_ANALYZE_STEP_METRICS", "0",
).lower() not in ("0", "false", "no", "off")
GLIMPSE_STEP_METRICS_TOKENS = int(os.environ.get("GLIMPSE_STEP_METRICS_TOKENS", "64"))
#########


# ---------------------------- Prompts ----------------------------

# NOTE: JUDGE_PROMPT is no longer used by default (replaced by GlimpRouter probe).
# Kept loaded (best-effort) so ablations that swap back to LLM-as-Judge still work.
try:
    JUDGE_PROMPT = (PROMPTS_DIR / "judge.md").read_text(encoding="utf-8")
except Exception:
    JUDGE_PROMPT = ""
REDACT_PROMPT = (PROMPTS_DIR / "redact.md").read_text(encoding="utf-8")


# ---------------------------- Session state ----------------------------

class RouterUsageCounter:
    """Track judge/redact LLM calls that are invisible to OpenClaw."""

    def __init__(self) -> None:
        self.judge_calls: int = 0
        self.judge_input_tokens: int = 0
        self.judge_output_tokens: int = 0
        self.redact_calls: int = 0
        self.redact_input_tokens: int = 0
        self.redact_output_tokens: int = 0

    def record(self, role: str, usage: dict) -> None:
        if role == "judge":
            self.judge_calls += 1
            self.judge_input_tokens += usage.get("prompt_tokens", 0)
            self.judge_output_tokens += usage.get("completion_tokens", 0)
        elif role == "redact":
            self.redact_calls += 1
            self.redact_input_tokens += usage.get("prompt_tokens", 0)
            self.redact_output_tokens += usage.get("completion_tokens", 0)

    def to_dict(self) -> dict:
        return {
            "judge": {
                "calls": self.judge_calls,
                "input_tokens": self.judge_input_tokens,
                "output_tokens": self.judge_output_tokens,
                "total_tokens": self.judge_input_tokens + self.judge_output_tokens,
            },
            "redact": {
                "calls": self.redact_calls,
                "input_tokens": self.redact_input_tokens,
                "output_tokens": self.redact_output_tokens,
                "total_tokens": self.redact_input_tokens + self.redact_output_tokens,
            },
            "total": {
                "calls": self.judge_calls + self.redact_calls,
                "input_tokens": self.judge_input_tokens + self.redact_input_tokens,
                "output_tokens": self.judge_output_tokens + self.redact_output_tokens,
                "total_tokens": (self.judge_input_tokens + self.judge_output_tokens +
                                 self.redact_input_tokens + self.redact_output_tokens),
            },
        }


router_usage = RouterUsageCounter()


class SessionState:
    """One agent run = one session, keyed by hash(system_message)."""

    def __init__(self) -> None:
        self.original_ctx: list[dict] = []   # what OpenClaw sees (proxy tool_call_ids)
        self.clean_ctx: list[dict] = []      # what cloud sees (real tool_call_ids, redacted)
        self.token_map: dict[str, str] = {}  # original_text → placeholder
        self.placeholder_counters: dict[str, int] = {}  # type → next seq

        # Tool-call ID remapping. OpenClaw mangles upstream IDs that start with
        # ``call_``, dropping the underscore. To stay compatible without
        # depending on OpenClaw internals, we hand OpenClaw "safe" IDs (no
        # ``_``) and translate back when speaking to the cloud.
        self.proxy_to_cloud_id: dict[str, str] = {}  # proxy id  → cloud id
        self.cloud_to_proxy_id: dict[str, str] = {}  # cloud id  → proxy id
        self.next_tc_seq: int = 0

        self.created_ts: float = time.time()

    def issue_proxy_id(self, cloud_id: str) -> str:
        """Stable proxy-side id for a cloud tool_call_id."""
        if cloud_id in self.cloud_to_proxy_id:
            return self.cloud_to_proxy_id[cloud_id]
        self.next_tc_seq += 1
        proxy_id = f"tc{self.next_tc_seq:06d}"
        self.cloud_to_proxy_id[cloud_id] = proxy_id
        self.proxy_to_cloud_id[proxy_id] = cloud_id
        return proxy_id


sessions: dict[str, SessionState] = {}
_session_lock = asyncio.Lock()


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _normalize_for_hash(m: Any) -> dict:
    """Strip provider-specific extras so messages from different sources hash the same.

    OpenClaw rebuilds ``messages[]`` from its own ``chat.jsonl`` on every
    round, so the assistant dict it sends in round N+1 is structurally
    different from the cloud reply dict we cached in round N (extra fields
    like ``refusal``, ``audio``, ``logprobs`` get dropped). Keep only the
    fields that survive that round-trip.
    """
    if not isinstance(m, dict):
        return {"_raw": str(m)}
    out: dict[str, Any] = {"role": m.get("role", "")}
    c = m.get("content")
    if isinstance(c, str):
        out["content"] = c
    elif isinstance(c, list):
        norm_parts: list[Any] = []
        for p in c:
            if isinstance(p, dict):
                t = p.get("type")
                if t == "text":
                    norm_parts.append({"type": "text", "text": p.get("text", "")})
                else:
                    # keep unknown parts as-is (image_url, input_image, etc.)
                    norm_parts.append(p)
            else:
                norm_parts.append({"_raw": str(p)})
        out["content"] = norm_parts
    elif c is None:
        out["content"] = None
    tcs = m.get("tool_calls")
    if tcs:
        out["tool_calls"] = []
        for tc in tcs:
            fn = (tc or {}).get("function") or {}
            out["tool_calls"].append({
                "id": (tc or {}).get("id"),
                "function": {
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments"),
                },
            })
    if m.get("tool_call_id") is not None:
        out["tool_call_id"] = m["tool_call_id"]
    if m.get("name") is not None:
        out["name"] = m["name"]
    return out


def _msg_hash(m: Any) -> str:
    return _stable_hash(_normalize_for_hash(m))


def session_id_for(messages: list[dict]) -> str:
    if not messages:
        return "_empty"
    return _msg_hash(messages[0])[:16]


def _msg_text_or_empty(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(parts)
    return ""


def is_incremental(new_msgs: list[dict], cached_orig: list[dict]) -> bool:
    """Append-only check using a lenient prefix match.

    OpenClaw is empirically fully append-only (verified via tap-proxy), but
    when it round-trips an assistant message back to us the format drifts
    (extra/missing fields, re-encoded ``tool_calls.arguments``, etc.). So we:

    1. require ``len(new) >= len(cached)`` (length-monotonic),
    2. for ``user`` / ``tool`` / ``system`` slots: compare role + textual
       content + (for tool) ``tool_call_id``,
    3. for ``assistant`` slots: only require role equality.

    On failure, dump a one-shot diagnostic of the first mismatched slot so
    we can keep tuning the normalization.
    """
    K = len(cached_orig)
    if K == 0 or len(new_msgs) < K:
        return False
    for i in range(K):
        old = cached_orig[i] if isinstance(cached_orig[i], dict) else {}
        new = new_msgs[i] if isinstance(new_msgs[i], dict) else {}
        rl_old = old.get("role")
        rl_new = new.get("role")
        if rl_old != rl_new:
            _log(f"incr MISMATCH @{i} role old={rl_old!r} new={rl_new!r} N={len(new_msgs)} K={K}")
            return False
        if rl_old in ("user", "system"):
            if _msg_text_or_empty(old) != _msg_text_or_empty(new):
                _log(f"incr MISMATCH @{i} {rl_old!r} text differs "
                     f"old_head={_msg_text_or_empty(old)[:80]!r} new_head={_msg_text_or_empty(new)[:80]!r}")
                return False
        elif rl_old == "tool":
            if old.get("tool_call_id") != new.get("tool_call_id"):
                _log(f"incr MISMATCH @{i} tool tool_call_id differs")
                return False
            if _msg_text_or_empty(old) != _msg_text_or_empty(new):
                _log(f"incr MISMATCH @{i} tool text differs")
                return False
        # assistant: trust append-only (format drift is normal)
    return True


# ---------------------------- Logging ----------------------------

def _log(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    msg = f"[{ts}] {line}\n"
    try:
        with open(ROUTER_LOG, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


def write_audit(record: dict) -> None:
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log(f"audit write failed: {exc}")


# ---------------------------- LLM helpers ----------------------------

async def _call_llm_text(
    base_url: str, api_key: str, model: str,
    system: str, user: str, max_tokens: int = 1024,
    response_format: dict | None = None,
    allow_reasoning_fallback: bool = True,
    disable_thinking: bool = False,
) -> tuple[str, str]:
    """Call an OpenAI-compatible chat endpoint and return (text, source).

    ``source`` is one of: ``"content"``, ``"reasoning_content"``,
    ``"reasoning"``, ``"empty"`` — useful for callers that need to know
    whether the model actually produced a final answer or only emitted
    chain-of-thought before hitting ``max_tokens``.

    When ``allow_reasoning_fallback=False`` (e.g. for the routing judge),
    we ONLY accept content from the ``content`` field. Reasoning fields
    contain pre-final-answer thinking and must NOT be parsed as the
    answer itself.

    When ``disable_thinking=True``, we send ``chat_template_kwargs:
    {"enable_thinking": false}`` to suppress Qwen3/3.5 chain-of-thought.
    This makes utility calls (judge, redact) faster and avoids the issue
    where thinking consumes the entire ``max_tokens`` budget leaving
    ``content`` empty.
    """
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        resp_json = r.json()
        msg = (resp_json.get("choices") or [{}])[0].get("message") or {}
        resp_usage = resp_json.get("usage") or {}
        keys = ("content",) if not allow_reasoning_fallback else (
            "content", "reasoning_content", "reasoning"
        )
        for key in keys:
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                return v, key, resp_usage
        return "", "empty", resp_usage


def _parse_json_loose(text: str) -> Any | None:
    """Best-effort JSON parse — strip markdown fences and try json_repair."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    try:
        return json.loads(t)
    except Exception:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(t))
        except Exception:
            return None


# ---------------------------- Message helpers ----------------------------

def _msg_text(m: dict) -> str | None:
    """Concatenate textual content from a message; return None if no text."""
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _set_msg_text(m: dict, new_text: str) -> dict:
    """Replace the textual content of a message, preserving non-text parts."""
    nm = json.loads(json.dumps(m, ensure_ascii=False))
    c = m.get("content")
    if isinstance(c, str):
        nm["content"] = new_text
    elif isinstance(c, list):
        new_parts: list[dict] = []
        merged = False
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                if not merged:
                    new_parts.append({"type": "text", "text": new_text})
                    merged = True
                # subsequent text parts dropped — collapsed into the merged one
            else:
                new_parts.append(p)
        if not merged:
            new_parts.append({"type": "text", "text": new_text})
        nm["content"] = new_parts
    return nm


def _msg_tool_call_args(m: dict) -> list[tuple[int, str]]:
    """Return [(index, raw_arguments_json), ...] for assistant tool_calls."""
    out = []
    for i, tc in enumerate(m.get("tool_calls") or []):
        fn = tc.get("function") or {}
        if isinstance(fn.get("arguments"), str):
            out.append((i, fn["arguments"]))
    return out


# ---------------------------- Redaction ----------------------------

async def _llm_find_pii(text: str) -> list[dict]:
    if not text or not text.strip():
        return []
    user = text
    try:
        raw, src, _usage = await _call_llm_text(
            REDACT_BASE_URL, REDACT_API_KEY, REDACT_MODEL,
            REDACT_PROMPT, user, max_tokens=4096,
            response_format={"type": "json_object"},
            allow_reasoning_fallback=False,
            disable_thinking=True,
        )
        router_usage.record("redact", _usage)
    except Exception as exc:
        _log(f"redact LLM call failed: {exc}")
        return []
    parsed = _parse_json_loose(raw)
    if not isinstance(parsed, dict):
        _log(f"redact LLM returned non-JSON (src={src}, {len(raw)} chars): {raw[:200]!r}")
        return []
    items = parsed.get("pii", []) or []
    if not items:
        # Don't spam the log on every empty round, but help debugging when
        # the redactor consistently misses obvious PII.
        _log(f"redact returned 0 PII for {len(text)}-char input "
             f"(head: {text[:120].replace(chr(10), ' ')!r})")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        orig = it.get("original")
        ptype = (it.get("type") or "OTHER").upper().strip().replace(" ", "_")
        if isinstance(orig, str) and orig.strip():
            out.append({"original": orig, "type": ptype})
    return out


def _placeholder_for(session: SessionState, original: str, ptype: str) -> str:
    if original in session.token_map:
        return session.token_map[original]
    seq = session.placeholder_counters.get(ptype, 0) + 1
    session.placeholder_counters[ptype] = seq
    placeholder = f"[{ptype}_{seq:03d}]"
    session.token_map[original] = placeholder
    return placeholder


def _apply_token_map(text: str, token_map: dict[str, str]) -> str:
    """Literal-replace originals in `text`. Long-first to avoid sub-string bleed."""
    if not text or not token_map:
        return text
    for orig in sorted(token_map.keys(), key=len, reverse=True):
        if orig and orig in text:
            text = text.replace(orig, token_map[orig])
    return text


def _remap_tool_ids_proxy_to_cloud(m: dict, session: SessionState) -> dict:
    """Replace proxy tool_call ids in ``m`` with the original cloud ids.

    OpenClaw stores whatever ids we hand it during the streaming response, so
    on incremental rounds the assistant ``tool_calls[*].id`` and the tool
    message ``tool_call_id`` come back as our proxy ids (``tc000001``…). The
    cloud, of course, only knows its own ids. Translate before forwarding.
    """
    nm = json.loads(json.dumps(m, ensure_ascii=False))
    tcid = nm.get("tool_call_id")
    if isinstance(tcid, str) and tcid in session.proxy_to_cloud_id:
        nm["tool_call_id"] = session.proxy_to_cloud_id[tcid]
    for tc in nm.get("tool_calls") or []:
        tid = (tc or {}).get("id")
        if isinstance(tid, str) and tid in session.proxy_to_cloud_id:
            tc["id"] = session.proxy_to_cloud_id[tid]
    return nm


async def redact_messages(
    msgs: list[dict], session: SessionState,
) -> list[dict]:
    """Redact text content (and tool_call arguments) of each message.

    PII is detected per message; new findings are merged into the session
    token_map, then the WHOLE current map is applied to the message text so
    that values redacted in earlier turns stay redacted across the run.

    Also remaps proxy tool_call ids → cloud tool_call ids so the cloud sees a
    consistent view (the assistant ids match the tool result ids).
    """
    out: list[dict] = []
    for m in msgs:
        # system prompts are public boilerplate (OpenClaw template) — no PII,
        # skip the expensive LLM call entirely.
        if m.get("role") == "system":
            out.append(json.loads(json.dumps(m, ensure_ascii=False)))
            continue

        # 1) message text
        text = _msg_text(m)
        if text is not None and text.strip():
            try:
                pii = await _llm_find_pii(text)
            except Exception as exc:
                _log(f"redact failure (text): {exc}")
                pii = []
            for it in pii:
                _placeholder_for(session, it["original"], it["type"])
            new_text = _apply_token_map(text, session.token_map)
            m_clean = _set_msg_text(m, new_text)
        else:
            m_clean = json.loads(json.dumps(m, ensure_ascii=False))

        # 2) assistant tool_calls.arguments — also a leak vector
        tc_args = _msg_tool_call_args(m_clean)
        if tc_args:
            for idx, args_str in tc_args:
                try:
                    pii = await _llm_find_pii(args_str)
                except Exception as exc:
                    _log(f"redact failure (tool_call args): {exc}")
                    pii = []
                for it in pii:
                    _placeholder_for(session, it["original"], it["type"])
                new_args = _apply_token_map(args_str, session.token_map)
                m_clean["tool_calls"][idx]["function"]["arguments"] = new_args

        # 3) translate proxy tool ids back to cloud ids
        m_clean = _remap_tool_ids_proxy_to_cloud(m_clean, session)

        out.append(m_clean)
    return out


def un_redact_text(text: str, token_map: dict[str, str]) -> str:
    """Inverse of `_apply_token_map` — replace each placeholder with its original."""
    if not text or not token_map:
        return text
    pairs = [(p, o) for o, p in token_map.items() if p]
    for p, o in sorted(pairs, key=lambda kv: len(kv[0]), reverse=True):
        if p in text:
            text = text.replace(p, o)
    return text


# ---------------------------- Judge ----------------------------

async def _glimpse_first_token(
    full_msgs: list[dict],
    tools: list | None = None,
    tool_choice: Any = None,
    top_k: int | None = None,
) -> tuple[float, str, dict, dict]:
    """Probe the edge model for a single next token and return
    ``(entropy, top_token_text, usage, metrics)``.

    The entropy is a truncated Shannon entropy computed over the top-``k``
    logprobs returned for the first generated token (renormalized to sum to
    1 so the probe's own truncation bias does not skew the scale).

    Design notes
    ------------
    * ``max_tokens=1`` + ``temperature=0`` keeps the probe cheap and
      deterministic. With vLLM prefix caching enabled (on by default), the
      probe shares its KV cache with the subsequent real generation, so the
      marginal cost is roughly one decoder step.
    * We forward the *actual* ``tools`` / ``tool_choice`` the caller sent,
      because under agentic settings the real first-token distribution
      depends on whether tools are available (tool-call vs. chat reply).
      Dropping them would bias entropy toward "chat only" and invalidate
      the signal.
    * ``enable_thinking`` is driven by ``GLIMPSE_ENABLE_THINKING`` (default
      ON) to stay faithful to the paper: on Qwen3 the ``<think>\\n`` opener
      is injected by the chat template, so ``logprobs.content[0]`` is the
      first token *after* ``<think>`` — exactly the "first token of a
      reasoning step" the paper probes. Setting it OFF measures the first
      token of the answer instead (a valid ablation, but not the paper).
    * If the backend does not return ``logprobs`` we surface
      ``entropy=+inf``; the caller treats that as a router failure instead
      of silently falling back to cloud routing.
    """
    k = GLIMPSE_TOP_K if top_k is None else top_k
    payload: dict[str, Any] = {
        "model": EDGE_MODEL,
        "messages": full_msgs,
        "max_tokens": 1,
        "temperature": 0.0,
        "stream": False,
        "logprobs": True,
        "top_logprobs": k,
        # enable_thinking=True (paper default): probe the first token of
        # the upcoming *reasoning step*, not the answer. Flip to False via
        # GLIMPSE_ENABLE_THINKING=0 only for ablation.
        "chat_template_kwargs": {"enable_thinking": GLIMPSE_ENABLE_THINKING},
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {EDGE_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        r = await client.post(
            f"{EDGE_BASE_URL}/chat/completions", json=payload, headers=headers,
        )
        r.raise_for_status()
        resp_json = r.json()

    choice0 = (resp_json.get("choices") or [{}])[0]
    msg = choice0.get("message") or {}
    usage = resp_json.get("usage") or {}

    lp = choice0.get("logprobs") or {}
    content_lp = lp.get("content") or []
    # NOTE: when enable_thinking=True, vLLM routes the reasoning text to
    # message.reasoning / message.reasoning_content, leaving message.content
    # null. So the canonical source for the top-token string is the logprobs
    # entry itself, which is populated in both thinking and non-thinking mode.
    top_token = ""
    if content_lp:
        top_token = (content_lp[0] or {}).get("token") or ""
    if not top_token:
        top_token = msg.get("content") or msg.get("reasoning") or ""

    if not content_lp:
        return float("inf"), top_token, usage, {}

    first_tok = content_lp[0] or {}
    top_lps = first_tok.get("top_logprobs") or []
    if not top_lps:
        return float("inf"), top_token, usage, {}

    top_items: list[dict] = []
    logprob_vals: list[float] = []
    for item in top_lps:
        if not isinstance(item, dict):
            continue
        v = item.get("logprob")
        if isinstance(v, (int, float)) and not math.isinf(v):
            top_items.append(item)
            logprob_vals.append(float(v))
    if not logprob_vals:
        return float("inf"), top_token, usage, {}

    probs = [math.exp(x) for x in logprob_vals]
    total = sum(probs)
    if total <= 0:
        return float("inf"), top_token, usage, {}
    raw_probs = list(probs)
    probs = [p / total for p in raw_probs]
    H = -sum(p * math.log(p + 1e-12) for p in probs)
    H_raw_topk = -sum(p * math.log(p + 1e-12) for p in raw_probs)
    top_probs = sorted(probs, reverse=True)
    raw_top_probs = sorted(raw_probs, reverse=True)
    top1_prob = top_probs[0] if top_probs else 0.0
    top2_prob = top_probs[1] if len(top_probs) > 1 else 0.0
    raw_top1_prob = raw_top_probs[0] if raw_top_probs else 0.0
    raw_top2_prob = raw_top_probs[1] if len(raw_top_probs) > 1 else 0.0
    top1_logprob = max(logprob_vals)
    sorted_logprobs = sorted(logprob_vals, reverse=True)
    top2_logprob = sorted_logprobs[1] if len(sorted_logprobs) > 1 else float("-inf")
    metrics = {
        "init_entropy": H,
        "init_entropy_raw_topk": H_raw_topk,
        "init_topk_mass": total,
        "init_top1_prob": top1_prob,
        "init_top2_prob": top2_prob,
        "init_prob_margin": top1_prob - top2_prob,
        "init_raw_top1_prob": raw_top1_prob,
        "init_raw_prob_margin": raw_top1_prob - raw_top2_prob,
        "init_logprob_margin": top1_logprob - top2_logprob if math.isfinite(top2_logprob) else float("inf"),
        "init_effective_vocab": math.exp(H),
        "init_topk": len(top_items),
    }
    return H, top_token, usage, metrics
#########


def _entropy_from_top_logprobs(top_lps: list) -> float | None:
    vals: list[float] = []
    for item in top_lps or []:
        if not isinstance(item, dict):
            continue
        v = item.get("logprob")
        if isinstance(v, (int, float)) and not math.isinf(v):
            vals.append(float(v))
    if not vals:
        return None
    probs = [math.exp(x) for x in vals]
    total = sum(probs)
    if total <= 0:
        return None
    probs = [p / total for p in probs]
    return -sum(p * math.log(p + 1e-12) for p in probs)


async def _probe_step_metrics(
    full_msgs: list[dict],
    tools: list | None = None,
    tool_choice: Any = None,
    max_tokens: int = GLIMPSE_STEP_METRICS_TOKENS,
) -> tuple[dict, dict]:
    """Generate a short edge draft and compute step-level uncertainty metrics.

    This is only for analysis, not routing. It intentionally mirrors the
    paper's comparison metrics:
      * step_ppl = exp(mean negative selected-token logprob)
      * step_entropy = mean token-level entropy across the generated draft

    It is much more expensive than the normal 1-token probe, so callers should
    guard it behind GLIMPSE_ANALYZE_STEP_METRICS.
    """
    payload: dict[str, Any] = {
        "model": EDGE_MODEL,
        "messages": full_msgs,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
        "logprobs": True,
        "top_logprobs": min(GLIMPSE_TOP_K, 20),
        "chat_template_kwargs": {"enable_thinking": GLIMPSE_ENABLE_THINKING},
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {EDGE_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        r = await client.post(
            f"{EDGE_BASE_URL}/chat/completions", json=payload, headers=headers,
        )
        r.raise_for_status()
        resp_json = r.json()

    choice0 = (resp_json.get("choices") or [{}])[0]
    usage = resp_json.get("usage") or {}
    content_lp = ((choice0.get("logprobs") or {}).get("content") or [])
    selected_logprobs: list[float] = []
    token_entropies: list[float] = []
    tokens: list[str] = []
    for tok in content_lp:
        if not isinstance(tok, dict):
            continue
        token = tok.get("token")
        if isinstance(token, str):
            tokens.append(token)
        lp = tok.get("logprob")
        if isinstance(lp, (int, float)) and not math.isinf(lp):
            selected_logprobs.append(float(lp))
        H_tok = _entropy_from_top_logprobs(tok.get("top_logprobs") or [])
        if H_tok is not None:
            token_entropies.append(H_tok)

    metrics: dict[str, Any] = {
        "step_tokens": len(content_lp),
        "step_first_tokens": "".join(tokens[:8])[:80],
    }
    if selected_logprobs:
        mean_nll = -sum(selected_logprobs) / len(selected_logprobs)
        metrics["step_mean_nll"] = mean_nll
        metrics["step_ppl"] = math.exp(min(mean_nll, 20.0))
        metrics["step_selected_logprob_mean"] = sum(selected_logprobs) / len(selected_logprobs)
    if token_entropies:
        metrics["step_entropy_mean"] = sum(token_entropies) / len(token_entropies)
        metrics["step_entropy_max"] = max(token_entropies)
        metrics["step_entropy_first4_mean"] = (
            sum(token_entropies[:4]) / min(len(token_entropies), 4)
        )
    return metrics, usage


async def judge_can_local(
    new_msgs: list[dict],
    full_msgs: list[dict] | None = None,
    req: dict | None = None,
) -> dict:
    """Decide whether this round can stay on the edge model.

    Implementation: GlimpRouter-style first-token entropy probe
    (arXiv:2601.05110). The edge model is asked to emit only its next token
    (``max_tokens=1``) with ``logprobs`` enabled; we compute Shannon
    entropy over the top-k logprobs and compare against
    ``GLIMPSE_ENTROPY_THRESHOLD``. Low entropy → confident → stay local;
    high entropy → "Aha Moment" / pivot → escalate to cloud.

    ``req`` is the raw OpenAI-compatible request so we can forward
    ``tools`` / ``tool_choice`` to the probe; this is required for the
    probe distribution to match the real generation distribution in
    agentic (tool-using) settings.
    """
    if ROUTER_MODE == "force_local":
        return {"can_local": True, "reason": "force_local"}
    if ROUTER_MODE == "force_cloud":
        return {"can_local": False, "reason": "force_cloud"}


    probe_msgs = full_msgs if full_msgs else new_msgs
    tools = (req or {}).get("tools")
    tool_choice = (req or {}).get("tool_choice")
    try:
        H_init, top_tok, _usage, init_metrics = await _glimpse_first_token(
            probe_msgs, tools=tools, tool_choice=tool_choice,
        )
    except Exception as exc:
        _log(f"glimpse probe failed: {exc!r} → fail request")
        raise HTTPException(
            status_code=503,
            detail=f"glimpse_failed_fail_request:{exc}",
        ) from exc
    # Account the probe's token usage under the "judge" bucket so the
    # summary keeps treating the router's extra cost in a single field.
    router_usage.record("judge", _usage)

    if not math.isfinite(H_init):
        reason = f"glimpse_invalid_entropy:{H_init!r}"
        _log(f"{reason} → fail request")
        raise HTTPException(status_code=503, detail=reason)

    can_local = math.isfinite(H_init) and H_init <= GLIMPSE_ENTROPY_THRESHOLD
    reason = (
        f"glimpse H={H_init:.3f} tau={GLIMPSE_ENTROPY_THRESHOLD:.3f} "
        f"top_tok={top_tok!r}"
    )
    init_metric_text = ""
    if init_metrics:
        init_metric_text = (
            f" top1p={init_metrics.get('init_top1_prob', 0.0):.3f}"
            f" margin={init_metrics.get('init_prob_margin', 0.0):.3f}"
            f" mass={init_metrics.get('init_topk_mass', 0.0):.3f}"
            f" raw_top1p={init_metrics.get('init_raw_top1_prob', 0.0):.3f}"
            f" Hraw={init_metrics.get('init_entropy_raw_topk', 0.0):.3f}"
            f" eff_vocab={init_metrics.get('init_effective_vocab', 0.0):.2f}"
        )
    _log(
        f"JUDGE(glimpse) H={H_init:.3f} τ={GLIMPSE_ENTROPY_THRESHOLD:.3f} "
        f"tools={'Y' if tools else 'N'}{init_metric_text} → "
        f"{'LOCAL' if can_local else 'CLOUD'} (top={top_tok!r})"
    )
    if GLIMPSE_ANALYZE_STEP_METRICS:
        try:
            step_metrics, step_usage = await _probe_step_metrics(
                probe_msgs, tools=tools, tool_choice=tool_choice,
            )
            router_usage.record("judge", step_usage)
            _log(
                "JUDGE(step_metrics) "
                f"tokens={step_metrics.get('step_tokens', 0)} "
                f"ppl={step_metrics.get('step_ppl', float('nan')):.3f} "
                f"step_H={step_metrics.get('step_entropy_mean', float('nan')):.3f} "
                f"step_Hmax={step_metrics.get('step_entropy_max', float('nan')):.3f} "
                f"first4_H={step_metrics.get('step_entropy_first4_mean', float('nan')):.3f} "
                f"draft={step_metrics.get('step_first_tokens', '')!r}"
            )
        except Exception as exc:
            _log(f"JUDGE(step_metrics) failed: {exc!r}")
    return {"can_local": can_local, "reason": reason[:200]}
    #########

    # ==================================================================
    # DEPRECATED: LLM-as-Judge path (kept for ablation / fallback).
    # Prompt-based routing showed bias and unstable parsing in our runs
    # (see docs/ROUTER_NOTES.md); superseded by the GlimpRouter probe above.
    # To re-enable, delete the glimpse return and uncomment the block below.
    # ------------------------------------------------------------------
    # user_task_text = ""
    # for m in (full_msgs or []):
    #     if m.get("role") == "user":
    #         user_task_text = (_msg_text(m) or "").strip()[:1500]
    #         if user_task_text:
    #             break
    #
    # summary = []
    # for m in new_msgs:
    #     role = m.get("role")
    #     text = _msg_text(m) or ""
    #     summary.append({"role": role, "content": text[:800]})
    # user_parts = []
    # if user_task_text:
    #     user_parts.append("=== ORIGINAL USER TASK ===\n" + user_task_text)
    # user_parts.append(
    #     "=== LATEST AGENT ROUND (new messages after the prior LLM call) ===\n"
    #     + json.dumps(summary, ensure_ascii=False, indent=2)[:4000]
    # )
    # user = "\n\n".join(user_parts)
    #
    # try:
    #     raw, src, _usage = await _call_llm_text(
    #         JUDGE_BASE_URL, JUDGE_API_KEY, JUDGE_MODEL,
    #         JUDGE_PROMPT, user, max_tokens=4096,
    #         response_format={"type": "json_object"},
    #         allow_reasoning_fallback=False,
    #         disable_thinking=True,
    #     )
    #     router_usage.record("judge", _usage)
    # except Exception as exc:
    #     _log(f"judge LLM call failed: {exc}")
    #     return {"can_local": False, "reason": f"judge_failed_default_cloud:{exc}"}
    #
    # if not raw:
    #     _log("JUDGE EMPTY content (likely thinking truncated) → default CLOUD")
    #     return {"can_local": False, "reason": "judge_empty_default_cloud"}
    #
    # parsed = _parse_json_loose(raw)
    # if not isinstance(parsed, dict) or "can_local" not in parsed:
    #     _log(f"JUDGE UNPARSEABLE src={src} raw={raw[:600]!r} → default CLOUD")
    #     return {"can_local": False, "reason": "judge_unparseable_default_cloud"}
    # return {
    #     "can_local": bool(parsed.get("can_local")),
    #     "reason": str(parsed.get("reason", ""))[:200],
    # }
    # ==================================================================


# ---------------------------- Upstream forwarding ----------------------------

async def _post_upstream_nonstream(
    base_url: str, api_key: str, body: dict, timeout_s: float = 240.0,
) -> tuple[dict, int]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0)) as client:
        r = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        if r.status_code >= 400:
            # Surface the upstream error body so we can debug 4xx/5xx without
            # needing to re-run the whole task.
            try:
                err_body = r.text[:1000]
            except Exception:
                err_body = "<unreadable body>"
            _log(
                f"upstream {r.status_code} from {base_url}: {err_body}"
                f" | n_msgs={len(body.get('messages') or [])}"
                f" tools={len(body.get('tools') or [])}"
                f" model={body.get('model')}"
            )
            # Also dump the raw outgoing body once for the FIRST 4xx/5xx so we
            # can replay it offline. Subsequent dumps are skipped.
            global _DUMPED_FAIL
            if not _DUMPED_FAIL:
                _DUMPED_FAIL = True
                try:
                    with open("/tmp/router_failed_request.json", "w", encoding="utf-8") as f:
                        json.dump(
                            {"url": f"{base_url}/chat/completions",
                             "status": r.status_code, "response_text": r.text[:5000],
                             "request_body": body}, f, ensure_ascii=False, indent=2,
                        )
                    _log("dumped first failing request to /tmp/router_failed_request.json")
                except Exception as exc:
                    _log(f"failed to dump request: {exc}")
            r.raise_for_status()
        latency = round((time.monotonic() - t0) * 1000)
        return r.json(), latency


_DUMPED_FAIL = False


def re_emit_as_stream(resp_json: dict) -> "asyncio.Iterable[bytes]":
    """Re-package a single non-stream OpenAI response into an SSE stream.

    Preserves ``reasoning`` / ``reasoning_content`` so that downstream
    consumers (OpenClaw → 9B on a subsequent LOCAL turn) see the same
    fields they would if the cloud model had been called directly.
    """
    base = {
        "id": resp_json.get("id", "router-resp"),
        "object": "chat.completion.chunk",
        "created": resp_json.get("created", int(time.time())),
        "model": resp_json.get("model", "router"),
    }

    choice0 = (resp_json.get("choices") or [{}])[0]
    msg = choice0.get("message") or {}
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    finish_reason = choice0.get("finish_reason", "stop")

    chunks: list[dict] = []

    role_chunk = dict(base)
    role_chunk["choices"] = [
        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None},
    ]
    chunks.append(role_chunk)

    # Always emit a reasoning chunk — even empty string — so OpenClaw
    # stores a thinking entry for every assistant message. 9B with
    # reasoning-parser always has thinking; missing it breaks 9B.
    r_chunk = dict(base)
    r_chunk["choices"] = [
        {"index": 0, "delta": {"reasoning": reasoning},
         "finish_reason": None},
    ]
    chunks.append(r_chunk)

    if content:
        n_target = max(1, min(20, len(content) // 200 or 1))
        chunk_size = max(50, (len(content) + n_target - 1) // n_target)
        for i in range(0, len(content), chunk_size):
            ck = dict(base)
            ck["choices"] = [
                {"index": 0, "delta": {"content": content[i: i + chunk_size]},
                 "finish_reason": None},
            ]
            chunks.append(ck)

    if tool_calls:
        tc_chunk = dict(base)
        tc_chunk["choices"] = [
            {"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": None},
        ]
        chunks.append(tc_chunk)

    final_chunk = dict(base)
    final_chunk["choices"] = [
        {"index": 0, "delta": {}, "finish_reason": finish_reason},
    ]
    if "usage" in resp_json:
        final_chunk["usage"] = resp_json["usage"]
    chunks.append(final_chunk)

    async def _gen():
        for ck in chunks:
            yield f"data: {json.dumps(ck, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return _gen()


def build_response(
    is_stream_req: bool, resp_json: dict,
) -> Response:
    if is_stream_req:
        return StreamingResponse(re_emit_as_stream(resp_json), media_type="text/event-stream")
    return Response(
        content=json.dumps(resp_json, ensure_ascii=False).encode("utf-8"),
        media_type="application/json",
    )


# ---------------------------- Main route ----------------------------

app = FastAPI(title="Cloud Privacy Router")


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "router_mode": ROUTER_MODE,
        "edge": {"base": EDGE_BASE_URL, "model": EDGE_MODEL},
        "cloud": {"base": CLOUD_BASE_URL, "model": CLOUD_MODEL},
        "judge_model": JUDGE_MODEL,
        "redact_model": REDACT_MODEL,
        "glimpse_entropy_threshold": GLIMPSE_ENTROPY_THRESHOLD,
        "glimpse_entropy_threshold_source": GLIMPSE_ENTROPY_THRESHOLD_SOURCE,
        "audit_file": AUDIT_FILE,
        "active_sessions": len(sessions),
    }


@app.get("/v1/models")
def models() -> dict:
    """Some clients ping /v1/models on startup; return both cloud and edge ids."""
    return {
        "object": "list",
        "data": [
            {"id": EDGE_MODEL, "object": "model", "owned_by": "router-edge"},
            {"id": CLOUD_MODEL, "object": "model", "owned_by": "router-cloud"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    raw = await request.body()
    try:
        req = json.loads(raw)
    except Exception:
        return Response(status_code=400, content="bad json")

    messages = req.get("messages") or []
    is_stream_req = bool(req.get("stream"))
    sid = session_id_for(messages)

    async with _session_lock:
        sess = sessions.setdefault(sid, SessionState())

    K = len(sess.original_ctx)
    incremental = is_incremental(messages, sess.original_ctx)
    if not incremental:
        # New session OR OpenClaw compacted history → reset
        sess.original_ctx = []
        sess.clean_ctx = []
        # token_map kept (deliberately) — same PII keeps the same placeholder.
        K = 0

    new_msgs = messages[K:]

    # ---- Judge ----

    decision = await judge_can_local(new_msgs, full_msgs=messages, req=req)
    can_local = bool(decision.get("can_local"))
    reason = decision.get("reason", "")

    target_model = EDGE_MODEL if can_local else CLOUD_MODEL
    _log(
        f"sid={sid} N={len(messages)} K={K} incr={incremental} "
        f"decision={'LOCAL' if can_local else 'CLOUD'} model={target_model} "
        f"reason={reason!r}"
    )

    # Persist the new full history regardless of decision.
    sess.original_ctx = list(messages)

    if can_local:
        return await handle_local(req, sess, is_stream_req)
    else:
        return await handle_cloud(req, sess, is_stream_req)


# ---------------------------- Local branch ----------------------------

async def handle_local(req: dict, session: SessionState, is_stream_req: bool) -> Response:
    """LOCAL path: be a *byte-faithful* proxy to the edge vLLM.

    advisor mode (OpenClaw → vLLM directly) works fine. Anything we lose by
    deserialising and re-encoding (e.g. the ``reasoning`` field that vLLM's
    reasoning-parser puts Qwen3.5-9B's ``<tool_call>`` XML into) breaks the
    agent. So when the caller asked for a stream, we open a streaming POST
    upstream and pipe raw chunks straight through. We then *do not* try to
    cache the assistant message in ``session.original_ctx`` — the next
    incoming request from OpenClaw will carry it as part of its message
    history and the incremental-detection logic will catch it up.

    Non-streaming callers (``stream=false``) are rare in practice but we
    keep the simple non-stream path for them.
    """
    body = dict(req)
    body["model"] = EDGE_MODEL

    if is_stream_req:
        body["stream"] = True
        return await _stream_passthrough(EDGE_BASE_URL, EDGE_API_KEY, body)

    body["stream"] = False
    body.pop("stream_options", None)
    try:
        resp_json, _latency = await _post_upstream_nonstream(
            EDGE_BASE_URL, EDGE_API_KEY, body,
        )
    except Exception as exc:
        _log(f"edge upstream failed: {exc}")
        return Response(status_code=502, content=f"edge upstream error: {exc}")

    msg = (resp_json.get("choices") or [{}])[0].get("message") or {}
    if msg:
        session.original_ctx.append(msg)
    return Response(
        content=json.dumps(resp_json, ensure_ascii=False).encode("utf-8"),
        media_type="application/json",
    )


async def _stream_passthrough(
    base_url: str, api_key: str, body: dict,
) -> StreamingResponse:
    """Open a streaming POST to upstream and pipe SSE chunks straight through.

    Doing zero parsing here is the whole point — it keeps quirky vLLM fields
    (``reasoning``, ``reasoning_content``, ``tool_calls`` in non-standard
    positions, etc.) intact for OpenClaw to parse exactly as it would in
    advisor (direct-connect) mode.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    async def _gen():
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", url, headers=headers, json=body,
                ) as resp:
                    if resp.status_code >= 400:
                        err_body = await resp.aread()
                        _log(
                            f"edge stream upstream {resp.status_code}: "
                            f"{err_body[:500]!r}"
                        )
                        yield (
                            "data: " + json.dumps({
                                "error": {
                                    "message": f"edge upstream {resp.status_code}",
                                    "type": "edge_error",
                                },
                            }) + "\n\n"
                        ).encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
        except Exception as exc:
            _log(f"edge stream passthrough failed: {exc}")
            yield (
                "data: " + json.dumps({
                    "error": {"message": str(exc), "type": "edge_passthrough_error"},
                }) + "\n\n"
            ).encode("utf-8")
            yield b"data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ---------------------------- Cloud branch ----------------------------

async def handle_cloud(req: dict, session: SessionState, is_stream_req: bool) -> Response:
    K_clean = len(session.clean_ctx)
    catchup = session.original_ctx[K_clean:]

    if REDACT_ENABLED:
        if catchup:
            new_clean = await redact_messages(catchup, session)
            session.clean_ctx.extend(new_clean)
        cloud_msgs = session.clean_ctx
    else:
        session.clean_ctx = list(session.original_ctx)
        cloud_msgs = session.clean_ctx

    body = dict(req)
    body["model"] = CLOUD_MODEL
    body["messages"] = cloud_msgs

    body["stream"] = False
    body.pop("stream_options", None)

    try:
        resp_json, latency = await _post_upstream_nonstream(
            CLOUD_BASE_URL, CLOUD_API_KEY, body,
        )
    except Exception as exc:
        _log(f"cloud upstream failed: {exc}")
        return Response(status_code=502, content=f"cloud upstream error: {exc}")

    choice = (resp_json.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    raw_content = msg.get("content") or ""
    raw_tool_calls = msg.get("tool_calls") or []
    raw_reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if raw_reasoning or raw_content:
        _log(f"cloud resp: content={len(raw_content)}chars reasoning={len(raw_reasoning)}chars "
             f"tool_calls={len(raw_tool_calls)} msg_keys={sorted(msg.keys())}")

    # Strip leading whitespace from content (artifact of <think> tag removal).
    raw_content = raw_content.lstrip("\n")

    # Ensure reasoning is always present so 9B sees consistent context.
    # 9B standalone always has thinking; cloud steps without it confuse 9B.
    if not raw_reasoning and raw_tool_calls:
        raw_reasoning = ""  # re_emit_as_stream will skip empty reasoning

    # Inject reasoning into the message so re_emit_as_stream can pick it up
    # and the response sent to OpenClaw matches 9B's format.
    msg["reasoning"] = raw_reasoning

    if REDACT_ENABLED:
        decoded_content = un_redact_text(raw_content, session.token_map)
    else:
        decoded_content = raw_content

    # Replace cloud tool_call ids with proxy ids before handing to OpenClaw.
    decoded_tool_calls = []
    for tc in raw_tool_calls:
        new_tc = json.loads(json.dumps(tc, ensure_ascii=False))
        cloud_id = new_tc.get("id")
        if isinstance(cloud_id, str) and cloud_id:
            new_tc["id"] = session.issue_proxy_id(cloud_id)
        fn = new_tc.get("function") or {}
        if isinstance(fn.get("arguments"), str):
            if REDACT_ENABLED:
                new_tc["function"]["arguments"] = un_redact_text(fn["arguments"], session.token_map)
        decoded_tool_calls.append(new_tc)

    decoded_msg = dict(msg)
    decoded_msg["content"] = decoded_content
    if raw_tool_calls:
        decoded_msg["tool_calls"] = decoded_tool_calls
    decoded_resp = json.loads(json.dumps(resp_json, ensure_ascii=False))
    decoded_resp["choices"][0]["message"] = decoded_msg

    session.clean_ctx.append({"role": "assistant", "content": raw_content,
                              **({"tool_calls": raw_tool_calls} if raw_tool_calls else {})})
    session.original_ctx.append(decoded_msg)

    usage = resp_json.get("usage") or {}
    audit_output_tool_calls = []
    for tc in raw_tool_calls or []:
        fn = (tc or {}).get("function") or {}
        audit_output_tool_calls.append({
            "name": fn.get("name"),
            "arguments": fn.get("arguments"),
        })

    ptd = usage.get("prompt_tokens_details") or {}
    cached_tokens = (
        ptd.get("cached_tokens")
        or usage.get("cache_read_input_tokens")
        or 0
    )
    cache_write_tokens = (
        ptd.get("cache_creation_tokens")
        or usage.get("cache_creation_input_tokens")
        or (usage.get("claude_cache_creation_5_m_tokens", 0) or 0)
            + (usage.get("claude_cache_creation_1_h_tokens", 0) or 0)
        or 0
    )
    write_audit({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": CLOUD_MODEL,
        "session_id": session_id_for(req.get("messages") or []),
        "decision": "cloud",
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cache_read_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "latency_ms": latency,
        "input": [m for m in (cloud_msgs[:-1] if len(cloud_msgs) > 0 else cloud_msgs)
                  if m.get("role") != "system"],
        "output": raw_content,
        "output_tool_calls": audit_output_tool_calls,
        "context_messages": len(cloud_msgs) - 1 if cloud_msgs else 0,
        "n_redacted_tokens": len(session.token_map),
        "redact_enabled": REDACT_ENABLED,
        "incremental": True if K_clean > 0 else False,
        "catchup_messages": len(catchup),
    })

    return build_response(is_stream_req, decoded_resp)


# ---------------------------- Entry point ----------------------------

if __name__ == "__main__":
    import uvicorn
    Path(AUDIT_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(ROUTER_LOG).parent.mkdir(parents=True, exist_ok=True)
    _log(
        f"router up: port={ROUTER_PORT} mode={ROUTER_MODE} redact={'ON' if REDACT_ENABLED else 'OFF'} "
        f"edge={EDGE_MODEL}@{EDGE_BASE_URL} cloud={CLOUD_MODEL}@{CLOUD_BASE_URL} "
        f"glimpse_tau={GLIMPSE_ENTROPY_THRESHOLD:.3f}({GLIMPSE_ENTROPY_THRESHOLD_SOURCE}) "
        f"judge={JUDGE_MODEL}@{JUDGE_BASE_URL} redact={REDACT_MODEL}@{REDACT_BASE_URL}"
    )
    # Hard sanity check: judge / redact MUST NOT loop back to the proxy.
    _proxy_url = f"http://localhost:{ROUTER_PORT}"
    _proxy_url2 = f"http://127.0.0.1:{ROUTER_PORT}"
    for _name, _url in (("JUDGE", JUDGE_BASE_URL), ("REDACT", REDACT_BASE_URL)):
        if _proxy_url in _url or _proxy_url2 in _url:
            _log(
                f"FATAL: {_name}_BASE_URL={_url!r} points back at the proxy itself "
                f"(would deadlock every routing decision). Refusing to start."
            )
            raise SystemExit(2)
    import atexit
    def _dump_router_usage():
        usage_path = Path(ROUTER_LOG).parent / "router_usage.json"
        try:
            usage_path.write_text(
                json.dumps(router_usage.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            _log(f"router_usage written to {usage_path}: {router_usage.to_dict()['total']}")
        except Exception as exc:
            _log(f"failed to write router_usage: {exc}")
    atexit.register(_dump_router_usage)

    uvicorn.run(app, host="0.0.0.0", port=ROUTER_PORT, log_level="warning")
