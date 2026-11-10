"""Cloud Privacy Router — transparent OpenAI-compatible proxy."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import math
import uuid
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
ROUTER_PORT = int(os.environ.get('ROUTER_PORT', '9303'))
CLOUD_BASE_URL = os.environ.get('CLOUD_BASE_URL', 'http://localhost:8001/v1').rstrip('/')
CLOUD_API_KEY = os.environ.get('CLOUD_API_KEY', '')
CLOUD_MODEL = os.environ.get('CLOUD_MODEL', 'GLM-5.3')
EDGE_BASE_URL = os.environ.get('EDGE_BASE_URL', 'http://localhost:8000/v1').rstrip('/')
EDGE_API_KEY = os.environ.get('EDGE_API_KEY', 'dummy')
EDGE_MODEL = os.environ.get('EDGE_MODEL', 'Qwen/Qwen3.5-9B')
AUDIT_FILE = os.environ.get('AUDIT_FILE', '/tmp/cloud_assistant_audit.jsonl')
ROUTER_LOG = os.environ.get('ROUTER_LOG', '/tmp/router_proxy.log')
ROUTER_MODE = os.environ.get('ROUTER_MODE', 'glimpse').lower()
if ROUTER_MODE not in ('glimpse', 'signal'):
    raise ValueError('ROUTER_MODE must be glimpse or signal')
SIGNAL_TOOL = os.environ.get('SIGNAL_TOOL', 'escalate')
try:
    SIGNAL_PROMPT = base64.b64decode(os.environ.get('SIGNAL_PROMPT_B64', '')).decode('utf-8').strip()
except Exception:
    SIGNAL_PROMPT = ''
GLIMPSE_ENTROPY_THRESHOLD = float(os.environ['GLIMPSE_ENTROPY_THRESHOLD']) if os.environ.get('GLIMPSE_ENTROPY_THRESHOLD') else None
GLIMPSE_TOP_K = int(os.environ.get('GLIMPSE_TOP_K', '20'))
GLIMPSE_PROBE_RETRIES = int(os.environ.get('GLIMPSE_PROBE_RETRIES', '3'))
GLIMPSE_PROBE_BACKOFF_MS = int(os.environ.get('GLIMPSE_PROBE_BACKOFF_MS', '250'))
GLIMPSE_POOL_MAX_CONN = int(os.environ.get('GLIMPSE_POOL_MAX_CONN', '64'))
_EDGE_CLIENT: 'httpx.AsyncClient | None' = None

def _edge_client() -> 'httpx.AsyncClient':
    """Return the process-wide edge HTTP client (lazily created)."""
    global _EDGE_CLIENT
    if _EDGE_CLIENT is None or _EDGE_CLIENT.is_closed:
        _EDGE_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), limits=httpx.Limits(max_connections=GLIMPSE_POOL_MAX_CONN, max_keepalive_connections=GLIMPSE_POOL_MAX_CONN, keepalive_expiry=300.0))
    return _EDGE_CLIENT
try:
    CLOUD_ROLE_PROMPT = base64.b64decode(os.environ.get('CLOUD_ROLE_PROMPT_B64', '')).decode('utf-8').strip()
except Exception:
    CLOUD_ROLE_PROMPT = ''

class RouterUsageCounter:
    """Track entropy-probe calls separately from action-generation usage."""

    def __init__(self) -> None:
        self.judge_calls: int = 0
        self.judge_input_tokens: int = 0
        self.judge_output_tokens: int = 0

    def record(self, role: str, usage: dict) -> None:
        if role == 'judge':
            self.judge_calls += 1
            self.judge_input_tokens += usage.get('prompt_tokens', 0)
            self.judge_output_tokens += usage.get('completion_tokens', 0)
        self.flush()

    def flush(self) -> None:
        """把当前计数写盘。"""
        try:
            path = Path(ROUTER_LOG).parent / 'router_usage.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    def to_dict(self) -> dict:
        return {'judge': {'calls': self.judge_calls, 'input_tokens': self.judge_input_tokens, 'output_tokens': self.judge_output_tokens, 'total_tokens': self.judge_input_tokens + self.judge_output_tokens}, 'total': {'calls': self.judge_calls, 'input_tokens': self.judge_input_tokens, 'output_tokens': self.judge_output_tokens, 'total_tokens': self.judge_input_tokens + self.judge_output_tokens}}
router_usage = RouterUsageCounter()

class SessionState:
    """One agent run = one session, keyed by hash(system_message)."""

    def __init__(self) -> None:
        self.original_ctx: list[dict] = []
        self.cloud_to_proxy_id: dict[str, str] = {}
        self.next_tc_seq: int = 0
        self.cloud_fail_streak: int = 0
        self.created_ts: float = time.time()

    def issue_proxy_id(self, cloud_id: str) -> str:
        """Stable proxy-side id for a cloud tool_call_id."""
        if cloud_id in self.cloud_to_proxy_id:
            return self.cloud_to_proxy_id[cloud_id]
        self.next_tc_seq += 1
        proxy_id = f'tc{self.next_tc_seq:06d}'
        self.cloud_to_proxy_id[cloud_id] = proxy_id
        return proxy_id
sessions: dict[str, SessionState] = {}
_session_lock = asyncio.Lock()

def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

def _normalize_for_hash(m: Any) -> dict:
    """Strip provider-specific extras so messages from different sources hash the same."""
    if not isinstance(m, dict):
        return {'_raw': str(m)}
    out: dict[str, Any] = {'role': m.get('role', '')}
    c = m.get('content')
    if isinstance(c, str):
        out['content'] = c
    elif isinstance(c, list):
        norm_parts: list[Any] = []
        for p in c:
            if isinstance(p, dict):
                t = p.get('type')
                if t == 'text':
                    norm_parts.append({'type': 'text', 'text': p.get('text', '')})
                else:
                    norm_parts.append(p)
            else:
                norm_parts.append({'_raw': str(p)})
        out['content'] = norm_parts
    elif c is None:
        out['content'] = None
    tcs = m.get('tool_calls')
    if tcs:
        out['tool_calls'] = []
        for tc in tcs:
            fn = (tc or {}).get('function') or {}
            out['tool_calls'].append({'id': (tc or {}).get('id'), 'function': {'name': fn.get('name'), 'arguments': fn.get('arguments')}})
    if m.get('tool_call_id') is not None:
        out['tool_call_id'] = m['tool_call_id']
    if m.get('name') is not None:
        out['name'] = m['name']
    return out

def _msg_hash(m: Any) -> str:
    return _stable_hash(_normalize_for_hash(m))

def session_id_for(messages: list[dict]) -> str:
    if not messages:
        return '_empty'
    return _msg_hash(messages[0])[:16]

def _msg_text_or_empty(m: dict) -> str:
    c = m.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [p.get('text', '') for p in c if isinstance(p, dict) and p.get('type') == 'text']
        return '\n'.join(parts)
    return ''

def is_incremental(new_msgs: list[dict], cached_orig: list[dict]) -> bool:
    """Append-only check using a lenient prefix match."""
    K = len(cached_orig)
    if K == 0 or len(new_msgs) < K:
        return False
    for i in range(K):
        old = cached_orig[i] if isinstance(cached_orig[i], dict) else {}
        new = new_msgs[i] if isinstance(new_msgs[i], dict) else {}
        rl_old = old.get('role')
        rl_new = new.get('role')
        if rl_old != rl_new:
            _log(f'incr MISMATCH @{i} role old={rl_old!r} new={rl_new!r} N={len(new_msgs)} K={K}')
            return False
        if rl_old in ('user', 'system'):
            if _msg_text_or_empty(old) != _msg_text_or_empty(new):
                _log(f'incr MISMATCH @{i} {rl_old!r} text differs old_head={_msg_text_or_empty(old)[:80]!r} new_head={_msg_text_or_empty(new)[:80]!r}')
                return False
        elif rl_old == 'tool':
            if old.get('tool_call_id') != new.get('tool_call_id'):
                _log(f'incr MISMATCH @{i} tool tool_call_id differs')
                return False
            if _msg_text_or_empty(old) != _msg_text_or_empty(new):
                _log(f'incr MISMATCH @{i} tool text differs')
                return False
    return True

def _log(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    msg = f'[{ts}] {line}\n'
    try:
        with open(ROUTER_LOG, 'a', encoding='utf-8') as f:
            f.write(msg)
    except Exception:
        pass

def write_audit(record: dict) -> None:
    try:
        with open(AUDIT_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as exc:
        _log(f'audit write failed: {exc}')
        raise

def _msg_text(m: dict) -> str | None:
    """Concatenate textual content from a message; return None if no text."""
    c = m.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [p.get('text', '') for p in c if isinstance(p, dict) and p.get('type') == 'text']
        return '\n'.join(parts) if parts else None
    return None

def _set_msg_text(m: dict, new_text: str) -> dict:
    """Replace the textual content of a message, preserving non-text parts."""
    nm = json.loads(json.dumps(m, ensure_ascii=False))
    c = m.get('content')
    if isinstance(c, str):
        nm['content'] = new_text
    elif isinstance(c, list):
        new_parts: list[dict] = []
        merged = False
        for p in c:
            if isinstance(p, dict) and p.get('type') == 'text':
                if not merged:
                    new_parts.append({'type': 'text', 'text': new_text})
                    merged = True
            else:
                new_parts.append(p)
        if not merged:
            new_parts.append({'type': 'text', 'text': new_text})
        nm['content'] = new_parts
    return nm

def _last_round_is_signal(full_msgs: list[dict]) -> bool:
    """True if the most recent round is the edge model's escalate() call."""
    if not full_msgs:
        return False
    last_assistant = None
    for m in reversed(full_msgs):
        if isinstance(m, dict) and m.get('role') == 'assistant':
            last_assistant = m
            break
    if last_assistant is None:
        return False
    for tc in last_assistant.get('tool_calls') or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get('function') or {}
        if fn.get('name') == SIGNAL_TOOL:
            return True
    return False

