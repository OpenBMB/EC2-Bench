from __future__ import annotations
import argparse
import atexit
import base64
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))
from config.time_format import format_elapsed_progress, format_duration_short
from config.judge_retry import DEFAULT_GRADING_MAX_ELAPSED, DEFAULT_GRADING_MAX_RETRIES, decide_grading_retry, grading_retry_decision, is_retryable_grading_failure
from utils.task_parser import parse_task_md
from utils.docker_utils import remove_container, start_container, setup_workspace, setup_skills, setup_plugin_source, inject_openclaw_models, configure_openclaw_compaction, install_openclaw_plugin, patch_openclaw_config, run_warmup, run_background, close_proc_log, collect_output_from_container, TMP_WORKSPACE
from utils.grading import run_grading, print_summary, print_global_summary, print_avg_summary, extract_usage_from_jsonl, extract_cloud_usage, extract_probe_usage, resolve_judge_endpoint_pool, JudgePoolConfigError
from utils import e2b_utils as e2b
from utils.query_router import load_route_table as _qr_load_route_table, decide as _qr_decide, build_router_usage_record as _qr_build_router_usage_record, DEFAULT_ROUTE_TABLE as _QR_DEFAULT_ROUTE_TABLE
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)
_LOG_VERBOSITY_LEVELS = {'compact': logging.ERROR, 'normal': logging.WARNING, 'verbose': logging.INFO}
_SHOW_RUNNING = True

def _configure_compact_logging(verbosity: str='compact') -> None:
    """按详细度设置日志级别。"""
    global _SHOW_RUNNING
    _SHOW_RUNNING = verbosity != 'compact'
    os.environ['LOG_VERBOSITY'] = verbosity
    level = _LOG_VERBOSITY_LEVELS.get(verbosity, logging.ERROR)
    logging.getLogger().setLevel(level)
    lib_level = logging.ERROR if level >= logging.ERROR else logging.WARNING
    for logger_name in ('httpx', 'httpcore', 'e2b', 'e2b.api', 'urllib3'):
        logging.getLogger(logger_name).setLevel(lib_level)
_active_containers: set[str] = set()
_active_containers_lock = threading.Lock()

def _banner(fmt: str, *args) -> None:
    """始终可见的进度行：print 到终端(绕过 --log-verbosity，与 RUNNING/DONE 一致)。"""
    try:
        line = fmt % args if args else fmt
    except Exception:
        line = fmt
    print(line, flush=True)
_SECTION_W = 66

def _disp_w(s: str) -> int:
    """Display width: CJK/full-width chars count as 2 columns."""
    import unicodedata
    return sum((2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s))

def _pad(s: str, width: int) -> str:
    """Left-justify to a display width (CJK-aware)."""
    return s + ' ' * max(0, width - _disp_w(s))

def _section_open(title: str, color: str='cyan') -> None:
    dashes = '─' * max(4, _SECTION_W - _disp_w(title) - 4)
    _banner('')
    _banner(_paint(f'╭─ {title} {dashes}', color))

def _section_line(text: str='', color: str='cyan') -> None:
    _banner(_paint('│ ', color) + text)

def _section_close(text: str, color: str='green') -> None:
    _banner(_paint('╰─ ', color) + _paint(text, color))

def _ratio_bar(cur: int, full: int, width: int=10) -> str:
    """A mini progress bar like ▓▓▓▓▓░░░░░ for cur/full."""
    full = max(1, full)
    filled = max(0, min(width, round(width * cur / full)))
    return '▓' * filled + '░' * (width - filled)

def _cleanup_active_containers() -> None:
    with _active_containers_lock:
        containers = list(_active_containers)
    if not containers:
        return
    logger.warning('Cleaning up %d active container(s)...', len(containers))
    for cid in containers:
        try:
            subprocess.run(['docker', 'rm', '-f', cid], capture_output=True, timeout=15)
            logger.info('  Removed container: %s', cid)
        except Exception:
            pass

def _reap_batch_on_exit() -> None:
    """Kill this batch's leftover sandboxes on exit. Registered only by main()."""
    global _EXITING
    _EXITING = True
    try:
        e2b.reap_current_batch()
    except BaseException as exc:
        logger.warning('批次退出清理未完成(%s): 残留沙箱会在 ace_expires_at 后由下次启动清理收掉', type(exc).__name__)
_EXITING = False

def _sigint_handler(signum, frame):
    if _EXITING:
        return
    logger.warning('Received signal %s — cleaning up containers before exit', signum)
    _cleanup_active_containers()
    sys.exit(1)
signal.signal(signal.SIGINT, _sigint_handler)
signal.signal(signal.SIGTERM, _sigint_handler)
atexit.register(_cleanup_active_containers)
GATEWAY_PORT = int(os.environ.get('GATEWAY_PORT', '18789'))
GATEWAY_READY_BACKOFFS = (2, 4, 8)

def _wait_for_gateway(task_id: str, probe, *, sleep_fn=time.sleep) -> None:
    """Wait for the gateway's WebSocket health RPC with bounded backoff."""
    last_error = 'health check returned not ready'
    for (attempt, delay) in enumerate(GATEWAY_READY_BACKOFFS, start=1):
        sleep_fn(delay)
        try:
            (ready, detail) = probe()
        except Exception as exc:
            (ready, detail) = (False, str(exc))
        if ready:
            logger.info('[%s] Gateway ready after health check %d', task_id, attempt)
            return
        last_error = detail or last_error
        logger.warning('[%s] Gateway health check %d/%d failed after %ds: %s', task_id, attempt, len(GATEWAY_READY_BACKOFFS), delay, last_error)
    raise RuntimeError(f'Gateway failed {len(GATEWAY_READY_BACKOFFS)} health checks: {last_error}')

def _gateway_health_command() -> str:
    auth_mode = 'python3 -c "import json; c=json.load(open(\'/root/.openclaw/openclaw.json\')); print(c.get(\'gateway\',{}).get(\'auth\',{}).get(\'mode\',\'none\'))"'
    auth_secret = 'python3 -c "import json; c=json.load(open(\'/root/.openclaw/openclaw.json\')); a=c.get(\'gateway\',{}).get(\'auth\',{}); print(a.get(a.get(\'mode\',\'none\'),\'\') or \'\')"'
    base = f'openclaw gateway health --json --timeout 3000 --url ws://127.0.0.1:{GATEWAY_PORT}'
    return f'auth_mode=$({auth_mode}); gateway_secret=$({auth_secret}); if [ "$auth_mode" = token ]; then {base} --token "$gateway_secret"; elif [ "$auth_mode" = password ]; then {base} --password "$gateway_secret"; else {base}; fi'

def _wait_for_docker_gateway(task_id: str) -> None:

    def probe() -> tuple[bool, str]:
        result = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', _gateway_health_command()], capture_output=True, text=True, timeout=10)
        detail = (result.stderr or result.stdout).strip()[-500:]
        if result.returncode != 0:
            return (False, detail or f'health command exited {result.returncode}')
        try:
            return (json.loads(result.stdout).get('ok') is True, detail)
        except (json.JSONDecodeError, AttributeError) as exc:
            return (False, f'invalid health response: {exc}')
    _wait_for_gateway(task_id, probe)

def _wait_for_e2b_gateway(sbx, task_id: str) -> None:

    def probe() -> tuple[bool, str]:
        (stdout, stderr, rc) = e2b.run_command(sbx, _gateway_health_command(), timeout=10, host_timeout=15)
        detail = (stderr or stdout).strip()[-500:]
        if rc != 0:
            return (False, detail or f'health command exited {rc}')
        try:
            return (json.loads(stdout).get('ok') is True, detail)
        except (json.JSONDecodeError, AttributeError) as exc:
            return (False, f'invalid health response: {exc}')
    _wait_for_gateway(task_id, probe)
ROOT_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT_DIR / os.environ.get('TASKS_SUBDIR', 'tasks')
OUTPUT_DIR = ROOT_DIR / os.environ.get('OUTPUT_SUBDIR', 'output')
SUMMARY_OUTPUT_DIR = OUTPUT_DIR

def build_task_output_dir(output_dir: Path, batch_timestamp: str, task_id: str, run_idx: int) -> Path:
    """Return the artifact directory for one task in one repeat."""
    if run_idx <= 0:
        raise ValueError(f'run_idx must be >= 1, got {run_idx}')
    return output_dir / batch_timestamp / f'repeat{run_idx}' / task_id

