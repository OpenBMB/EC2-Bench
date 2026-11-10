"""E2B sandbox utilities for benchmark evaluation."""
from __future__ import annotations
import json
import logging
import os
import queue
import random
import base64
import shlex
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
_THIS_DIR = Path(__file__).resolve().parent
load_dotenv(_THIS_DIR.parent / '.env')
logger = logging.getLogger(__name__)
DEFAULT_COMMAND_TIMEOUT = 3600
WARMUP_COMMAND_TIMEOUT = 3600
WARMUP_SCRIPT_TIMEOUT = 3600
E2B_FILE_OPERATION_TIMEOUT = int(os.environ.get('E2B_FILE_OPERATION_TIMEOUT', '120'))
E2B_KILL_TIMEOUT = int(os.environ.get('E2B_KILL_TIMEOUT', '30'))
E2B_COMMAND_DEADLINE_BUFFER = float(os.environ.get('E2B_COMMAND_DEADLINE_BUFFER', '30'))
E2B_STATUS_POLL_TIMEOUT = float(os.environ.get('E2B_STATUS_POLL_TIMEOUT', '30'))
E2B_STATUS_POLL_ATTEMPTS = int(os.environ.get('E2B_STATUS_POLL_ATTEMPTS', '3'))
E2B_STATUS_POLL_BACKOFFS = (5.0, 15.0)
E2B_CREATE_TIMEOUT = int(os.environ.get('E2B_CREATE_TIMEOUT', '300'))
E2B_CREATE_MAX_RETRIES = int(os.environ.get('E2B_CREATE_MAX_RETRIES', '3'))
E2B_CREATE_RETRY_BACKOFF = float(os.environ.get('E2B_CREATE_RETRY_BACKOFF', '5'))
E2B_CREATE_RETRY_MAX_BACKOFF = float(os.environ.get('E2B_CREATE_RETRY_MAX_BACKOFF', '30'))
_E2B_CREATE_NO_RETRY_MARKERS = ('invalid api key', 'invalid_api_key', 'unauthorized', 'forbidden', 'permission denied', 'authentication failed', 'template not found', 'not found template', 'no such template', 'quota', 'payment required', '402', '401', '403')

class SandboxCreateError(RuntimeError):
    """Raised when sandbox creation fails after all retries. The message is kept"""
E2B_IMAGE = os.environ.get('E2B_IMAGE', '')
E2B_TEMPLATE = os.environ.get('E2B_TEMPLATE', 'openclaw')
E2B_API_KEY = os.environ.get('E2B_API_KEY', '')
E2B_SANDBOX_TIMEOUT = int(os.environ.get('E2B_SANDBOX_TIMEOUT', '3600'))
E2B_SANDBOX_TIMEOUT_BUFFER = int(os.environ.get('E2B_SANDBOX_TIMEOUT_BUFFER', '3600'))
E2B_USER = os.environ.get('E2B_USER', 'root')
E2B_HOME = '/root' if E2B_USER == 'root' else f'/home/{E2B_USER}'
OPENCLAW_CONTEXT_WINDOW = 131072
OPENCLAW_COMPACTION_RESERVE_TOKENS = 40000
OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS = 16000
OPENCLAW_COMPACTION_MAX_HISTORY_SHARE = 0.5
E2B_CPU = os.environ.get('E2B_CPU', '1')
E2B_MEMORY = os.environ.get('E2B_MEMORY', '4Gi')
NODE_OPTIONS = os.environ.get('NODE_OPTIONS', '--max-old-space-size=2048')
_TMP_WORKSPACE_DEFAULT = os.environ.get('TMP_WORKSPACE', '/tmp_workspace')
TMP_WORKSPACE = f'{E2B_HOME}/tmp_workspace' if E2B_USER != 'root' else _TMP_WORKSPACE_DEFAULT
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
SEARCH_PROVIDER = os.environ.get('SEARCH_PROVIDER', 'serper').strip().lower()

def _escape_shell_value(v: str) -> str:
    """Escape a value for safe embedding inside single-quoted shell strings."""
    return v.replace("'", "'\\''")

def run_command(sbx, command: str, timeout: int | float=DEFAULT_COMMAND_TIMEOUT, *, host_timeout: int | float | None=None) -> tuple[str, str, int]:
    """Run *command* inside the sandbox with env vars sourced."""
    full_cmd = f'. {E2B_HOME}/.e2b_env 2>/dev/null; {command}'
    local_deadline = float(host_timeout) if host_timeout is not None else float(timeout) + E2B_COMMAND_DEADLINE_BUFFER
    result = _run_with_deadline('commands.run', local_deadline, sbx.commands.run, full_cmd, user=E2B_USER, timeout=timeout)
    return (result.stdout or '', result.stderr or '', result.exit_code)

def _run_with_deadline(description: str, deadline: int | float, fn, *args, **kwargs):
    """Run an SDK call with a local deadline."""
    result_q: queue.Queue = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            result_q.put((True, fn(*args, **kwargs)))
        except Exception as exc:
            result_q.put((False, exc))
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(deadline)
    if thread.is_alive():
        raise TimeoutError(f'{description} timed out after {deadline}s')
    (ok, value) = result_q.get_nowait()
    if ok:
        return value
    raise value

def write_file(sbx, remote_path: str, content, *, timeout: int | float=E2B_FILE_OPERATION_TIMEOUT) -> None:
    """Write through the E2B files API with a host-side deadline."""
    _run_with_deadline(f'files.write {remote_path}', timeout, sbx.files.write, remote_path, content, user=E2B_USER)

def read_file(sbx, remote_path: str, *, timeout: int | float=E2B_FILE_OPERATION_TIMEOUT):
    """Read through the E2B files API with a host-side deadline."""
    return _run_with_deadline(f'files.read {remote_path}', timeout, sbx.files.read, remote_path, user=E2B_USER)

