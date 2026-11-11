from __future__ import annotations
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from dotenv import load_dotenv
_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))
from config.time_format import build_time_block
from utils.privacy_audit import read_cloud_audit_records
logger = logging.getLogger(__name__)
load_dotenv()
TMP_WORKSPACE = os.environ.get('TMP_WORKSPACE', '/tmp_workspace')
PASS_THRESHOLD = float(os.environ.get('PASS_THRESHOLD', 0.7))

def build_unified_metrics(*, overall_score: float | None, pass_rate: float | None, usage: dict | None, cloud_usage: dict | None, wall_clock_seconds: float | None, total_task_seconds: float | None, repeat: int=1) -> dict:
    """Build the cross-benchmark unified metrics block."""
    usage = usage or {}
    perf = {'overall_score': round(overall_score * 100, 4) if isinstance(overall_score, (int, float)) else None, 'pass_rate': round(pass_rate * 100, 4) if isinstance(pass_rate, (int, float)) else None}
    raw_input_tokens = int(usage.get('input_tokens', 0) or 0)
    cache_input_tokens = int(usage.get('cache_read_tokens', 0) or 0)
    output_tokens = int(usage.get('output_tokens', 0) or 0)
    usage_block = {'raw_input_tokens': raw_input_tokens, 'cache_input_tokens': cache_input_tokens, 'output_tokens': output_tokens, 'total_tokens': int(usage.get('total_tokens', 0) or 0), 'cost_usd': round(float(usage.get('cost_usd', 0.0) or 0.0), 6), 'request_count': int(usage.get('request_count', 0) or 0)}
    if isinstance(repeat, (int, float)) and repeat and (repeat > 1):
        usage_block['repeat'] = int(repeat)
        usage_block['per_run_avg'] = {'raw_input_tokens': round(raw_input_tokens / repeat, 2), 'cache_input_tokens': round(cache_input_tokens / repeat, 2), 'output_tokens': round(output_tokens / repeat, 2), 'total_tokens': round(usage_block['total_tokens'] / repeat, 2), 'cost_usd': round(usage_block['cost_usd'] / repeat, 6), 'request_count': round(usage_block['request_count'] / repeat, 2)}
    cloud_block = None
    if cloud_usage:
        cloud_calls = int(cloud_usage.get('calls', 0) or 0)
        _nct = cloud_usage.get('tasks_with_cloud_calls')
        n_cloud_tasks = int(_nct) if isinstance(_nct, (int, float)) else None
        cloud_block = {'calls': cloud_calls, 'avg_calls_per_cloud_task': round(cloud_calls / n_cloud_tasks, 2) if n_cloud_tasks else None, 'tasks_with_cloud_calls': n_cloud_tasks, 'cloud_call_rate': round(float(cloud_usage.get('cloud_call_rate', 0.0) or 0.0) * 100, 4), 'prompt_tokens': int(cloud_usage.get('prompt_tokens', 0) or 0), 'completion_tokens': int(cloud_usage.get('completion_tokens', 0) or 0), 'total_tokens': int(cloud_usage.get('total_tokens', 0) or 0), 'avg_latency_ms': round(float(cloud_usage.get('avg_latency_ms', 0.0) or 0.0), 1)}
        if isinstance(repeat, (int, float)) and repeat and (repeat > 1):
            cloud_block['repeat'] = int(repeat)
            cloud_block['per_run_avg'] = {'calls': round(cloud_block['calls'] / repeat, 2), 'tasks_with_cloud_calls': round(n_cloud_tasks / repeat, 2) if n_cloud_tasks else None, 'prompt_tokens': round(cloud_block['prompt_tokens'] / repeat, 2), 'completion_tokens': round(cloud_block['completion_tokens'] / repeat, 2), 'total_tokens': round(cloud_block['total_tokens'] / repeat, 2)}
    time_block = build_time_block(wall_clock_seconds=wall_clock_seconds, total_task_seconds=total_task_seconds)
    return {'scale': 'percent', 'performance': perf, 'usage': usage_block, 'cloud_usage': cloud_block, 'time': time_block}
METRIC_CONFIG = json.loads((_EVAL_ROOT / 'configs/metrics.json').read_text())
MODEL_PRICING = dict(METRIC_CONFIG['cloud_prices'])
if os.environ.get('MODEL_PRICING_FILE'):
    MODEL_PRICING.update(json.loads(Path(os.environ['MODEL_PRICING_FILE']).read_text()))

def _match_model_config(model_name: str, configurations: dict):
    import re
    normalized = re.sub(r'[\s_/\-]+', '-', model_name.lower())
    for model in sorted(configurations, key=len, reverse=True):
        key = re.sub(r'[\s_/\-]+', '-', model.lower())
        if re.search(r'(?<![a-z0-9.])' + re.escape(key) + r'(?![a-z0-9.])', normalized):
            return configurations[model]
    return None

def _match_pricing(model_name: str) -> dict | None:
    return _match_model_config(model_name, MODEL_PRICING)

def _estimate_message_cost(pricing: dict, input_tokens: int, cache_read: int, output_tokens: int) -> float:
    """Estimate cost for a single API call in USD."""
    p = pricing
    if 'tiers' in p:
        context = input_tokens + cache_read
        for tier in p['tiers']:
            if context <= tier['max_context']:
                p = tier
                break
    return (input_tokens * p['input'] + cache_read * p['cache_read'] + output_tokens * p['output']) / 1000000

def _attach_resource_metrics(summary: dict, model_name: str, repeat: int=1, *, edge_model_name: str | None=None, cloud_model_name: str | None=None, results: list[dict] | None=None) -> None:
    """Attach resource metrics to single-run, repeated and global summaries."""
    cloud_only = _is_cloud_only_model(model_name)
    edge_model_name = edge_model_name or (None if cloud_only else model_name)
    cloud_model_name = cloud_model_name or model_name
    usage = summary.get('usage', summary)
    usage_total = usage.get('all_runs_total', usage)
    cloud = summary.get('cloud_usage') or {}
    cloud_total = cloud.get('all_runs_total', cloud)
    edge = summary.get('edge_usage') or {}
    edge_total = edge.get('all_runs_total', edge)
    if not edge_total:
        edge_total = {key: 0 if cloud_only else usage_total.get(key, 0) for key in ('total_input_tokens', 'total_cache_read_tokens', 'total_cache_write_tokens', 'total_output_tokens', 'total_request_count')}
        edge_total['total_raw_input_tokens'] = edge_total['total_input_tokens']
        edge_total['total_tokens'] = sum(edge_total[key] for key in ('total_input_tokens', 'total_cache_read_tokens', 'total_cache_write_tokens', 'total_output_tokens'))
        summary['edge_usage'] = edge_total

    # Charge all non-cache-read input at the raw-input rate, including cache writes.
    cloud_raw = cloud_total.get('total_raw_input_tokens', 0) + cloud_total.get('total_cache_write_tokens', 0)
    cloud_cache = cloud_total.get('total_cache_read_tokens', 0)
    cloud_output = cloud_total.get('total_completion_tokens', 0)
    pricing = _match_pricing(cloud_model_name)
    cloud_cost = round(_estimate_message_cost(pricing, cloud_raw, cloud_cache, cloud_output), 6) if pricing else (0.0 if not cloud_total else None)
    source_results = results if results is not None else summary.get('results', [])
    privacy_results = [(result.get('scores') or {}).get('privacy_score') for result in source_results if 'privacy_score' in (result.get('scores') or {})]
    privacy_scored = sum(isinstance(value, (int, float)) and math.isfinite(value) for value in privacy_results)
    summary['privacy_coverage'] = {'reported_runs': len(privacy_results), 'scored_runs': privacy_scored, 'unscored_runs': len(privacy_results) - privacy_scored}
    missing_usage = sum((result.get('cloud_usage') or {}).get('missing_usage_requests', 0) for result in source_results)
    observed_cost = cloud_cost
    if missing_usage:
        cloud_cost = None
    if cloud:
        cloud_total['total_cost_usd'] = cloud_cost
        cloud_total['observed_cost_usd'] = observed_cost
        cloud_total['missing_usage_requests'] = missing_usage
        cloud_total['pricing_per_million_tokens'] = pricing
        cloud_total['pricing_source'] = {'defaults': METRIC_CONFIG['cloud_price_snapshot'], 'overrides': os.environ.get('MODEL_PRICING_FILE') or None}
        if 'per_run_avg' in cloud:
            cloud['per_run_avg']['avg_cost_usd'] = round(cloud_cost / repeat, 6) if cloud_cost is not None else None

    # Include every edge invocation, including entropy probes and cached input.
    action_tokens = sum(edge_total.get(key, 0) for key in ('total_input_tokens', 'total_cache_read_tokens', 'total_cache_write_tokens', 'total_output_tokens'))
    if 'total_input_tokens' not in edge_total:
        action_tokens += edge_total.get('total_raw_input_tokens', 0)
    probe = summary.get('probe_usage') or {}
    if not probe:
        probes = [(result.get('probe_usage') or {}).get('total') or {} for result in summary.get('results', [])]
        probe = {'total_input_tokens': sum(p.get('input_tokens', 0) for p in probes), 'total_output_tokens': sum(p.get('output_tokens', 0) for p in probes)}
    probe_tokens = probe.get('total_input_tokens', 0) + probe.get('total_output_tokens', 0)
    edge_tokens = action_tokens + probe_tokens
    active_parameters = None
    if edge_model_name:
        override = os.environ.get('EDGE_ACTIVE_PARAMETERS', '').strip()
        if override:
            active_parameters = float(override)
        else:
            active_parameters = _match_model_config(edge_model_name, METRIC_CONFIG['edge_active_parameters'])
        if active_parameters is not None and (not math.isfinite(active_parameters) or active_parameters <= 0):
            raise ValueError('EDGE_ACTIVE_PARAMETERS must be a finite positive parameter count')
    flops = 0 if cloud_only else (2 * active_parameters * edge_tokens if active_parameters is not None else None)
    edge_total.update(active_parameters=active_parameters, action_tokens=action_tokens, probe_tokens=probe_tokens, compute_tokens=edge_tokens, total_flops=flops, total_petaflops=flops / 1e15 if flops is not None else None)
    if 'per_run_avg' in edge:
        edge['per_run_avg'].update(avg_compute_tokens=edge_tokens / repeat, avg_flops=flops / repeat if flops is not None else None, avg_petaflops=flops / repeat / 1e15 if flops is not None else None)

    unified = summary['unified_metrics']
    unified['edge_usage'] = {'model': edge_model_name, 'active_parameters': active_parameters, 'action_tokens': action_tokens, 'probe_tokens': probe_tokens, 'compute_tokens': edge_tokens, 'flops': flops, 'petaflops': flops / 1e15 if flops is not None else None}
    if repeat > 1:
        unified['edge_usage']['per_run_avg'] = {'compute_tokens': edge_tokens / repeat, 'flops': flops / repeat if flops is not None else None, 'petaflops': flops / repeat / 1e15 if flops is not None else None}
    # Use cloud usage for monetary cost and keep executor-chat usage separately.
    unified['usage'].update(cost_usd=cloud_cost, cost_scope='cloud', token_scope='executor_chat')
    usage_total['cost_scope'] = 'executor_chat'
    if 'per_run_avg' in unified['usage']:
        unified['usage']['per_run_avg']['cost_usd'] = round(cloud_cost / repeat, 6) if cloud_cost is not None else None
    if unified.get('cloud_usage'):
        unified['cloud_usage'].update(model=cloud_model_name, raw_input_tokens=cloud_raw, cache_read_tokens=cloud_cache, cost_usd=cloud_cost, observed_cost_usd=observed_cost, missing_usage_requests=missing_usage)
        if 'per_run_avg' in unified['cloud_usage']:
            unified['cloud_usage']['per_run_avg'].update(raw_input_tokens=cloud_raw / repeat, cache_read_tokens=cloud_cache / repeat, cost_usd=round(cloud_cost / repeat, 6) if cloud_cost is not None else None)
_PRIVACY_AUDIT_SRC_PATH = Path(__file__).resolve().parent / 'privacy_audit.py'