def _hide_signal_tools(tools: list | None) -> list | None:
    """Drop SIGNAL_TOOL's definition (and description) from a tools array."""
    if not tools:
        return tools
    return [t for t in tools if not (isinstance(t, dict) and (t.get('function') or {}).get('name') == SIGNAL_TOOL)]

def _hide_signal(msgs: list[dict]) -> list[dict]:
    out = []
    prompt_stripped = False
    for message in msgs:
        if isinstance(message, dict) and (not prompt_stripped) and SIGNAL_PROMPT and (message.get('role') == 'user'):
            text = _msg_text(message)
            if SIGNAL_PROMPT in text:
                message = _set_msg_text(message, text.replace(SIGNAL_PROMPT, '').replace('\n\n\n', '\n\n'))
                prompt_stripped = True
        out.append(message)
    return out

async def _glimpse_first_token(full_msgs: list[dict], tools: list | None=None, tool_choice: Any=None, top_k: int | None=None) -> tuple[float, str, dict, dict]:
    """Probe the edge model for a single next token and return"""
    k = GLIMPSE_TOP_K if top_k is None else top_k
    payload: dict[str, Any] = {'model': EDGE_MODEL, 'messages': full_msgs, 'max_tokens': 1, 'temperature': 0.0, 'stream': False, 'logprobs': True, 'top_logprobs': k, 'chat_template_kwargs': {'enable_thinking': True}}
    if tools:
        payload['tools'] = tools
    if tool_choice is not None:
        payload['tool_choice'] = tool_choice
    headers = {'Authorization': f'Bearer {EDGE_API_KEY}', 'Content-Type': 'application/json'}
    client = _edge_client()
    for attempt in range(GLIMPSE_PROBE_RETRIES):
        try:
            r = await client.post(f'{EDGE_BASE_URL}/chat/completions', json=payload, headers=headers)
            r.raise_for_status()
            resp_json = r.json()
            if attempt:
                _log(f'glimpse probe recovered after {attempt} retry(ies)')
            break
        except httpx.TransportError as exc:
            if attempt == GLIMPSE_PROBE_RETRIES - 1:
                raise
            delay = GLIMPSE_PROBE_BACKOFF_MS * 2 ** attempt / 1000.0
            _log(f'glimpse probe transport error ({type(exc).__name__}), retry {attempt + 1}/{GLIMPSE_PROBE_RETRIES - 1} in {delay:.2f}s')
            await asyncio.sleep(delay)
    choice0 = (resp_json.get('choices') or [{}])[0]
    msg = choice0.get('message') or {}
    usage = resp_json.get('usage') or {}
    lp = choice0.get('logprobs') or {}
    content_lp = lp.get('content') or []
    if not content_lp:
        content_lp = lp.get('reasoning_content') or []
    top_token = ''
    if content_lp:
        top_token = (content_lp[0] or {}).get('token') or ''
    if not top_token:
        top_token = msg.get('content') or msg.get('reasoning') or ''
    if not content_lp:
        return (float('inf'), top_token, usage, {})
    first_tok = content_lp[0] or {}
    top_lps = first_tok.get('top_logprobs') or []
    if not top_lps:
        return (float('inf'), top_token, usage, {})
    top_items: list[dict] = []
    logprob_vals: list[float] = []
    for item in top_lps:
        if not isinstance(item, dict):
            continue
        v = item.get('logprob')
        if isinstance(v, (int, float)) and (not math.isinf(v)):
            top_items.append(item)
            logprob_vals.append(float(v))
    if not logprob_vals:
        return (float('inf'), top_token, usage, {})
    probs = [math.exp(x) for x in logprob_vals]
    total = sum(probs)
    if total <= 0:
        return (float('inf'), top_token, usage, {})
    raw_probs = list(probs)
    probs = [p / total for p in raw_probs]
    H = -sum((p * math.log(p + 1e-12) for p in probs))
    metrics = {}
    return (H, top_token, usage, metrics)