def _write_text_with_command(sbx, remote_path: str, content: str, timeout: int=E2B_FILE_OPERATION_TIMEOUT) -> None:
    """Write text from inside the sandbox, avoiding files API permission issues under /root."""
    payload = base64.b64encode(content.encode('utf-8')).decode('ascii')
    parent = os.path.dirname(remote_path)
    mkdir = f'mkdir -p {shlex.quote(parent)} && ' if parent else ''
    command = f'{mkdir}printf %s {shlex.quote(payload)} | base64 -d > {shlex.quote(remote_path)}'
    (stdout, stderr, rc) = run_command(sbx, command, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f'Failed to write {remote_path}:\n{stdout}\n{stderr}')
_PIP_CONF = '[global]\nindex-url = https://mirrors.aliyun.com/pypi/simple/\ntrusted-host = mirrors.aliyun.com\n'
_NPMRC = 'registry=https://registry.npmmirror.com\n'

def _setup_cn_mirrors(sbx, task_id: str) -> None:
    """Configure pip and npm to use Chinese mirrors for faster installs."""
    _write_text_with_command(sbx, f'{E2B_HOME}/.pip/pip.conf', _PIP_CONF)
    _write_text_with_command(sbx, f'{E2B_HOME}/.npmrc', _NPMRC)
    logger.info('[%s] CN mirrors configured (pip: aliyun, npm: npmmirror)', task_id)
LARGE_FILE_THRESHOLD = 200 * 1024 * 1024
CHUNK_SIZE = 100 * 1024 * 1024

def _get_remote_file_size(sbx, remote_path: str) -> int:
    """Return the size of a remote file in bytes, or -1 on failure."""
    (stdout, _, rc) = run_command(sbx, f"stat -c %s '{remote_path}' 2>/dev/null", timeout=E2B_FILE_OPERATION_TIMEOUT)
    if rc != 0:
        return -1
    try:
        return int(stdout.strip())
    except ValueError:
        return -1

def _upload_file_chunked(sbx, local_path: str, remote_path: str) -> bool:
    """Upload a large local file by splitting it into chunks, then"""
    import hashlib
    import io
    tag = hashlib.md5(local_path.encode()).hexdigest()[:8]
    chunk_dir = f'/tmp/_ul_chunks_{tag}'
    try:
        run_command(sbx, f"rm -rf '{chunk_dir}' && mkdir -p '{chunk_dir}'")
        chunk_index = 0
        with open(local_path, 'rb') as f:
            while True:
                data = f.read(CHUNK_SIZE)
                if not data:
                    break
                chunk_remote = f'{chunk_dir}/chunk_{chunk_index:04d}'
                write_file(sbx, chunk_remote, io.BytesIO(data))
                chunk_index += 1
                logger.debug('Uploaded chunk %d of %s (%d MB)', chunk_index, local_path, len(data) // (1024 * 1024))
        remote_parent = os.path.dirname(remote_path)
        if remote_parent:
            run_command(sbx, f"mkdir -p '{remote_parent}'")
        (_, stderr, rc) = run_command(sbx, f"cat '{chunk_dir}'/chunk_* > '{remote_path}'", timeout=3600)
        if rc != 0:
            logger.warning('cat chunks → %s failed: %s', remote_path, stderr)
            return False
        run_command(sbx, f"rm -rf '{chunk_dir}'")
        logger.info('Chunked upload complete: %s → %s (%d chunks)', local_path, remote_path, chunk_index)
        return True
    except Exception as exc:
        logger.warning('Chunked upload %s failed: %s', local_path, exc)
        run_command(sbx, f"rm -rf '{chunk_dir}'")
        return False

def _download_file_chunked(sbx, remote_path: str, local_path: str) -> bool:
    """Download a large remote file by splitting it into chunks on the"""
    import hashlib
    tag = hashlib.md5(remote_path.encode()).hexdigest()[:8]
    chunk_dir = f'/tmp/_dl_chunks_{tag}'
    try:
        run_command(sbx, f"rm -rf '{chunk_dir}' && mkdir -p '{chunk_dir}'")
        (_, stderr, rc) = run_command(sbx, f"split -b {CHUNK_SIZE} '{remote_path}' '{chunk_dir}/chunk_'", timeout=3600)
        if rc != 0:
            logger.warning('split %s failed: %s', remote_path, stderr)
            return False
        (stdout, _, rc) = run_command(sbx, f"ls -1 '{chunk_dir}/' | sort")
        if rc != 0 or not stdout.strip():
            return False
        chunks = [c.strip() for c in stdout.strip().splitlines() if c.strip()]
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, 'wb') as out_f:
            for (i, chunk_name) in enumerate(chunks):
                content = read_file(sbx, f'{chunk_dir}/{chunk_name}')
                if isinstance(content, bytes):
                    out_f.write(content)
                else:
                    out_f.write(content.encode('utf-8'))
                logger.debug('Downloaded chunk %d/%d of %s', i + 1, len(chunks), remote_path)
        run_command(sbx, f"rm -rf '{chunk_dir}'")
        logger.info('Chunked download complete: %s → %s (%d chunks)', remote_path, local_path, len(chunks))
        return True
    except Exception as exc:
        logger.warning('Chunked download %s failed: %s', remote_path, exc)
        run_command(sbx, f"rm -rf '{chunk_dir}'")
        return False