def _load_privacy_audit_module_src() -> str:
    """Load privacy_audit.py and wrap it into an inline module so task graders"""
    if not _PRIVACY_AUDIT_SRC_PATH.exists():
        return ''
    src = _PRIVACY_AUDIT_SRC_PATH.read_text(encoding='utf-8')
    return "import sys as _sys, types as _types\n_pa_mod = _types.ModuleType('privacy_audit')\n_pa_src = " + repr(src) + "\nexec(compile(_pa_src, '<privacy_audit>', 'exec'), _pa_mod.__dict__)\n_sys.modules['privacy_audit'] = _pa_mod\n"
_LLM_JUDGE_LOG_PATCH = '\nimport threading as _threading, time as _time, json as _json_log, pathlib as _pathlib_log, os as _os_log\n\n_judge_log_path = _pathlib_log.Path("/tmp/llm_judge.jsonl")\n_judge_log_lock = _threading.Lock()\n_judge_context = _threading.local()\n\ndef _set_judge_type(t: str):\n    _judge_context.type = t\n\ndef _get_judge_type() -> str:\n    return getattr(_judge_context, "type", "completion")\n\nimport builtins as _builtins\n_builtins._set_judge_type = _set_judge_type\n\ndef _patch_openai_judge_logging():\n    try:\n        from openai.resources.chat.completions import Completions\n    except ImportError:\n        return\n    _orig_create = Completions.create\n    def _truncate_content(text, max_words=100):\n        if not text or not isinstance(text, str):\n            return text\n        words = text.split()\n        if len(words) <= max_words * 2:\n            return text\n        return " ".join(words[:max_words]) + " [...TRUNCATED...] " + " ".join(words[-max_words:])\n\n    def _truncate_messages(msgs):\n        out = []\n        for m in msgs:\n            mc = dict(m)\n            if isinstance(mc.get("content"), str):\n                mc["content"] = _truncate_content(mc["content"])\n            out.append(mc)\n        return out\n\n    def _logged_create(self, *args, **kwargs):\n        model = kwargs.get("model", "")\n        messages = kwargs.get("messages", [])\n        t0 = _time.time()\n        error_str = None\n        output_content = None\n        try:\n            resp = _orig_create(self, *args, **kwargs)\n            output_content = resp.choices[0].message.content if resp.choices else None\n            return resp\n        except Exception as _e:\n            error_str = str(_e)\n            for _name in ("JUDGE_API_KEY", "OPENROUTER_API_KEY"):\n                for _secret in _os_log.environ.get(_name, "").split(","):\n                    if _secret:\n                        error_str = error_str.replace(_secret, "***")\n            raise\n        finally:\n            latency_ms = int((_time.time() - t0) * 1000)\n            entry = {\n                "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()),\n                "type": _get_judge_type(),\n                "model": model,\n                "reasoning_effort": kwargs.get("reasoning_effort"),\n                "max_completion_tokens": kwargs.get("max_completion_tokens"),\n                "messages": _truncate_messages(messages),\n                "output": _truncate_content(output_content),\n                "error": error_str,\n                "latency_ms": latency_ms,\n            }\n            with _judge_log_lock:\n                with open(_judge_log_path, "a", encoding="utf-8") as _f:\n                    _f.write(_json_log.dumps(entry, ensure_ascii=False, default=str) + "\\n")\n    Completions.create = _logged_create\n\n_patch_openai_judge_logging()\n'
_JUDGE_TELEMETRY_PATCH = '\n_judge_attempts = []\ndef _redact_judge_secrets_jt(value):\n    import os as _os_redact_jt\n    text = str(value)\n    for name in ("JUDGE_API_KEY", "OPENROUTER_API_KEY"):\n        for secret in _os_redact_jt.environ.get(name, "").split(","):\n            if secret:\n                text = text.replace(secret, "***")\n    return text\ndef _install_judge_telemetry():\n    import datetime as _dt_jt, json as _json_jt, os as _os_jt\n    import threading as _th_jt, time as _time_jt\n    try:\n        from openai.resources.chat.completions import Completions\n    except Exception:\n        return\n    _lock_jt = _th_jt.Lock()\n    _active_jt = [0]\n    _orig_jt_create = Completions.create\n    def _prompt_chars_jt(messages):\n        total = 0\n        for message in messages or []:\n            content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")\n            if isinstance(content, str):\n                total += len(content)\n            else:\n                total += len(_json_jt.dumps(content, ensure_ascii=False, default=str))\n        return total\n    def _telemetry_create(self, *args, **kwargs):\n        client = self._client\n        base = str(getattr(client, "base_url", "")).rstrip("/")\n        key = str(getattr(client, "api_key", "") or "")\n        request_bytes = len(_json_jt.dumps(kwargs, ensure_ascii=False, default=str).encode("utf-8"))\n        with _lock_jt:\n            _active_jt[0] += 1\n            active = _active_jt[0]\n        meta = {\n            "started_at": _dt_jt.datetime.now(_dt_jt.timezone.utc).isoformat(),\n            "duration_ms": None,\n            "endpoint": base + "/chat/completions",\n            "key_suffix": "..." + key[-6:] if len(key) > 6 else "***",\n            "request_bytes": request_bytes,\n            "prompt_chars": _prompt_chars_jt(kwargs.get("messages", [])),\n            "proxy_enabled": any(_os_jt.environ.get(n) for n in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")),\n            "http_status": None,\n            "error_type": None,\n            "error": None,\n            "active_judge_requests": active,\n        }\n        _judge_attempts.append(meta)\n        started = _time_jt.perf_counter()\n        try:\n            response = _orig_jt_create(self, *args, **kwargs)\n            meta["http_status"] = 200\n            return response\n        except Exception as exc:\n            response = getattr(exc, "response", None)\n            root_exc = exc\n            seen_exc = set()\n            while id(root_exc) not in seen_exc:\n                seen_exc.add(id(root_exc))\n                next_exc = getattr(root_exc, "__cause__", None) or getattr(root_exc, "__context__", None)\n                if not isinstance(next_exc, BaseException):\n                    break\n                root_exc = next_exc\n            meta["http_status"] = getattr(exc, "status_code", None) or getattr(response, "status_code", None)\n            meta["error_type"] = type(root_exc).__name__\n            meta["error"] = _redact_judge_secrets_jt(exc)\n            raise\n        finally:\n            meta["duration_ms"] = round((_time_jt.perf_counter() - started) * 1000)\n            with _lock_jt:\n                _active_jt[0] -= 1\n    Completions.create = _telemetry_create\n_install_judge_telemetry()\n'
_JUDGE_POOL_PATCH = '\ndef _install_judge_pool_rotation():\n    import os as _os_jp\n    _bases = [b.strip() for b in _os_jp.environ.get("JUDGE_BASE_URL", "").split(",") if b.strip()]\n    _keys = [k.strip() for k in _os_jp.environ.get("JUDGE_API_KEY", "").split(",") if k.strip()]\n    if not _bases or not _keys:\n        return\n    if len(_bases) == 1:\n        endpoints = [(_bases[0], k) for k in _keys]\n    elif len(_bases) == len(_keys):\n        endpoints = list(zip(_bases, _keys))\n    else:\n        # Mirrors grading.resolve_judge_endpoint_pool. Never collapse to the\n        # first pair: that silently runs at a fraction of the intended judge\n        # throughput. The wording keeps the deterministic marker so the retry\n        # policy stops instead of re-running a config error.\n        raise RuntimeError(\n            "JUDGE_BASE_URL / JUDGE_API_KEY pool mismatch: %d base URL(s) vs %d key(s). "\n            "Use 1 base URL with N keys, or N base URLs paired with N keys."\n            % (len(_bases), len(_keys))\n        )\n    try:\n        from openai.resources.chat.completions import Completions\n    except Exception:\n        return\n    import threading as _th, itertools as _it\n    _lock = _th.Lock()\n    _counter = _it.count()\n    _orig_pool_create = Completions.create\n    def _pooled_create(self, *args, **kwargs):\n        n = len(endpoints)\n        with _lock:\n            start = next(_counter)\n            start += int(_os_jp.environ.get("JUDGE_ENDPOINT_OFFSET", "0"))\n        base, key = endpoints[start % n]\n        client = self._client\n        try:\n            client.base_url = base.rstrip("/") + "/"\n        except Exception:\n            pass\n        try:\n            client.api_key = key\n        except Exception:\n            pass\n        try:\n            client.max_retries = 0\n        except Exception:\n            pass\n        return _orig_pool_create(self, *args, **kwargs)\n    Completions.create = _pooled_create\n\n_install_judge_pool_rotation()\n'
_JUDGE_CONFIG_PATCH = "\ndef _install_judge_configuration():\n    import os as _os_jc\n    from openai.resources.chat.completions import Completions\n    _orig_jc = Completions.create\n    def _configured_judge(self, *args, **kwargs):\n        kwargs['model'] = _os_jc.environ.get('JUDGE_MODEL') or 'gpt-5.4-mini'\n        kwargs.setdefault('reasoning_effort', _os_jc.environ.get('JUDGE_REASONING_EFFORT') or 'medium')\n        if 'max_tokens' in kwargs:\n            kwargs.setdefault('max_completion_tokens', kwargs.pop('max_tokens'))\n        return _orig_jc(self, *args, **kwargs)\n    Completions.create = _configured_judge\n_install_judge_configuration()\n"
_TELEMETRY_KEYS = ('_judge_attempts',)

def _scores_without_telemetry(scores):
    if not isinstance(scores, dict):
        return scores
    return {k: v for (k, v) in scores.items() if k not in _TELEMETRY_KEYS}

class JudgePoolConfigError(ValueError):
    """JUDGE_BASE_URL / JUDGE_API_KEY describe a pool that cannot be honoured."""

def resolve_judge_endpoint_pool(base_urls: str | None=None, api_keys: str | None=None) -> list[tuple[str, str]]:
    """Expand JUDGE_BASE_URL / JUDGE_API_KEY into the (base_url, api_key) pool."""
    raw_bases = base_urls if base_urls is not None else os.environ.get('JUDGE_BASE_URL', '')
    raw_keys = api_keys if api_keys is not None else os.environ.get('JUDGE_API_KEY', '')
    bases = [b.strip() for b in (raw_bases or '').split(',') if b.strip()]
    keys = [k.strip() for k in (raw_keys or '').split(',') if k.strip()]
    if not bases or not keys:
        return []
    if len(bases) == 1:
        return [(bases[0], k) for k in keys]
    if len(bases) == len(keys):
        return list(zip(bases, keys))
    raise JudgePoolConfigError(f'JUDGE_BASE_URL / JUDGE_API_KEY pool mismatch: {len(bases)} base URL(s) vs {len(keys)} key(s). Use 1 base URL with N keys, or N base URLs paired with N keys.')