async def judge_can_local(new_msgs: list[dict], full_msgs: list[dict] | None=None, req: dict | None=None, session: 'SessionState | None'=None) -> dict:
    """Decide whether this round can stay on the edge model."""
    if ROUTER_MODE == 'signal':
        go_cloud = _last_round_is_signal(full_msgs or new_msgs)
        _log(f"JUDGE(signal) tool={SIGNAL_TOOL!r} → {('CLOUD' if go_cloud else 'LOCAL')}")
        return {'can_local': not go_cloud, 'reason': 'signal_escalate' if go_cloud else 'signal_no_escalate'}
    if GLIMPSE_ENTROPY_THRESHOLD is None or not math.isfinite(GLIMPSE_ENTROPY_THRESHOLD):
        raise HTTPException(status_code=503, detail='Set a finite calibrated GLIMPSE_ENTROPY_THRESHOLD')
    probe_msgs = full_msgs if full_msgs else new_msgs
    tools = (req or {}).get('tools')
    tool_choice = (req or {}).get('tool_choice')
    try:
        (H_init, top_tok, _usage, init_metrics) = await _glimpse_first_token(probe_msgs, tools=tools, tool_choice=tool_choice)
    except Exception as exc:
        _log(f'glimpse probe failed: {exc!r} → fail request')
        raise HTTPException(status_code=503, detail=f'glimpse_failed_fail_request:{exc}') from exc
    router_usage.record('judge', _usage)
    if not math.isfinite(H_init):
        reason = f'glimpse_invalid_entropy:{H_init!r}'
        _log(f'{reason} → fail request')
        raise HTTPException(status_code=503, detail=reason)
    can_local = math.isfinite(H_init) and H_init <= GLIMPSE_ENTROPY_THRESHOLD
    reason = f'glimpse H={H_init:.3f} tau={GLIMPSE_ENTROPY_THRESHOLD:.3f} top_tok={top_tok!r}'
    _log(f"JUDGE(glimpse) H={H_init:.3f} τ={GLIMPSE_ENTROPY_THRESHOLD:.3f} tools={('Y' if tools else 'N')} → {('LOCAL' if can_local else 'CLOUD')} (top={top_tok!r})")
    return {'can_local': can_local, 'reason': reason[:200]}