def upload_directory(sbx, local_dir: str, remote_dir: str) -> None:
    """Recursively upload a local directory into the sandbox."""
    local_dir = str(local_dir)
    for (root, _dirs, files) in os.walk(local_dir):
        for fname in files:
            local_path = os.path.join(root, fname)
            rel = os.path.relpath(local_path, local_dir)
            remote_path = f'{remote_dir}/{rel}'
            try:
                file_size = os.path.getsize(local_path)
                if file_size >= LARGE_FILE_THRESHOLD:
                    logger.info('Large file %s (%d MB), using chunked upload', local_path, file_size // (1024 * 1024))
                    if not _upload_file_chunked(sbx, local_path, remote_path):
                        logger.warning('Chunked upload failed: %s → %s', local_path, remote_path)
                else:
                    with open(local_path, 'rb') as fh:
                        write_file(sbx, remote_path, fh)
            except Exception as exc:
                logger.warning('upload %s → %s failed: %s', local_path, remote_path, exc)

def download_file(sbx, remote_path: str, local_path: str) -> bool:
    """Download a single file from the sandbox to the local filesystem."""
    try:
        file_size = _get_remote_file_size(sbx, remote_path)
        if file_size >= LARGE_FILE_THRESHOLD:
            logger.info('Large file %s (%d MB), using chunked download', remote_path, file_size // (1024 * 1024))
            return _download_file_chunked(sbx, remote_path, local_path)
        content = read_file(sbx, remote_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            with open(local_path, 'wb') as f:
                f.write(content)
        else:
            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(content)
        return True
    except Exception as exc:
        logger.warning('download_file %s failed: %s', remote_path, exc)
        return False

def download_directory(sbx, remote_dir: str, local_dir: str) -> bool:
    """Download an entire remote directory by listing then fetching each file."""
    try:
        (stdout, _, rc) = run_command(sbx, f'find {remote_dir} -type f 2>/dev/null', timeout=E2B_FILE_OPERATION_TIMEOUT)
        if rc != 0 or not stdout.strip():
            return False
        for rp in stdout.strip().splitlines():
            rp = rp.strip()
            if not rp:
                continue
            rel = os.path.relpath(rp, remote_dir)
            download_file(sbx, rp, os.path.join(local_dir, rel))
        return True
    except Exception as exc:
        logger.warning('download_directory %s failed: %s', remote_dir, exc)
        return False
E2B_OWNER_TAG = os.environ.get('E2B_OWNER_TAG', 'anonymous-eval')
E2B_BATCH_ID = os.environ.get('E2B_BATCH_ID', '')
E2B_REAP_GRACE_SECONDS = float(os.environ.get('E2B_REAP_GRACE_SECONDS', '1800'))
E2B_REAP_MAX_WORKERS = int(os.environ.get('E2B_REAP_MAX_WORKERS', '8'))
E2B_LIST_TIMEOUT = float(os.environ.get('E2B_LIST_TIMEOUT', '60'))

def sandbox_tracking_metadata(sandbox_timeout: int | float, tags: dict[str, str] | None=None) -> dict[str, str]:
    """Build the ``ace_*`` identity tags stored on every sandbox."""
    meta = {'ace_owner': E2B_OWNER_TAG, 'ace_expires_at': str(int(time.time() + float(sandbox_timeout)))}
    if E2B_BATCH_ID:
        meta['ace_batch_id'] = E2B_BATCH_ID
    for (key, value) in (tags or {}).items():
        if value:
            meta[key] = str(value)
    return meta

def _list_sandboxes(where: dict[str, str] | None) -> list:
    """Return every sandbox matching *where*, across all pages and states."""
    from e2b import Sandbox
    from e2b.sandbox.sandbox_api import SandboxQuery
    try:
        from e2b.api.client.models import SandboxState
        states = list(SandboxState)
    except Exception:
        states = None

    def _collect() -> list:
        paginator = Sandbox.list(query=SandboxQuery(metadata=where, state=states), api_key=E2B_API_KEY)
        out = []
        pages = 0
        while paginator.has_next:
            out.extend(paginator.next_items())
            pages += 1
            if pages > 200:
                logger.warning('sandbox 分页超过 200 页,提前停止')
                break
        return out
    return _run_with_deadline('list sandboxes', E2B_LIST_TIMEOUT, _collect)

def kill_sandboxes_where(where: dict[str, str], *, reason: str, age_filter: bool=False) -> int:
    """Kill every sandbox matching the metadata *where*, returning the count."""
    from concurrent.futures import ThreadPoolExecutor
    from e2b import Sandbox
    try:
        candidates = _list_sandboxes(where)
    except Exception as exc:
        logger.warning('列举沙箱失败,跳过本次清理(%s): %s', reason, exc)
        return 0
    now = time.time()
    victims = []
    for info in candidates:
        if age_filter:
            raw = (info.metadata or {}).get('ace_expires_at')
            try:
                expires_at = float(raw)
            except (TypeError, ValueError):
                logger.debug('跳过无 ace_expires_at 的沙箱 %s', info.sandbox_id)
                continue
            if now - expires_at < E2B_REAP_GRACE_SECONDS:
                continue
        victims.append(info.sandbox_id)
    if not victims:
        return 0
    logger.warning('清理 %d 个孤儿沙箱(%s)', len(victims), reason)

    def _kill(sandbox_id: str) -> bool:
        try:
            _run_with_deadline(f'reap {sandbox_id}', E2B_KILL_TIMEOUT, Sandbox.kill, sandbox_id, api_key=E2B_API_KEY)
            return True
        except Exception as exc:
            logger.warning('清理沙箱 %s 失败: %s', sandbox_id, exc)
            return False
    workers = min(E2B_REAP_MAX_WORKERS, len(victims))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        killed = sum((1 for ok in pool.map(_kill, victims) if ok))
    logger.warning('孤儿清理完成: %d/%d 已杀(%s)', killed, len(victims), reason)
    return killed

def reap_current_batch() -> int:
    """Kill leftovers from *this* batch. Call on normal exit."""
    if not E2B_BATCH_ID:
        return 0
    return kill_sandboxes_where({'ace_owner': E2B_OWNER_TAG, 'ace_batch_id': E2B_BATCH_ID}, reason=f'batch {E2B_BATCH_ID} 结束')

def reap_stale_batches() -> int:
    """Kill long-expired sandboxes from earlier runs. Call on startup."""
    return kill_sandboxes_where({'ace_owner': E2B_OWNER_TAG}, reason='上次运行的残留(已过期)', age_filter=True)

def create_sandbox(task_id: str, workspace_path: str, extra_env: str='', tmp_path: str='', lifecycle_timeout: int | float | None=None, tags: dict[str, str] | None=None):
    """Create an E2B sandbox, set up env vars, and upload workspace files."""
    from e2b import Sandbox
    env_vars: dict[str, str] = {}
    proxy_http = os.environ.get('HTTP_PROXY_INNER', '')
    proxy_https = os.environ.get('HTTPS_PROXY_INNER', '')
    pip_index_url = os.environ.get('PIP_INDEX_URL_INNER', '')
    playwright_download_host = os.environ.get('PLAYWRIGHT_DOWNLOAD_HOST_INNER', '')
    env_vars['http_proxy'] = proxy_http
    env_vars['https_proxy'] = proxy_https
    env_vars['HTTP_PROXY'] = proxy_http
    env_vars['HTTPS_PROXY'] = proxy_https
    env_vars['PIP_INDEX_URL'] = pip_index_url
    env_vars['PLAYWRIGHT_DOWNLOAD_HOST'] = playwright_download_host
    env_vars['SERPER_API_KEY'] = SERPER_API_KEY
    env_vars['SEARCH_PROVIDER'] = SEARCH_PROVIDER
    env_vars['PRIVACY_JUDGE'] = os.environ.get('PRIVACY_JUDGE', '')
    env_vars['EDGE_CLOUD_MODE'] = os.environ.get('EDGE_CLOUD_MODE', '')
    env_vars['NODE_OPTIONS'] = NODE_OPTIONS
    for judge_key in ('JUDGE_BASE_URL', 'JUDGE_API_KEY', 'JUDGE_MODEL', 'JUDGE_REASONING_EFFORT'):
        if os.environ.get(judge_key):
            env_vars[judge_key] = os.environ[judge_key]
    env_vars['no_proxy'] = '' if not proxy_http else os.environ.get('NO_PROXY_INNER', '')
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith('#'):
            continue
        value = os.environ.get(key, '')
        env_vars[key] = value
        masked = value[:4] + '***' if value else '(empty)'
        logger.info('[%s] Injecting env var: %s=%s', task_id, key, masked)
    sandbox_timeout = max(E2B_SANDBOX_TIMEOUT, int(lifecycle_timeout or 0))
    logger.info('[%s] Creating E2B sandbox (template=%s, image=%s, timeout=%ss) ...', task_id, E2B_TEMPLATE, E2B_IMAGE, sandbox_timeout)
    t0 = time.time()

    def _create_once():
        return Sandbox.create(timeout=sandbox_timeout, api_key=E2B_API_KEY, template=E2B_TEMPLATE, proxy=os.environ.get('http_proxy'), metadata={**({'x-custom-config': json.dumps({'image': E2B_IMAGE, 'imageRegistryType': 'enterprise', 'command': ['/bin/sh', '-c'], 'args': ['/mnt/envd', '-port', '49983'], 'resources': {'cpu': E2B_CPU, 'memory': E2B_MEMORY}})} if E2B_IMAGE else {}), **sandbox_tracking_metadata(sandbox_timeout, tags)})
    sbx = None
    last_exc = None
    for attempt in range(E2B_CREATE_MAX_RETRIES):
        try:
            sbx = _run_with_deadline('Sandbox.create', E2B_CREATE_TIMEOUT, _create_once)
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            permanent = any((m in msg for m in _E2B_CREATE_NO_RETRY_MARKERS))
            is_last = attempt >= E2B_CREATE_MAX_RETRIES - 1
            if sbx is not None:
                try:
                    kill_sandbox(sbx, task_id)
                except Exception:
                    pass
                sbx = None
            if permanent or is_last:
                raise SandboxCreateError(f'[{task_id}] sandbox creation failed after {attempt + 1} attempt(s) (last error type: {type(exc).__name__})') from exc
            delay = min(E2B_CREATE_RETRY_MAX_BACKOFF, E2B_CREATE_RETRY_BACKOFF * 2 ** attempt) * random.uniform(0.5, 1.5)
            logger.warning('[%s] Sandbox.create attempt %d/%d failed: %s — retrying in %.1fs', task_id, attempt + 1, E2B_CREATE_MAX_RETRIES, exc, delay)
            time.sleep(delay)
    if sbx is None:
        raise SandboxCreateError(f'[{task_id}] Sandbox.create returned no sandbox (last error type: {type(last_exc).__name__})')
    logger.info('[%s] Sandbox ready id=%s (%.1fs, attempt≤%d)', task_id, sbx.sandbox_id, time.time() - t0, E2B_CREATE_MAX_RETRIES)
    try:
        app_dir = f'{E2B_HOME}/app' if E2B_USER != 'root' else '/app'
        env_lines = [f"export {k}='{_escape_shell_value(v)}'" for (k, v) in env_vars.items()]
        _write_text_with_command(sbx, f'{E2B_HOME}/.e2b_env', '\n'.join(env_lines) + '\n')
        _setup_cn_mirrors(sbx, task_id)
        upload_directory(sbx, workspace_path, app_dir)
        logger.info('[%s] Workspace uploaded %s → %s', task_id, workspace_path, app_dir)
        if tmp_path and os.path.exists(tmp_path):
            run_command(sbx, f'mkdir -p {TMP_WORKSPACE}/tmp')
            upload_directory(sbx, tmp_path, f'{TMP_WORKSPACE}/tmp')
            logger.info('[%s] Temp files uploaded → %s/tmp', task_id, TMP_WORKSPACE)
    except Exception:
        kill_sandbox(sbx, task_id)
        raise
    return sbx

def kill_sandbox(sbx, task_id: str) -> None:
    """Kill the E2B sandbox (mirrors ``docker_utils.remove_container``)."""
    try:
        _run_with_deadline(f'kill sandbox {task_id}', E2B_KILL_TIMEOUT, sbx.kill)
        logger.info('[%s] Sandbox killed', task_id)
    except Exception as exc:
        logger.warning('[%s] kill_sandbox error: %s', task_id, exc)

def setup_workspace(sbx, task_id: str, thinking: str | None=None, agent_timeout_seconds: int | None=None) -> None:
    """Copy app dir → TMP_WORKSPACE inside the sandbox."""
    app_dir = f'{E2B_HOME}/app' if E2B_USER != 'root' else '/app'
    logger.info('[%s] Copying %s → %s', task_id, app_dir, TMP_WORKSPACE)
    (stdout, stderr, rc) = run_command(sbx, f'cp -r {app_dir}/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}')
    if rc != 0:
        raise RuntimeError(f'Workspace copy failed:\n{stdout}\n{stderr}')
    if thinking is not None:
        logger.info('[%s] Setting thinkingDefault to %s', task_id, thinking)
        (stdout, stderr, rc) = run_command(sbx, f'openclaw config set agents.defaults.thinkingDefault {thinking}')
        if rc != 0:
            raise RuntimeError(f'Set thinkingDefault failed:\n{stdout}\n{stderr}')
    run_command(sbx, f'rm -rf {E2B_HOME}/.openclaw/workspace && ln -s {TMP_WORKSPACE} {E2B_HOME}/.openclaw/workspace')
    _patch_openclaw_streaming_usage(sbx, task_id)
    _patch_openclaw_overflow_recovery(sbx, task_id)
    if agent_timeout_seconds:
        configure_openclaw_agent_timeout(sbx, task_id, agent_timeout_seconds)
    _install_search_plugin(sbx, task_id)
SERPER_PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'skills' / 'serper-search'

def _patch_openclaw_streaming_usage(sbx, task_id: str) -> None:
    """Patch OpenClaw's openai-completions adapter to request streaming usage."""
    patch_cmd = "python3 - <<'PY'\nimport pathlib, sys, re\n\nsearch_roots = [\n    pathlib.Path('/usr/local/lib/node_modules'),\n    pathlib.Path('/usr/lib/node_modules'),\n    pathlib.Path('/opt/node_modules'),\n    pathlib.Path('/root/.local/share/openclaw'),\n    pathlib.Path('/app'),\n]\n\ntarget = None\nfor root in search_roots:\n    if not root.exists():\n        continue\n    hits = list(root.rglob('providers/openai-completions.js'))\n    if hits:\n        target = hits[0]\n        break\n\nif target is None:\n    print('[patch-usage] openai-completions.js not found, skipping', file=sys.stderr)\n    sys.exit(0)\n\ntext = target.read_text(encoding='utf-8')\nnew_text = re.sub(\n    r'compat\\.supportsUsageInStreaming\\s*!==\\s*false',\n    'true /* patched: always enable streaming usage */',\n    text,\n)\n\nif new_text == text:\n    print(f'[patch-usage] already patched or pattern not found in {target}', file=sys.stderr)\nelse:\n    target.write_text(new_text, encoding='utf-8')\n    print(f'[patch-usage] patched {target}', file=sys.stderr)\nPY"
    (stdout, stderr, rc) = run_command(sbx, patch_cmd)
    if rc != 0:
        logger.warning('[%s] streaming-usage patch failed: %s', task_id, stderr)
    else:
        logger.info('[%s] streaming-usage patch: %s', task_id, stderr.strip() or stdout.strip())

def _patch_openclaw_overflow_recovery(sbx, task_id: str) -> None:
    """Install the shared OpenAI-provider overflow recovery patch."""
    patch_path = Path(__file__).resolve().parents[1] / 'scripts' / 'patch_openclaw_overflow_recovery.py'
    script = patch_path.read_text(encoding='utf-8')
    write_file(sbx, '/tmp/_patch_openclaw_overflow_recovery.py', script)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_patch_openclaw_overflow_recovery.py')
    if rc != 0:
        raise RuntimeError(f'[{task_id}] overflow recovery patch failed (rc={rc}):\n{stdout}\n{stderr}')
    logger.info('[%s] overflow recovery patch: %s', task_id, (stderr or stdout).strip()[:500])

def install_openclaw_plugin(sbx, task_id: str, sandbox_plugin_path: str) -> None:
    """Install an OpenClaw plugin from a directory already inside the sandbox."""
    (stdout, stderr, rc) = run_command(sbx, f"openclaw plugins install '{sandbox_plugin_path}'")
    if rc != 0:
        combined_output = f'{stdout}\n{stderr}'
        if 'Installed plugin:' in combined_output:
            logger.warning('[%s] OpenClaw plugin install returned rc=%s after installing: %s', task_id, rc, stderr.strip() or stdout.strip())
            return
        raise RuntimeError(f'Plugin install failed ({sandbox_plugin_path}):\n{stdout}\n{stderr}')
    logger.info('[%s] OpenClaw plugin installed: %s', task_id, sandbox_plugin_path)

def _fallback_install_search_plugin(sbx, task_id: str, plugin_id: str, plugin_dir: Path) -> None:
    """Fallback for runtimes where `openclaw plugins install` rejects local plugin code."""
    extension_path = f'{E2B_HOME}/.openclaw/extensions/{plugin_id}'
    (stdout, stderr, rc) = run_command(sbx, f'rm -rf {extension_path} && mkdir -p {extension_path}')
    if rc != 0:
        raise RuntimeError(f'{plugin_id} fallback setup failed:\n{stdout}\n{stderr}')
    upload_directory(sbx, str(plugin_dir), extension_path)
    (stdout, stderr, rc) = run_command(sbx, f'cd {extension_path} && npm install --omit=dev')
    if rc != 0:
        raise RuntimeError(f'{plugin_id} fallback npm install failed:\n{stdout}\n{stderr}')
    logger.warning('[%s] Fallback-installed %s plugin', task_id, plugin_id)

def _install_search_plugin(sbx, task_id: str) -> None:
    if SEARCH_PROVIDER in ('', 'none', 'disabled'):
        return
    if SEARCH_PROVIDER != 'serper':
        raise ValueError('SEARCH_PROVIDER must be serper or none')
    _install_openclaw_search_plugin(sbx, task_id, 'serper-search', SERPER_PLUGIN_DIR, 'SERPER_API_KEY')

def _install_openclaw_search_plugin(sbx, task_id: str, plugin_id: str, plugin_dir: Path, api_key_name: str) -> None:
    """Upload a search plugin into the sandbox and install it."""
    if not plugin_dir.is_dir():
        raise RuntimeError(f'{plugin_id} plugin not found: {plugin_dir}')
    sandbox_plugin_path = f'{E2B_HOME}/{plugin_id}-plugin'
    commands = [f'rm -rf {E2B_HOME}/skills/web-search {E2B_HOME}/.openclaw/extensions/serp-search {E2B_HOME}/.openclaw/extensions/serper-search {sandbox_plugin_path}', f'mkdir -p {sandbox_plugin_path}']
    for command in commands:
        (stdout, stderr, rc) = run_command(sbx, command)
        if rc != 0:
            raise RuntimeError(f'{plugin_id} setup failed:\n{stdout}\n{stderr}')
    upload_directory(sbx, str(plugin_dir), sandbox_plugin_path)
    sanitize_py = f"import json, pathlib\ncp = pathlib.Path('{E2B_HOME}/.openclaw/openclaw.json')\nc = json.loads(cp.read_text()) if cp.exists() else {{}}\nc.setdefault('tools', {{}}).setdefault('web', {{}})['search'] = {{'enabled': False}}\ncp.write_text(json.dumps(c, indent=2))\n"
    write_file(sbx, '/tmp/_configure_search_plugin.py', sanitize_py)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_configure_search_plugin.py')
    if rc != 0:
        raise RuntimeError(f'{plugin_id} config update failed:\n{stdout}\n{stderr}')
    try:
        install_openclaw_plugin(sbx, task_id, sandbox_plugin_path)
    except RuntimeError as exc:
        logger.warning('[%s] OpenClaw plugin install failed, using fallback: %s', task_id, exc)
        _fallback_install_search_plugin(sbx, task_id, plugin_id, plugin_dir)
    logger.info('[%s] Replaced built-in web_search with %s plugin (%s)', task_id, plugin_id, api_key_name)

def setup_skills(sbx, task_id: str, skills: str, skills_path: str) -> None:
    """Upload and register skill directories in the sandbox."""
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        run_command(sbx, f'mkdir -p {E2B_HOME}/skills/{line}')
        local_skill = os.path.join(skills_path, line)
        if os.path.isdir(local_skill):
            upload_directory(sbx, local_skill, f'{E2B_HOME}/skills/{line}')

def inject_openclaw_models(sbx, task_id: str, models_config: dict) -> None:
    """Inject custom models (with a uniform contextWindow) into openclaw.json."""
    write_file(sbx, '/tmp/openclaw_models.json', json.dumps(models_config, indent=2))
    inject_py = f"import json, pathlib\ncp = pathlib.Path('{E2B_HOME}/.openclaw/openclaw.json')\nmp = pathlib.Path('/tmp/openclaw_models.json')\nc = json.loads(cp.read_text()) if cp.exists() else {{}}\nmodels = json.loads(mp.read_text())\nfor provider in models.get('providers', {{}}).values():\n    if not isinstance(provider, dict):\n        continue\n    for model in provider.get('models', []):\n        if isinstance(model, dict):\n            model['contextWindow'] = {OPENCLAW_CONTEXT_WINDOW}\nc['models'] = models\ncp.write_text(json.dumps(c, indent=2))\n"
    write_file(sbx, '/tmp/_inject_models.py', inject_py)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_inject_models.py')
    if rc != 0:
        raise RuntimeError(f'inject_openclaw_models failed:\n{stdout}\n{stderr}')
    logger.info('[%s] Injected custom models config', task_id)

def configure_openclaw_compaction(sbx, task_id: str) -> None:
    """Make OpenClaw compact history *before* a model call would overflow."""
    configure_py = f"import json, pathlib\ncp = pathlib.Path('{E2B_HOME}/.openclaw/openclaw.json')\nc = json.loads(cp.read_text()) if cp.exists() else {{}}\ndefaults = c.setdefault('agents', {{}}).setdefault('defaults', {{}})\ncompaction = defaults.setdefault('compaction', {{}})\ncompaction['mode'] = 'safeguard'\ncompaction['reserveTokens'] = {OPENCLAW_COMPACTION_RESERVE_TOKENS}\ncompaction['reserveTokensFloor'] = {OPENCLAW_COMPACTION_RESERVE_TOKENS}\ncompaction['keepRecentTokens'] = {OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS}\ncompaction['maxHistoryShare'] = {OPENCLAW_COMPACTION_MAX_HISTORY_SHARE}\ncp.parent.mkdir(parents=True, exist_ok=True)\ncp.write_text(json.dumps(c, indent=2))\n"
    write_file(sbx, '/tmp/_configure_compaction.py', configure_py)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_configure_compaction.py')
    if rc != 0:
        logger.warning('[%s] compaction safeguard NOT applied: %s', task_id, (stderr or stdout).strip())
        return
    logger.info('[%s] Compaction safeguard configured (reserve=%d keep_recent=%d share=%s)', task_id, OPENCLAW_COMPACTION_RESERVE_TOKENS, OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS, OPENCLAW_COMPACTION_MAX_HISTORY_SHARE)

def configure_openclaw_agent_timeout(sbx, task_id: str, timeout_seconds: int) -> None:
    """Configure OpenClaw's per-request agent timeout inside the sandbox."""
    (stdout, stderr, rc) = run_command(sbx, f'openclaw config set agents.defaults.timeoutSeconds {int(timeout_seconds)}')
    if rc != 0:
        raise RuntimeError(f'Agent timeout setup failed:\n{stdout}\n{stderr}')
    logger.info('[%s] agent timeoutSeconds set: %ss', task_id, timeout_seconds)

def run_warmup(sbx, task_id: str, warmup: str) -> None:
    """Execute warmup bash commands in the sandbox."""
    if not warmup.strip():
        return
    commands = [l.strip() for l in warmup.splitlines() if l.strip() and (not l.strip().startswith('#'))]
    if not commands:
        return
    has_background = any((cmd.endswith('&') for cmd in commands))
    if not has_background:
        logger.info('[%s] Running warmup (%d commands)', task_id, len(commands))
        for cmd in commands:
            logger.info('[%s] warmup: %s', task_id, cmd)
            (stdout, stderr, rc) = run_command(sbx, cmd, timeout=WARMUP_COMMAND_TIMEOUT)
            if rc != 0:
                raise RuntimeError(f'Warmup failed: {cmd!r}\n{stdout}\n{stderr}')
        return
    bg_count = sum((1 for c in commands if c.endswith('&')))
    logger.info('[%s] Running warmup script (%d commands, %d background)', task_id, len(commands), bg_count)
    script_lines = ['#!/usr/bin/env bash', 'set -e']
    for cmd in commands:
        if cmd.endswith('&'):
            bg_part = cmd[:-1].rstrip()
            script_lines.append(f'{{ {bg_part} ; }} </dev/null >>/tmp/_warmup_bg.log 2>&1 &')
        else:
            script_lines.append(cmd)
    script = '\n'.join(script_lines) + '\n'
    write_file(sbx, '/tmp/_warmup.sh', script)
    (stdout, stderr, rc) = run_command(sbx, 'bash /tmp/_warmup.sh', timeout=WARMUP_SCRIPT_TIMEOUT)
    if rc != 0:
        raise RuntimeError(f'Warmup script failed (rc={rc}):\nstdout: {stdout}\nstderr: {stderr}')

def set_model(sbx, task_id: str, model: str) -> None:
    (stdout, stderr, rc) = run_command(sbx, f"openclaw models set '{model}'")
    if rc != 0:
        raise RuntimeError(f'Model setup failed:\n{stdout}\n{stderr}')
    logger.info('[%s] Model set: %s', task_id, model)

def configure_openclaw_image_model(sbx, task_id: str, image_model: str) -> None:
    """Configure OpenClaw's image model inside the sandbox."""
    (stdout, stderr, rc) = run_command(sbx, f"openclaw config set agents.defaults.imageModel.primary '{image_model}'")
    if rc != 0:
        raise RuntimeError(f'Image model setup failed:\n{stdout}\n{stderr}')
    (stdout, stderr, rc) = run_command(sbx, f"""openclaw config set agents.defaults.imageModel.fallbacks '["{image_model}"]'""")
    if rc != 0:
        raise RuntimeError(f'Image model fallback setup failed:\n{stdout}\n{stderr}')
    logger.info('[%s] imageModel set: %s', task_id, image_model)

def setup_plugin_source(sbx, task_id: str, host_src_dir: str, sandbox_dst: str, timeout: int=120) -> None:
    """Upload a plugin source directory into the sandbox at an arbitrary path."""
    parent = os.path.dirname(sandbox_dst.rstrip('/')) or '/'
    (stdout, stderr, rc) = run_command(sbx, f"mkdir -p '{parent}' && rm -rf '{sandbox_dst}'", timeout=timeout)
    if rc != 0:
        raise RuntimeError(f'setup_plugin_source mkdir/rm failed:\n{stdout}\n{stderr}')
    upload_directory(sbx, host_src_dir, sandbox_dst)
    logger.info('[%s] Plugin source uploaded: %s -> %s', task_id, host_src_dir, sandbox_dst)

def patch_openclaw_config(sbx, task_id: str, tools_also_allow: list[str] | None=None, model_compat: dict | None=None) -> None:
    """Patch ~/.openclaw/openclaw.json like docker_utils.patch_openclaw_config."""
    patches = []
    if tools_also_allow:
        patches.append(f"tools = c.setdefault('tools', {{}})\nkey = 'allow' if 'allow' in tools else 'alsoAllow'\nallow = tools.setdefault(key, [])\nfor t in {tools_also_allow!r}:\n    if '*' not in allow and t not in allow: allow.append(t)\nif key == 'allow': tools.pop('alsoAllow', None)")
    if model_compat:
        patches.append(f"for prov in c.get('models', {{}}).get('providers', {{}}).values():\n    for m in prov.get('models', []):\n        m.setdefault('compat', {{}}).update({model_compat!r})")
    if not patches:
        return
    script = f"import json, pathlib\np = pathlib.Path('{E2B_HOME}/.openclaw/openclaw.json')\nc = json.loads(p.read_text()) if p.exists() else {{}}\n" + '\n'.join(patches) + '\np.write_text(json.dumps(c, indent=2))\n'
    write_file(sbx, '/tmp/_patch_openclaw_config.py', script)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_patch_openclaw_config.py')
    if rc != 0:
        raise RuntimeError(f'patch_openclaw_config failed:\n{stdout}\n{stderr}')
    logger.info('[%s] OpenClaw config patched', task_id)

class RemoteProcess:
    """Small Popen-like handle for a command running inside an E2B sandbox."""

    def __init__(self, sbx, task_id: str, command: str, local_log_path: Path, remote_log_path: str, remote_status_path: str, remote_pid_path: str):
        self.sbx = sbx
        self.task_id = task_id
        self.command = command
        self.local_log_path = local_log_path
        self.remote_log_path = remote_log_path
        self.remote_status_path = remote_status_path
        self.remote_pid_path = remote_pid_path
        self.returncode = None

    def _download_log(self) -> None:
        download_file(self.sbx, self.remote_log_path, str(self.local_log_path))

    def poll(self, *, host_timeout: int | float=E2B_STATUS_POLL_TIMEOUT):
        if self.returncode is not None:
            return self.returncode
        (stdout, _, rc) = run_command(self.sbx, f"cat '{self.remote_status_path}' 2>/dev/null || true", timeout=30, host_timeout=host_timeout)
        value = stdout.strip().splitlines()[-1] if stdout.strip() else ''
        if rc == 0 and value:
            try:
                self.returncode = int(value)
            except ValueError:
                self.returncode = 1
            self._download_log()
            return self.returncode
        return None

    def wait(self, timeout: int | float | None=None):
        deadline = None if timeout is None else time.time() + float(timeout)
        while True:
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(f'remote command timed out after {timeout}s')
            last_exc = None
            for attempt in range(E2B_STATUS_POLL_ATTEMPTS):
                poll_timeout = E2B_STATUS_POLL_TIMEOUT
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise TimeoutError(f'remote command timed out after {timeout}s')
                    poll_timeout = min(poll_timeout, remaining)
                try:
                    rc = self.poll(host_timeout=poll_timeout)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= E2B_STATUS_POLL_ATTEMPTS - 1:
                        raise TimeoutError(f'E2B status query failed {E2B_STATUS_POLL_ATTEMPTS} times') from exc
                    base_backoff = E2B_STATUS_POLL_BACKOFFS[min(attempt, len(E2B_STATUS_POLL_BACKOFFS) - 1)]
                    backoff = base_backoff * random.uniform(0.8, 1.2)
                    if deadline is not None:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise TimeoutError(f'remote command timed out after {timeout}s') from exc
                        backoff = min(backoff, remaining)
                    logger.warning('[%s] E2B status query failed (%d/%d): %s; retrying in %.1fs', self.task_id, attempt + 1, E2B_STATUS_POLL_ATTEMPTS, exc, backoff)
                    time.sleep(backoff)
            if last_exc is not None:
                raise TimeoutError('E2B status query failed') from last_exc
            if rc is not None:
                return rc
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(f'remote command timed out after {timeout}s')
            sleep_for = 2.0
            if deadline is not None:
                sleep_for = min(sleep_for, max(0.0, deadline - time.time()))
            time.sleep(sleep_for)

    def terminate(self):
        self.kill()

    def kill(self):
        run_command(self.sbx, f"""pid=$(cat '{self.remote_pid_path}' 2>/dev/null || true); if [ -n "$pid" ]; then kill -TERM -$pid 2>/dev/null || kill -TERM $pid 2>/dev/null || true; fi""", timeout=30, host_timeout=E2B_STATUS_POLL_TIMEOUT)
        self._download_log()
        if self.returncode is None:
            self.returncode = -15

def run_background(sbx, task_id: str, bash_cmd: str, log_path: Path) -> RemoteProcess:
    """Start a command in the sandbox and return a Popen-like handle."""
    import hashlib
    tag = hashlib.md5(f'{task_id}:{bash_cmd}:{time.time()}'.encode()).hexdigest()[:10]
    remote_log = f'/tmp/e2b_bg_{tag}.log'
    remote_status = f'/tmp/e2b_bg_{tag}.status'
    remote_pid = f'/tmp/e2b_bg_{tag}.pid'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script = f"#!/usr/bin/env bash\necho $$ >'{remote_pid}'\n. {E2B_HOME}/.e2b_env 2>/dev/null || true\ncd {TMP_WORKSPACE}\nset -o pipefail\n{{ {bash_cmd}; }} >'{remote_log}' 2>&1\necho $? >'{remote_status}'\n"
    remote_script = f'/tmp/e2b_bg_{tag}.sh'
    write_file(sbx, remote_script, script)

    def _run_remote() -> None:
        try:
            sbx.commands.run(f"chmod +x '{remote_script}' && bash '{remote_script}'", user=E2B_USER, timeout=0)
        except Exception as exc:
            logger.debug('[%s] E2B background command ended: %s', task_id, exc)
    threading.Thread(target=_run_remote, daemon=True).start()
    logger.info('[%s] Started E2B background command -> %s', task_id, log_path)
    return RemoteProcess(sbx, task_id, bash_cmd, log_path, remote_log, remote_status, remote_pid)

def close_proc_log(_proc) -> None:
    """Compatibility shim for docker_utils.close_proc_log."""
    return None
from utils.grading import _scores_without_telemetry, _LLM_JUDGE_LOG_PATCH, _JUDGE_TELEMETRY_PATCH, _JUDGE_POOL_PATCH, _JUDGE_CONFIG_PATCH, _load_privacy_audit_module_src

def run_grading(sbx, task_id: str, automated_checks: str, output_dir: Path, judge_endpoint_offset: int=0) -> dict:
    logger.info('[%s] Starting in-sandbox grading ...', task_id)
    env_remap = "import os\n_jb = os.environ.get('JUDGE_BASE_URL', '')\n_jk = os.environ.get('JUDGE_API_KEY', '')\nif _jb:\n    os.environ['OPENROUTER_BASE_URL'] = _jb.split(',')[0].strip()\nif _jk:\n    os.environ['OPENROUTER_API_KEY'] = _jk.split(',')[0].strip()\n"
    runner_code = '\n'.join(['import json, sys', env_remap, f"os.environ['JUDGE_ENDPOINT_OFFSET'] = '{judge_endpoint_offset}'", _LLM_JUDGE_LOG_PATCH, _JUDGE_TELEMETRY_PATCH, _JUDGE_POOL_PATCH, _JUDGE_CONFIG_PATCH, _load_privacy_audit_module_src(), automated_checks, '', 'try:', f'    result = grade(transcript=[], workspace_path="{TMP_WORKSPACE}")', 'except Exception as _grade_exc:', "    result = {'error': f'{type(_grade_exc).__name__}: {_redact_judge_secrets_jt(_grade_exc)}'}", "if isinstance(result, dict): result['_judge_attempts'] = _judge_attempts", 'print(json.dumps(result))']) + '\n'
    write_file(sbx, '/tmp/_grade_runner.py', runner_code)
    run_command(sbx, 'pip install -q json_repair', timeout=120)
    (stdout, stderr, rc) = run_command(sbx, 'python3 /tmp/_grade_runner.py', timeout=1200)
    if rc != 0:
        logger.error('[%s] Grading script failed: %s', task_id, stderr)
        return {'error': f'grade script failed: {stderr}'}
    try:
        scores = json.loads(stdout.strip())
    except json.JSONDecodeError:
        scores = None
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    scores = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if scores is None:
            logger.error('[%s] No valid JSON in grading stdout: %s', task_id, stdout[:500])
            return {'error': 'json parse failed: no valid JSON in stdout'}
    score_path = output_dir / 'score.json'
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(_scores_without_telemetry(scores), indent=2, ensure_ascii=False), encoding='utf-8')
    logger.info('[%s] Grading -> %s', task_id, score_path)
    download_file(sbx, '/tmp/llm_judge.jsonl', str(output_dir / 'llm_judge.jsonl'))
    return scores

def collect_output_from_sandbox(sbx, task_id: str, output_dir: Path) -> None:
    """Download task output files from the sandbox (mirrors"""
    task_output_dir = output_dir / 'task_output'
    task_output_dir.mkdir(parents=True, exist_ok=True)
    download_directory(sbx, '/tmp/openclaw', str(task_output_dir))
    results_out = task_output_dir / 'workspace' / 'results'
    results_out.mkdir(parents=True, exist_ok=True)
    ok = download_directory(sbx, f'{TMP_WORKSPACE}/results', str(results_out))
    if not ok:
        logger.warning('[%s] results/ dir missing or empty', task_id)