def _recover_scores_from_disk(output_dir: Path, batch_timestamp: str, task_id_ori: str, run_idx: int, not_older_than: float | None=None) -> dict | None:
    """Load an already-graded score.json for a rollout whose runner later crashed."""
    try:
        score_path = build_task_output_dir(output_dir, batch_timestamp, task_id_ori, run_idx) / 'score.json'
        if not score_path.exists():
            return None
        if not_older_than is not None and score_path.stat().st_mtime < not_older_than:
            logger.warning('[%s] score.json 比本次 attempt 旧,疑似上一次重试的残留,不恢复', task_id_ori)
            return None
        scores = json.loads(score_path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning('[%s] score.json recovery failed: %s', task_id_ori, exc)
        return None
    if not isinstance(scores, dict) or 'error' in scores:
        return None
    if not isinstance(scores.get('overall_score'), (int, float)):
        return None
    return scores

def _try_recover_crashed_result(result: dict, task_id_ori: str, run_idx: int, batch_timestamp: str, not_older_than: float | None=None, model_name: str='') -> bool:
    """Salvage a crashed rollout's result in place from its on-disk artifacts."""
    if not result.get('error'):
        return False
    if isinstance((result.get('scores') or {}).get('overall_score'), (int, float)):
        return False
    recovered = _recover_scores_from_disk(OUTPUT_DIR, batch_timestamp, task_id_ori, run_idx, not_older_than=not_older_than)
    if not recovered:
        return False
    logger.warning('[%s] Runner crashed after grading (%s) — recovered overall_score=%s from score.json', task_id_ori, str(result['error'])[:120], recovered.get('overall_score'))
    result['scores'] = recovered
    result['_recovered_error'] = result['error']
    result['_score_recovered_from_disk'] = True
    result['error'] = None
    out_dir = build_task_output_dir(OUTPUT_DIR, batch_timestamp, task_id_ori, run_idx)
    result['output_dir'] = str(out_dir)
    try:
        transcript = out_dir / 'chat.jsonl'
        if transcript.exists():
            usage = extract_usage_from_jsonl(transcript, model_name=model_name)
            usage['elapsed_time'] = 0.0
            result['usage'] = usage
        cloud_usage = extract_cloud_usage(out_dir)
        if cloud_usage:
            result['cloud_usage'] = cloud_usage
        probe_usage = extract_probe_usage(out_dir)
        if probe_usage:
            result['probe_usage'] = probe_usage
    except Exception as exc:
        logger.warning('[%s] usage rebuild after recovery failed: %s', task_id_ori, exc)
    return True

def write_run_invocation(output_dir: Path, args: argparse.Namespace, *, batch_timestamp: str | None=None, category: str | None=None, summary_category: str | None=None) -> None:
    """Persist the exact run_batch invocation for reproducibility."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cwd = Path.cwd()
    command = shlex.join([sys.executable, *sys.argv])
    metadata = {'timestamp': datetime.now().isoformat(timespec='seconds'), 'cwd': str(cwd), 'command': command, 'argv': [sys.executable, *sys.argv], 'args': vars(args), 'batch_timestamp': batch_timestamp, 'category': category, 'summary_category': summary_category, 'output_dir': str(output_dir)}
    (output_dir / 'run_command.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    (output_dir / 'run_command.sh').write_text(f'#!/usr/bin/env bash\nset -euo pipefail\ncd {shlex.quote(str(cwd))}\n{command}\n', encoding='utf-8')
DEFAULT_MODEL = os.environ.get('DEFAULT_MODEL', 'openrouter/anthropic/claude-sonnet-4.6')
DEFAULT_PARALLEL = int(os.environ.get('DEFAULT_PARALLEL', '1'))
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '') or os.environ.get('JUDGE_API_KEY', '')
MODELS_API_KEY_PLACEHOLDER = '${MY_PROXY_API_KEY}'
ALL_CATEGORIES = ['ACE_Bench']

def _colors_enabled() -> bool:
    return sys.stderr.isatty() and (not os.environ.get('NO_COLOR'))

def _paint(text: str, color: str) -> str:
    if not _colors_enabled():
        return text
    palette = {'red': '31', 'green': '32', 'yellow': '33', 'blue': '34', 'magenta': '35', 'cyan': '36', 'gray': '90', 'white_bold': '1;37'}
    return f'\x1b[{palette[color]}m{text}\x1b[0m'

def _fmt_score(value: object) -> str:
    if isinstance(value, (int, float)) and (not isinstance(value, bool)):
        return f'{float(value):.4f}'
    return 'N/A'

def _result_overall_score(result: dict) -> float | None:
    scores = result.get('scores') if isinstance(result.get('scores'), dict) else {}
    final_score = scores.get('overall_score')
    if isinstance(final_score, (int, float)) and (not isinstance(final_score, bool)):
        return float(final_score)
    numeric_scores = [float(value) for value in scores.values() if isinstance(value, (int, float)) and (not isinstance(value, bool))]
    return sum(numeric_scores) / len(numeric_scores) if numeric_scores else None

def _progress_bar(completed: int, total: int, width: int=20) -> str:
    ratio = min(max(completed / total, 0.0), 1.0) if total > 0 else 0.0
    filled = int(ratio * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {ratio:6.1%}"

def _short_task_label(task: dict) -> str:
    """Show only the task number in progress messages."""
    task_id = str(task.get('task_id') or '')
    match = re.search('task[_-](\\d+)', task_id, flags=re.IGNORECASE)
    return f'task_{match.group(1)}' if match else task_id

def _compact_start_line(*, attempt_number: int, total_attempts: int, task: dict, rollout_index: int, total_rollouts: int) -> str:
    return ' '.join([_paint(f'[task {attempt_number}/{total_attempts}]', 'white_bold'), _paint('RUNNING', 'yellow'), _paint(f'task={_short_task_label(task)}', 'cyan'), _paint(f'rollout={rollout_index}/{total_rollouts}', 'magenta')])

def _compact_done_line(*, attempt_number: int, total_attempts: int, task: dict, rollout_index: int, total_rollouts: int, result: dict, elapsed: float | None=None, completed_attempts: int | None=None, average_score: float | None=None, total_elapsed: float | None=None) -> str:
    scores = result.get('scores') if isinstance(result.get('scores'), dict) else {}
    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
    final_score = _result_overall_score(result)
    progress_completed = completed_attempts if completed_attempts is not None else attempt_number
    status = 'ERROR' if result.get('error') or scores.get('error') else 'DONE'
    status_color = 'red' if status == 'ERROR' else 'green'
    parts = [_paint(f'[task {progress_completed}/{total_attempts}]', 'white_bold'), _paint(_progress_bar(progress_completed, total_attempts), 'white_bold')]
    total_elapsed_text = format_duration_short(total_elapsed)
    if total_elapsed_text:
        parts.append(_paint(f'total={total_elapsed_text}', 'yellow'))
    parts += [_paint(status, status_color), _paint(f'task={_short_task_label(task)}', 'cyan'), _paint(f'rollout={rollout_index}/{total_rollouts}', 'magenta'), _paint(f'overall={_fmt_score(final_score)}', status_color if final_score is not None else 'yellow'), _paint(f'avg={_fmt_score(average_score)}', 'blue' if average_score is not None else 'yellow')]
    privacy = scores.get('privacy_score')
    if privacy is not None:
        parts.append(_paint(f'privacy={_fmt_score(privacy)}', 'blue'))
    if usage:
        parts.append(_paint(f"tokens={usage.get('total_tokens', 0)}", 'gray'))
    elapsed_seconds = elapsed
    if elapsed_seconds is None and usage:
        usage_elapsed = usage.get('elapsed_time')
        if isinstance(usage_elapsed, (int, float)) and (not isinstance(usage_elapsed, bool)):
            elapsed_seconds = float(usage_elapsed)
    elapsed_text = format_elapsed_progress(elapsed_seconds)
    if elapsed_text:
        parts.append(_paint(f'elapsed={elapsed_text}', 'gray'))
    if result.get('error'):
        parts.append(_paint(f"error={result['error']}", 'red'))
    elif scores.get('error'):
        parts.append(_paint(f"grading_error={scores['error']}", 'red'))
    return ' '.join(parts)
GRADING_MAX_RETRIES = DEFAULT_GRADING_MAX_RETRIES

def _grade_scores_with_retries(task: dict, grade_once, max_retries: int=GRADING_MAX_RETRIES, sleep_fn=time.sleep, monotonic_fn=time.monotonic, max_elapsed_seconds: float=DEFAULT_GRADING_MAX_ELAPSED):
    """Retry only grading while retaining the completed model trajectory."""
    history: list[dict] = []
    scores: dict = {}
    reason: str | None = None
    grading_started = monotonic_fn()
    for attempt_index in range(max_retries + 1):
        started_at = datetime.now(timezone.utc).isoformat()
        attempt_started = time.perf_counter()
        try:
            scores = grade_once(attempt_index)
        except Exception as exc:
            scores = {'error': str(exc), '_outer_error_type': type(exc).__name__}
        judge_requests = scores.pop('_judge_attempts', []) if isinstance(scores, dict) else []
        outer_error_type = scores.pop('_outer_error_type', None) if isinstance(scores, dict) else None
        if not isinstance(scores, dict) or not scores:
            reason = 'grading returned no result'
        elif scores.get('error'):
            reason = f"grading error: {str(scores['error'])[:300]}"
        elif task.get('automated_checks') and (not any((isinstance(value, (int, float)) for value in scores.values()))):
            reason = 'grading returned no numeric scores'
        else:
            reason = None
        (retryable, decision_reason) = grading_retry_decision(reason)
        history_item = {'attempt': attempt_index + 1, 'started_at': started_at, 'duration_ms': round((time.perf_counter() - attempt_started) * 1000), 'endpoint': None, 'key_suffix': None, 'request_bytes': None, 'prompt_chars': None, 'proxy_enabled': None, 'http_status': None, 'error_type': outer_error_type, 'error': reason, 'active_judge_requests': None, 'status': 'success' if reason is None else 'error', 'reason': reason, 'retryable': retryable, 'decision_reason': decision_reason, 'backoff_seconds': None, 'judge_requests': judge_requests if isinstance(judge_requests, list) else []}
        if isinstance(judge_requests, list) and judge_requests:
            history_item.update(judge_requests[-1])
            history_item['attempt'] = attempt_index + 1
            history_item['judge_requests'] = judge_requests
        history.append(history_item)
        if reason is None:
            break
        decision = decide_grading_retry(reason, attempt_index=attempt_index, max_retries=max_retries, elapsed_seconds=monotonic_fn() - grading_started, max_elapsed_seconds=max_elapsed_seconds)
        history_item['classification'] = decision.classification
        history_item['stop_reason'] = decision.stop_reason
        if not decision.retry:
            logger.warning('[%s] Grading stopped after %d/%d attempt(s) (%s): %s', task.get('task_id'), attempt_index + 1, max_retries + 1, decision.stop_reason, reason)
            break
        history_item['backoff_seconds'] = decision.delay_seconds
        logger.warning('[%s] Grading attempt %d/%d failed; retrying same trajectory in %.1fs: %s', task.get('task_id'), attempt_index + 1, max_retries + 1, decision.delay_seconds, reason)
        sleep_fn(decision.delay_seconds)
    return (scores, history, reason)

def grade_the_task(task_id: str, workspace_path: str, output_dir: Path, task: dict, result: dict, grading_max_retries: int=GRADING_MAX_RETRIES):
    gt_host = os.path.join(workspace_path, 'gt')
    if os.path.isdir(gt_host):
        r_gt = subprocess.run(['docker', 'cp', gt_host, f'{task_id}:{TMP_WORKSPACE}/gt'], capture_output=True, text=True)
        if r_gt.returncode != 0:
            logger.warning('[%s] gt directory copy failed: %s', task_id, r_gt.stderr)
        else:
            logger.info('[%s] gt directory copied to container %s/gt', task_id, TMP_WORKSPACE)
    if not result.get('error') and task.get('automated_checks'):

        def _grade_once(attempt_index: int) -> dict:
            return run_grading(task_id=task_id, automated_checks=task['automated_checks'], output_dir=output_dir, judge_endpoint_offset=attempt_index)
        (scores, history, reason) = _grade_scores_with_retries(task, _grade_once, grading_max_retries)
        result['grading_attempt_count'] = len(history)
        result['grading_max_attempts'] = grading_max_retries + 1
        result['grading_attempt_history'] = history
        result['grading_retry_exhausted'] = reason is not None
        result['grading_failure_reason'] = reason
        try:
            result['scores'] = scores
            if reason is None:
                logger.info('[%s] Grading complete after %d attempt(s)', task_id, len(history))
            else:
                logger.error('[%s] Grading failed after %d attempt(s): %s', task_id, len(history), reason)
        except Exception as exc:
            logger.error('[%s] Failed to format grading result: %s', task_id, exc)
            result['scores'] = scores
    elif not task.get('automated_checks'):
        logger.info('[%s] No Automated Checks, skipping grading', task_id)
    return result

def cal_cost(task_id: str, output_dir: Path, result: dict, elapsed_time: float, model_name: str=''):
    transcript_container = '/root/.openclaw/agents/main/sessions/chat.jsonl'
    transcript_host = output_dir / 'chat.jsonl'
    output_dir.mkdir(parents=True, exist_ok=True)
    r_cp = subprocess.run(['docker', 'cp', f'{task_id}:{transcript_container}', str(transcript_host)], capture_output=True, text=True)
    if r_cp.returncode == 0 and transcript_host.exists():
        usage = extract_usage_from_jsonl(transcript_host, model_name=model_name)
    else:
        logger.warning('[%s] Transcript copy failed: %s', task_id, r_cp.stderr.strip())
        usage = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0, 'request_count': 0}
    usage['elapsed_time'] = round(elapsed_time, 2)
    result['usage'] = usage
    if usage['request_count'] > 0:
        logger.info('[%s] Token usage — input:%d output:%d cache_read:%d cache_write:%d total:%d cost:$%.4f', task_id, usage['input_tokens'], usage['output_tokens'], usage['cache_read_tokens'], usage['cache_write_tokens'], usage['total_tokens'], usage['cost_usd'])
    cloud_usage = extract_cloud_usage(output_dir)
    probe_usage = extract_probe_usage(output_dir)
    if cloud_usage:
        result['cloud_usage'] = cloud_usage
        logger.info('[%s] Cloud usage — prompt:%d (cache_read:%d cache_write:%d) completion:%d total:%d calls:%d avg_latency:%dms', task_id, cloud_usage['prompt_tokens'], cloud_usage.get('cache_read_tokens', 0), cloud_usage.get('cache_write_tokens', 0), cloud_usage['completion_tokens'], cloud_usage['total_tokens'], cloud_usage['request_count'], cloud_usage['avg_latency_ms'])
    if probe_usage:
        result['probe_usage'] = probe_usage
    usage_path = output_dir / 'usage.json'
    usage_path.write_text(json.dumps(usage, indent=2, ensure_ascii=False), encoding='utf-8')
    logger.info('[%s] Usage written to → %s', task_id, usage_path)
    return result

def collect_task_output(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/."""
    try:
        collect_output_from_container(task_id, output_dir)
    except Exception as exc:
        logger.warning('[%s] Failed to collect task output: %s', task_id, exc)

def grade_the_task_e2b(sbx, task_id: str, workspace_path: str, output_dir: Path, task: dict, result: dict, grading_max_retries: int=GRADING_MAX_RETRIES):
    gt_host = os.path.join(workspace_path, 'gt')
    if os.path.isdir(gt_host):
        try:
            e2b.upload_directory(sbx, gt_host, f'{e2b.TMP_WORKSPACE}/gt')
            logger.info('[%s] gt directory uploaded to sandbox %s/gt', task_id, e2b.TMP_WORKSPACE)
        except Exception as exc:
            logger.warning('[%s] gt directory upload failed: %s', task_id, exc)
    if not result.get('error') and task.get('automated_checks'):

        def _grade_once(attempt_index: int) -> dict:
            return e2b.run_grading(sbx=sbx, task_id=task_id, automated_checks=task['automated_checks'], output_dir=output_dir, judge_endpoint_offset=attempt_index)
        (scores, history, reason) = _grade_scores_with_retries(task, _grade_once, grading_max_retries)
        result['grading_attempt_count'] = len(history)
        result['grading_max_attempts'] = grading_max_retries + 1
        result['grading_attempt_history'] = history
        result['grading_retry_exhausted'] = reason is not None
        result['grading_failure_reason'] = reason
        try:
            result['scores'] = scores
            if reason is None:
                logger.info('[%s] Grading complete after %d attempt(s)', task_id, len(history))
            else:
                logger.error('[%s] Grading failed after %d attempt(s): %s', task_id, len(history), reason)
        except Exception as exc:
            logger.error('[%s] Failed to format grading result: %s', task_id, exc)
            result['scores'] = scores
    elif not task.get('automated_checks'):
        logger.info('[%s] No Automated Checks, skipping grading', task_id)
    return result

def _fetch_chat_transcript_e2b(sbx, task_id: str, output_dir: Path) -> Path:
    """把沙箱里的 chat.jsonl 拉到 output_dir,返回本地路径(可能不存在)。"""
    transcript_host = output_dir / 'chat.jsonl'
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        e2b.download_file(sbx, f'{e2b.E2B_HOME}/.openclaw/agents/main/sessions/chat.jsonl', str(transcript_host))
    except Exception as exc:
        logger.warning('[%s] Transcript download failed: %s', task_id, exc)
    return transcript_host

def cal_cost_e2b(sbx, task_id: str, output_dir: Path, result: dict, elapsed_time: float, model_name: str=''):
    transcript_host = _fetch_chat_transcript_e2b(sbx, task_id, output_dir)
    if transcript_host.exists():
        usage = extract_usage_from_jsonl(transcript_host, model_name=model_name)
    else:
        logger.warning('[%s] Transcript download failed', task_id)
        usage = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0, 'cache_write_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0, 'request_count': 0}
    usage['elapsed_time'] = round(elapsed_time, 2)
    result['usage'] = usage
    cloud_usage = extract_cloud_usage(output_dir)
    probe_usage = extract_probe_usage(output_dir)
    if cloud_usage:
        result['cloud_usage'] = cloud_usage
        logger.info('[%s] Cloud usage — prompt:%d completion:%d total:%d calls:%d avg_latency:%dms', task_id, cloud_usage['prompt_tokens'], cloud_usage['completion_tokens'], cloud_usage['total_tokens'], cloud_usage['request_count'], cloud_usage['avg_latency_ms'])
    if probe_usage:
        result['probe_usage'] = probe_usage
    usage_path = output_dir / 'usage.json'
    usage_path.write_text(json.dumps(usage, indent=2, ensure_ascii=False), encoding='utf-8')
    logger.info('[%s] Usage written to -> %s', task_id, usage_path)
    return result

def set_model(task_id: str, model: str) -> None:
    r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f"openclaw models set '{model}'"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'Model setup failed:\n{r.stderr}')
    logger.info('[%s] Model set: %s', task_id, model)