def run_grading(task_id: str, automated_checks: str, output_dir: Path, judge_endpoint_offset: int=0) -> dict:
    logger.info('[%s] Starting in-container grading...', task_id)
    env_remap = "import os\n_jb = os.environ.get('JUDGE_BASE_URL', '')\n_jk = os.environ.get('JUDGE_API_KEY', '')\nif _jb:\n    os.environ['OPENROUTER_BASE_URL'] = _jb.split(',')[0].strip()\nif _jk:\n    os.environ['OPENROUTER_API_KEY'] = _jk.split(',')[0].strip()\n"
    privacy_audit_src = _load_privacy_audit_module_src()
    runner_code = '\n'.join(['import json, sys', env_remap, f"os.environ['JUDGE_ENDPOINT_OFFSET'] = '{judge_endpoint_offset}'", _LLM_JUDGE_LOG_PATCH, _JUDGE_TELEMETRY_PATCH, _JUDGE_POOL_PATCH, _JUDGE_CONFIG_PATCH, privacy_audit_src, automated_checks, '', 'try:', f'    result = grade(transcript=[], workspace_path="{TMP_WORKSPACE}")', 'except Exception as _grade_exc:', "    result = {'error': f'{type(_grade_exc).__name__}: {_redact_judge_secrets_jt(_grade_exc)}'}", "if isinstance(result, dict): result['_judge_attempts'] = _judge_attempts", 'print(json.dumps(result))']) + '\n'
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(runner_code)
        tmp_host = f.name
    try:
        r = subprocess.run(['docker', 'cp', tmp_host, f'{task_id}:/tmp/_grade_runner.py'], capture_output=True, text=True)
        if r.returncode != 0:
            logger.error('[%s] docker cp failed: %s', task_id, r.stderr)
            return {'error': f'docker cp failed: {r.stderr}'}
        subprocess.run(['docker', 'exec', task_id, 'pip', 'install', '-q', 'json_repair'], capture_output=True, text=True, timeout=30)
        r = subprocess.run(['docker', 'exec', task_id, 'python3', '/tmp/_grade_runner.py'], capture_output=True, text=True, timeout=1200)
        if r.returncode != 0:
            logger.error('[%s] Grading script execution failed: %s', task_id, r.stderr)
            return {'error': f'grade script failed: {r.stderr}'}
        try:
            scores = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            scores = None
            for line in reversed(r.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        scores = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if scores is None:
                logger.error('[%s] Failed to parse grading result, no valid JSON found in stdout\nstdout: %s', task_id, r.stdout[:500])
                return {'error': 'json parse failed: no valid JSON in stdout'}
    finally:
        Path(tmp_host).unlink(missing_ok=True)
    score_path = output_dir / 'score.json'
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(_scores_without_telemetry(scores), indent=2, ensure_ascii=False), encoding='utf-8')
    logger.info('[%s] Grading results written to → %s', task_id, score_path)
    judge_log_host = output_dir / 'llm_judge.jsonl'
    r_jl = subprocess.run(['docker', 'cp', f'{task_id}:/tmp/llm_judge.jsonl', str(judge_log_host)], capture_output=True, text=True)
    if r_jl.returncode == 0 and judge_log_host.exists():
        logger.info('[%s] Collected llm_judge.jsonl (%d bytes)', task_id, judge_log_host.stat().st_size)
    return scores

def print_summary(results: list[dict], category: str, output_dir: Path, model_name: str, expected_tasks: int=0, wall_clock_time: float=0.0, *, edge_model_name: str | None=None, cloud_model_name: str | None=None) -> None:
    per_task = os.environ.get('LOG_VERBOSITY', 'compact') != 'compact'
    print(f"\n{'#' * 60}")
    print(f'  Summary Report — {category}')
    print(f"{'#' * 60}")
    all_scores: dict[str, float] = {}
    for r in results:
        task_id = r['task_id']
        if r.get('error'):
            print(f"  ✗ {task_id}: {r['error']}")
            continue
        scores = r['scores']
        if not scores:
            print(f'  - {task_id}: No scores')
            continue
        if 'error' in scores:
            print(f"  ✗ {task_id}: Grading error {scores['error']}")
            continue
        numeric_dict = {k: v for (k, v) in scores.items() if isinstance(v, (int, float))}
        if not numeric_dict:
            print(f'  - {task_id}: No valid numeric scores')
            continue
        avg = sum(numeric_dict.values()) / len(numeric_dict)
        if per_task:
            print(f'  ✓ {task_id}: avg {avg:.2f}  ({len(numeric_dict)} items)')
        final_score_val = numeric_dict.get('overall_score', avg)
        all_scores[task_id] = final_score_val
    if all_scores and per_task:
        print('\n  Final scores per task (overall / privacy):')
        for (k, score) in sorted(all_scores.items()):
            bar = '█' * int(score * 10) + '░' * (10 - int(score * 10))
            priv = ''
            for r in results:
                if r['task_id'] == k and r.get('scores'):
                    ps = r['scores'].get('privacy_score')
                    if ps is not None:
                        pbar = '█' * int(ps * 10) + '░' * (10 - int(ps * 10))
                        priv = f'  privacy: {pbar} {ps:.2f}'
                    break
            print(f'    {bar} {score:.2f}  {k}{priv}')
    if per_task:
        print('\n  Token usage and cost per task:')
        print(f"    {'Task ID':<55} {'Output Tokens':>12} {'Cost(USD)':>12}")
        print(f"    {'-' * 55} {'-' * 12} {'-' * 12}")
    total_output_tokens = 0
    total_cost_usd = 0.0
    for r in sorted(results, key=lambda x: x['task_id']):
        usage = r.get('usage', {})
        out_tok = usage.get('output_tokens', 0)
        cost = usage.get('cost_usd', 0.0)
        total_output_tokens += out_tok
        total_cost_usd += cost
        if per_task:
            print(f"    {r['task_id']:<55} {out_tok:>12} {cost:>11.4f}$")
    if per_task:
        print(f"    {'Total':<55} {total_output_tokens:>12} {total_cost_usd:>11.4f}$")
    else:
        print(f'\n  Output tokens total: {total_output_tokens}  cost: {total_cost_usd:.4f}$')
    total_elapsed = sum((r.get('usage', {}).get('elapsed_time', 0.0) for r in results))
    print(f'\n  Time — sum of per-task elapsed: {total_elapsed:.2f}s | actual wall-clock: {wall_clock_time:.2f}s')
    from datetime import datetime
    date_tag = datetime.now().strftime('%Y%m%d')
    summary_path = output_dir / f'0_summary_{model_name}_{date_tag}.json'
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    def make_bar(val):
        return '█' * int(val * 10) + '░' * (10 - int(val * 10))
    final_scores_table = {}
    pass_count = 0
    for (tid, score) in sorted(all_scores.items()):
        passed = score > PASS_THRESHOLD
        if passed:
            pass_count += 1
        entry = {'overall_score': score, 'overall_bar': make_bar(score), 'pass': passed}
        for r in results:
            if r['task_id'] == tid and r.get('scores'):
                ps = r['scores'].get('privacy_score')
                if ps is not None:
                    entry['privacy_score'] = ps
                    entry['privacy_bar'] = make_bar(ps)
                break
        final_scores_table[tid] = entry
    returned_tasks = len(results)
    scored_tasks = len(all_scores)
    total_tasks = max(expected_tasks, returned_tasks)
    missing_score_tasks = total_tasks - scored_tasks
    global_avg = sum(all_scores.values()) / total_tasks if total_tasks > 0 else 0.0
    pass_rate = pass_count / total_tasks if total_tasks else 0.0
    zero_token_tasks = sum((1 for r in results if not r.get('error') and (not r.get('_score_recovered_from_disk')) and (int((r.get('usage') or {}).get('output_tokens', 0) or 0) == 0) and (int((r.get('usage') or {}).get('input_tokens', 0) or 0) == 0)))
    zero_token_rate = zero_token_tasks / returned_tasks if returned_tasks else 0.0
    glimpse_failed_tasks = 0
    glimpse_fail_events = 0
    for r in results:
        tid = r.get('task_id')
        if not tid:
            continue
        log_path = output_dir / tid / 'agent.log'
        try:
            if not log_path.exists():
                continue
            text = log_path.read_text(errors='replace')
        except OSError:
            continue
        hits = text.count('glimpse_failed')
        if hits:
            glimpse_failed_tasks += 1
            glimpse_fail_events += hits
    glimpse_failed_rate = glimpse_failed_tasks / returned_tasks if returned_tasks else 0.0
    probe_tasks = [r for r in results if r.get('probe_usage')]
    total_probe_usage = None
    if probe_tasks:

        def _psum(role: str, field: str) -> int:
            return sum(((r['probe_usage'].get(role) or {}).get(field, 0) for r in probe_tasks))
        total_probe_usage = {'tasks_with_probe': len(probe_tasks), 'total_tasks': total_tasks, 'judge': {'calls': _psum('judge', 'calls'), 'input_tokens': _psum('judge', 'input_tokens'), 'output_tokens': _psum('judge', 'output_tokens')}, 'total_calls': _psum('total', 'calls'), 'total_input_tokens': _psum('total', 'input_tokens'), 'total_output_tokens': _psum('total', 'output_tokens'), 'total_tokens': _psum('total', 'total_tokens'), '_note': '路由探针消耗的端侧 token,不含在 edge_usage 里。探针 max_tokens=1 但要跑完整 prefill,输入量约等于真实请求'}
    privacy_scores_list = [r['scores']['privacy_score'] for r in results if r.get('scores') and r['scores'].get('privacy_score') is not None]
    privacy_avg = sum(privacy_scores_list) / len(privacy_scores_list) if privacy_scores_list else None
    if all_scores:
        bar = make_bar(global_avg)
        print(f'\n  Completed tasks: {scored_tasks} / {total_tasks}  (returned {returned_tasks})')
        if missing_score_tasks > 0:
            print(f'  Tasks without valid score: {missing_score_tasks}')
        print(f'  Overall average:  {bar} {global_avg:.4f}')
        print(f'  pass (>{PASS_THRESHOLD}): {pass_count}/{total_tasks} ({pass_rate:.2%})')
        if total_probe_usage:
            print(f"  路由探针(未计入 edge): {total_probe_usage['total_calls']} 次调用, 输入 {total_probe_usage['total_input_tokens']:,} tokens")
        if zero_token_tasks:
            print(f'  ⚠ 零 token 任务: {zero_token_tasks}/{returned_tasks} ({zero_token_rate:.1%}) — 端侧一步未走,分数偏低源于基础设施而非模型')
        if glimpse_failed_tasks:
            print(f'  ⚠ glimpse 探针失败: {glimpse_failed_tasks}/{returned_tasks} ({glimpse_failed_rate:.1%}, 共 {glimpse_fail_events} 次) — rollout 中途 503,cost 与分数均偏低,慎用此批数据')
        if privacy_avg is not None:
            pbar = make_bar(privacy_avg)
            print(f'  Privacy average:  {pbar} {privacy_avg:.4f}')
    turns_list = [r.get('usage', {}).get('n_turns', r.get('usage', {}).get('request_count', 0)) for r in results]
    total_turns = sum(turns_list)
    raw_input = sum((r.get('usage', {}).get('input_tokens', 0) for r in results))
    cache_read = sum((r.get('usage', {}).get('cache_read_tokens', 0) for r in results))
    total_usage = {'total_input_tokens': raw_input, 'total_output_tokens': total_output_tokens, 'total_cache_read_tokens': cache_read, 'total_cache_write_tokens': sum((r.get('usage', {}).get('cache_write_tokens', 0) for r in results)), 'total_actual_input_tokens': raw_input + cache_read, 'total_request_count': sum((r.get('usage', {}).get('request_count', 0) for r in results)), 'total_turns': total_turns, 'avg_turns_per_task': round(total_turns / total_tasks, 2) if total_tasks else 0, 'max_turns': max(turns_list) if turns_list else 0, 'min_turns': min(turns_list) if turns_list else 0, 'total_elapsed_time': round(sum((r.get('usage', {}).get('elapsed_time', 0.0) for r in results)), 2), 'wall_clock_time': round(wall_clock_time, 2), 'total_cost_usd': round(total_cost_usd, 6), '_note': _usage_note(include_cache_write=False)}
    cloud_tasks = [r for r in results if r.get('cloud_usage')]
    task_routed_cloud = [r for r in results if r.get('task_route') == 'CLOUD']
    total_cloud_usage = None
    if cloud_tasks:
        total_prompt = sum((r['cloud_usage'].get('prompt_tokens', 0) for r in cloud_tasks))
        total_completion = sum((r['cloud_usage'].get('completion_tokens', 0) for r in cloud_tasks))
        total_cloud_tokens = sum((r['cloud_usage'].get('total_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_read = sum((r['cloud_usage'].get('cache_read_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_write = sum((r['cloud_usage'].get('cache_write_tokens', 0) for r in cloud_tasks))
        total_cloud_req = sum((r['cloud_usage'].get('request_count', 0) for r in cloud_tasks))
        total_latency = sum((r['cloud_usage'].get('total_latency_ms', 0) for r in cloud_tasks))
        n_cloud_tasks = len(cloud_tasks)
        total_cloud_raw = _cloud_raw_input(total_prompt, total_cloud_cache_read, total_cloud_cache_write)
        total_cloud_usage = {'tasks_with_cloud_calls': n_cloud_tasks, 'total_tasks': total_tasks, 'cloud_call_rate': round(n_cloud_tasks / total_tasks, 4) if total_tasks else 0, 'total_request_count': total_cloud_req, 'avg_cloud_calls_per_task': round(total_cloud_req / total_tasks, 2) if total_tasks else 0, 'total_prompt_tokens': total_prompt, 'total_raw_input_tokens': total_cloud_raw, 'total_cache_read_tokens': total_cloud_cache_read, 'total_cache_write_tokens': total_cloud_cache_write, 'total_completion_tokens': total_completion, 'total_tokens': total_cloud_tokens, 'cloud_token_share': 0, 'cache_hit_rate': round(total_cloud_cache_read / total_prompt, 4) if total_prompt else 0, 'cache_write_rate': round(total_cloud_cache_write / total_prompt, 4) if total_prompt else 0, 'total_latency_ms': total_latency, 'avg_latency_ms': round(total_latency / total_cloud_req) if total_cloud_req else 0, 'source': 'audit_jsonl'}
    elif task_routed_cloud:
        total_cloud_usage = _task_route_cloud_usage(task_routed_cloud, total_tasks)
    elif _is_cloud_only_model(model_name):
        total_cloud_usage = _cloud_only_cloud_usage(total_usage, total_tasks)
    _u_total_tokens = total_usage['total_input_tokens'] + total_usage['total_cache_read_tokens'] + total_usage['total_cache_write_tokens'] + total_usage['total_output_tokens']
    unified_metrics = build_unified_metrics(overall_score=global_avg if all_scores else None, pass_rate=pass_rate if all_scores else None, usage={'input_tokens': total_usage['total_input_tokens'], 'cache_read_tokens': total_usage['total_cache_read_tokens'], 'output_tokens': total_usage['total_output_tokens'], 'total_tokens': _u_total_tokens, 'cost_usd': total_usage['total_cost_usd'], 'request_count': total_usage['total_request_count']}, cloud_usage={'calls': total_cloud_usage.get('total_request_count', 0), 'tasks_with_cloud_calls': total_cloud_usage.get('tasks_with_cloud_calls', 0), 'prompt_tokens': total_cloud_usage.get('total_prompt_tokens', 0), 'completion_tokens': total_cloud_usage.get('total_completion_tokens', 0), 'total_tokens': total_cloud_usage.get('total_tokens', 0), 'cloud_call_rate': total_cloud_usage.get('cloud_call_rate', 0), 'avg_latency_ms': total_cloud_usage.get('avg_latency_ms', 0)} if total_cloud_usage else None, wall_clock_seconds=wall_clock_time, total_task_seconds=total_usage['total_elapsed_time'])
    summary_data = {'unified_metrics': unified_metrics, 'category': category, 'model': model_name, 'total_tasks': total_tasks, 'returned_tasks': returned_tasks, 'scored_tasks': scored_tasks, 'overall_average': round(global_avg, 4), 'overall_bar': make_bar(global_avg), 'pass_threshold': PASS_THRESHOLD, 'pass_count': pass_count, 'pass_rate': round(pass_rate, 4), 'zero_token_tasks': zero_token_tasks, 'zero_token_rate': round(zero_token_rate, 4), 'glimpse_failed_tasks': glimpse_failed_tasks, 'glimpse_failed_rate': round(glimpse_failed_rate, 4), 'glimpse_fail_events': glimpse_fail_events, 'privacy_exposure': round(1 - privacy_avg, 4) if privacy_avg is not None else None, 'privacy_average': round(privacy_avg, 4) if privacy_avg is not None else None, 'privacy_bar': make_bar(privacy_avg) if privacy_avg is not None else None, 'usage': total_usage}
    if total_cloud_usage:
        cloud_source = total_cloud_usage.get('source', 'audit_jsonl')
        is_step_router = _is_step_router_model(model_name)
        _split = _edge_component_split(total_usage, total_cloud_usage, model_name)
        edge_input = _split['input']
        edge_cache_read = _split['cache_read']
        edge_cache_write = _split['cache_write']
        edge_output = _split['output']
        edge_req = _split['request']
        edge_tokens = edge_input + edge_cache_read + edge_cache_write + edge_output
        local_plus_cloud = edge_tokens + total_cloud_usage.get('total_tokens', 0)
        total_cloud_usage['cloud_token_share'] = round(total_cloud_usage.get('total_tokens', 0) / local_plus_cloud, 4) if local_plus_cloud else 0
        summary_data['edge_usage'] = {'total_request_count': edge_req, 'total_input_tokens': edge_input, 'total_raw_input_tokens': edge_input, 'total_cache_read_tokens': edge_cache_read, 'total_cache_write_tokens': edge_cache_write, 'total_output_tokens': edge_output, 'total_tokens': edge_tokens, 'edge_token_share': round(edge_tokens / local_plus_cloud, 4) if local_plus_cloud else 0, 'cache_hit_rate': round(edge_cache_read / (edge_input + edge_cache_read + edge_cache_write), 4) if edge_input + edge_cache_read + edge_cache_write else 0}
        if is_step_router and cloud_source == 'audit_jsonl':
            combined_usage = _normalize_step_router_total_usage(total_usage, summary_data['edge_usage'], total_cloud_usage)
            summary_data['combined_usage'] = combined_usage
            summary_data['usage'] = _edge_usage_as_total_usage(total_usage, summary_data['edge_usage'], model_name, total_tasks)
        summary_data['cloud_usage'] = total_cloud_usage
    if total_probe_usage:
        _edge_in = total_usage.get('total_actual_input_tokens') or 0
        total_probe_usage['input_vs_edge_ratio'] = round(total_probe_usage['total_input_tokens'] / _edge_in, 4) if _edge_in else None
        summary_data['probe_usage'] = total_probe_usage
    if not total_cloud_usage:
        has_routing = any((r.get('task_route') for r in results))
        if has_routing:
            edge_input = total_usage['total_input_tokens']
            edge_cr = total_usage['total_cache_read_tokens']
            edge_cw = total_usage['total_cache_write_tokens']
            edge_out = total_usage['total_output_tokens']
            edge_req = total_usage['total_request_count']
            edge_tok = edge_input + edge_cr + edge_cw + edge_out
            edge_input_total = edge_input + edge_cr + edge_cw
            edge_cache_hit_rate = round(edge_cr / edge_input_total, 4) if edge_input_total else 0
            summary_data['edge_usage'] = {'total_request_count': edge_req, 'total_input_tokens': edge_input, 'total_raw_input_tokens': edge_input, 'total_cache_read_tokens': edge_cr, 'total_cache_write_tokens': edge_cw, 'total_output_tokens': edge_out, 'total_tokens': edge_tok, 'edge_token_share': 1.0, 'cache_hit_rate': edge_cache_hit_rate, 'note': 'all tasks routed LOCAL — no cloud calls'}
            summary_data['cloud_usage'] = None
        else:
            summary_data['edge_usage'] = None
            summary_data['cloud_usage'] = None
            summary_data['_routing_note'] = _no_split_note(model_name)
    summary_data['final_scores'] = final_scores_table
    summary_data['results'] = results
    _attach_resource_metrics(summary_data, model_name, edge_model_name=edge_model_name, cloud_model_name=cloud_model_name, results=results)
    summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f'\n  Summary written to → {summary_path}')
    print('#' * 60)

def extract_usage_from_jsonl(jsonl_path: Path, model_name: str='') -> dict:
    totals = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0, 'request_count': 0}
    if not jsonl_path.exists():
        return totals
    pricing = _match_pricing(model_name) if model_name else None
    estimated_cost = 0.0
    for line in jsonl_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get('type') != 'message':
            continue
        msg = entry.get('message', {})
        if msg.get('role') != 'assistant':
            continue
        totals['request_count'] += 1
        usage = msg.get('usage', {})
        raw_input = usage.get('input', 0)
        cache_read = usage.get('cacheRead', 0)
        cache_write = usage.get('cacheWrite', 0)
        output_tok = usage.get('output', 0)
        totals['input_tokens'] += raw_input
        totals['output_tokens'] += output_tok
        totals['cache_read_tokens'] += cache_read
        totals['cache_write_tokens'] += cache_write
        totals['total_tokens'] += usage.get('totalTokens', 0)
        cost = usage.get('cost', {})
        totals['cost_usd'] += cost.get('total', 0.0)
        if pricing:
            estimated_cost += _estimate_message_cost(pricing, raw_input, cache_read, output_tok)
    if totals['cost_usd'] == 0.0 and estimated_cost > 0:
        totals['cost_usd'] = estimated_cost
    totals['cost_usd'] = round(totals['cost_usd'], 6)
    totals['n_turns'] = totals['request_count']
    return totals

def extract_probe_usage(output_dir: Path) -> dict | None:
    """Read edge token usage for routing probes from router_usage.json."""
    path = output_dir / 'router_usage.json'
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for role in ('judge', 'total'):
        blk = data.get(role)
        if isinstance(blk, dict):
            out[role] = {'calls': blk.get('calls', 0), 'input_tokens': blk.get('input_tokens', 0), 'output_tokens': blk.get('output_tokens', 0), 'total_tokens': blk.get('total_tokens', 0)}
    if not out or not (out.get('total') or {}).get('calls'):
        return None
    return out

def extract_cloud_usage(output_dir: Path) -> dict | None:
    """Parse cloud_assistant_audit.jsonl to aggregate cloud model token usage."""
    audit_path = output_dir / 'cloud_assistant_audit.jsonl'
    if not audit_path.exists() or audit_path.stat().st_size == 0:
        return None
    totals = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0, 'request_count': 0, 'total_latency_ms': 0, 'missing_usage_requests': 0}
    for entry in read_cloud_audit_records(audit_path):
        if not (entry.get('model') or 'prompt_tokens' in entry or 'total_tokens' in entry or entry.get('status') == 'incomplete'):
            continue
        totals['request_count'] += 1
        complete = entry.get('usage_recorded', 'prompt_tokens' in entry and 'completion_tokens' in entry)
        if not complete or entry.get('status') in ('pending', 'error', 'incomplete'):
            totals['missing_usage_requests'] += 1
        totals['prompt_tokens'] += entry.get('prompt_tokens', 0)
        totals['completion_tokens'] += entry.get('completion_tokens', 0)
        totals['total_tokens'] += entry.get('total_tokens', 0)
        totals['cache_read_tokens'] += entry.get('cache_read_tokens', 0)
        totals['cache_write_tokens'] += entry.get('cache_write_tokens', 0)
        totals['total_latency_ms'] += entry.get('latency_ms', 0)
    if totals['request_count'] == 0:
        return None
    totals['avg_latency_ms'] = round(totals['total_latency_ms'] / totals['request_count'])
    return totals

