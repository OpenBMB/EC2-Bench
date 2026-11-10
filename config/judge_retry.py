"""Shared bounded, task-local retry policy for grading/Judge calls."""
from __future__ import annotations
import os
import random
import re
from dataclasses import dataclass
DEFAULT_GRADING_MAX_RETRIES = int(os.environ.get('GRADING_MAX_RETRIES', '6'))
DEFAULT_GRADING_MAX_ELAPSED = float(os.environ.get('GRADING_MAX_ELAPSED', '900'))
GRADING_RETRY_BACKOFF = float(os.environ.get('GRADING_RETRY_BACKOFF', '5'))
GRADING_RETRY_MAX_BACKOFF = float(os.environ.get('GRADING_RETRY_MAX_BACKOFF', '60'))
GRADING_RETRY_JITTER_RATIO = float(os.environ.get('GRADING_RETRY_JITTER_RATIO', '0.2'))
_HTTP_STATUS_RE = re.compile('(?:\\bhttp(?:\\s+error)?\\b|\\bstatus(?:\\s+code)?\\b|\\berror\\s+code\\b|\\breturned\\b)[^0-9]{0,20}(\\d{3})\\b', re.IGNORECASE)
_DETERMINISTIC_HTTP_CODES = {400, 401, 403, 404, 405, 410, 413, 415, 422}
_TRANSIENT_MARKERS = ('deadline exceeded', 'timed out', 'timeout', 'connection reset', 'connection refused', 'connection aborted', 'connection error', 'peer closed connection', 'remote end closed', 'incomplete chunked read', 'incomplete read', 'broken pipe', 'eof occurred', 'temporarily unavailable', 'service unavailable', 'bad gateway', 'gateway timeout', 'internal server error', 'server error', 'too many requests', 'rate limit', 'overloaded', 'ssl', 'docker cp failed', '<html', 'judge unavailable', 'judge error', 'llm_judge', 'openrouter', 'apitimeout', 'api timeout', 'apiconnection')
_DETERMINISTIC_MARKERS = ('invalid api key', 'invalid_api_key', 'incorrect api key', 'authentication failed', 'unauthorized', 'forbidden', 'permission denied', 'unsupported model', 'unknown model', 'model not found', 'model_not_found', 'no such model', 'model does not exist', 'context length exceeded', 'maximum context length', 'context length', 'maximum context', 'payload too large', 'request too large', 'unsupported media type', 'unprocessable entity', 'judge_base_url and judge_api_key', 'judge_base_url / judge_api_key', 'no automated grading code', 'automated grading function missing', 'grading function not found', 'gt directory copy failed', 'not enough disk space', 'no space left on device', 'not found', 'does not exist', 'missing file', 'missing artifact', 'missing output', 'missing summary', 'no booking found', 'judge output truncated (finish_reason=length)', 'invalid request', 'invalid schema', 'modulenotfounderror', 'importerror', 'syntaxerror')

@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    classification: str
    delay_seconds: float = 0.0
    stop_reason: str | None = None

def _classify_with_explanation(reason: str | None) -> tuple[str, str]:
    """Return ``(classification, human-readable explanation)``."""
    if not reason:
        return ('none', 'success')
    if os.environ.get('GRADING_RETRY_ALL', '').strip().lower() in ('1', 'true', 'yes'):
        return ('retryable', 'GRADING_RETRY_ALL override')
    lower = reason.lower()
    transient = next((m for m in _TRANSIENT_MARKERS if m in lower), None)
    status = _HTTP_STATUS_RE.search(lower)
    if status:
        code = int(status.group(1))
        if code not in _DETERMINISTIC_HTTP_CODES:
            return ('retryable', f'retryable HTTP {code}')
        if transient:
            return ('retryable', f'transient marker overrides deterministic HTTP {code}: {transient}')
        return ('deterministic', f'deterministic HTTP {code}')
    if transient:
        return ('retryable', f'transient marker: {transient}')
    deterministic = next((m for m in _DETERMINISTIC_MARKERS if m in lower), None)
    if deterministic:
        return ('deterministic', f'deterministic marker: {deterministic}')
    return ('retryable', 'unknown failure; default retry')

def classify_grading_failure(reason: str | None) -> str:
    """Classify only proven stable failures as deterministic."""
    return _classify_with_explanation(reason)[0]

def grading_retry_decision(reason: str | None) -> tuple[bool, str]:
    """Return ``(retryable, diagnostic reason)`` for run-history records."""
    (classification, explanation) = _classify_with_explanation(reason)
    return (classification == 'retryable', explanation)

def is_retryable_grading_failure(reason: str | None) -> bool:
    """Boolean view of :func:`classify_grading_failure` for call sites that only"""
    return classify_grading_failure(reason) != 'deterministic'

def grading_retry_delay(attempt_index: int, *, random_value: float | None=None) -> float:
    base = min(GRADING_RETRY_BACKOFF * 2 ** attempt_index, GRADING_RETRY_MAX_BACKOFF)
    ratio = max(0.0, GRADING_RETRY_JITTER_RATIO)
    if ratio == 0:
        return base
    value = random.random() if random_value is None else random_value
    jitter = (2.0 * min(1.0, max(0.0, value)) - 1.0) * ratio
    return max(0.0, base * (1.0 + jitter))

def decide_grading_retry(reason: str | None, *, attempt_index: int, max_retries: int, elapsed_seconds: float, max_elapsed_seconds: float=DEFAULT_GRADING_MAX_ELAPSED, random_value: float | None=None) -> RetryDecision:
    """Decide whether to start another attempt for one completed trajectory."""
    classification = classify_grading_failure(reason)
    if classification != 'retryable':
        return RetryDecision(False, classification, stop_reason='deterministic_failure')
    if attempt_index >= max_retries:
        return RetryDecision(False, classification, stop_reason='retry_limit')
    delay = grading_retry_delay(attempt_index, random_value=random_value)
    if max_elapsed_seconds >= 0 and elapsed_seconds + delay > max_elapsed_seconds:
        return RetryDecision(False, classification, stop_reason='elapsed_budget')
    return RetryDecision(True, classification, delay_seconds=delay)