def load_models_config(models_config_path: Path) -> dict:
    """Expand JSON string values from explicit environment variables."""
    parsed = json.loads(models_config_path.read_text(encoding='utf-8'))
    pattern = re.compile('\\$\\{([A-Za-z_][A-Za-z0-9_]*)\\}')

    def expand(v):
        if isinstance(v, dict):
            return {k: expand(x) for (k, x) in v.items()}
        if isinstance(v, list):
            return [expand(x) for x in v]
        if not isinstance(v, str):
            return v

        def replace(match):
            name = match.group(1)
            if name not in os.environ or not os.environ[name]:
                raise ValueError(f'Set environment variable {name} required by models config')
            return os.environ[name]
        return pattern.sub(replace, v)
    if not isinstance(parsed, dict):
        raise ValueError('Models config must be a JSON object')
    return expand(parsed)

def _make_ec_tag(run_mode: str | None, cloud_model: str | None) -> str:
    """Build the task-id/summary suffix for a given run mode."""
    if not run_mode:
        return ''
    if run_mode == 'cloud-only':
        base = '_cloud-only'
    elif cloud_model:
        short_cloud = re.sub('[^a-zA-Z0-9.\\-_]', '_', cloud_model.rsplit('/', 1)[-1])
        base = f'_ec_{run_mode}-{short_cloud}'
    else:
        base = f'_ec_{run_mode}'
    return base

def _resolve_provider_model(models_config: dict | None, model_spec: str) -> tuple[str, str, str, str]:
    """Look up which provider hosts ``model_spec`` and return its connection info."""
    if not models_config:
        return ('', '', '', model_spec)
    providers = models_config.get('providers', {}) or {}
    if '/' in model_spec:
        (head, tail) = model_spec.split('/', 1)
        if head in providers:
            p = providers[head]
            if any((m.get('id') == tail for m in p.get('models') or [])):
                return (head, p.get('baseUrl', ''), p.get('apiKey', ''), tail)
            return ('', '', '', model_spec)
    for (pname, pconf) in providers.items():
        for m in pconf.get('models') or []:
            if m.get('id') == model_spec:
                return (pname, pconf.get('baseUrl', ''), pconf.get('apiKey', ''), model_spec)
    return ('', '', '', model_spec)

def _resolve_judge_endpoint() -> tuple[str, str, str]:
    """Resolve the judge endpoint the grader will use, from the environment."""
    jb = (os.environ.get('JUDGE_BASE_URL', '') or '').strip()
    jk = (os.environ.get('JUDGE_API_KEY', '') or '').strip()
    jm = (os.environ.get('JUDGE_MODEL', '') or '').strip()
    if not jb:
        jb = (os.environ.get('OPENROUTER_BASE_URL', '') or '').strip()
        jk = jk or (os.environ.get('OPENROUTER_API_KEY', '') or '').strip()
    base = jb.split(',')[0].strip()
    key = jk.split(',')[0].strip() if jk else ''
    model = jm or 'gpt-5.4-mini'
    return (base, key, model)

def _resolve_judge_endpoints() -> list[tuple[str, str, str]]:
    """Every (base_url, api_key, model_id) the grader may rotate onto."""
    model = (os.environ.get('JUDGE_MODEL', '') or '').strip() or 'gpt-5.4-mini'
    pool = resolve_judge_endpoint_pool()
    if pool:
        return [(base, key, model) for (base, key) in pool]
    (base, key, model) = _resolve_judge_endpoint()
    return [(base, key, model)]

def _probe_once(base_url: str, api_key: str, model_id: str, timeout: float, use_proxy: bool) -> str | None:
    """One minimal chat-completion probe. ``None`` on success, else error str."""
    import httpx
    from openai import OpenAI
    http_client = httpx.Client(timeout=timeout, trust_env=use_proxy)
    try:
        client = OpenAI(base_url=base_url, api_key=api_key or 'EMPTY', timeout=timeout, max_retries=0, http_client=http_client)
        try:
            client.chat.completions.create(model=model_id, messages=[{'role': 'user', 'content': 'ping'}], max_tokens=8, temperature=0)
            return None
        except Exception as exc:
            msg = str(exc)
            if '400' in msg and ('max_tokens' in msg or 'output limit' in msg):
                return None
            if '400' in msg and 'stream' in msg.lower():
                logger.info('[preflight] %s 拒绝非流式(仅支持流式),改用流式重试', model_id)
                try:
                    stream = client.chat.completions.create(model=model_id, messages=[{'role': 'user', 'content': 'ping'}], max_tokens=8, temperature=0, stream=True)
                    for _ in stream:
                        break
                    return None
                except Exception as exc2:
                    return f'{type(exc2).__name__}: {exc2} (流式回退)'
            return f'{type(exc).__name__}: {exc}'
    finally:
        try:
            http_client.close()
        except Exception:
            pass
PREFLIGHT_MAX_RETRIES = int(os.environ.get('PREFLIGHT_MAX_RETRIES', '3'))
PREFLIGHT_RETRY_BACKOFFS = (2.0, 5.0, 10.0)

def _probe_endpoint(label: str, ref: str, base_url: str, api_key: str, model_id: str, timeout: float, max_retries: int=PREFLIGHT_MAX_RETRIES) -> tuple[str, str | None]:
    """Probe one OpenAI-compatible endpoint, with a direct-connection fallback."""
    if not base_url:
        return ('fail', f'{label} ({ref}): base_url 未解析（模型不在 models-config 里，或未配置 judge 端点）')
    err_proxy = err_direct = None
    for attempt in range(max(1, max_retries)):
        err_proxy = _probe_once(base_url, api_key, model_id, timeout, use_proxy=True)
        if err_proxy is None:
            return ('ok', None)
        err_direct = _probe_once(base_url, api_key, model_id, timeout, use_proxy=False)
        if err_direct is None:
            return ('ok_direct', f'{label} ({ref}) @ {base_url}: 走代理不通[{err_proxy}]，直连成功')
        if not is_retryable_grading_failure(f'{err_proxy} {err_direct}'):
            break
        if attempt + 1 >= max(1, max_retries):
            break
        delay = PREFLIGHT_RETRY_BACKOFFS[min(attempt, len(PREFLIGHT_RETRY_BACKOFFS) - 1)]
        logger.info('[preflight] %s 第 %d/%d 次探测失败(瞬时),%.1fs 后重试: %s', label, attempt + 1, max_retries, delay, err_proxy)
        time.sleep(delay)
    return ('fail', f'{label} ({ref}) @ {base_url} [model={model_id}] (探测 {max(1, max_retries)} 次): 代理[{err_proxy}] 直连[{err_direct}]')

def _preflight_targets(models_config: dict | None, run_mode: str | None, main_model: str | None, other_model: str | None) -> list[tuple[str, str, str, str, str]]:
    """Build the (label, ref, base_url, api_key, model_id) probe list."""
    if run_mode == 'cloud-only':
        (main_label, other_label) = ('云侧(cloud)', '云侧(cloud)')
    else:
        (main_label, other_label) = ('端侧(edge)', '云侧(cloud)')
    targets: list[tuple[str, str, str, str, str]] = []
    if main_model:
        (_, burl, key, mid) = _resolve_provider_model(models_config, main_model)
        targets.append((main_label, main_model, burl, key, mid))
    if other_model:
        (_, burl, key, mid) = _resolve_provider_model(models_config, other_model)
        targets.append((other_label, other_model, burl, key, mid))
    judge_endpoints = _resolve_judge_endpoints()
    for (idx, (j_base, j_key, j_model)) in enumerate(judge_endpoints, start=1):
        label = '判分(judge)' if len(judge_endpoints) == 1 else f'判分(judge) {idx}/{len(judge_endpoints)}'
        targets.append((label, j_model, j_base, j_key, j_model))
    return targets

def preflight_check_endpoints(models_config: dict | None, run_mode: str | None, main_model: str | None, other_model: str | None, timeout: float=20.0) -> None:
    """Verify edge / cloud / judge endpoints are reachable before the run starts."""
    targets = _preflight_targets(models_config, run_mode, main_model, other_model)
    _section_open(f'启动前自检 · 端点连通性  (timeout {timeout:.0f}s/个)')
    failures: list[str] = []
    direct_only: list[tuple[str, str]] = []
    for (label, ref, base_url, api_key, model_id) in targets:
        (status, detail) = _probe_endpoint(label, ref, base_url, api_key, model_id, timeout)
        role = _pad(label, 20)
        if status == 'ok':
            _section_line(_paint('✓ ', 'green') + _paint(role, 'white_bold') + _paint(_pad(f'model={model_id}', 22), 'cyan') + _paint(base_url, 'gray'))
        elif status == 'ok_direct':
            _section_line(_paint('✓ ', 'yellow') + _paint(role, 'white_bold') + _paint(_pad(f'model={model_id}', 22), 'cyan') + _paint('直连OK · 走代理不通', 'yellow'))
            direct_only.append((label, base_url))
        else:
            _section_line(_paint('✗ ', 'red') + _paint(role, 'white_bold') + _paint(detail, 'red'))
            failures.append(detail)
    if direct_only:
        hosts = sorted({re.sub('^https?://', '', b).split('/')[0].split(':')[0] for (_, b) in direct_only})
        _section_line(_paint('↳ 内网端点需绕过代理。建议加入 NO_PROXY（本机）与 NO_PROXY_INNER（sandbox）：', 'yellow'))
        _section_line(_paint(f'''    export NO_PROXY="$NO_PROXY,{','.join(hosts)}"''', 'yellow'))
    if failures:
        _section_close(f'自检失败：{len(failures)}/{len(targets)} 个端点不通 → 已终止（未启动评测；跳过用 --skip-preflight）', 'red')
        sys.exit(1)
    ok_n = len(targets) - len(failures)
    _section_close(f'全部连通 ✓  ({ok_n}/{len(targets)})', 'green')

def _warmup_worker(deadline: float, client, model_id: str, stats: dict, lock: 'threading.Lock') -> None:
    """Keep firing tiny completions at the edge endpoint until ``deadline``."""
    while time.time() < deadline:
        t0 = time.time()
        try:
            client.chat.completions.create(model=model_id, messages=[{'role': 'user', 'content': 'warmup'}], max_tokens=16, temperature=0)
            dt = time.time() - t0
            with lock:
                stats['lat'].append(dt)
                stats['ok'] += 1
        except Exception:
            with lock:
                stats['fail'] += 1
            time.sleep(0.5)