def _is_step_router_model(model_name: str) -> bool:
    """这条轨迹的 chat.jsonl 是不是「端云混合流」?"""
    name = (model_name or '').lower()
    if 'advise' in name:
        return False
    return any((tag in name for tag in ('step-router', 'agentic_executor_router')))

def _task_route_cloud_usage(task_routed_cloud: list[dict], total_tasks: int) -> dict:
    """query-router 等 task 级路由的 cloud usage 汇总(Source 2)。"""
    cloud_in_raw = sum((r.get('usage', {}).get('input_tokens', 0) for r in task_routed_cloud))
    cloud_cache_r = sum((r.get('usage', {}).get('cache_read_tokens', 0) for r in task_routed_cloud))
    cloud_cache_w = sum((r.get('usage', {}).get('cache_write_tokens', 0) for r in task_routed_cloud))
    cloud_out = sum((r.get('usage', {}).get('output_tokens', 0) for r in task_routed_cloud))
    cloud_req = sum((r.get('usage', {}).get('request_count', 0) for r in task_routed_cloud))
    cloud_elapsed = sum((r.get('usage', {}).get('elapsed_time', 0) for r in task_routed_cloud))
    cloud_prompt = cloud_in_raw + cloud_cache_r + cloud_cache_w
    cloud_tok = cloud_prompt + cloud_out
    n_cloud = len(task_routed_cloud)
    return {'tasks_with_cloud_calls': n_cloud, 'total_tasks': total_tasks, 'cloud_call_rate': round(n_cloud / total_tasks, 4) if total_tasks else 0, 'total_request_count': cloud_req, 'avg_cloud_calls_per_task': round(cloud_req / total_tasks, 2) if total_tasks else 0, 'total_prompt_tokens': cloud_prompt, 'total_raw_input_tokens': cloud_in_raw, 'total_cache_read_tokens': cloud_cache_r, 'total_cache_write_tokens': cloud_cache_w, 'total_completion_tokens': cloud_out, 'total_tokens': cloud_tok, 'cloud_token_share': 0, 'total_elapsed_time': round(cloud_elapsed, 2), 'source': 'task_route'}

