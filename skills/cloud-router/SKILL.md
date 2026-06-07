# cloud-router

A transparent OpenAI-compatible HTTP proxy that intercepts every chat
completions call OpenClaw makes, decides per round whether to keep the
call local or escalate to the cloud, and (when escalating) redacts PII
through a small local detector LLM. Cloud responses are reverse-mapped
back to the original tokens before they reach the agent, so the agent
runs as if no proxy existed.

## Files

- `privacy_proxy.py` — FastAPI service, listens on `ROUTER_PORT` (default 9303).
- `start_router.sh`  — install deps, export env vars, launch the proxy in background.
- `tap_proxy.py`     — old experimental tap (verifies how OpenClaw assembles `messages[]`).
- `start_tap.sh`     — launches `tap_proxy.py` (used by `08_Toy_task_3_tap_messages.md`).
- `prompts/judge.md` — local LLM prompt: edge-vs-cloud routing decision.
- `prompts/redact.md` — local LLM prompt: PII detection for redaction.

## Architecture

```
OpenClaw → proxy:9303 ── judge → local? ── yes ─→ edge vLLM ─→ proxy → OpenClaw
                              └ no  ─→ redact ─→ cloud LLM ─→ unredact ─→ OpenClaw
```

The proxy keeps per-session state:
- `original_ctx`  full PII-bearing history (used to detect incremental rounds)
- `clean_ctx`     redacted history sent to the cloud (preserves cloud prompt cache)
- `token_map`     `{original → placeholder}` accumulated across the session

Redaction is **lazy and incremental** — local-only rounds skip redaction;
the first round that escalates to the cloud "catches up" on every history
message that has not been redacted yet.

## Required env vars (set by `eval/run_batch.py` for `--run-mode router`)

- `CLOUD_BASE_URL`, `CLOUD_API_KEY`, `CLOUD_MODEL`
- `EDGE_BASE_URL`,  `EDGE_API_KEY`,  `EDGE_MODEL`
- (optional) `JUDGE_BASE_URL` / `JUDGE_API_KEY` / `JUDGE_MODEL` — defaults to edge
- (optional) `REDACT_BASE_URL` / `REDACT_API_KEY` / `REDACT_MODEL` — defaults to edge
- (optional) `ROUTER_PORT` (default 9303), `AUDIT_FILE` (default `/tmp/cloud_assistant_audit.jsonl`)

## Audit

Every cloud call appends one JSONL line to `AUDIT_FILE`. The `input`
field contains the **clean (redacted)** messages that actually reached
the cloud — it is what `utils/privacy_audit.py` scores against the
`sensitive_items.json` ground truth.