def warmup_edge_endpoint(models_config: dict | None, run_mode: str | None, main_model: str | None, seconds: float, full_parallel: int, timeout: float=30.0) -> None:
    """Pre-warm the edge (vLLM) endpoint before the real run."""
    if seconds <= 0:
        return
    if run_mode == 'cloud-only':
        _section_open('端侧预热')
        _section_close('cloud-only 模式，端侧不参与 → 跳过预热', 'gray')
        return
    (_, base, key, model) = _resolve_provider_model(models_config, main_model)
    if not base:
        _section_open('端侧预热')
        _section_close(f'端侧 endpoint 未解析（{main_model} 不在 models-config？）→ 跳过', 'yellow')
        return
    use_proxy = True
    err = _probe_once(base, key, model, timeout, use_proxy=True)
    probe_note = None
    if err is not None:
        if _probe_once(base, key, model, timeout, use_proxy=False) is None:
            use_proxy = False
        else:
            probe_note = err
    import httpx
    from openai import OpenAI

    def _make_client():
        return OpenAI(base_url=base, api_key=key or 'EMPTY', timeout=timeout, max_retries=0, http_client=httpx.Client(timeout=timeout, trust_env=use_proxy))
    fractions = [0.4, 0.7, 1.0, 1.0]
    per = max(1.0, seconds / len(fractions))
    _dur = f'{seconds:.0f}s' if seconds < 90 else f'{seconds / 60:.0f}min'
    _section_open(f'端侧预热 · {_dur} 并发爬坡')
    _section_line(_paint(f'model={model}', 'cyan') + '  ' + _paint(f"proxy={('直连' if not use_proxy else '走代理')}", 'magenta') + '  ' + _paint(base, 'gray'))
    if probe_note:
        _section_line(_paint(f'探活失败（代理[{probe_note}]），仍尝试预热', 'yellow'))
    grand_ok = grand_fail = 0
    for (i, frac) in enumerate(fractions, 1):
        conc = max(1, round(full_parallel * frac))
        deadline = time.time() + per
        stats: dict = {'lat': [], 'ok': 0, 'fail': 0}
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=conc) as pool:
            clients = [_make_client() for _ in range(conc)]
            for c in clients:
                pool.submit(_warmup_worker, deadline, c, model, stats, lock)
        lat = sorted(stats['lat'])

        def _pct(p: float) -> float:
            if not lat:
                return float('nan')
            return lat[min(len(lat) - 1, int(len(lat) * p))]
        p95 = _pct(0.95)
        p95_color = 'green' if p95 == p95 and p95 < 3 else 'yellow'
        _section_line(_paint(f'阶段{i}/{len(fractions)} ', 'gray') + _paint(_ratio_bar(conc, full_parallel), 'blue') + ' ' + _paint(_pad(f'conc={conc}/{full_parallel}', 14), 'white_bold') + _paint(_pad(f"ok={stats['ok']}", 10), 'green') + _paint(_pad(f"fail={stats['fail']}", 9), 'red' if stats['fail'] else 'gray') + _paint(f'p50={_pct(0.5):.1f}s ', 'gray') + _paint(f'p95={p95:.1f}s', p95_color))
        grand_ok += stats['ok']
        grand_fail += stats['fail']
    tail = 'green' if grand_fail == 0 else 'yellow'
    _section_close(f'预热完成  ok={grand_ok} fail={grand_fail}  → 开始正式评测', tail)
EDGE_CLOUD_REGISTRY = {'agentic-advisor': {'skill': 'cloud-advisor', 'plugin_subdir': 'plugin', 'plugin_only': True, 'needs_proxy': False, 'gateway_cloud_env': True, 'collect_artifacts': ['/tmp/cloud_assistant_audit.jsonl'], 'tools_also_allow': ['advisor'], 'model_compat': {'supportsTools': True, 'maxTokensField': 'max_tokens'}}, 'agentic_executor_router': {'skill': 'cloud-router', 'plugin_subdir': 'plugin', 'needs_proxy': True, 'gateway_cloud_env': False, 'collect_artifacts': ['/tmp/cloud_assistant_audit.jsonl', '/tmp/router_proxy.log', '/tmp/router_proxy.stdout', '/tmp/router_failed_request.json', '/tmp/router_usage.json'], 'tools_also_allow': ['escalate'], 'model_compat': None, 'rewrite_model_baseurl': True, 'router_mode': 'signal'}, 'query-router': {'skill': None, 'plugin_subdir': None, 'needs_proxy': False, 'gateway_cloud_env': False, 'collect_artifacts': [], 'tools_also_allow': [], 'model_compat': None, 'rewrite_model_baseurl': True}, 'step-router-execute-glimpse': {'skill': 'cloud-router', 'plugin_subdir': None, 'needs_proxy': True, 'gateway_cloud_env': False, 'collect_artifacts': ['/tmp/cloud_assistant_audit.jsonl', '/tmp/router_proxy.log', '/tmp/router_proxy.stdout', '/tmp/router_failed_request.json', '/tmp/router_usage.json'], 'tools_also_allow': [], 'model_compat': None, 'rewrite_model_baseurl': True, 'router_mode': 'glimpse'}}
_prompt_config = json.loads((ROOT_DIR / 'configs/prompts.json').read_text(encoding='utf-8'))
for (_mode, _prompt_values) in _prompt_config.items():
    EDGE_CLOUD_REGISTRY[_mode].update(_prompt_values)

def _signal_prompt_env(ec_config: dict) -> list[str]:
    """Export the signal-mode prompt pair to the router proxy (base64)."""
    if ec_config.get('router_mode') != 'signal':
        return []
    out: list[str] = []
    text = ec_config.get('prompt', '')
    if text and text.strip():
        blob = base64.b64encode(text.strip().encode('utf-8')).decode('ascii')
        out.append(f"export SIGNAL_PROMPT_B64='{blob}'")
    role = ec_config.get('cloud_role_prompt') or ''
    if role.strip():
        blob = base64.b64encode(role.strip().encode('utf-8')).decode('ascii')
        out.append(f"export CLOUD_ROLE_PROMPT_B64='{blob}'")
    return out

def run_single_task(task: dict, model: str, thinking: str | None=None, models_config: dict | None=None, batch_timestamp: str | None=None, edge_cloud_mode: str | None=None, cloud_model: str | None=None, run_idx: int=1, query_router_table: str | None=None, agent_timeout_seconds: int | None=None, grading_max_retries: int=GRADING_MAX_RETRIES) -> dict:
    """Execute a single task, returning a {"task_id", "scores", "error"} dict."""
    task_id_ori = task['task_id']
    workspace_path = task['workspace_path']
    prompt = task['prompt']
    timeout_seconds = task['timeout_seconds']
    skills = task['skills']
    skills_path = task['skills_path']
    system_prompt = f'You are an expert in a restricted, non-interactive environment. Solve the task efficiently before the timeout ({timeout_seconds}s). Run all processes in the foreground without user input or background services. Provide a complete, functional solution in a single pass with no placeholders. \n'
    ec_config = EDGE_CLOUD_REGISTRY.get(edge_cloud_mode) if edge_cloud_mode else None
    ec_prompt = ec_config.get('prompt', '').rstrip() + '\n\n' if ec_config and ec_config.get('prompt') else ''
    prompt = system_prompt + ec_prompt + prompt
    if ec_config:
        os.environ['EDGE_CLOUD_MODE'] = edge_cloud_mode
        os.environ['PRIVACY_JUDGE'] = '1'
        logger.info('[%s] Run mode=%s, EDGE_CLOUD_MODE set, timeout=%ds', task_id_ori, edge_cloud_mode, timeout_seconds)
    else:
        os.environ['EDGE_CLOUD_MODE'] = edge_cloud_mode or 'local-only'
        os.environ['PRIVACY_JUDGE'] = '1'
    timestamp = batch_timestamp or datetime.now().strftime('%y_%m%d_%H%M')
    run_id = uuid.uuid4().hex[:6]
    _m = re.match('(\\d+)_.*?(task_\\d+)', task_id_ori)
    short_task_id = f'{_m.group(1)}_{_m.group(2)}' if _m else task_id_ori
    short_model = re.sub('[^a-zA-Z0-9.\\-_]', '_', model.rsplit('/', 1)[-1])
    ec_tag = _make_ec_tag(edge_cloud_mode, cloud_model)
    task_id = f'{short_task_id}_{short_model}{ec_tag}_{timestamp}_{run_id}'
    output_dir = build_task_output_dir(OUTPUT_DIR, timestamp, task_id_ori, run_idx)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {'task_id': task_id, 'scores': {}, 'error': None, 'output_dir': str(output_dir)}
    gateway_proc = None
    agent_proc = None
    elapsed_time = float(timeout_seconds)
    try:
        exec_path = os.path.join(workspace_path, 'exec')
        tmp_path = os.path.join(workspace_path, 'tmp')
        os.makedirs(exec_path, exist_ok=True)
        start_container(task_id, exec_path, extra_env=task.get('env', ''), tmp_path=tmp_path)
        with _active_containers_lock:
            _active_containers.add(task_id)
        setup_workspace(task_id, thinking=thinking)
        setup_skills(task_id, skills, skills_path)
        cloud_gateway_env = ''
        if ec_config:
            cloud_base_url = ''
            cloud_api_key = ''
            _ = ''
            cloud_model_id = cloud_model or model or 'gpt-5.4-mini'
            if models_config:
                providers = models_config.get('providers', {})
                if '/' in cloud_model_id:
                    (provider_name, model_id) = cloud_model_id.split('/', 1)
                    provider = providers.get(provider_name, {})
                    if provider and (not any((m.get('id') == model_id for m in provider.get('models', [])))):
                        raise RuntimeError(f"router mode cloud model '{cloud_model_id}' is not listed under provider '{provider_name}'")
                    _ = provider_name if provider else ''
                else:
                    provider = {}
                    model_id = cloud_model_id
                    for (pname, pconf) in providers.items():
                        if any((m.get('id') == cloud_model_id for m in pconf.get('models', []))):
                            provider = pconf
                            _ = pname
                            logger.info("Auto-resolved cloud model '%s' → provider '%s'", cloud_model_id, pname)
                            break
                if provider:
                    cloud_base_url = provider.get('baseUrl', '')
                    cloud_api_key = provider.get('apiKey', '')
                    cloud_model_id = model_id
            if edge_cloud_mode == 'query-router':
                table_path = query_router_table or os.environ.get('QUERY_ROUTER_TABLE') or _QR_DEFAULT_ROUTE_TABLE
                qr_table = _qr_load_route_table(table_path)
                (qr_decision, qr_reason, qr_info) = _qr_decide(task_id_ori, qr_table)
                logger.info('[%s] Query-router → %s (router=%s, reason=%s, score=%s, τ=%s)', task_id, qr_decision, qr_table.get('router', '?'), qr_reason, qr_info.get('score'), qr_info.get('threshold', qr_table.get('threshold')))
                route_log = output_dir / 'task_route.json'
                route_log.write_text(json.dumps({'decision': qr_decision, 'reason': qr_reason, 'router': qr_table.get('router', 'routellm-mf'), 'router_kind': 'query-router', 'table_path': str(table_path), 'table_threshold': qr_table.get('threshold'), 'task_score': qr_info.get('score'), 'edge_model': model, 'cloud_model': cloud_model_id, 'edge_label': qr_table.get('edge_label'), 'cloud_label': qr_table.get('cloud_label')}, indent=2, ensure_ascii=False), encoding='utf-8')
                router_usage_path = output_dir / 'router_usage.json'
                router_usage_path.write_text(json.dumps(_qr_build_router_usage_record(qr_decision, qr_reason, qr_info), indent=2, ensure_ascii=False), encoding='utf-8')
                if qr_decision == 'CLOUD':
                    (cloud_provider, cloud_base_url, cloud_api_key, cloud_model_id) = _resolve_provider_model(models_config, cloud_model or cloud_model_id)
                    if not cloud_provider:
                        raise RuntimeError(f"Query Routing cloud model '{cloud_model or cloud_model_id}' is not declared in models_config")
                    model = f'{cloud_provider}/{cloud_model_id}'
                    logger.info('[%s] Query-router → CLOUD: switching to %s @ %s', task_id, model, cloud_base_url)
                for _attempt in range(2):
                    _r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f"echo '{qr_decision}' > /tmp/task_route_decision"], capture_output=True, text=True)
                    if _r.returncode == 0:
                        break
                else:
                    logger.error('[%s] route_decision 标记写入失败(rc=%s): %s', task_id, _r.returncode, (_r.stderr or '')[:200])
                    result['_route_marker_write_failed'] = True
                result['task_route'] = qr_decision
            orig_edge_base_url = orig_edge_api_key = orig_edge_model_id = ''
            edge_provider = ''
            if ec_config.get('rewrite_model_baseurl') and edge_cloud_mode != 'query-router':
                import copy as _copy
                if not models_config:
                    raise RuntimeError('router mode requires --models-config')
                if not cloud_base_url:
                    raise RuntimeError(f"router mode could not resolve cloud model '{cloud_model_id}' from models_config")
                models_config = _copy.deepcopy(models_config)
                (edge_provider, orig_edge_base_url, orig_edge_api_key, orig_edge_model_id) = _resolve_provider_model(models_config, model)
                if not edge_provider:
                    raise RuntimeError(f"router mode could not resolve edge model '{model}' from models_config")
                router_port = int(os.environ.get('ROUTER_PORT', '9303'))
                router_proxy_url = f'http://localhost:{router_port}/v1'
                models_config['providers'][edge_provider]['baseUrl'] = router_proxy_url
                models_config['providers'][edge_provider]['apiKey'] = 'router-no-key'
                logger.info("[%s] Router rewrite: provider '%s' baseUrl %s → %s (edge_model=%s, cloud_model=%s)", task_id, edge_provider, orig_edge_base_url, router_proxy_url, orig_edge_model_id, cloud_model_id)
            if ec_config['skill']:
                if ec_config.get('plugin_only'):
                    if not ec_config['plugin_subdir']:
                        raise ValueError(f'plugin_only=True requires plugin_subdir, but ec_config={ec_config!r}')
                    host_plugin_src = str(ROOT_DIR / 'skills' / ec_config['skill'] / ec_config['plugin_subdir'])
                    container_plugin_dst = f"/root/openclaw_plugins/{ec_config['skill']}"
                    setup_plugin_source(task_id, host_plugin_src, container_plugin_dst)
                    install_openclaw_plugin(task_id, container_plugin_dst)
                else:
                    setup_skills(task_id, ec_config['skill'], str(ROOT_DIR / 'skills'))
                    if ec_config['plugin_subdir']:
                        plugin_container_path = f"/root/skills/{ec_config['skill']}/{ec_config['plugin_subdir']}"
                        install_openclaw_plugin(task_id, plugin_container_path)
            if ec_config['needs_proxy']:
                if ec_config.get('rewrite_model_baseurl'):
                    router_env_parts = [f'export ROUTER_PORT={router_port}', f"export CLOUD_BASE_URL='{cloud_base_url}'", f"export CLOUD_API_KEY='{cloud_api_key}'", f"export CLOUD_MODEL='{cloud_model_id}'", f"export EDGE_BASE_URL='{orig_edge_base_url}'", f"export EDGE_API_KEY='{orig_edge_api_key}'", f"export EDGE_MODEL='{orig_edge_model_id}'"]
                    rm = ec_config.get('router_mode') or os.environ.get('ROUTER_MODE')
                    if rm:
                        router_env_parts.append(f"export ROUTER_MODE='{rm}'")
                    for env_name in ('GLIMPSE_ENTROPY_THRESHOLD', 'GLIMPSE_TOP_K', 'EDGE_STREAM_READ_TIMEOUT_S'):
                        env_val = os.environ.get(env_name)
                        if env_val is not None:
                            router_env_parts.append(f"export {env_name}='{env_val}'")
                    router_env_parts.extend(_signal_prompt_env(ec_config))
                    router_warmup = ' && '.join(router_env_parts) + ' && bash /root/skills/cloud-router/start_router.sh'
                    run_warmup(task_id, router_warmup)
            if ec_config['gateway_cloud_env']:
                effective_base_url = cloud_base_url or 'https://openrouter.ai/api/v1'
                cloud_gateway_env = f"export CLOUD_API_KEY='{cloud_api_key}' && export CLOUD_BASE_URL='{effective_base_url}' && export CLOUD_MODEL='{cloud_model_id}' && "
            logger.info('[%s] Run mode=%s: skill=%s, cloud_model=%s, base_url=%s', task_id, edge_cloud_mode, ec_config['skill'] or '<none>', cloud_model_id, cloud_base_url or 'default')
        run_warmup(task_id, task.get('warmup', ''))
        if models_config:
            inject_openclaw_models(task_id, models_config)
        configure_openclaw_compaction(task_id)
        set_model(task_id, model)
        if ec_config and (ec_config['tools_also_allow'] or ec_config['model_compat']):
            patch_openclaw_config(task_id, tools_also_allow=ec_config['tools_also_allow'] or None, model_compat=ec_config['model_compat'])
        if OPENROUTER_API_KEY:
            auth_profile_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
            inject_cmd = f'''python3 -c "import json, pathlib; p = pathlib.Path('{auth_profile_path}'); d = json.loads(p.read_text()) if p.exists() else {{'version':1,'profiles':{{}}}}; d.setdefault('profiles',{{}})['openrouter:default'] = {{'type':'api_key','provider':'openrouter','key':'{OPENROUTER_API_KEY}'}}; p.write_text(json.dumps(d, indent=2))"'''
            subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', inject_cmd], capture_output=True, text=True)
            logger.info('[%s] Injected OPENROUTER_API_KEY into auth-profiles.json', task_id)
        subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f"openclaw config set agents.defaults.imageModel.primary '{model}'"], capture_output=True, text=True)
        subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f"""openclaw config set agents.defaults.imageModel.fallbacks '["{model}"]'"""], capture_output=True, text=True)
        logger.info('[%s] imageModel set (primary+fallbacks): %s', task_id, model)
        if edge_cloud_mode in ('agentic-advisor',):
            import base64
            ctx_b64 = base64.b64encode(prompt.encode('utf-8')).decode('ascii')
            ctx_path = '/dev/shm/.advisor_ctx.bin'
            ctx_result = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f"echo '{ctx_b64}' | base64 -d > {ctx_path} && chmod 600 {ctx_path}"], capture_output=True, text=True)
            if ctx_result.returncode == 0:
                logger.info('[%s] advisor: wrote executor ctx to %s (%d bytes)', task_id, ctx_path, len(prompt))
            else:
                logger.warning('[%s] advisor: failed to write executor ctx: %s', task_id, ctx_result.stderr.strip())
        gateway_proc = run_background(task_id, bash_cmd=f"export OPENROUTER_API_KEY='{OPENROUTER_API_KEY}' && {cloud_gateway_env}openclaw gateway --port {GATEWAY_PORT}", log_path=output_dir / 'gateway.log')
        _wait_for_docker_gateway(task_id)
        safe_prompt = prompt.replace("'", "'\\''")
        start_time = time.perf_counter()
        agent_proc = run_background(task_id, bash_cmd=f"openclaw agent --session-id chat --timeout {timeout_seconds} --message '{safe_prompt}'", log_path=output_dir / 'agent.log')
        logger.info('[%s] Waiting for agent to finish...', task_id)
        try:
            agent_proc.wait(timeout=timeout_seconds)
            elapsed_time = time.perf_counter() - start_time
            logger.info('[%s] Agent finished successfully, elapsed: %.2f seconds', task_id, elapsed_time)
        except (subprocess.TimeoutExpired, TimeoutError):
            logger.info('[%s] Agent timed out...', task_id)
            elapsed_time = timeout_seconds
            agent_proc.kill()
            agent_proc.wait()
        logger.info('[%s] Agent exit code: %s', task_id, agent_proc.returncode)
    except Exception as exc:
        logger.error('[%s] Execution error: %s', task_id, exc)
        elapsed_time = timeout_seconds
        result['error'] = str(exc)
    finally:
        result = grade_the_task(task_id, workspace_path, output_dir, task, result, grading_max_retries)
        try:
            collect_task_output(task_id, output_dir)
        except Exception as exc:
            logger.warning('[%s] Failed to collect task output: %s', task_id, exc)
        for artifact_path in ec_config['collect_artifacts'] if ec_config else []:
            artifact_name = Path(artifact_path).name
            artifact_dest = output_dir / artifact_name
            cp_art = subprocess.run(['docker', 'cp', f'{task_id}:{artifact_path}', str(artifact_dest)], capture_output=True, text=True)
            if cp_art.returncode == 0:
                logger.info('[%s] Collected %s → %s', task_id, artifact_path, artifact_dest)
        result = cal_cost(task_id, output_dir, result, elapsed_time, model_name=model)
        if gateway_proc is not None:
            try:
                gateway_proc.terminate()
            except Exception:
                pass
        else:
            logger.warning('[%s] Gateway not started, task incomplete — likely missing required result files, check %s', task_id, output_dir)
        for _proc in [gateway_proc, agent_proc]:
            if _proc is not None:
                try:
                    close_proc_log(_proc)
                except Exception:
                    pass
        remove_container(task_id)
        with _active_containers_lock:
            _active_containers.discard(task_id)
        logger.info('[%s] Container cleaned up', task_id)
    return result