def _is_cloud_only_model(model_name: str) -> bool:
    """summary 的 model 名里带不带 cloud-only tag。"""
    return '_cloud-only' in (model_name or '')

def _cloud_only_cloud_usage(total_usage: dict, total_tasks: int) -> dict:
    """cloud-only 的 cloud_usage:agent 就是云端模型,整份 chat usage 都是云侧。"""
    raw = total_usage.get('total_input_tokens', 0)
    cr = total_usage.get('total_cache_read_tokens', 0)
    cw = total_usage.get('total_cache_write_tokens', 0)
    out = total_usage.get('total_output_tokens', 0)
    req = total_usage.get('total_request_count', 0)
    prompt = raw + cr + cw
    return {'tasks_with_cloud_calls': total_tasks, 'total_tasks': total_tasks, 'cloud_call_rate': 1.0 if total_tasks else 0, 'total_request_count': req, 'avg_cloud_calls_per_task': round(req / total_tasks, 2) if total_tasks else 0, 'total_prompt_tokens': prompt, 'total_raw_input_tokens': raw, 'total_cache_read_tokens': cr, 'total_cache_write_tokens': cw, 'total_completion_tokens': out, 'total_tokens': prompt + out, 'cloud_token_share': 1.0, 'total_elapsed_time': total_usage.get('total_elapsed_time', 0.0), 'source': 'cloud_only_chat'}

def _no_split_note(model_name: str) -> str:
    """无 cloud_usage 时的 _routing_note 文案。"""
    if _is_cloud_only_model(model_name):
        return 'cloud-only run: agent IS the cloud model — 100% of usage is cloud-side (no audit-based split)'
    return 'single-model run, no edge/cloud split'

def _edge_component_split(total_usage: dict, total_cloud_usage: dict | None, model_name: str) -> dict:
    """chat 口径下 edge 侧五分量(input/cache_read/cache_write/output/request)。"""
    tu = total_usage
    full = {'input': tu.get('total_input_tokens', 0), 'cache_read': tu.get('total_cache_read_tokens', 0), 'cache_write': tu.get('total_cache_write_tokens', 0), 'output': tu.get('total_output_tokens', 0), 'request': tu.get('total_request_count', 0)}
    cu = total_cloud_usage or {}
    if not cu:
        return full
    src = cu.get('source', 'audit_jsonl')
    if src in ('task_route', 'cloud_only_chat'):
        cloud_input = cu.get('total_raw_input_tokens', 0)
    elif _is_step_router_model(model_name):
        cloud_input = _cloud_input_as_chat_usage(cu.get('total_prompt_tokens', 0), cu.get('total_cache_read_tokens', 0))
    else:
        return full
    return {'input': max(full['input'] - cloud_input, 0), 'cache_read': max(full['cache_read'] - cu.get('total_cache_read_tokens', 0), 0), 'cache_write': max(full['cache_write'] - cu.get('total_cache_write_tokens', 0), 0), 'output': max(full['output'] - cu.get('total_completion_tokens', 0), 0), 'request': max(full['request'] - cu.get('total_request_count', 0), 0)}

def _actual_input_tokens(raw_input: int, cache_read: int, cache_write: int=0) -> int:
    return raw_input + cache_read + cache_write

def _cloud_raw_input(prompt_tokens: int, cache_read: int, cache_write: int) -> int:
    return max(prompt_tokens - cache_read - cache_write, 0)

def _cloud_input_as_chat_usage(prompt_tokens: int, cache_read: int) -> int:
    return max(prompt_tokens - cache_read, 0)

def _usage_note(include_cache_write: bool) -> str:
    if include_cache_write:
        return 'total_input_tokens counts non-cached raw tokens; total_actual_input_tokens = total_input_tokens + total_cache_read_tokens + total_cache_write_tokens'
    return 'total_input_tokens counts only non-cached new tokens; total_actual_input_tokens = total_input_tokens + total_cache_read_tokens'

def _normalize_step_router_total_usage(total_usage: dict, edge_usage: dict, cloud_usage: dict) -> dict:
    """Return provider-normalized total usage for step-router summaries."""
    out = dict(total_usage)
    raw_input = edge_usage.get('total_raw_input_tokens', edge_usage.get('total_input_tokens', 0)) + cloud_usage.get('total_raw_input_tokens', 0)
    cache_read = edge_usage.get('total_cache_read_tokens', 0) + cloud_usage.get('total_cache_read_tokens', 0)
    cache_write = edge_usage.get('total_cache_write_tokens', 0) + cloud_usage.get('total_cache_write_tokens', 0)
    output = edge_usage.get('total_output_tokens', 0) + cloud_usage.get('total_completion_tokens', 0)
    request_count = edge_usage.get('total_request_count', 0) + cloud_usage.get('total_request_count', 0)
    out['total_input_tokens'] = raw_input
    out['total_output_tokens'] = output
    out['total_cache_read_tokens'] = cache_read
    out['total_cache_write_tokens'] = cache_write
    out['total_actual_input_tokens'] = _actual_input_tokens(raw_input, cache_read, cache_write)
    out['total_request_count'] = request_count
    out['_note'] = _usage_note(include_cache_write=True)
    out['_normalization'] = 'step-router chat usage split into edge usage plus audited cloud raw/cache_read/cache_write'
    return out

def _edge_usage_as_total_usage(total_usage: dict, edge_usage: dict, model_name: str, denom_tasks: int) -> dict:
    """Return the default top-level usage for step-router summaries."""
    out = dict(total_usage)
    raw_input = edge_usage.get('total_raw_input_tokens', edge_usage.get('total_input_tokens', 0))
    cache_read = edge_usage.get('total_cache_read_tokens', 0)
    cache_write = edge_usage.get('total_cache_write_tokens', 0)
    output = edge_usage.get('total_output_tokens', 0)
    request_count = edge_usage.get('total_request_count', 0)
    out['total_input_tokens'] = raw_input
    out['total_output_tokens'] = output
    out['total_cache_read_tokens'] = cache_read
    out['total_cache_write_tokens'] = cache_write
    out['total_actual_input_tokens'] = _actual_input_tokens(raw_input, cache_read, cache_write)
    out['total_request_count'] = request_count
    out['total_turns'] = request_count
    out['avg_turns_per_task'] = round(request_count / denom_tasks, 2) if denom_tasks else 0
    pricing = _match_pricing(model_name)
    if pricing:
        out['total_cost_usd'] = round(_estimate_message_cost(pricing, raw_input + cache_write, cache_read, output), 6)
    out['_note'] = 'step-router top-level usage is edge/executor only; cloud model usage is reported separately in cloud_usage'
    out['_normalization'] = 'step-router usage=edge_usage; combined_usage=edge_usage+cloud_usage'
    return out