UPSTREAM_RETRY_BACKOFFS = tuple((float(x) for x in os.environ.get('UPSTREAM_RETRY_BACKOFFS', '2,5,10').split(',') if x.strip()))
UPSTREAM_RETRY_STATUS = {429, 500, 502, 503, 504}

def _is_retryable_upstream(exc: Exception) -> bool:
    """Transient upstream failure worth retrying?"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in UPSTREAM_RETRY_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectTimeout))

async def _post_upstream_nonstream(base_url: str, api_key: str, body: dict, timeout_s: float=300.0, *, audit_retries: bool=False) -> tuple[dict, int]:
    """POST to an upstream, retrying transient gateway failures."""
    last_exc: Exception | None = None
    attempts = len(UPSTREAM_RETRY_BACKOFFS) + 1
    for attempt in range(attempts):
        try:
            return await _post_upstream_once(base_url, api_key, body, timeout_s)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts or not _is_retryable_upstream(exc):
                raise
            if audit_retries:
                write_audit({'event': 'retry_failure', 'status': 'error', 'model': body.get('model'), 'usage_recorded': False, 'input': body.get('messages'), 'error_type': type(exc).__name__})
            delay = UPSTREAM_RETRY_BACKOFFS[attempt]
            _log(f'upstream transient failure (attempt {attempt + 1}/{attempts}), retrying in {delay}s: {type(exc).__name__}: {str(exc)[:160]}')
            await asyncio.sleep(delay)
    raise last_exc

async def _post_upstream_once(base_url: str, api_key: str, body: dict, timeout_s: float=300.0) -> tuple[dict, int]:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0)) as client:
        r = await client.post(f'{base_url}/chat/completions', json=body, headers=headers)
        if r.status_code >= 400:
            try:
                err_body = r.text[:1000]
            except Exception:
                err_body = '<unreadable body>'
            _log(f"upstream {r.status_code} from {base_url}: {err_body} | n_msgs={len(body.get('messages') or [])} tools={len(body.get('tools') or [])} model={body.get('model')}")
            global _DUMPED_FAIL
            if not _DUMPED_FAIL:
                _DUMPED_FAIL = True
                try:
                    with open('/tmp/router_failed_request.json', 'w', encoding='utf-8') as f:
                        json.dump({'url': f'{base_url}/chat/completions', 'status': r.status_code, 'response_text': r.text[:5000], 'request_body': body}, f, ensure_ascii=False, indent=2)
                    _log('dumped first failing request to /tmp/router_failed_request.json')
                except Exception as exc:
                    _log(f'failed to dump request: {exc}')
            r.raise_for_status()
        latency = round((time.monotonic() - t0) * 1000)
        return (r.json(), latency)
_DUMPED_FAIL = False

def re_emit_as_stream(resp_json: dict) -> 'asyncio.Iterable[bytes]':
    """Re-package a single non-stream OpenAI response into an SSE stream."""
    base = {'id': resp_json.get('id', 'router-resp'), 'object': 'chat.completion.chunk', 'created': resp_json.get('created', int(time.time())), 'model': resp_json.get('model', 'router')}
    choice0 = (resp_json.get('choices') or [{}])[0]
    msg = choice0.get('message') or {}
    content = msg.get('content') or ''
    tool_calls = msg.get('tool_calls') or []
    reasoning = msg.get('reasoning_content') or msg.get('reasoning') or ''
    finish_reason = choice0.get('finish_reason', 'stop')
    chunks: list[dict] = []
    role_chunk = dict(base)
    role_chunk['choices'] = [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]
    chunks.append(role_chunk)
    r_chunk = dict(base)
    r_chunk['choices'] = [{'index': 0, 'delta': {'reasoning': reasoning}, 'finish_reason': None}]
    chunks.append(r_chunk)
    if content:
        n_target = max(1, min(20, len(content) // 200 or 1))
        chunk_size = max(50, (len(content) + n_target - 1) // n_target)
        for i in range(0, len(content), chunk_size):
            ck = dict(base)
            ck['choices'] = [{'index': 0, 'delta': {'content': content[i:i + chunk_size]}, 'finish_reason': None}]
            chunks.append(ck)
    if tool_calls:
        tc_chunk = dict(base)
        tc_chunk['choices'] = [{'index': 0, 'delta': {'tool_calls': tool_calls}, 'finish_reason': None}]
        chunks.append(tc_chunk)
    final_chunk = dict(base)
    final_chunk['choices'] = [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}]
    if 'usage' in resp_json:
        final_chunk['usage'] = resp_json['usage']
    chunks.append(final_chunk)

    async def _gen():
        for ck in chunks:
            yield f'data: {json.dumps(ck, ensure_ascii=False)}\n\n'.encode('utf-8')
        yield b'data: [DONE]\n\n'
    return _gen()

def build_response(is_stream_req: bool, resp_json: dict) -> Response:
    if is_stream_req:
        return StreamingResponse(re_emit_as_stream(resp_json), media_type='text/event-stream')
    return Response(content=json.dumps(resp_json, ensure_ascii=False).encode('utf-8'), media_type='application/json')
app = FastAPI(title='Cloud Privacy Router')

@app.get('/healthz')
def healthz() -> dict:
    return {'ok': True, 'router_mode': ROUTER_MODE, 'edge_model': EDGE_MODEL, 'cloud_model': CLOUD_MODEL, 'glimpse_entropy_threshold': GLIMPSE_ENTROPY_THRESHOLD, 'active_sessions': len(sessions)}

@app.get('/v1/models')
def models() -> dict:
    """Some clients ping /v1/models on startup; return both cloud and edge ids."""
    return {'object': 'list', 'data': [{'id': EDGE_MODEL, 'object': 'model', 'owned_by': 'router-edge'}, {'id': CLOUD_MODEL, 'object': 'model', 'owned_by': 'router-cloud'}]}

@app.post('/v1/chat/completions')
async def chat_completions(request: Request) -> Response:
    raw = await request.body()
    try:
        req = json.loads(raw)
    except Exception:
        return Response(status_code=400, content='bad json')
    messages = req.get('messages') or []
    is_stream_req = bool(req.get('stream'))
    sid = session_id_for(messages)
    async with _session_lock:
        sess = sessions.setdefault(sid, SessionState())
    K = len(sess.original_ctx)
    incremental = is_incremental(messages, sess.original_ctx)
    if not incremental:
        sess.original_ctx = []
        K = 0
    new_msgs = messages[K:]
    decision = await judge_can_local(new_msgs, full_msgs=messages, req=req, session=sess)
    can_local = bool(decision.get('can_local'))
    reason = decision.get('reason', '')
    target_model = EDGE_MODEL if can_local else CLOUD_MODEL
    _log(f"sid={sid} N={len(messages)} K={K} incr={incremental} decision={('LOCAL' if can_local else 'CLOUD')} model={target_model} action={('-' if can_local else 'execute')} reason={reason!r}")
    sess.original_ctx = list(messages)
    if can_local:
        return await handle_local(req, sess, is_stream_req)
    return await handle_cloud(req, sess, is_stream_req)

async def handle_local(req: dict, session: SessionState, is_stream_req: bool) -> Response:
    """LOCAL path: be a *byte-faithful* proxy to the edge vLLM."""
    body = dict(req)
    body['model'] = EDGE_MODEL
    body.pop('_judge_tools', None)
    body.pop('_judge_msgs', None)
    if is_stream_req:
        body['stream'] = True
        return await _stream_passthrough(EDGE_BASE_URL, EDGE_API_KEY, body)
    body['stream'] = False
    body.pop('stream_options', None)
    try:
        (resp_json, _latency) = await _post_upstream_nonstream(EDGE_BASE_URL, EDGE_API_KEY, body)
    except Exception as exc:
        _log(f'edge upstream failed: {exc}')
        return Response(status_code=502, content=f'edge upstream error: {exc}')
    msg = (resp_json.get('choices') or [{}])[0].get('message') or {}
    if msg:
        session.original_ctx.append(msg)
    return Response(content=json.dumps(resp_json, ensure_ascii=False).encode('utf-8'), media_type='application/json')

async def _stream_passthrough(base_url: str, api_key: str, body: dict) -> StreamingResponse:
    """Open a streaming POST to upstream and pipe SSE chunks straight through."""
    url = base_url.rstrip('/') + '/chat/completions'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}

    async def _gen():
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        attempts = len(UPSTREAM_RETRY_BACKOFFS) + 1
        last_err = None
        for attempt in range(attempts):
            started = False
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream('POST', url, headers=headers, json=body) as resp:
                        if resp.status_code >= 400:
                            err_body = await resp.aread()
                            _log(f'edge stream upstream {resp.status_code}: {err_body[:500]!r}')
                            if resp.status_code in UPSTREAM_RETRY_STATUS and attempt + 1 < attempts:
                                delay = UPSTREAM_RETRY_BACKOFFS[attempt]
                                _log(f'edge stream {resp.status_code} transient (attempt {attempt + 1}/{attempts}), retry in {delay}s')
                                await asyncio.sleep(delay)
                                continue
                            yield ('data: ' + json.dumps({'error': {'message': f'edge upstream {resp.status_code}', 'type': 'edge_error'}}) + '\n\n').encode('utf-8')
                            yield b'data: [DONE]\n\n'
                            return
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                started = True
                                yield chunk
                return
            except Exception as exc:
                last_err = exc
                if not started and _is_retryable_upstream(exc) and (attempt + 1 < attempts):
                    delay = UPSTREAM_RETRY_BACKOFFS[attempt]
                    _log(f'edge stream transport failure (attempt {attempt + 1}/{attempts}), retry in {delay}s: {type(exc).__name__}: {str(exc)[:160]}')
                    await asyncio.sleep(delay)
                    continue
                break
        _log(f'edge stream passthrough failed: {last_err}')
        yield ('data: ' + json.dumps({'error': {'message': str(last_err), 'type': 'edge_passthrough_error'}}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'
    return StreamingResponse(_gen(), media_type='text/event-stream')

async def _build_cloud_view(session: SessionState) -> list[dict]:
    return session.original_ctx

async def handle_cloud(req: dict, session: SessionState, is_stream_req: bool) -> Response:
    cloud_msgs = await _build_cloud_view(session)
    body = dict(req)
    body.pop('_judge_tools', None)
    body.pop('_judge_msgs', None)
    body['model'] = CLOUD_MODEL
    body['messages'] = cloud_msgs
    if ROUTER_MODE in ('signal',):
        (n_msgs, n_tools) = (len(body['messages']), len(body.get('tools') or []))
        body['messages'] = _hide_signal(body['messages'])
        body['tools'] = _hide_signal_tools(body.get('tools'))
        n_hidden = len(body['messages'])
        if CLOUD_ROLE_PROMPT:
            body['messages'] = body['messages'] + [{'role': 'system', 'content': CLOUD_ROLE_PROMPT}]
        _log(f"cloud: hid {SIGNAL_TOOL!r} → msgs {n_msgs}→{n_hidden} tools {n_tools}→{len(body.get('tools') or [])} role_brief={('Y' if CLOUD_ROLE_PROMPT else 'N')}")
    sent_msgs = body['messages']
    body['stream'] = False
    body.pop('stream_options', None)
    request_id = uuid.uuid4().hex
    write_audit({'event': 'request', 'request_id': request_id, 'timestamp': datetime.now(timezone.utc).isoformat(), 'model': CLOUD_MODEL, 'input': sent_msgs})
    try:
        (resp_json, latency) = await _post_upstream_nonstream(CLOUD_BASE_URL, CLOUD_API_KEY, body, audit_retries=True)
    except Exception as exc:
        write_audit({'event': 'result', 'request_id': request_id, 'status': 'error', 'usage_recorded': False, 'error_type': type(exc).__name__})
        session.cloud_fail_streak += 1
        _body = ''
        _resp = getattr(exc, 'response', None)
        if _resp is not None:
            try:
                _body = _resp.text[:600]
            except Exception:
                try:
                    _body = _resp.content[:600].decode('utf-8', errors='replace')
                except Exception:
                    _body = '<body unreadable>'
        _n_msgs = len(body.get('messages') or [])
        _n_chars = sum((len(str(_m.get('content') or '')) for _m in body.get('messages') or []))
        _log(f"cloud upstream failed: {exc} (连续失败 {session.cloud_fail_streak} 次) | model={body.get('model')} n_msgs={_n_msgs} chars={_n_chars} tools={len(body.get('tools') or [])} max_tokens={body.get('max_tokens')} | body={_body!r}")
        return Response(status_code=502, content=f'cloud upstream error: {exc}')
    session.cloud_fail_streak = 0
    choice = (resp_json.get('choices') or [{}])[0]
    msg = choice.get('message') or {}
    raw_content = msg.get('content') or ''
    raw_tool_calls = msg.get('tool_calls') or []
    raw_reasoning = msg.get('reasoning_content') or msg.get('reasoning') or ''
    if raw_reasoning or raw_content:
        _log(f'cloud resp: content={len(raw_content)}chars reasoning={len(raw_reasoning)}chars tool_calls={len(raw_tool_calls)} msg_keys={sorted(msg.keys())}')
    raw_content = raw_content.lstrip('\n')
    if not raw_reasoning and raw_tool_calls:
        raw_reasoning = ''
    msg['reasoning'] = raw_reasoning
    decoded_content = raw_content
    decoded_tool_calls = []
    for tc in raw_tool_calls:
        new_tc = json.loads(json.dumps(tc, ensure_ascii=False))
        cloud_id = new_tc.get('id')
        if isinstance(cloud_id, str) and cloud_id:
            new_tc['id'] = session.issue_proxy_id(cloud_id)
        fn = new_tc.get('function') or {}
        decoded_tool_calls.append(new_tc)
    decoded_msg = dict(msg)
    decoded_msg['content'] = decoded_content
    if raw_tool_calls:
        decoded_msg['tool_calls'] = decoded_tool_calls
    decoded_resp = json.loads(json.dumps(resp_json, ensure_ascii=False))
    decoded_resp['choices'][0]['message'] = decoded_msg
    session.original_ctx.append(decoded_msg)
    usage = resp_json.get('usage') or {}
    audit_output_tool_calls = []
    for tc in raw_tool_calls or []:
        fn = (tc or {}).get('function') or {}
        audit_output_tool_calls.append({'name': fn.get('name'), 'arguments': fn.get('arguments')})
    ptd = usage.get('prompt_tokens_details') or {}
    cached_tokens = ptd.get('cached_tokens') or usage.get('cache_read_input_tokens') or 0
    cache_write_tokens = ptd.get('cache_creation_tokens') or usage.get('cache_creation_input_tokens') or (usage.get('claude_cache_creation_5_m_tokens', 0) or 0) + (usage.get('claude_cache_creation_1_h_tokens', 0) or 0) or 0
    write_audit({'event': 'result', 'request_id': request_id, 'status': 'ok', 'usage_recorded': 'prompt_tokens' in usage and 'completion_tokens' in usage, 'timestamp': datetime.now(timezone.utc).isoformat(), 'model': CLOUD_MODEL, 'session_id': session_id_for(req.get('messages') or []), 'decision': 'cloud', 'prompt_tokens': usage.get('prompt_tokens', 0), 'completion_tokens': usage.get('completion_tokens', 0), 'total_tokens': usage.get('total_tokens', 0), 'cache_read_tokens': cached_tokens, 'cache_write_tokens': cache_write_tokens, 'latency_ms': latency, 'output': raw_content, 'output_tool_calls': audit_output_tool_calls, 'context_messages': len(sent_msgs) - 1 if sent_msgs else 0})
    return build_response(is_stream_req, decoded_resp)
if __name__ == '__main__':
    import uvicorn
    Path(AUDIT_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(ROUTER_LOG).parent.mkdir(parents=True, exist_ok=True)
    _log(f'router up: port={ROUTER_PORT} mode={ROUTER_MODE} edge={EDGE_MODEL} cloud={CLOUD_MODEL} tau={GLIMPSE_ENTROPY_THRESHOLD}')
    _proxy_url = f'http://localhost:{ROUTER_PORT}'
    _proxy_url2 = f'http://127.0.0.1:{ROUTER_PORT}'
    for (_name, _url) in (('EDGE', EDGE_BASE_URL), ('CLOUD', CLOUD_BASE_URL)):
        if _proxy_url in _url or _proxy_url2 in _url:
            _log(f'FATAL: {_name}_BASE_URL={_url!r} points back at the proxy itself (would deadlock every routing decision). Refusing to start.')
            raise SystemExit(2)
    import atexit

    def _dump_router_usage():
        usage_path = Path(ROUTER_LOG).parent / 'router_usage.json'
        try:
            usage_path.write_text(json.dumps(router_usage.to_dict(), indent=2, ensure_ascii=False), encoding='utf-8')
            _log(f"router_usage written to {usage_path}: {router_usage.to_dict()['total']}")
        except Exception as exc:
            _log(f'failed to write router_usage: {exc}')
    atexit.register(_dump_router_usage)
    uvicorn.run(app, host='0.0.0.0', port=ROUTER_PORT, log_level='warning')