def run_single_task_e2b(task: dict, model: str, thinking: str | None=None, models_config: dict | None=None, batch_timestamp: str | None=None, edge_cloud_mode: str | None=None, cloud_model: str | None=None, run_idx: int=1, query_router_table: str | None=None, agent_timeout_seconds: int | None=None, grading_max_retries: int=GRADING_MAX_RETRIES) -> dict:
    """Execute a single task, returning a {"task_id", "scores", "error"} dict."""
    task_id_ori = task['task_id']
    workspace_path = task['workspace_path']
    prompt = task['prompt']
    timeout_seconds = task['timeout_seconds']
    skills = task['skills']
    skills_path = task['skills_path']
    system_prompt = f'You are an expert in a restricted, non-interactive environment. Solve the task efficiently before the timeout ({timeout_seconds}s). Run all processes in the foreground without user input or background services. Provide a complete, functional solution in a single pass with no placeholders. \n'
    ec_config = EDGE_CLOUD_REGISTRY.get(edge_cloud_mode) if edge_cloud_mode else None
    ec_prompt = ec_config.get('prompt', '').rstrip() + '\n\n' if ec_config and ec_config.get('prompt') else ''
    prompt = system_prompt + ec_prompt + prompt
    if ec_config:
        os.environ['EDGE_CLOUD_MODE'] = edge_cloud_mode
        os.environ['PRIVACY_JUDGE'] = '1'
        logger.info('[%s] Run mode=%s, EDGE_CLOUD_MODE set, timeout=%ds', task_id_ori, edge_cloud_mode, timeout_seconds)
    else:
        os.environ['EDGE_CLOUD_MODE'] = edge_cloud_mode or 'local-only'
        os.environ['PRIVACY_JUDGE'] = '1'
    timestamp = batch_timestamp or datetime.now().strftime('%y_%m%d_%H%M')
    run_id = uuid.uuid4().hex[:6]
    _m = re.match('(\\d+)_.*?(task_\\d+)', task_id_ori)
    short_task_id = f'{_m.group(1)}_{_m.group(2)}' if _m else task_id_ori
    short_model = re.sub('[^a-zA-Z0-9.\\-_]', '_', model.rsplit('/', 1)[-1])
    ec_tag = _make_ec_tag(edge_cloud_mode, cloud_model)
    task_id = f'{short_task_id}_{short_model}{ec_tag}_{timestamp}_{run_id}'
    output_dir = build_task_output_dir(OUTPUT_DIR, timestamp, task_id_ori, run_idx)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {'task_id': task_id, 'scores': {}, 'error': None, 'output_dir': str(output_dir)}
    gateway_proc = None
    agent_proc = None
    sbx = None
    elapsed_time = float(timeout_seconds)
    try:
        exec_path = os.path.join(workspace_path, 'exec')
        tmp_path = os.path.join(workspace_path, 'tmp')
        os.makedirs(exec_path, exist_ok=True)
        sbx = e2b.create_sandbox(task_id, exec_path, extra_env=task.get('env', ''), tmp_path=tmp_path, lifecycle_timeout=timeout_seconds + e2b.E2B_SANDBOX_TIMEOUT_BUFFER, tags={'ace_task_id': task_id_ori, 'ace_attempt_id': uuid.uuid4().hex, 'ace_actor_instance_id': os.environ.get('E2B_ACTOR_INSTANCE_ID', '')})
        e2b.setup_workspace(sbx, task_id, thinking=thinking, agent_timeout_seconds=agent_timeout_seconds)
        e2b.setup_skills(sbx, task_id, skills, skills_path)
        cloud_gateway_env = ''
        if ec_config:
            cloud_base_url = ''
            cloud_api_key = ''
            _ = ''
            cloud_model_id = cloud_model or model or 'gpt-5.4-mini'
            if models_config:
                providers = models_config.get('providers', {})
                if '/' in cloud_model_id:
                    (provider_name, model_id) = cloud_model_id.split('/', 1)
                    provider = providers.get(provider_name, {})
                    if provider and (not any((m.get('id') == model_id for m in provider.get('models', [])))):
                        raise RuntimeError(f"router mode cloud model '{cloud_model_id}' is not listed under provider '{provider_name}'")
                    _ = provider_name if provider else ''
                else:
                    provider = {}
                    model_id = cloud_model_id
                    for (pname, pconf) in providers.items():
                        if any((m.get('id') == cloud_model_id for m in pconf.get('models', []))):
                            provider = pconf
                            _ = pname
                            logger.info("Auto-resolved cloud model '%s' → provider '%s'", cloud_model_id, pname)
                            break
                if provider:
                    cloud_base_url = provider.get('baseUrl', '')
                    cloud_api_key = provider.get('apiKey', '')
                    cloud_model_id = model_id
            if edge_cloud_mode == 'query-router':
                table_path = query_router_table or os.environ.get('QUERY_ROUTER_TABLE') or _QR_DEFAULT_ROUTE_TABLE
                qr_table = _qr_load_route_table(table_path)
                (qr_decision, qr_reason, qr_info) = _qr_decide(task_id_ori, qr_table)
                logger.info('[%s] Query-router → %s (router=%s, reason=%s, score=%s, τ=%s)', task_id, qr_decision, qr_table.get('router', '?'), qr_reason, qr_info.get('score'), qr_info.get('threshold', qr_table.get('threshold')))
                route_log = output_dir / 'task_route.json'
                route_log.write_text(json.dumps({'decision': qr_decision, 'reason': qr_reason, 'router': qr_table.get('router', 'routellm-mf'), 'router_kind': 'query-router', 'table_path': str(table_path), 'table_threshold': qr_table.get('threshold'), 'task_score': qr_info.get('score'), 'edge_model': model, 'cloud_model': cloud_model_id, 'edge_label': qr_table.get('edge_label'), 'cloud_label': qr_table.get('cloud_label')}, indent=2, ensure_ascii=False), encoding='utf-8')
                router_usage_path = output_dir / 'router_usage.json'
                router_usage_path.write_text(json.dumps(_qr_build_router_usage_record(qr_decision, qr_reason, qr_info), indent=2, ensure_ascii=False), encoding='utf-8')
                if qr_decision == 'CLOUD':
                    (cloud_provider, cloud_base_url, cloud_api_key, cloud_model_id) = _resolve_provider_model(models_config, cloud_model or cloud_model_id)
                    if not cloud_provider:
                        raise RuntimeError(f"Query Routing cloud model '{cloud_model or cloud_model_id}' is not declared in models_config")
                    model = f'{cloud_provider}/{cloud_model_id}'
                    logger.info('[%s] Query-router → CLOUD: switching to %s @ %s', task_id, model, cloud_base_url)
                for _attempt in range(2):
                    (_out, _err, _rc) = e2b.run_command(sbx, f"echo '{qr_decision}' > /tmp/task_route_decision")
                    if _rc == 0:
                        break
                else:
                    logger.error('[%s] route_decision 标记写入失败(rc=%s): %s', task_id, _rc, (_err or '')[:200])
                    result['_route_marker_write_failed'] = True
                result['task_route'] = qr_decision
            orig_edge_base_url = orig_edge_api_key = orig_edge_model_id = ''
            edge_provider = ''
            if ec_config.get('rewrite_model_baseurl') and edge_cloud_mode != 'query-router':
                import copy as _copy
                if not models_config:
                    raise RuntimeError('router mode requires --models-config')
                if not cloud_base_url:
                    raise RuntimeError(f"router mode could not resolve cloud model '{cloud_model_id}' from models_config")
                models_config = _copy.deepcopy(models_config)
                (edge_provider, orig_edge_base_url, orig_edge_api_key, orig_edge_model_id) = _resolve_provider_model(models_config, model)
                if not edge_provider:
                    raise RuntimeError(f"router mode could not resolve edge model '{model}' from models_config")
                router_port = int(os.environ.get('ROUTER_PORT', '9303'))
                router_proxy_url = f'http://localhost:{router_port}/v1'
                models_config['providers'][edge_provider]['baseUrl'] = router_proxy_url
                models_config['providers'][edge_provider]['apiKey'] = 'router-no-key'
                logger.info("[%s] Router rewrite: provider '%s' baseUrl %s → %s (edge_model=%s, cloud_model=%s)", task_id, edge_provider, orig_edge_base_url, router_proxy_url, orig_edge_model_id, cloud_model_id)
            if ec_config['skill']:
                if ec_config.get('plugin_only'):
                    if not ec_config['plugin_subdir']:
                        raise ValueError(f'plugin_only=True requires plugin_subdir, but ec_config={ec_config!r}')
                    host_plugin_src = str(ROOT_DIR / 'skills' / ec_config['skill'] / ec_config['plugin_subdir'])
                    container_plugin_dst = f"/root/openclaw_plugins/{ec_config['skill']}"
                    e2b.setup_plugin_source(sbx, task_id, host_plugin_src, container_plugin_dst)
                    e2b.install_openclaw_plugin(sbx, task_id, container_plugin_dst)
                else:
                    e2b.setup_skills(sbx, task_id, ec_config['skill'], str(ROOT_DIR / 'skills'))
                    if ec_config['plugin_subdir']:
                        plugin_container_path = f"/root/skills/{ec_config['skill']}/{ec_config['plugin_subdir']}"
                        e2b.install_openclaw_plugin(sbx, task_id, plugin_container_path)
            if ec_config['needs_proxy']:
                if ec_config.get('rewrite_model_baseurl'):
                    router_env_parts = [f'export ROUTER_PORT={router_port}', f"export CLOUD_BASE_URL='{cloud_base_url}'", f"export CLOUD_API_KEY='{cloud_api_key}'", f"export CLOUD_MODEL='{cloud_model_id}'", f"export EDGE_BASE_URL='{orig_edge_base_url}'", f"export EDGE_API_KEY='{orig_edge_api_key}'", f"export EDGE_MODEL='{orig_edge_model_id}'"]
                    rm = ec_config.get('router_mode') or os.environ.get('ROUTER_MODE')
                    if rm:
                        router_env_parts.append(f"export ROUTER_MODE='{rm}'")
                    for env_name in ('GLIMPSE_ENTROPY_THRESHOLD', 'GLIMPSE_TOP_K', 'EDGE_STREAM_READ_TIMEOUT_S'):
                        env_val = os.environ.get(env_name)
                        if env_val is not None:
                            router_env_parts.append(f"export {env_name}='{env_val}'")
                    router_env_parts.extend(_signal_prompt_env(ec_config))
                    router_warmup = ' && '.join(router_env_parts) + ' && bash /root/skills/cloud-router/start_router.sh'
                    e2b.run_warmup(sbx, task_id, router_warmup)
            if ec_config['gateway_cloud_env']:
                effective_base_url = cloud_base_url or 'https://openrouter.ai/api/v1'
                cloud_gateway_env = f"export CLOUD_API_KEY='{cloud_api_key}' && export CLOUD_BASE_URL='{effective_base_url}' && export CLOUD_MODEL='{cloud_model_id}' && "
            logger.info('[%s] Run mode=%s: skill=%s, cloud_model=%s, base_url=%s', task_id, edge_cloud_mode, ec_config['skill'] or '<none>', cloud_model_id, cloud_base_url or 'default')
        e2b.run_warmup(sbx, task_id, task.get('warmup', ''))
        if models_config:
            e2b.inject_openclaw_models(sbx, task_id, models_config)
        e2b.configure_openclaw_compaction(sbx, task_id)
        e2b.set_model(sbx, task_id, model)
        if ec_config and (ec_config['tools_also_allow'] or ec_config['model_compat']):
            e2b.patch_openclaw_config(sbx, task_id, tools_also_allow=ec_config['tools_also_allow'] or None, model_compat=ec_config['model_compat'])
        if OPENROUTER_API_KEY:
            auth_profile_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
            inject_cmd = f'''python3 -c "import json, pathlib; p = pathlib.Path('{auth_profile_path}'); d = json.loads(p.read_text()) if p.exists() else {{'version':1,'profiles':{{}}}}; d.setdefault('profiles',{{}})['openrouter:default'] = {{'type':'api_key','provider':'openrouter','key':'{OPENROUTER_API_KEY}'}}; p.write_text(json.dumps(d, indent=2))"'''
            e2b.run_command(sbx, inject_cmd)
            logger.info('[%s] Injected OPENROUTER_API_KEY into auth-profiles.json', task_id)
        e2b.configure_openclaw_image_model(sbx, task_id, model)
        if edge_cloud_mode in ('agentic-advisor',):
            import base64
            ctx_b64 = base64.b64encode(prompt.encode('utf-8')).decode('ascii')
            ctx_path = '/dev/shm/.advisor_ctx.bin'
            (_, ctx_stderr, ctx_rc) = e2b.run_command(sbx, f"echo '{ctx_b64}' | base64 -d > {ctx_path} && chmod 600 {ctx_path}")
            if ctx_rc == 0:
                logger.info('[%s] advisor: wrote executor ctx to %s (%d bytes)', task_id, ctx_path, len(prompt))
            else:
                logger.warning('[%s] advisor: failed to write executor ctx: %s', task_id, ctx_stderr.strip())
        gateway_proc = e2b.run_background(sbx, task_id, bash_cmd=f"export OPENROUTER_API_KEY='{OPENROUTER_API_KEY}' && {cloud_gateway_env}openclaw gateway --port {GATEWAY_PORT}", log_path=output_dir / 'gateway.log')
        _wait_for_e2b_gateway(sbx, task_id)
        safe_prompt = prompt.replace("'", "'\\''")
        start_time = time.perf_counter()
        if ec_config is None:
            logger.info('[%s] Running baseline agent synchronously...', task_id)
            try:
                (agent_stdout, agent_stderr, agent_rc) = e2b.run_command(sbx, f"cd {e2b.TMP_WORKSPACE} && openclaw agent --session-id chat --timeout {timeout_seconds} --message '{safe_prompt}'", timeout=int(timeout_seconds) + 180)
                elapsed_time = time.perf_counter() - start_time
                (output_dir / 'agent.log').write_text(f'{agent_stdout}\n--- stderr ---\n{agent_stderr}\n', encoding='utf-8')
                logger.info('[%s] Agent finished (exit=%s, %.2f seconds)', task_id, agent_rc, elapsed_time)
            except Exception as agent_exc:
                elapsed_time = time.perf_counter() - start_time
                (output_dir / 'agent.log').write_text(f'Agent error after {elapsed_time:.1f}s: {agent_exc}\n', encoding='utf-8')
                logger.warning('[%s] Agent timed out or errored (%.2f seconds): %s', task_id, elapsed_time, agent_exc)
        else:
            agent_proc = e2b.run_background(sbx, task_id, bash_cmd=f"openclaw agent --session-id chat --timeout {timeout_seconds} --message '{safe_prompt}'", log_path=output_dir / 'agent.log')
            logger.info('[%s] Waiting for agent to finish...', task_id)
            try:
                agent_proc.wait(timeout=timeout_seconds)
                elapsed_time = time.perf_counter() - start_time
                logger.info('[%s] Agent finished successfully, elapsed: %.2f seconds', task_id, elapsed_time)
            except (subprocess.TimeoutExpired, TimeoutError):
                logger.info('[%s] Agent timed out...', task_id)
                elapsed_time = timeout_seconds
                agent_proc.kill()
                agent_proc.wait()
            logger.info('[%s] Agent exit code: %s', task_id, agent_proc.returncode)
    except Exception as exc:
        logger.error('[%s] Execution error: %s', task_id, exc)
        elapsed_time = timeout_seconds
        result['error'] = str(exc)
    finally:
        if sbx is None:
            logger.warning('[%s] Sandbox not created or setup failed before assignment — check %s', task_id, output_dir)
            return result
        result = grade_the_task_e2b(sbx, task_id, workspace_path, output_dir, task, result, grading_max_retries)
        try:
            e2b.collect_output_from_sandbox(sbx, task_id, output_dir)
        except Exception as exc:
            logger.warning('[%s] Failed to collect task output: %s', task_id, exc)
        if not result.get('error'):
            _fetch_chat_transcript_e2b(sbx, task_id, output_dir)
            (_timed, _term) = _rollout_request_failures(output_dir)
            if _timed + _term >= int(os.environ.get('DEGRADED_RETRY_MIN_FAILS', '1')):
                _sc = _result_overall_score(result)
                result['error'] = f'degraded rollout: {_timed} model request(s) timed out / {_term} terminated mid-conversation (score={_sc}) — retrying'
                result['_degraded_request_failures'] = {'timed_out': _timed, 'terminated': _term}
                logger.warning('[%s] 降级 rollout(%d 超时 / %d 断连,score=%s)→ 标记重试', task_id, _timed, _term, _sc)
        for artifact_path in ec_config['collect_artifacts'] if ec_config else []:
            artifact_name = Path(artifact_path).name
            artifact_dest = output_dir / artifact_name
            if e2b.download_file(sbx, artifact_path, str(artifact_dest)):
                logger.info('[%s] Collected %s -> %s', task_id, artifact_path, artifact_dest)
        result = cal_cost_e2b(sbx, task_id, output_dir, result, elapsed_time, model_name=model)
        if gateway_proc is not None:
            try:
                gateway_proc.terminate()
            except Exception:
                pass
        else:
            logger.warning('[%s] Gateway not started, task incomplete — likely missing required result files, check %s', task_id, output_dir)
        for _proc in [gateway_proc, agent_proc]:
            if _proc is not None:
                try:
                    e2b.close_proc_log(_proc)
                except Exception:
                    pass
        e2b.kill_sandbox(sbx, task_id) if sbx is not None else None
        logger.info('[%s] Sandbox cleaned up', task_id)
    return result