def print_global_summary(results: list[dict], output_dir: Path, model_name: str, repeat: int=1, wall_clock_time: float=0.0, *, edge_model_name: str | None=None, cloud_model_name: str | None=None) -> None:
    print(f"\n{'#' * 60}")
    print('  Global Summary Report — ALL CATEGORIES')
    print(f"{'#' * 60}")
    total_tasks = len(results)
    scored_tasks = 0
    missing_score_tasks = 0
    total_score = 0.0
    for r in results:
        scores = r.get('scores', {})
        if r.get('error') or not scores or 'error' in scores:
            missing_score_tasks += 1
            continue
        numeric = {k: v for (k, v) in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            missing_score_tasks += 1
            continue
        final = numeric.get('overall_score', sum(numeric.values()) / len(numeric))
        total_score += final
        scored_tasks += 1
    global_avg = 0.0
    task_runs: dict[str, list[float]] = {}
    import re
    for result in results:
        tid = re.sub(r'_run\d+', '', result['task_id'])
        match = re.match(r'(.*?task_\d+)(?:_|$)', tid)
        key = match.group(1) if match else tid.rsplit('_', 2)[0]
        scores = result.get('scores') or {}
        numeric = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        score = numeric.get('overall_score', sum(numeric.values()) / len(numeric)) if numeric and not result.get('error') and 'error' not in scores else 0.0
        task_runs.setdefault(key, []).append(score)
    pass_rate = sum(len(runs) == repeat and all(value > PASS_THRESHOLD for value in runs) for runs in task_runs.values()) / len(task_runs) if task_runs else 0.0
    if total_tasks > 0:
        global_avg = total_score / total_tasks
        bar = '█' * int(global_avg * 10) + '░' * (10 - int(global_avg * 10))
        print(f'\n  Completed tasks: {scored_tasks} / {total_tasks}')
        print(f'  Tasks without a valid score.json: {missing_score_tasks}')
        if missing_score_tasks > 0:
            print('  Possible causes: task execution failed, such as OOM, or grading failed.')
        print(f'  Global average: {bar} {global_avg:.4f}')
    else:
        print('  No tasks found')
    turns_list = [r.get('usage', {}).get('n_turns', r.get('usage', {}).get('request_count', 0)) for r in results]
    total_turns = sum(turns_list)
    g_raw_input = sum((r.get('usage', {}).get('input_tokens', 0) for r in results))
    g_cache_read = sum((r.get('usage', {}).get('cache_read_tokens', 0) for r in results))
    total_usage = {'total_input_tokens': g_raw_input, 'total_output_tokens': sum((r.get('usage', {}).get('output_tokens', 0) for r in results)), 'total_cache_read_tokens': g_cache_read, 'total_cache_write_tokens': sum((r.get('usage', {}).get('cache_write_tokens', 0) for r in results)), 'total_actual_input_tokens': g_raw_input + g_cache_read, 'total_request_count': sum((r.get('usage', {}).get('request_count', 0) for r in results)), 'total_turns': total_turns, 'avg_turns_per_task': round(total_turns / total_tasks, 2) if total_tasks else 0, 'max_turns': max(turns_list) if turns_list else 0, 'min_turns': min(turns_list) if turns_list else 0, 'total_elapsed_time': round(sum((r.get('usage', {}).get('elapsed_time', 0.0) for r in results)), 2), 'wall_clock_time': round(wall_clock_time, 2), 'total_cost_usd': round(sum((r.get('usage', {}).get('cost_usd', 0.0) for r in results)), 6), '_note': 'total_input_tokens counts only non-cached new tokens; total_actual_input_tokens = total_input_tokens + total_cache_read_tokens'}
    print('\n  [Model Usage]')
    print(f"  Total input tokens:  {total_usage['total_input_tokens']}  (non-cached)")
    print(f"  Total actual input:  {total_usage['total_actual_input_tokens']}  (non-cached + cache_read)")
    print(f"  Total output tokens: {total_usage['total_output_tokens']}")
    print(f"  Total cache read:    {total_usage['total_cache_read_tokens']}")
    print(f"  Total cache write:   {total_usage['total_cache_write_tokens']}")
    print(f"  Total requests:      {total_usage['total_request_count']}")
    print(f"  Total turns:         {total_turns}  (avg {total_usage['avg_turns_per_task']}/task, range {total_usage['min_turns']}~{total_usage['max_turns']})")
    print(f"  Total elapsed time:  {total_usage['total_elapsed_time']:.2f}s  (sum of per-task)")
    print(f"  Actual wall-clock:   {total_usage['wall_clock_time']:.2f}s  (real batch duration)")
    print(f"  Total cost:          ${total_usage['total_cost_usd']:.4f}")
    cloud_tasks = [r for r in results if r.get('cloud_usage')]
    total_cloud_usage = None
    if cloud_tasks:
        total_prompt = sum((r['cloud_usage'].get('prompt_tokens', 0) for r in cloud_tasks))
        total_completion = sum((r['cloud_usage'].get('completion_tokens', 0) for r in cloud_tasks))
        total_cloud_tokens = sum((r['cloud_usage'].get('total_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_read = sum((r['cloud_usage'].get('cache_read_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_write = sum((r['cloud_usage'].get('cache_write_tokens', 0) for r in cloud_tasks))
        total_cloud_req = sum((r['cloud_usage'].get('request_count', 0) for r in cloud_tasks))
        total_latency = sum((r['cloud_usage'].get('total_latency_ms', 0) for r in cloud_tasks))
        n_cloud_tasks = len(cloud_tasks)
        total_cloud_raw = max(total_prompt - total_cloud_cache_read - total_cloud_cache_write, 0)
        sample_source = (cloud_tasks[0].get('cloud_usage') or {}).get('source', 'audit_jsonl')
        _split = _edge_component_split(total_usage, {'source': sample_source, 'total_prompt_tokens': total_prompt, 'total_raw_input_tokens': total_cloud_raw, 'total_cache_read_tokens': total_cloud_cache_read, 'total_cache_write_tokens': total_cloud_cache_write, 'total_completion_tokens': total_completion, 'total_request_count': total_cloud_req}, model_name)
        edge_tokens = _split['input'] + _split['cache_read'] + _split['cache_write'] + _split['output']
        denom = edge_tokens + total_cloud_tokens
        cache_hit_rate = round(total_cloud_cache_read / total_prompt, 4) if total_prompt else 0
        cache_write_rate = round(total_cloud_cache_write / total_prompt, 4) if total_prompt else 0
        total_cloud_usage = {'source': sample_source, 'tasks_with_cloud_calls': n_cloud_tasks, 'total_tasks': total_tasks, 'cloud_call_rate': round(n_cloud_tasks / total_tasks, 4) if total_tasks else 0, 'total_request_count': total_cloud_req, 'avg_cloud_calls_per_task': round(total_cloud_req / total_tasks, 2) if total_tasks else 0, 'total_prompt_tokens': total_prompt, 'total_raw_input_tokens': total_cloud_raw, 'total_cache_read_tokens': total_cloud_cache_read, 'total_cache_write_tokens': total_cloud_cache_write, 'total_completion_tokens': total_completion, 'total_tokens': total_cloud_tokens, 'cloud_token_share': round(total_cloud_tokens / denom, 4) if denom else 0, 'cache_hit_rate': cache_hit_rate, 'cache_write_rate': cache_write_rate, 'total_latency_ms': total_latency, 'avg_latency_ms': round(total_latency / total_cloud_req) if total_cloud_req else 0}
        print('\n  [Cloud Model Usage]')
        print(f"  Tasks with cloud calls:  {n_cloud_tasks}/{total_tasks} ({total_cloud_usage['cloud_call_rate'] * 100:.1f}%)")
        print(f'  Total cloud requests:    {total_cloud_req}')
        print(f"  Avg cloud calls / task:  {total_cloud_usage['avg_cloud_calls_per_task']}")
        print(f'  Prompt tokens:           {total_prompt}  =  raw {total_cloud_raw} + cache_read {total_cloud_cache_read} + cache_write {total_cloud_cache_write}  (read {cache_hit_rate * 100:.1f}%, write {cache_write_rate * 100:.1f}%)')
        print(f'  Completion tokens:       {total_completion}')
        print(f"  Cloud token share:       {total_cloud_usage['cloud_token_share'] * 100:.2f}% (cloud / (local+cloud))")
        print(f"  Avg latency per call:    {total_cloud_usage['avg_latency_ms']}ms")
    else:
        task_routed_cloud = [r for r in results if r.get('task_route') == 'CLOUD']
        if task_routed_cloud:
            total_cloud_usage = _task_route_cloud_usage(task_routed_cloud, total_tasks)
            _tot = total_usage['total_input_tokens'] + total_usage['total_cache_read_tokens'] + total_usage['total_cache_write_tokens'] + total_usage['total_output_tokens']
            total_cloud_usage['cloud_token_share'] = round(total_cloud_usage['total_tokens'] / _tot, 4) if _tot else 0
            print('\n  [Cloud Model Usage]  (task-level routing)')
            print(f'  Tasks routed CLOUD:      {len(task_routed_cloud)}/{total_tasks}')
            print(f"  Cloud token share:       {total_cloud_usage['cloud_token_share'] * 100:.2f}%")
        elif _is_cloud_only_model(model_name):
            total_cloud_usage = _cloud_only_cloud_usage(total_usage, total_tasks)
            print('\n  [Cloud Model Usage]  (cloud-only: agent IS the cloud model)')
            print('  Cloud token share:       100.00%')
    from datetime import datetime
    date_tag = datetime.now().strftime('%Y%m%d')
    repeat_tag = f'_avg@{repeat}' if repeat > 1 else ''
    summary_path = output_dir / f'0_summary_all_{model_name}{repeat_tag}_{date_tag}.json'
    _u_total_tokens = total_usage['total_input_tokens'] + total_usage['total_cache_read_tokens'] + total_usage['total_cache_write_tokens'] + total_usage['total_output_tokens']
    unified_metrics = build_unified_metrics(overall_score=global_avg if total_tasks else None, pass_rate=pass_rate if total_tasks else None, usage={'input_tokens': total_usage['total_input_tokens'], 'cache_read_tokens': total_usage['total_cache_read_tokens'], 'output_tokens': total_usage['total_output_tokens'], 'total_tokens': _u_total_tokens, 'cost_usd': total_usage['total_cost_usd'], 'request_count': total_usage['total_request_count']}, cloud_usage={'calls': total_cloud_usage.get('total_request_count', 0), 'tasks_with_cloud_calls': total_cloud_usage.get('tasks_with_cloud_calls', 0), 'prompt_tokens': total_cloud_usage.get('total_prompt_tokens', 0), 'completion_tokens': total_cloud_usage.get('total_completion_tokens', 0), 'total_tokens': total_cloud_usage.get('total_tokens', 0), 'cloud_call_rate': total_cloud_usage.get('cloud_call_rate', 0), 'avg_latency_ms': total_cloud_usage.get('avg_latency_ms', 0)} if total_cloud_usage else None, wall_clock_seconds=wall_clock_time, total_task_seconds=total_usage.get('total_elapsed_time', 0.0), repeat=repeat)
    summary_data = {'unified_metrics': unified_metrics, 'global_avg': global_avg if total_tasks else None, 'repeat': repeat, 'task_count': total_tasks, 'scored_task_count': scored_tasks, 'missing_score_task_count': missing_score_tasks, **total_usage, '_routing_note': None if total_cloud_usage else _no_split_note(model_name), 'cloud_usage': total_cloud_usage, 'edge_usage': (lambda _s: {'total_request_count': _s['request'], 'total_raw_input_tokens': _s['input'], 'total_cache_read_tokens': _s['cache_read'], 'total_cache_write_tokens': _s['cache_write'], 'total_output_tokens': _s['output'], 'total_tokens': _s['input'] + _s['cache_read'] + _s['cache_write'] + _s['output']})(_edge_component_split(total_usage, total_cloud_usage, model_name)) if total_cloud_usage else None, 'results': results}
    summary_data['unified_metrics']['performance'].update(pass_definition='all_runs_successful', pass_repeat=repeat)
    _attach_resource_metrics(summary_data, model_name, repeat, edge_model_name=edge_model_name, cloud_model_name=cloud_model_name, results=results)
    summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f'\n  Global summary written to → {summary_path}')
    print('#' * 60)

def print_avg_summary(per_run_results: list[list[dict]], category: str, output_dir: Path, model_name: str, repeat: int, wall_clock_time: float=0.0, *, edge_model_name: str | None=None, cloud_model_name: str | None=None) -> None:
    """Aggregate N runs into an avg@N summary with mean and std per task."""
    import statistics
    print(f"\n{'#' * 60}")
    print(f'  avg@{repeat} Summary — {category}')
    print(f"{'#' * 60}")

    def _extract_overall(r: dict) -> float | None:
        scores = r.get('scores', {})
        if r.get('error') or not scores or 'error' in scores:
            return None
        numeric = {k: v for (k, v) in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            return None
        return numeric.get('overall_score', sum(numeric.values()) / len(numeric))

    def _extract_task_key(r: dict) -> str:
        """把 task_id 归一化成跨 repeat 稳定的 key。"""
        import re
        tid = re.sub('_run\\d+', '', r['task_id'])
        m = re.match('(.*?task_\\d+)(?:_|$)', tid)
        return m.group(1) if m else tid.rsplit('_', 2)[0]
    task_runs: dict[str, list[float]] = {}
    task_privacy_runs: dict[str, list[float]] = {}
    for (run_index, results) in enumerate(per_run_results):
        seen_this_run: set[str] = set()
        for r in results:
            tkey = _extract_task_key(r)
            seen_this_run.add(tkey)
            ov = _extract_overall(r)
            runs_list = task_runs.get(tkey)
            if runs_list is None:
                runs_list = task_runs[tkey] = [0.0] * run_index
            runs_list.append(ov if ov is not None else 0.0)
            ps = (r.get('scores') or {}).get('privacy_score')
            if ps is not None:
                task_privacy_runs.setdefault(tkey, []).append(ps)
        for tkey in task_runs:
            if tkey not in seen_this_run:
                task_runs[tkey].append(0.0)

    def make_bar(val):
        return '█' * int(val * 10) + '░' * (10 - int(val * 10))
    final_scores_table = {}
    all_task_avgs = []
    pass_at_n_count = 0
    pass_pow_n_count = 0
    print(f'\n  Per-task avg@{repeat} scores:')
    for tkey in sorted(task_runs.keys()):
        runs = task_runs[tkey]
        avg_val = statistics.mean(runs)
        std_val = statistics.stdev(runs) if len(runs) > 1 else 0.0
        all_task_avgs.append(avg_val)
        any_passed = any((v > PASS_THRESHOLD for v in runs))
        all_passed = all((v > PASS_THRESHOLD for v in runs)) and len(runs) == repeat
        if any_passed:
            pass_at_n_count += 1
        if all_passed:
            pass_pow_n_count += 1
        entry = {'overall_avg': round(avg_val, 4), 'overall_std': round(std_val, 4), 'overall_bar': make_bar(avg_val), f'pass@{repeat}': any_passed, f'pass^{repeat}': all_passed, 'runs': [round(v, 4) for v in runs], 'n_runs': len(runs)}
        p_runs = task_privacy_runs.get(tkey)
        if p_runs:
            entry['privacy_avg'] = round(statistics.mean(p_runs), 4)
            entry['privacy_exposure'] = round(1 - entry['privacy_avg'], 4)
            entry['privacy_std'] = round(statistics.stdev(p_runs) if len(p_runs) > 1 else 0.0, 4)
            entry['privacy_runs'] = [round(v, 4) for v in p_runs]
        final_scores_table[tkey] = entry
        bar = make_bar(avg_val)
        runs_str = ', '.join((f'{v:.2f}' for v in runs))
        pass_mark = '✓✓' if all_passed else '✓' if any_passed else '✗'
        priv_str = ''
        if p_runs:
            priv_str = f"  privacy_avg: {entry['privacy_avg']:.2f}"
        print(f'    {bar} {avg_val:.4f} ±{std_val:.4f}  {tkey}  [{runs_str}] {pass_mark}{priv_str}')
    total_tasks = len(task_runs)
    global_avg = statistics.mean(all_task_avgs) if all_task_avgs else 0.0
    global_std = statistics.stdev(all_task_avgs) if len(all_task_avgs) > 1 else 0.0
    pass_at_n_rate = pass_at_n_count / total_tasks if total_tasks else 0.0
    pass_pow_n_rate = pass_pow_n_count / total_tasks if total_tasks else 0.0
    privacy_avgs = [e['privacy_avg'] for e in final_scores_table.values() if 'privacy_avg' in e]
    privacy_global = statistics.mean(privacy_avgs) if privacy_avgs else None
    print(f'\n  Tasks: {total_tasks}')
    print(f'  Overall avg@{repeat}:  {make_bar(global_avg)} {global_avg:.4f} ±{global_std:.4f}')
    print(f'  pass@{repeat} (>{PASS_THRESHOLD}): {pass_at_n_count}/{total_tasks} ({pass_at_n_rate:.2%})')
    print(f'  pass^{repeat} (>{PASS_THRESHOLD}): {pass_pow_n_count}/{total_tasks} ({pass_pow_n_rate:.2%})')
    if privacy_global is not None:
        print(f'  Privacy avg@{repeat}:  {make_bar(privacy_global)} {privacy_global:.4f}')
    all_flat = [r for run in per_run_results for r in run]
    total_usage = {}
    for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'request_count', 'cost_usd'):
        vals = [r.get('usage', {}).get(key, 0) for r in all_flat]
        total_usage[f'total_{key}'] = round(sum(vals), 6) if 'cost' in key else sum(vals)
    total_usage['total_actual_input_tokens'] = total_usage['total_input_tokens'] + total_usage['total_cache_read_tokens']
    _probe_tasks = [r for r in all_flat if r.get('probe_usage')]
    total_probe_usage = None
    if _probe_tasks:

        def _psum(role: str, field: str) -> int:
            return sum(((r['probe_usage'].get(role) or {}).get(field, 0) for r in _probe_tasks))
        total_probe_usage = {'tasks_with_probe': len(_probe_tasks), 'repeat': repeat, 'judge': {'calls': _psum('judge', 'calls'), 'input_tokens': _psum('judge', 'input_tokens'), 'output_tokens': _psum('judge', 'output_tokens')}, 'total_calls': _psum('total', 'calls'), 'total_input_tokens': _psum('total', 'input_tokens'), 'total_output_tokens': _psum('total', 'output_tokens'), 'total_tokens': _psum('total', 'total_tokens'), 'input_vs_edge_ratio': round(_psum('total', 'input_tokens') / total_usage['total_actual_input_tokens'], 4) if total_usage['total_actual_input_tokens'] else None, '_note': '路由探针消耗的端侧 token,不含在 usage/edge_usage 里。数值为 repeat 轮总和'}
    total_usage['total_elapsed_time'] = round(sum((r.get('usage', {}).get('elapsed_time', 0.0) for r in all_flat)), 2)
    total_usage['wall_clock_time'] = round(wall_clock_time, 2)
    turns_list = [r.get('usage', {}).get('n_turns', r.get('usage', {}).get('request_count', 0)) for r in all_flat]
    total_usage['total_turns'] = sum(turns_list)
    total_usage['avg_turns_per_task'] = round(sum(turns_list) / len(turns_list), 2) if turns_list else 0
    total_usage['_note'] = _usage_note(include_cache_write=False)
    avg_usage = {'avg_input_tokens': round(total_usage['total_input_tokens'] / repeat, 2), 'avg_output_tokens': round(total_usage['total_output_tokens'] / repeat, 2), 'avg_cache_read_tokens': round(total_usage['total_cache_read_tokens'] / repeat, 2), 'avg_cache_write_tokens': round(total_usage['total_cache_write_tokens'] / repeat, 2), 'avg_request_count': round(total_usage['total_request_count'] / repeat, 2), 'avg_turns_per_task': round(total_usage['total_turns'] / repeat / total_tasks, 2) if total_tasks else 0, 'avg_cost_usd': round(total_usage['total_cost_usd'] / repeat, 6), 'avg_elapsed_time': round(total_usage['total_elapsed_time'] / repeat, 2), 'avg_wall_clock_time': round(wall_clock_time / repeat, 2) if repeat else 0.0}
    from datetime import datetime
    date_tag = datetime.now().strftime('%Y%m%d')
    summary_path = output_dir / f'0_summary_{model_name}_avg@{repeat}_{date_tag}.json'
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_data = {'category': category, 'model': model_name, 'repeat': repeat, 'total_tasks': total_tasks, f'overall_avg@{repeat}': round(global_avg, 4), 'overall_std': round(global_std, 4), 'overall_bar': make_bar(global_avg), 'pass_threshold': PASS_THRESHOLD, f'pass@{repeat}': pass_at_n_count, f'pass@{repeat}_rate': round(pass_at_n_rate, 4), f'pass^{repeat}': pass_pow_n_count, f'pass^{repeat}_rate': round(pass_pow_n_rate, 4), f'privacy_exposure_avg@{repeat}': round(1 - privacy_global, 4) if privacy_global is not None else None, f'privacy_avg@{repeat}': round(privacy_global, 4) if privacy_global is not None else None, 'usage': {'per_run_avg': avg_usage, 'all_runs_total': total_usage}}
    if total_probe_usage:
        summary_data['probe_usage'] = total_probe_usage
        print(f"  路由探针(未计入 usage): {total_probe_usage['total_calls']} 次调用, 输入 {total_probe_usage['total_input_tokens']:,} tokens ({total_probe_usage['input_vs_edge_ratio']}× 端侧输入)")
    cloud_tasks = [r for r in all_flat if r.get('cloud_usage')]
    if cloud_tasks:
        total_cloud_prompt = sum((r['cloud_usage'].get('prompt_tokens', 0) for r in cloud_tasks))
        total_cloud_completion = sum((r['cloud_usage'].get('completion_tokens', 0) for r in cloud_tasks))
        total_cloud_tokens = sum((r['cloud_usage'].get('total_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_read = sum((r['cloud_usage'].get('cache_read_tokens', 0) for r in cloud_tasks))
        total_cloud_cache_write = sum((r['cloud_usage'].get('cache_write_tokens', 0) for r in cloud_tasks))
        total_cloud_req = sum((r['cloud_usage'].get('request_count', 0) for r in cloud_tasks))
        total_cloud_latency = sum((r['cloud_usage'].get('total_latency_ms', 0) for r in cloud_tasks))
        n_cloud_tasks = len(cloud_tasks)
        total_cloud_raw = _cloud_raw_input(total_cloud_prompt, total_cloud_cache_read, total_cloud_cache_write)
        cache_hit_rate_cloud = round(total_cloud_cache_read / total_cloud_prompt, 4) if total_cloud_prompt else 0
        cache_write_rate_cloud = round(total_cloud_cache_write / total_cloud_prompt, 4) if total_cloud_prompt else 0
        sample_source = (cloud_tasks[0].get('cloud_usage') or {}).get('source', 'audit_jsonl')
        is_step_router = _is_step_router_model(model_name)
        _split = _edge_component_split(total_usage, {'source': sample_source, 'total_raw_input_tokens': total_cloud_raw, 'total_prompt_tokens': total_cloud_prompt, 'total_cache_read_tokens': total_cloud_cache_read, 'total_cache_write_tokens': total_cloud_cache_write, 'total_completion_tokens': total_cloud_completion, 'total_request_count': total_cloud_req}, model_name)
        edge_only_input = _split['input']
        edge_only_cache_read = _split['cache_read']
        edge_only_cache_write = _split['cache_write']
        edge_only_output = _split['output']
        edge_only_req = _split['request']
        edge_only_tokens = edge_only_input + edge_only_cache_read + edge_only_cache_write + edge_only_output
        denom = edge_only_tokens + total_cloud_tokens
        cache_hit_rate = cache_hit_rate_cloud
        avg_cloud_tasks = round(n_cloud_tasks / repeat, 2)
        cloud_call_rate = round(avg_cloud_tasks / total_tasks, 4) if total_tasks else 0
        avg_cloud_calls_per_task = round(total_cloud_req / repeat / total_tasks, 2) if total_tasks else 0
        summary_data['cloud_usage'] = {'source': sample_source, 'total_tasks': total_tasks, 'cloud_call_rate': cloud_call_rate, 'avg_cloud_calls_per_task': avg_cloud_calls_per_task, 'all_runs_total': {'tasks_with_cloud_calls': n_cloud_tasks, 'total_request_count': total_cloud_req, 'total_prompt_tokens': total_cloud_prompt, 'total_raw_input_tokens': total_cloud_raw, 'total_cache_read_tokens': total_cloud_cache_read, 'total_cache_write_tokens': total_cloud_cache_write, 'total_completion_tokens': total_cloud_completion, 'total_tokens': total_cloud_tokens, 'cloud_token_share': round(total_cloud_tokens / denom, 4) if denom else 0, 'cache_hit_rate': cache_hit_rate, 'cache_write_rate': cache_write_rate_cloud, 'total_latency_ms': total_cloud_latency, 'avg_latency_ms': round(total_cloud_latency / total_cloud_req) if total_cloud_req else 0}, 'per_run_avg': {'avg_tasks_with_cloud_calls': avg_cloud_tasks, 'avg_request_count': round(total_cloud_req / repeat, 2), 'avg_prompt_tokens': round(total_cloud_prompt / repeat, 2), 'avg_raw_input_tokens': round(total_cloud_raw / repeat, 2), 'avg_cache_read_tokens': round(total_cloud_cache_read / repeat, 2), 'avg_cache_write_tokens': round(total_cloud_cache_write / repeat, 2), 'avg_output_tokens': round(total_cloud_completion / repeat, 2), 'avg_tokens': round(total_cloud_tokens / repeat, 2), 'avg_cloud_calls_per_task': avg_cloud_calls_per_task, 'avg_latency_ms': round(total_cloud_latency / total_cloud_req) if total_cloud_req else 0, 'cloud_token_share': round(total_cloud_tokens / denom, 4) if denom else 0, 'cache_hit_rate': cache_hit_rate, 'cache_write_rate': cache_write_rate_cloud}}
        edge_share = round(edge_only_tokens / denom, 4) if denom else 0
        edge_cache_hit_rate = round(edge_only_cache_read / (edge_only_input + edge_only_cache_read + edge_only_cache_write), 4) if edge_only_input + edge_only_cache_read + edge_only_cache_write else 0
        summary_data['edge_usage'] = {'all_runs_total': {'total_request_count': edge_only_req, 'total_input_tokens': edge_only_input, 'total_raw_input_tokens': edge_only_input, 'total_cache_read_tokens': edge_only_cache_read, 'total_cache_write_tokens': edge_only_cache_write, 'total_output_tokens': edge_only_output, 'total_tokens': edge_only_tokens, 'edge_token_share': edge_share, 'cache_hit_rate': edge_cache_hit_rate}, 'per_run_avg': {'avg_request_count': round(edge_only_req / repeat, 2), 'avg_raw_input_tokens': round(edge_only_input / repeat, 2), 'avg_cache_read_tokens': round(edge_only_cache_read / repeat, 2), 'avg_cache_write_tokens': round(edge_only_cache_write / repeat, 2), 'avg_output_tokens': round(edge_only_output / repeat, 2), 'avg_tokens': round(edge_only_tokens / repeat, 2), 'edge_token_share': edge_share, 'cache_hit_rate': edge_cache_hit_rate}}
        if is_step_router and sample_source == 'audit_jsonl':
            combined_usage = _normalize_step_router_total_usage(total_usage, summary_data['edge_usage']['all_runs_total'], summary_data['cloud_usage']['all_runs_total'])
            edge_total_usage = _edge_usage_as_total_usage(total_usage, summary_data['edge_usage']['all_runs_total'], model_name, total_tasks * repeat)
            summary_data['usage'] = {'per_run_avg': {'avg_input_tokens': round(edge_total_usage['total_input_tokens'] / repeat, 2), 'avg_output_tokens': round(edge_total_usage['total_output_tokens'] / repeat, 2), 'avg_cache_read_tokens': round(edge_total_usage['total_cache_read_tokens'] / repeat, 2), 'avg_cache_write_tokens': round(edge_total_usage['total_cache_write_tokens'] / repeat, 2), 'avg_request_count': round(edge_total_usage['total_request_count'] / repeat, 2), 'avg_turns_per_task': round(edge_total_usage['total_turns'] / repeat / total_tasks, 2) if total_tasks else 0, 'avg_cost_usd': round(edge_total_usage['total_cost_usd'] / repeat, 6), 'avg_elapsed_time': round(edge_total_usage['total_elapsed_time'] / repeat, 2)}, 'all_runs_total': edge_total_usage}
            summary_data['combined_usage'] = {'per_run_avg': {'avg_input_tokens': round(combined_usage['total_input_tokens'] / repeat, 2), 'avg_output_tokens': round(combined_usage['total_output_tokens'] / repeat, 2), 'avg_cache_read_tokens': round(combined_usage['total_cache_read_tokens'] / repeat, 2), 'avg_cache_write_tokens': round(combined_usage['total_cache_write_tokens'] / repeat, 2), 'avg_request_count': round(combined_usage['total_request_count'] / repeat, 2), 'avg_turns_per_task': round(combined_usage['total_turns'] / repeat / total_tasks, 2) if total_tasks else 0, 'avg_cost_usd': round(combined_usage['total_cost_usd'] / repeat, 6), 'avg_elapsed_time': round(combined_usage['total_elapsed_time'] / repeat, 2)}, 'all_runs_total': combined_usage}
    elif (task_routed_cloud := [r for r in all_flat if r.get('task_route') == 'CLOUD']):
        cloud_in_raw = sum((r.get('usage', {}).get('input_tokens', 0) for r in task_routed_cloud))
        cloud_cache_r = sum((r.get('usage', {}).get('cache_read_tokens', 0) for r in task_routed_cloud))
        cloud_cache_w = sum((r.get('usage', {}).get('cache_write_tokens', 0) for r in task_routed_cloud))
        cloud_out = sum((r.get('usage', {}).get('output_tokens', 0) for r in task_routed_cloud))
        cloud_req = sum((r.get('usage', {}).get('request_count', 0) for r in task_routed_cloud))
        cloud_elapsed = sum((r.get('usage', {}).get('elapsed_time', 0) for r in task_routed_cloud))
        cloud_prompt = cloud_in_raw + cloud_cache_r + cloud_cache_w
        cloud_tok = cloud_prompt + cloud_out
        n_cloud = len(task_routed_cloud)
        avg_cloud_tasks = round(n_cloud / repeat, 2)
        cloud_call_rate = round(avg_cloud_tasks / total_tasks, 4) if total_tasks else 0
        avg_cloud_calls_per_task = round(cloud_req / repeat / total_tasks, 2) if total_tasks else 0
        edge_only_input = max(total_usage['total_input_tokens'] - cloud_in_raw, 0)
        edge_only_cache_read = max(total_usage['total_cache_read_tokens'] - cloud_cache_r, 0)
        edge_only_cache_write = max(total_usage['total_cache_write_tokens'] - cloud_cache_w, 0)
        edge_only_output = max(total_usage['total_output_tokens'] - cloud_out, 0)
        edge_only_req = max(total_usage['total_request_count'] - cloud_req, 0)
        edge_only_tokens = edge_only_input + edge_only_cache_read + edge_only_cache_write + edge_only_output
        denom = edge_only_tokens + cloud_tok
        cloud_token_share = round(cloud_tok / denom, 4) if denom else 0
        edge_token_share = round(edge_only_tokens / denom, 4) if denom else 0
        cloud_cache_hit_rate = round(cloud_cache_r / cloud_prompt, 4) if cloud_prompt else 0
        cloud_cache_write_rate = round(cloud_cache_w / cloud_prompt, 4) if cloud_prompt else 0
        edge_input_total = edge_only_input + edge_only_cache_read + edge_only_cache_write
        edge_cache_hit_rate = round(edge_only_cache_read / edge_input_total, 4) if edge_input_total else 0
        summary_data['cloud_usage'] = {'source': 'task_route', 'total_tasks': total_tasks, 'cloud_call_rate': cloud_call_rate, 'avg_cloud_calls_per_task': avg_cloud_calls_per_task, 'all_runs_total': {'tasks_with_cloud_calls': n_cloud, 'total_request_count': cloud_req, 'total_prompt_tokens': cloud_prompt, 'total_raw_input_tokens': cloud_in_raw, 'total_cache_read_tokens': cloud_cache_r, 'total_cache_write_tokens': cloud_cache_w, 'total_completion_tokens': cloud_out, 'total_tokens': cloud_tok, 'cloud_token_share': cloud_token_share, 'cache_hit_rate': cloud_cache_hit_rate, 'cache_write_rate': cloud_cache_write_rate, 'total_elapsed_time': round(cloud_elapsed, 2)}, 'per_run_avg': {'avg_tasks_with_cloud_calls': avg_cloud_tasks, 'avg_request_count': round(cloud_req / repeat, 2), 'avg_prompt_tokens': round(cloud_prompt / repeat, 2), 'avg_raw_input_tokens': round(cloud_in_raw / repeat, 2), 'avg_cache_read_tokens': round(cloud_cache_r / repeat, 2), 'avg_cache_write_tokens': round(cloud_cache_w / repeat, 2), 'avg_output_tokens': round(cloud_out / repeat, 2), 'avg_tokens': round(cloud_tok / repeat, 2), 'avg_cloud_calls_per_task': avg_cloud_calls_per_task, 'cloud_token_share': cloud_token_share, 'cache_hit_rate': cloud_cache_hit_rate, 'cache_write_rate': cloud_cache_write_rate, 'avg_elapsed_time': round(cloud_elapsed / repeat, 2)}}
        summary_data['edge_usage'] = {'all_runs_total': {'total_request_count': edge_only_req, 'total_input_tokens': edge_only_input, 'total_raw_input_tokens': edge_only_input, 'total_cache_read_tokens': edge_only_cache_read, 'total_cache_write_tokens': edge_only_cache_write, 'total_output_tokens': edge_only_output, 'total_tokens': edge_only_tokens, 'edge_token_share': edge_token_share, 'cache_hit_rate': edge_cache_hit_rate}, 'per_run_avg': {'avg_request_count': round(edge_only_req / repeat, 2), 'avg_raw_input_tokens': round(edge_only_input / repeat, 2), 'avg_cache_read_tokens': round(edge_only_cache_read / repeat, 2), 'avg_cache_write_tokens': round(edge_only_cache_write / repeat, 2), 'avg_output_tokens': round(edge_only_output / repeat, 2), 'avg_tokens': round(edge_only_tokens / repeat, 2), 'edge_token_share': edge_token_share, 'cache_hit_rate': edge_cache_hit_rate}}
    else:
        has_routing = any((r.get('task_route') for r in all_flat))
        if has_routing:
            edge_input = total_usage['total_input_tokens']
            edge_cr = total_usage['total_cache_read_tokens']
            edge_cw = total_usage['total_cache_write_tokens']
            edge_out = total_usage['total_output_tokens']
            edge_req = total_usage['total_request_count']
            edge_tok = edge_input + edge_cr + edge_cw + edge_out
            edge_input_total = edge_input + edge_cr + edge_cw
            edge_cache_hit_rate = round(edge_cr / edge_input_total, 4) if edge_input_total else 0
            summary_data['edge_usage'] = {'all_runs_total': {'total_request_count': edge_req, 'total_input_tokens': edge_input, 'total_raw_input_tokens': edge_input, 'total_cache_read_tokens': edge_cr, 'total_cache_write_tokens': edge_cw, 'total_output_tokens': edge_out, 'total_tokens': edge_tok, 'edge_token_share': 1.0, 'cache_hit_rate': edge_cache_hit_rate, 'note': 'all tasks routed LOCAL — no cloud calls'}, 'per_run_avg': {'avg_request_count': round(edge_req / repeat, 2), 'avg_raw_input_tokens': round(edge_input / repeat, 2), 'avg_cache_read_tokens': round(edge_cr / repeat, 2), 'avg_cache_write_tokens': round(edge_cw / repeat, 2), 'avg_output_tokens': round(edge_out / repeat, 2), 'avg_tokens': round(edge_tok / repeat, 2)}}
            summary_data['cloud_usage'] = None
        elif _is_cloud_only_model(model_name):
            _raw = total_usage.get('total_input_tokens', 0)
            _cr = total_usage.get('total_cache_read_tokens', 0)
            _cw = total_usage.get('total_cache_write_tokens', 0)
            _out = total_usage.get('total_output_tokens', 0)
            _req = total_usage.get('total_request_count', 0)
            _prompt = _raw + _cr + _cw
            _tok = _prompt + _out
            _n_rollouts = total_tasks * repeat
            _avg_calls = round(_req / repeat / total_tasks, 2) if total_tasks else 0
            summary_data['cloud_usage'] = {'source': 'cloud_only_chat', 'total_tasks': total_tasks, 'cloud_call_rate': 1.0 if total_tasks else 0, 'avg_cloud_calls_per_task': _avg_calls, 'all_runs_total': {'tasks_with_cloud_calls': _n_rollouts, 'total_request_count': _req, 'total_prompt_tokens': _prompt, 'total_raw_input_tokens': _raw, 'total_cache_read_tokens': _cr, 'total_cache_write_tokens': _cw, 'total_completion_tokens': _out, 'total_tokens': _tok, 'cloud_token_share': 1.0, 'cache_hit_rate': round(_cr / _prompt, 4) if _prompt else 0, 'cache_write_rate': round(_cw / _prompt, 4) if _prompt else 0, 'total_latency_ms': 0, 'avg_latency_ms': 0}, 'per_run_avg': {'avg_tasks_with_cloud_calls': round(_n_rollouts / repeat, 2), 'avg_request_count': round(_req / repeat, 2), 'avg_prompt_tokens': round(_prompt / repeat, 2), 'avg_raw_input_tokens': round(_raw / repeat, 2), 'avg_cache_read_tokens': round(_cr / repeat, 2), 'avg_cache_write_tokens': round(_cw / repeat, 2), 'avg_output_tokens': round(_out / repeat, 2), 'avg_tokens': round(_tok / repeat, 2), 'avg_cloud_calls_per_task': _avg_calls, 'avg_latency_ms': 0, 'cloud_token_share': 1.0, 'cache_hit_rate': round(_cr / _prompt, 4) if _prompt else 0}}
            _zero_edge = {'total_request_count': 0, 'total_input_tokens': 0, 'total_raw_input_tokens': 0, 'total_cache_read_tokens': 0, 'total_cache_write_tokens': 0, 'total_output_tokens': 0, 'total_tokens': 0, 'edge_token_share': 0.0, 'cache_hit_rate': 0, 'note': 'cloud-only run: agent IS the cloud model'}
            summary_data['edge_usage'] = {'all_runs_total': _zero_edge, 'per_run_avg': {k: 0 for k in ('avg_request_count', 'avg_raw_input_tokens', 'avg_cache_read_tokens', 'avg_cache_write_tokens', 'avg_output_tokens', 'avg_tokens')}}
            summary_data['_routing_note'] = _no_split_note(model_name)
        else:
            summary_data['edge_usage'] = None
            summary_data['cloud_usage'] = None
            summary_data['_routing_note'] = _no_split_note(model_name)
    summary_data['final_scores'] = final_scores_table
    _avg_cloud = summary_data.get('cloud_usage')
    _avg_cloud_total = (_avg_cloud or {}).get('all_runs_total') if isinstance(_avg_cloud, dict) else None
    _u_total_tokens = total_usage['total_input_tokens'] + total_usage['total_cache_read_tokens'] + total_usage['total_cache_write_tokens'] + total_usage['total_output_tokens']
    unified_metrics = build_unified_metrics(overall_score=global_avg, pass_rate=pass_pow_n_rate, usage={'input_tokens': total_usage['total_input_tokens'], 'cache_read_tokens': total_usage['total_cache_read_tokens'], 'output_tokens': total_usage['total_output_tokens'], 'total_tokens': _u_total_tokens, 'cost_usd': total_usage['total_cost_usd'], 'request_count': total_usage['total_request_count']}, cloud_usage={'calls': _avg_cloud_total.get('total_request_count', 0), 'tasks_with_cloud_calls': _avg_cloud_total.get('tasks_with_cloud_calls', 0), 'prompt_tokens': _avg_cloud_total.get('total_prompt_tokens', 0), 'completion_tokens': _avg_cloud_total.get('total_completion_tokens', 0), 'total_tokens': _avg_cloud_total.get('total_tokens', 0), 'cloud_call_rate': (_avg_cloud or {}).get('cloud_call_rate', 0), 'avg_latency_ms': _avg_cloud_total.get('avg_latency_ms', 0)} if _avg_cloud_total else None, wall_clock_seconds=wall_clock_time, total_task_seconds=total_usage.get('total_elapsed_time', 0.0), repeat=repeat)
    summary_data = {'unified_metrics': unified_metrics, **summary_data}
    summary_data['unified_metrics']['performance'].update(pass_definition='all_runs_successful', pass_repeat=repeat)
    _attach_resource_metrics(summary_data, model_name, repeat, edge_model_name=edge_model_name, cloud_model_name=cloud_model_name, results=all_flat)
    summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    summary_header = {k: v for (k, v) in summary_data.items() if k != 'final_scores'}
    print(f"\n{'=' * 60}")
    print(f'  avg@{repeat} Summary (excluding per-task final_scores)')
    print(f"{'=' * 60}")
    print(json.dumps(summary_header, indent=2, ensure_ascii=False, default=str))
    print('=' * 60)
    print(f'\n  avg@{repeat} summary written to → {summary_path}')
    print('#' * 60)