TASK_MAX_RETRIES = int(os.environ.get('MAX_TASK_RETRIES', os.environ.get('TASK_MAX_RETRIES', '5')))
TASK_RETRY_BACKOFF = float(os.environ.get('TASK_RETRY_BACKOFF', '5'))
TASK_RETRY_MAX_BACKOFF = float(os.environ.get('TASK_RETRY_MAX_BACKOFF', '60'))
_TIMEOUT_MARKERS = ('deadline exceeded', 'timed out after', 'timeoutexpired')
_PERMANENT_MARKERS = ('模型不存在', 'model not found', 'model_not_found', 'no such model', 'unknown model', 'invalid model', 'model does not exist', 'invalid api key', 'invalid_api_key', 'incorrect api key', 'unauthorized', 'permission denied', 'invalid_request_error', 'forbidden', 'authentication failed')
_HTTP_STATUS_RE = re.compile('(?:\\b(?:status|error|http)[ _-]?(?:code)?\\b\\D{0,6}|http/\\d\\.\\d\\s+)(?<![\\d.])(?:400|401|403|404|422)(?![\\d.])|(?<![\\d.])(?:400|401|403|404|422)(?![\\d.])\\s+(?:bad request|unauthorized|forbidden|not found|unprocessable)')

def _no_retry(err) -> bool:
    """True if the failure should NOT be retried (timeout OR permanent/config error)."""
    if not err:
        return False
    e = str(err).lower()
    return any((m in e for m in _TIMEOUT_MARKERS)) or any((m in e for m in _PERMANENT_MARKERS)) or bool(_HTTP_STATUS_RE.search(e))

def _retry_sleep(_try: int) -> float:
    return min(TASK_RETRY_BACKOFF * 2 ** _try, TASK_RETRY_MAX_BACKOFF)

def _rollout_request_failures(output_dir) -> tuple[int, int]:
    """扫 chat.jsonl,统计对话中途**瞬时性**的模型请求失败次数。"""
    _DROP_MARKERS = ('"errorMessage":"terminated"', 'peer closed connection', 'incomplete chunked read', 'Request was aborted', '"errorMessage":"502', '"errorMessage":"503', '"errorMessage":"504')
    try:
        p = Path(output_dir) / 'chat.jsonl'
        if not p.exists():
            return (0, 0)
        timed = dropped = 0
        for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            if '"stopReason":"error"' not in line:
                continue
            if 'request timed out' in line.lower():
                timed += 1
            elif any((m in line for m in _DROP_MARKERS)):
                dropped += 1
        return (timed, dropped)
    except Exception:
        return (0, 0)

def _env_flag(name: str, *, default: bool) -> bool:
    """Read a boolean env var, falling back to *default* when unset/blank."""
    raw = os.environ.get(name, '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}
RAMP = os.environ.get('RAMP', '60:0.25,180:0.5,360:0.8').strip()
_ramp_stages = []
if RAMP and RAMP.lower() != 'off':
    for _p in RAMP.split(','):
        _p = _p.strip()
        if not _p:
            continue
        try:
            (_t, _v) = _p.split(':')
            _ramp_stages.append((float(_t), float(_v)))
        except Exception:
            pass
_ramp_stages.sort()
_ramp_lock = threading.RLock()
_ramp_inflight = 0
_ramp_start = None
_ramp_last_cap = None

def _ramp_cap(full: int) -> int:
    if not _ramp_stages or _ramp_start is None:
        return full
    elapsed = time.time() - _ramp_start
    for (_t, _v) in _ramp_stages:
        if elapsed < _t:
            cap = int(full * _v) if _v < 1 else int(_v)
            return max(1, min(full, cap))
    return full

def _ramp_begin(full: int) -> None:
    """Start the concurrency-ramp clock (idempotent)."""
    global _ramp_start
    if not _ramp_stages:
        return
    with _ramp_lock:
        if _ramp_start is None:
            _ramp_start = time.time()
            _banner(_paint('▶ 并发爬坡启用 ', 'cyan') + _paint(RAMP, 'white_bold') + _paint(f'（目标全量={full}）', 'gray'))

def _ramp_cap_announced(full: int) -> int:
    """Current stage cap, printing one line whenever it changes."""
    global _ramp_last_cap
    if not _ramp_stages:
        return full
    with _ramp_lock:
        cap = _ramp_cap(full)
        if cap != _ramp_last_cap:
            elapsed = int(time.time() - _ramp_start) if _ramp_start else 0
            at_full = cap >= full
            tag = _paint('全量', 'green') if at_full else _paint('限流', 'yellow')
            _banner(_paint('▶ ramp ', 'cyan') + _paint(f'+{elapsed:>4}s ', 'gray') + _paint(_ratio_bar(cap, full), 'green' if at_full else 'yellow') + ' ' + _paint(f'并发上限 {cap}/{full}', 'white_bold') + '  ' + tag)
            _ramp_last_cap = cap
        return cap

def _ramp_acquire(full: int) -> None:
    """Block until fewer than the current-stage cap of tasks are in flight."""
    global _ramp_inflight
    if not _ramp_stages:
        return
    _ramp_begin(full)
    while True:
        with _ramp_lock:
            cap = _ramp_cap_announced(full)
            if _ramp_inflight < cap:
                _ramp_inflight += 1
                return
        time.sleep(1.0)

def _ramp_release() -> None:
    global _ramp_inflight
    if not _ramp_stages:
        return
    with _ramp_lock:
        _ramp_inflight = max(0, _ramp_inflight - 1)

def main() -> None:
    _configure_compact_logging()
    parser = argparse.ArgumentParser(description='EC2-Bench evaluation entry point', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='See README.md and configs/strategies.json for the six paper strategies.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--task', '-t', help='Path to a single task.md file')
    mode.add_argument('--category', '-c', help='Task category; use ACE_Bench for the complete 128-task suite.')
    parser.add_argument('--edge-model', default=None, help='Provider/model reference for the edge executor.')
    parser.add_argument('--parallel', '-p', type=int, default=DEFAULT_PARALLEL, metavar='N', help='Number of parallel containers (default: 1, i.e. sequential)')
    parser.add_argument('--max-retries', type=int, default=TASK_MAX_RETRIES, metavar='N', help=f'Retry recoverable execution failures up to N times (default: {TASK_MAX_RETRIES})')
    parser.add_argument('--skip-sandbox-reap', action='store_true', help='跳过启动时对上次运行残留沙箱的清理(仅 --runtime e2b)。清理只针对本项目(ace_owner) 且已过期超过 E2B_REAP_GRACE_SECONDS 的沙箱,正常不会误杀;排查问题需要保留现场时再用。')
    parser.add_argument('--log-verbosity', choices=['compact', 'normal', 'verbose'], default=os.environ.get('LOG_VERBOSITY', 'compact'), help='日志详细度:compact=只显示 running/done + error(默认);normal=+警告(download failed / results 缺失等);verbose=+e2b/http 细节')
    parser.add_argument('--grading-max-retries', type=int, default=GRADING_MAX_RETRIES, metavar='N', help=f'Retry grading N times against the same trajectory (default: {GRADING_MAX_RETRIES}; total attempts=N+1). Deterministic failures stop early regardless. GRADING_MAX_ELAPSED={DEFAULT_GRADING_MAX_ELAPSED:.0f}s is a soft budget: it blocks starting another attempt, it cannot cancel one already running, so worst-case grading is that budget plus one attempt')
    parser.add_argument('--runtime', choices=['docker', 'e2b'], default='docker', help='Execution runtime: local Docker containers (default) or E2B sandboxes')
    parser.add_argument('--models-config', default=None, help='Path to a JSON file that will replace the top-level models field in ~/.openclaw/openclaw.json before each task')
    parser.add_argument('--thinking', default=None, help='OpenClaw thinking level.')
    parser.add_argument('--agent-timeout-seconds', type=int, default=None, help='Maximum duration of an agent model request, in seconds.')
    parser.add_argument('--output-dir', '-o', default=None, help='Custom output directory path (default: <project_root>/output/)')
    parser.add_argument('--limit', type=int, default=0, help='Only run the first N tasks per category (0 = all)')
    parser.add_argument('--task-filter', default=None, help="Regex pattern to filter task filenames, e.g. 'task_9|task_10|task_11'")
    parser.add_argument('--repeat', type=int, default=1, metavar='N', help='Run each task N times and compute avg@N (default: 1, single run)')
    parser.add_argument('--run-mode', default='local-only', choices=['local-only', 'cloud-only', 'query-router', 'step-router-execute-glimpse', 'agentic_executor_router', 'agentic-advisor'], help='Six paper strategies; see README.md for definitions.')
    parser.add_argument('--cloud-model', default=None, help='Provider/model reference for the cloud collaborator or cloud-only executor.')
    parser.add_argument('--query-router-table', default=None, help=f'(query-router only) Path to the offline RouteLLM-MF route table JSON. If omitted, falls back to env QUERY_ROUTER_TABLE, then to {_QR_DEFAULT_ROUTE_TABLE}. Generate with `python -m utils.query_router build --output ...`.')
    parser.add_argument('--skip-preflight', action='store_true', help='Skip the pre-launch endpoint health check for the edge / cloud / judge models. By default every endpoint is probed once and the run aborts immediately if any is unreachable.')
    parser.add_argument('--preflight-timeout', type=float, default=float(os.environ.get('PREFLIGHT_TIMEOUT_S', '20.0')), help='Per-endpoint timeout (seconds) for the pre-launch health check (default: 20, 可用 PREFLIGHT_TIMEOUT_S 覆盖).')
    parser.add_argument('--warmup-seconds', type=float, default=float(os.environ.get('WARMUP_SECONDS', '60')), help='正式评测前预热端侧(vLLM)模型的时长(秒)：并发从低爬到 --parallel，把冷 vLLM 各 batch-size 的 CUDA graph 都热起来，避免首轮大量请求超时被判 0。0=跳过。也可用环境变量 WARMUP_SECONDS 覆盖。默认 60(1 分钟)。')
    args = parser.parse_args()
    _configure_compact_logging(args.log_verbosity)
    if args.repeat <= 0:
        parser.error('--repeat must be >= 1')
    if args.max_retries < 0:
        parser.error('--max-retries must be non-negative')
    if args.grading_max_retries < 0:
        parser.error('--grading-max-retries must be non-negative')
    run_mode = None if args.run_mode == 'local-only' else args.run_mode
    args.edge_cloud_mode = run_mode
    EDGE_LED_MODES = {'query-router', 'agentic-advisor', 'agentic_executor_router', 'step-router-execute-glimpse'}
    em = args.edge_model
    cm = args.cloud_model
    if run_mode is None:
        if em is None:
            parser.error('local-only requires --edge-model')
        if cm is not None:
            logger.warning('local-only: --cloud-model is ignored')
        args.model = em
        args.cloud_model = None
    elif run_mode == 'cloud-only':
        if cm is None:
            parser.error('cloud-only requires --cloud-model')
        if em is not None:
            logger.warning('cloud-only: --edge-model is ignored')
        args.model = cm
        args.cloud_model = None
    elif run_mode in EDGE_LED_MODES:
        if em is None or cm is None:
            parser.error(f'{run_mode} requires both --edge-model and --cloud-model')
        args.model = em
        args.cloud_model = cm
    else:
        parser.error(f'unhandled run-mode {run_mode!r}')
    resource_models = {'edge_model_name': None if run_mode == 'cloud-only' else args.model, 'cloud_model_name': args.model if run_mode == 'cloud-only' else args.cloud_model}
    logger.info('Resolved: run_mode=%s  main_agent(args.model)=%s  other(args.cloud_model)=%s  [edge=%s cloud=%s]', run_mode, args.model, args.cloud_model, em, cm)
    if args.model is None:
        parser.error('--edge-model or --cloud-model required (depending on --run-mode)')
    run_task_fn = run_single_task_e2b if args.runtime == 'e2b' else run_single_task
    logger.info('Runtime: %s', args.runtime)
    _banner(_paint('▶ executor=thread ', 'cyan') + _paint('单进程 ThreadPoolExecutor', 'gray'))
    if args.runtime == 'e2b':
        os.environ.setdefault('E2B_BATCH_ID', uuid.uuid4().hex)
        e2b.E2B_BATCH_ID = os.environ['E2B_BATCH_ID']
        _banner(_paint('▶ batch ', 'cyan') + _paint(e2b.E2B_BATCH_ID[:12], 'white_bold') + _paint(' (沙箱按此标签回收)', 'gray'))
        if not args.skip_sandbox_reap:
            try:
                reaped = e2b.reap_stale_batches()
                if reaped:
                    _banner(_paint(f'▶ 清理了 {reaped} 个上次运行的残留沙箱', 'yellow'))
            except Exception as exc:
                logger.warning('启动清理失败(不影响本次运行): %s', exc)
        atexit.register(_reap_batch_on_exit)
    if args.output_dir:
        global OUTPUT_DIR, SUMMARY_OUTPUT_DIR
        OUTPUT_DIR = Path(args.output_dir).resolve()
    batch_timestamp = datetime.now().strftime('%y_%m%d_%H%M')
    SUMMARY_OUTPUT_DIR = OUTPUT_DIR / batch_timestamp
    write_run_invocation(SUMMARY_OUTPUT_DIR, args, batch_timestamp=batch_timestamp)
    models_config = None
    if args.models_config:
        models_config_path = Path(args.models_config).expanduser()
        if not models_config_path.is_file():
            logger.error('Models config not found: %s', models_config_path)
            sys.exit(1)
        try:
            models_config = load_models_config(models_config_path.resolve())
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error('Invalid models config: %s', exc)
            sys.exit(1)
    if args.edge_cloud_mode:
        try:
            from utils.privacy_audit import PRIVACY_DESIGNS
        except ImportError as exc:
            logger.error('无法导入隐私设计注册表: %s', exc)
            sys.exit(1)
        if args.edge_cloud_mode not in PRIVACY_DESIGNS:
            logger.error('run-mode %r 没有登记隐私设计，拒绝启动。\n      每新增一种端云协作方案，都必须先决定云端会看到什么，并在 utils/privacy_audit.py 的 PRIVACY_DESIGNS 里登记：\n        audit_all      每次上云只送一个问题（问答式）\n        audit_last     每次上云带累积上下文（只判最后一条）\n        chat_full      全程在云端\n        route_decision 按路由决策取数\n        asserted_safe  结构性无泄露（须写明理由）\n      已登记: %s', args.edge_cloud_mode, ', '.join(sorted(PRIVACY_DESIGNS)))
            sys.exit(1)
        logger.info('[privacy] run-mode=%s 采用 %s 口径', args.edge_cloud_mode, PRIVACY_DESIGNS[args.edge_cloud_mode])
    try:
        judge_pool = resolve_judge_endpoint_pool()
    except JudgePoolConfigError as exc:
        logger.error('%s', exc)
        sys.exit(1)
    if len(judge_pool) > 1:
        logger.info('Judge pool: %d endpoint(s) — grading rotates across them', len(judge_pool))
    if not args.skip_preflight:
        preflight_check_endpoints(models_config, run_mode, args.model, args.cloud_model, timeout=args.preflight_timeout)
    if args.warmup_seconds and args.warmup_seconds > 0:
        warmup_edge_endpoint(models_config, run_mode, args.model, args.warmup_seconds, args.parallel, timeout=max(args.preflight_timeout, 30.0))
    if args.task:
        task_file = Path(args.task)
        if not task_file.exists():
            logger.error('File not found: %s', task_file)
            sys.exit(1)
        task = parse_task_md(task_file)
        logger.info('Single task mode: %s', task['task_id'])
        safe_model_name = re.sub('[^a-zA-Z0-9.\\-_]', '_', args.model)
        ec_summary_tag = _make_ec_tag(args.edge_cloud_mode, args.cloud_model)
        summary_label = f'{safe_model_name}{ec_summary_tag}'
        summary_category = task['category']
        per_run_results: list[list[dict]] = []
        per_run_wall_clocks: list[float] = []
        completed_score_sum = 0.0
        for run_idx in range(1, args.repeat + 1):
            cur_run_idx = run_idx
            if _SHOW_RUNNING:
                print(_compact_start_line(attempt_number=run_idx, total_attempts=args.repeat, task=task, rollout_index=run_idx, total_rollouts=args.repeat), file=sys.stderr, flush=True)
            run_wall_start = time.perf_counter()
            for attempt_index in range(args.max_retries + 1):
                result = run_task_fn(task, args.model, models_config=models_config, thinking=args.thinking, batch_timestamp=batch_timestamp, edge_cloud_mode=args.edge_cloud_mode, cloud_model=args.cloud_model, run_idx=cur_run_idx, query_router_table=args.query_router_table, agent_timeout_seconds=args.agent_timeout_seconds, grading_max_retries=args.grading_max_retries)
                error = result.get('error')
                if not error or _no_retry(error) or attempt_index >= args.max_retries:
                    break
                logger.warning('[%s] Recoverable failure on attempt %d/%d; retrying after backoff: %s', task.get('task_id'), attempt_index + 1, args.max_retries + 1, str(error)[:300])
                time.sleep(_retry_sleep(attempt_index))
            run_elapsed = time.perf_counter() - run_wall_start
            score = _result_overall_score(result)
            if not result.get('error') and (not (result.get('scores') or {}).get('error')) and (score is not None):
                completed_score_sum += score
            running_average = completed_score_sum / run_idx
            print(_compact_done_line(attempt_number=run_idx, total_attempts=args.repeat, task=task, rollout_index=run_idx, total_rollouts=args.repeat, result=result, elapsed=run_elapsed, completed_attempts=run_idx, average_score=running_average), file=sys.stderr, flush=True)
            run_wall_clock = run_elapsed
            per_run_wall_clocks.append(run_wall_clock)
            per_run_results.append([result])
            run_summary_label = f'{summary_label}_run{run_idx}' if args.repeat > 1 else summary_label
            print_summary([result], summary_category, SUMMARY_OUTPUT_DIR, run_summary_label, expected_tasks=1, wall_clock_time=run_wall_clock, **resource_models)
        if args.repeat > 1:
            print_avg_summary(per_run_results, summary_category, SUMMARY_OUTPUT_DIR, summary_label, args.repeat, wall_clock_time=sum(per_run_wall_clocks), **resource_models)
        return
    if args.category.lower() == 'all':
        categories = ALL_CATEGORIES
    else:
        categories = [args.category]
    all_results: list[dict] = []
    batch_wall_start = time.perf_counter()
    safe_model_name = re.sub('[^a-zA-Z0-9.\\-_]', '_', args.model)
    for category in categories:

        def _task_sort_key(p: Path) -> tuple:
            import re as _re
            m = _re.search('task_(\\d+)', p.stem)
            return (int(m.group(1)) if m else float('inf'), p.stem)
        category_dir = TASKS_DIR / category
        if category_dir.is_dir():
            task_files = sorted(category_dir.glob('*task_*.md'), key=_task_sort_key)
        else:

            def _fm_category(p: Path) -> str | None:
                import re as _re
                try:
                    head = p.read_text(encoding='utf-8')[:2000]
                except Exception:
                    return None
                m = _re.search('(?m)^category:\\s*"?([^"\\n]+?)"?\\s*$', head)
                return m.group(1).strip() if m else None
            task_files = sorted([f for f in TASKS_DIR.glob('*task_*.md') if _fm_category(f) == category], key=_task_sort_key)
            if not task_files:
                logger.error('Category directory not found and no flat tasks matched: %s', category_dir)
                continue
        if args.task_filter:
            import re as _re_filt
            pat = _re_filt.compile(args.task_filter)
            task_files = [f for f in task_files if pat.search(f.stem)]
        if not task_files:
            logger.error('No task_*.md files found in: %s (filter=%s)', category_dir, args.task_filter)
            continue
        logger.info('Category: %s, %d tasks, parallelism: %d', category, len(task_files), args.parallel)
        tasks = []
        for tf in task_files:
            try:
                tasks.append(parse_task_md(tf))
            except Exception as exc:
                logger.error('Parse failed %s: %s', tf, exc)
        if not tasks:
            continue
        if args.limit > 0:
            tasks = tasks[:args.limit]
            logger.info('Limiting to first %d tasks', args.limit)
        if _env_flag('LONGEST_FIRST', default=True) and len(tasks) > 1:
            tasks.sort(key=lambda t: -int(t.get('timeout_seconds') or 0))
            _budgets = [int(t.get('timeout_seconds') or 0) for t in tasks]
            _banner(_paint('▶ LPT ', 'cyan') + _paint(f'最长作业优先({_budgets[0]}s…{_budgets[-1]}s),队首 ', 'gray') + _paint(str(tasks[0].get('task_id')), 'white_bold'))
        ec_summary_tag = _make_ec_tag(args.edge_cloud_mode, args.cloud_model)
        summary_label = f'{safe_model_name}{ec_summary_tag}'
        summary_category = category
        write_run_invocation(SUMMARY_OUTPUT_DIR, args, batch_timestamp=batch_timestamp, category=category, summary_category=summary_category)
        repeat = args.repeat
        per_run_results: list[list[dict]] = []
        per_run_wall_clocks: list[float] = []
        progress_lock = threading.Lock()
        total_attempts = len(tasks) * repeat
        progress_completed = 0
        progress_score_sum = 0.0

        def _run_task_attempt(attempt_number: int, task: dict, run_idx: int) -> tuple[int, dict]:
            nonlocal progress_completed, progress_score_sum
            cur_run_idx = run_idx
            with progress_lock:
                if _SHOW_RUNNING:
                    print(_compact_start_line(attempt_number=attempt_number, total_attempts=total_attempts, task=task, rollout_index=run_idx, total_rollouts=repeat), file=sys.stderr, flush=True)
            _ramp_acquire(args.parallel)
            attempt_started = time.perf_counter()
            try:
                for _try in range(args.max_retries + 1):
                    result = run_task_fn(task, args.model, models_config=models_config, thinking=args.thinking, batch_timestamp=batch_timestamp, edge_cloud_mode=args.edge_cloud_mode, cloud_model=args.cloud_model, run_idx=cur_run_idx, query_router_table=args.query_router_table, agent_timeout_seconds=args.agent_timeout_seconds, grading_max_retries=args.grading_max_retries)
                    _err = result.get('error')
                    if not _err or _no_retry(_err) or _try >= args.max_retries:
                        break
                    logger.warning('[%s] 可重试失败(%s)，重试 %d/%d', task.get('task_id'), str(_err)[:120], _try + 1, args.max_retries)
                    time.sleep(_retry_sleep(_try))
            finally:
                _ramp_release()
            attempt_elapsed = time.perf_counter() - attempt_started
            with progress_lock:
                progress_completed += 1
                score = _result_overall_score(result)
                if not result.get('error') and (not (result.get('scores') or {}).get('error')) and (score is not None):
                    progress_score_sum += score
                print(_compact_done_line(attempt_number=attempt_number, total_attempts=total_attempts, task=task, rollout_index=run_idx, total_rollouts=repeat, result=result, elapsed=attempt_elapsed, completed_attempts=progress_completed, average_score=progress_score_sum / progress_completed, total_elapsed=time.perf_counter() - batch_wall_start), file=sys.stderr, flush=True)
            return (attempt_number, result)
        run_buckets: dict[int, dict[int, dict]] = {r: {} for r in range(1, repeat + 1)}
        repeat_start: dict[int, float] = {}
        per_run_results_map: dict[int, list] = {}
        repeat_wall_map: dict[int, float] = {}
        flat_wall_start = time.perf_counter()

        def _specs_for_repeat(r):
            return [((r - 1) * len(tasks) + off, task, r) for (off, task) in enumerate(tasks, start=1)]

        def _finalize_repeat(r: int) -> None:
            """某个 repeat 全部 task 完成后调用:写它的 run_N summary(用该 repeat 自己的墙钟)。"""
            phase_specs = _specs_for_repeat(r)
            results = [run_buckets[r][n] for (n, _t, _ri) in phase_specs if n in run_buckets[r]]
            rwall = time.perf_counter() - repeat_start.get(r, flat_wall_start)
            per_run_results_map[r] = results
            repeat_wall_map[r] = rwall
            run_summary_label = f'{summary_label}_run{r}' if repeat > 1 else summary_label
            print_summary(results, summary_category, SUMMARY_OUTPUT_DIR, run_summary_label, expected_tasks=len(tasks), wall_clock_time=rwall, **resource_models)
            all_results.extend(results)
            logger.info('[repeat %d] 全部完成,已写 run%d summary(%.0fs, %d tasks)', r, r, rwall, len(results))
        if args.parallel <= 1:
            for r in range(1, repeat + 1):
                repeat_start[r] = time.perf_counter()
                for (attempt_number, task, run_idx) in _specs_for_repeat(r):
                    (_, result) = _run_task_attempt(attempt_number, task, run_idx)
                    run_buckets[run_idx][attempt_number] = result
                _finalize_repeat(r)
        else:
            outstanding = {r: 0 for r in range(1, repeat + 1)}
            futures: dict = {}
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:

                def _submit_repeat(r):
                    new = []
                    repeat_start[r] = time.perf_counter()
                    for (attempt_number, task, run_idx) in _specs_for_repeat(r):
                        fut = pool.submit(_run_task_attempt, attempt_number, task, run_idx)
                        futures[fut] = (attempt_number, task, run_idx)
                        outstanding[r] += 1
                        new.append(fut)
                    logger.info('[repeat %d] 已提交 %d 个 task', r, len(tasks))
                    return new
                logger.info('Category %s — starting (FULL FLATTEN: %d attempts @ parallel=%d)', category, total_attempts, args.parallel)
                pending = set()
                for r in range(1, repeat + 1):
                    pending |= set(_submit_repeat(r))
                while pending:
                    (done, pending) = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        (attempt_number, task, run_idx) = futures[future]
                        try:
                            (done_attempt, result) = future.result()
                            run_buckets[run_idx][done_attempt] = result
                        except Exception as exc:
                            tid = task['task_id']
                            logger.error('[%s] Thread exception: %s', tid, exc)
                            result = {'task_id': tid, 'scores': {}, 'error': str(exc)}
                            _try_recover_crashed_result(result, tid, run_idx, batch_timestamp, model_name=args.model)
                            run_buckets[run_idx][attempt_number] = result
                            with progress_lock:
                                progress_completed += 1
                                print(_compact_done_line(attempt_number=attempt_number, total_attempts=total_attempts, task=task, rollout_index=run_idx, total_rollouts=repeat, result=result, elapsed=0.0, completed_attempts=progress_completed, average_score=progress_score_sum / progress_completed), file=sys.stderr, flush=True)
                        outstanding[run_idx] -= 1
                        if outstanding[run_idx] == 0:
                            _finalize_repeat(run_idx)
        flat_wall_clock = time.perf_counter() - flat_wall_start
        per_run_results = [per_run_results_map[r] for r in range(1, repeat + 1) if r in per_run_results_map]
        per_run_wall_clocks = [repeat_wall_map[r] for r in range(1, repeat + 1) if r in repeat_wall_map]
        if repeat > 1:
            print_avg_summary(per_run_results, summary_category, SUMMARY_OUTPUT_DIR, summary_label, repeat, wall_clock_time=flat_wall_clock, **resource_models)
    if len(categories) > 1 and all_results:
        ec_summary_tag = _make_ec_tag(args.edge_cloud_mode, args.cloud_model)
        summary_label = f'{safe_model_name}{ec_summary_tag}'
        print_global_summary(all_results, SUMMARY_OUTPUT_DIR, summary_label, repeat=args.repeat, wall_clock_time=time.perf_counter() - batch_wall_start, **resource_models)
if __name__ == '__main__':
    main()
