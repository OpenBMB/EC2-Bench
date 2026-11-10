from __future__ import annotations
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)
DOCKER_IMAGE = os.environ.get('DOCKER_IMAGE', 'ec2-bench-runtime:review')
TMP_WORKSPACE = os.environ.get('TMP_WORKSPACE', '/tmp_workspace')
OPENCLAW_CONTEXT_WINDOW = 131072
OPENCLAW_COMPACTION_RESERVE_TOKENS = 40000
OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS = 16000
OPENCLAW_COMPACTION_MAX_HISTORY_SHARE = 0.5
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
SEARCH_PROVIDER = os.environ.get('SEARCH_PROVIDER', 'serper').strip().lower()
JUDGE_BASE_URL = os.environ.get('JUDGE_BASE_URL', '')
JUDGE_API_KEY = os.environ.get('JUDGE_API_KEY', '')
JUDGE_MODEL = os.environ.get('JUDGE_MODEL', '')

def remove_container(name: str) -> None:
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

def start_container(task_id: str, workspace_path: str, extra_env: str='', tmp_path: str='') -> None:
    proxy_http = os.environ.get('HTTP_PROXY_INNER', '')
    proxy_https = os.environ.get('HTTPS_PROXY_INNER', '')
    env_args = ['-e', f'http_proxy={proxy_http}', '-e', f'https_proxy={proxy_https}', '-e', f'HTTP_PROXY={proxy_http}', '-e', f'HTTPS_PROXY={proxy_https}', '-e', f'SERPER_API_KEY={SERPER_API_KEY}', '-e', f'SEARCH_PROVIDER={SEARCH_PROVIDER}', '-e', f"no_proxy={('' if not proxy_http else os.environ.get('NO_PROXY_INNER', ''))}", '-e', f"PRIVACY_JUDGE={os.environ.get('PRIVACY_JUDGE', '')}", '-e', f"EDGE_CLOUD_MODE={os.environ.get('EDGE_CLOUD_MODE', '')}"]
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith('#'):
            continue
        value = os.environ.get(key, '')
        env_args += ['-e', f'{key}={value}']
        masked = value[:4] + '***' if value else '(empty)'
        logger.info('[%s] Injecting env var: %s=%s', task_id, key, masked)
    if JUDGE_BASE_URL:
        env_args += ['-e', f'JUDGE_BASE_URL={JUDGE_BASE_URL}']
        logger.info('[%s] Injecting JUDGE_BASE_URL=%s', task_id, JUDGE_BASE_URL)
    if JUDGE_API_KEY:
        env_args += ['-e', f'JUDGE_API_KEY={JUDGE_API_KEY}']
        logger.info('[%s] Injecting JUDGE_API_KEY', task_id)
    if os.environ.get('JUDGE_REASONING_EFFORT'):
        env_args += ['-e', 'JUDGE_REASONING_EFFORT=' + os.environ['JUDGE_REASONING_EFFORT']]
    if JUDGE_MODEL:
        env_args += ['-e', f'JUDGE_MODEL={JUDGE_MODEL}']
        logger.info('[%s] Injecting JUDGE_MODEL=%s', task_id, JUDGE_MODEL)
    cmd = ['docker', 'run', '-d', '--name', task_id, *env_args, '-v', f'{workspace_path}:/app:ro', DOCKER_IMAGE, '/bin/bash', '-c', 'tail -f /dev/null']
    logger.info('[%s] Starting container, mounting %s → /app (ro)', task_id, workspace_path)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'Container startup failed:\n{r.stderr}')
    logger.info('[%s] Container ID: %s', task_id, r.stdout.strip()[:12])
    if tmp_path and os.path.exists(tmp_path):
        mkdir_cmd = ['docker', 'exec', task_id, 'mkdir', '-p', '/tmp_workspace/tmp']
        subprocess.run(mkdir_cmd, capture_output=True)
        cp_cmd = ['docker', 'cp', f'{tmp_path}/.', f'{task_id}:/tmp_workspace/tmp/']
        logger.info('[%s] Copying temp files: %s → /tmp_workspace/tmp', task_id, tmp_path)
        cp_r = subprocess.run(cp_cmd, capture_output=True, text=True)
        if cp_r.returncode != 0:
            logger.error('[%s] File copy failed: %s', task_id, cp_r.stderr)
        else:
            logger.info('[%s] Temp file copy complete', task_id)

def setup_workspace(task_id: str, thinking: str | None=None) -> None:
    logger.info('[%s] Copying /app → %s', task_id, TMP_WORKSPACE)
    r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f'cp -r /app/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}'], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f'Workspace copy failed:\n{r.stderr}')
    patch_cmd = 'sed -i \'s/if (params\\.modelOverride?\\.trim()) addRaw(params\\.modelOverride)/if (params.modelOverride\\?.trim() \\&\\& params.modelOverride.trim() !== "default") addRaw(params.modelOverride)/g\' /usr/lib/node_modules/openclaw/dist/compact-*.js 2>/dev/null'
    patch_r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', patch_cmd], capture_output=True, text=True, timeout=60)
    if patch_r.returncode == 0:
        logger.info("[%s] Patched OpenClaw to ignore model='default' override", task_id)
    else:
        logger.warning('[%s] OpenClaw model-override patch failed: %s', task_id, patch_r.stderr)
    if thinking is not None:
        logger.info('[%s] Setting thinkingDefault to %s', task_id, thinking)
        thinking_result = subprocess.run(['docker', 'exec', task_id, 'openclaw', 'config', 'set', 'agents.defaults.thinkingDefault', thinking], capture_output=True, text=True, timeout=60)
        if thinking_result.returncode != 0:
            raise RuntimeError(f'Failed to set thinkingDefault to {thinking}:\n{thinking_result.stderr}')
    subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', f'rm -rf /root/.openclaw/workspace && ln -s {TMP_WORKSPACE} /root/.openclaw/workspace'], capture_output=True, text=True, timeout=60)
    _patch_openclaw_streaming_usage(task_id)
    _patch_openclaw_overflow_recovery(task_id)
    if SEARCH_PROVIDER == 'serper':
        _install_openclaw_search_plugin(task_id, 'serper-search', SERPER_PLUGIN_DIR, 'SERPER_API_KEY')
    elif SEARCH_PROVIDER not in ('', 'none', 'disabled'):
        raise ValueError('SEARCH_PROVIDER must be serper or none')

def setup_skills(task_id: str, skills: str, skills_path: str) -> None:
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        subprocess.run(['docker', 'exec', task_id, 'mkdir', '-p', f'/root/skills/{line}'], capture_output=True, text=True, timeout=60)
        subprocess.run(['docker', 'cp', f'{skills_path}/{line}', f'{task_id}:/root/skills'], capture_output=True, text=True, timeout=60)

def setup_plugin_source(task_id: str, host_src_dir: str, container_dst: str, timeout: int=120) -> None:
    """Copy a plugin source directory into the container at an arbitrary path"""
    parent = os.path.dirname(container_dst.rstrip('/')) or '/'
    subprocess.run(['docker', 'exec', task_id, 'mkdir', '-p', parent], capture_output=True, text=True, timeout=timeout)
    subprocess.run(['docker', 'exec', task_id, 'rm', '-rf', container_dst], capture_output=True, text=True, timeout=timeout)
    r = subprocess.run(['docker', 'cp', host_src_dir, f'{task_id}:{container_dst}'], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f'setup_plugin_source: docker cp {host_src_dir} -> {task_id}:{container_dst} failed:\nstderr: {r.stderr}\nstdout: {r.stdout}')

def inject_openclaw_models(task_id: str, models_config: dict) -> None:
    """Inject custom models (with a uniform contextWindow) into openclaw.json."""
    container_tmp_path = '/tmp/openclaw_models.json'
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.json', delete=False) as tmp_file:
        json.dump(models_config, tmp_file, indent=2)
        tmp_file_path = tmp_file.name
    try:
        cp_r = subprocess.run(['docker', 'cp', tmp_file_path, f'{task_id}:{container_tmp_path}'], capture_output=True, text=True, timeout=60)
        if cp_r.returncode != 0:
            raise RuntimeError(f'Failed to copy models config into container:\n{cp_r.stderr}')
        inject_cmd = f"python3 - <<'PY'\nimport json\nimport pathlib\n\nconfig_path = pathlib.Path('/root/.openclaw/openclaw.json')\nmodels_path = pathlib.Path('{container_tmp_path}')\n\nconfig = json.loads(config_path.read_text()) if config_path.exists() else {{}}\nmodels = json.loads(models_path.read_text())\n\nfor provider in models.get('providers', {{}}).values():\n    if not isinstance(provider, dict):\n        continue\n    for model in provider.get('models', []):\n        if isinstance(model, dict):\n            model['contextWindow'] = {OPENCLAW_CONTEXT_WINDOW}\n\nconfig['models'] = models\n\nconfig_path.write_text(json.dumps(config, indent=2))\nPY"
        r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', inject_cmd], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f'Failed to inject models config:\n{r.stderr}')
    finally:
        Path(tmp_file_path).unlink(missing_ok=True)
    logger.info('[%s] Injected custom models config', task_id)

def configure_openclaw_compaction(task_id: str) -> None:
    """Make OpenClaw compact history *before* a model call would overflow."""
    configure_cmd = f"python3 - <<'PY'\nimport json\nimport pathlib\n\nconfig_path = pathlib.Path('/root/.openclaw/openclaw.json')\nconfig = json.loads(config_path.read_text()) if config_path.exists() else {{}}\n\ndefaults = config.setdefault('agents', {{}}).setdefault('defaults', {{}})\ncompaction = defaults.setdefault('compaction', {{}})\ncompaction['mode'] = 'safeguard'\ncompaction['reserveTokens'] = {OPENCLAW_COMPACTION_RESERVE_TOKENS}\ncompaction['reserveTokensFloor'] = {OPENCLAW_COMPACTION_RESERVE_TOKENS}\ncompaction['keepRecentTokens'] = {OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS}\ncompaction['maxHistoryShare'] = {OPENCLAW_COMPACTION_MAX_HISTORY_SHARE}\n\nconfig_path.parent.mkdir(parents=True, exist_ok=True)\nconfig_path.write_text(json.dumps(config, indent=2))\nPY"
    r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', configure_cmd], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        logger.warning('[%s] compaction safeguard NOT applied: %s', task_id, (r.stderr or r.stdout).strip())
        return
    logger.info('[%s] Compaction safeguard configured (reserve=%d keep_recent=%d share=%s)', task_id, OPENCLAW_COMPACTION_RESERVE_TOKENS, OPENCLAW_COMPACTION_KEEP_RECENT_TOKENS, OPENCLAW_COMPACTION_MAX_HISTORY_SHARE)

def run_warmup(task_id: str, warmup: str) -> None:
    """Execute warmup bash commands line by line inside the container (skip blank lines and comments)."""
    if not warmup.strip():
        return
    commands = [line.strip() for line in warmup.splitlines() if line.strip() and (not line.strip().startswith('#'))]
    if not commands:
        return
    logger.info('[%s] Running warmup (%d commands)', task_id, len(commands))
    for cmd in commands:
        logger.info('[%s] warmup: %s', task_id, cmd)
        r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', cmd], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f'Warmup command failed: {cmd!r}\n{r.stderr}')

def run_background(task_id: str, bash_cmd: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open('w', encoding='utf-8')
    proc = subprocess.Popen(['docker', 'exec', task_id, '/bin/bash', '-c', f'cd {TMP_WORKSPACE} && {bash_cmd}'], stdout=log_file, stderr=subprocess.STDOUT, encoding='utf-8')
    proc._log_file = log_file
    logger.info('[%s] Started process PID=%s → %s', task_id, proc.pid, log_path)
    return proc

def close_proc_log(proc: subprocess.Popen) -> None:
    """Close the log file handle created by run_background."""
    log_file = getattr(proc, '_log_file', None)
    if log_file and (not log_file.closed):
        log_file.close()

def collect_output_from_container(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/."""
    task_output_dir = output_dir / 'task_output'
    task_output_dir.mkdir(parents=True, exist_ok=True)
    _copy_dir_from_container(task_id, '/tmp/openclaw/.', str(task_output_dir))
    results_out = task_output_dir / 'workspace' / 'results'
    results_out.mkdir(parents=True, exist_ok=True)
    ok = _copy_dir_from_container(task_id, f'{TMP_WORKSPACE}/results/.', str(results_out))
    if not ok:
        logger.warning('[%s] results/ directory does not exist or is empty', task_id)

def patch_openclaw_config(task_id: str, tools_also_allow: list[str] | None=None, model_compat: dict | None=None) -> None:
    """Patch tool permissions and model compat settings in openclaw.json."""
    patches = []
    if tools_also_allow:
        patches.append(f"tools = c.setdefault('tools', {{}})\nkey = 'allow' if 'allow' in tools else 'alsoAllow'\nallow = tools.setdefault(key, [])\nfor t in {tools_also_allow!r}:\n    if '*' not in allow and t not in allow: allow.append(t)\nif key == 'allow': tools.pop('alsoAllow', None)")
    if model_compat:
        patches.append(f"for prov in c.get('models', {{}}).get('providers', {{}}).values():\n    for m in prov.get('models', []):\n        m.setdefault('compat', {{}}).update({model_compat!r})")
    if not patches:
        return
    script = "import json, pathlib\np = pathlib.Path('/root/.openclaw/openclaw.json')\nc = json.loads(p.read_text())\n" + '\n'.join(patches) + '\np.write_text(json.dumps(c, indent=2))\n'
    r = subprocess.run(['docker', 'exec', task_id, 'python3', '-c', script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'patch_openclaw_config failed:\n{r.stderr}')
    applied = []
    if tools_also_allow:
        applied.append(f'tools.alsoAllow={tools_also_allow}')
    if model_compat:
        applied.append(f'model_compat={model_compat}')
    logger.info('[%s] OpenClaw config patched: %s', task_id, ', '.join(applied))

def install_openclaw_plugin(task_id: str, container_plugin_path: str, timeout: int=300, retries: int=3) -> None:
    """Install an OpenClaw plugin from a directory already inside the container."""
    subprocess.run(['docker', 'exec', task_id, 'chmod', '-R', '755', container_plugin_path], capture_output=True, text=True, timeout=60)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            install_r = subprocess.run(['docker', 'exec', task_id, 'openclaw', 'plugins', 'install', container_plugin_path], capture_output=True, text=True, timeout=timeout)
            if install_r.returncode == 0:
                logger.info('[%s] OpenClaw plugin installed: %s', task_id, container_plugin_path)
                return
            last_err = f'Plugin install failed ({container_plugin_path}):\nstderr: {install_r.stderr}\nstdout: {install_r.stdout}'
        except subprocess.TimeoutExpired:
            last_err = f'Plugin install timed out after {timeout}s ({container_plugin_path})'
        logger.warning('[%s] Plugin install attempt %d/%d failed: %s', task_id, attempt, retries, last_err)
    raise RuntimeError(last_err)
SERPER_PLUGIN_DIR = Path(__file__).resolve().parent.parent / 'skills' / 'serper-search'

def _patch_openclaw_streaming_usage(task_id: str) -> None:
    """Patch OpenClaw's openai-completions adapter so it always sends"""
    patch_cmd = "python3 - <<'PY'\nimport pathlib, sys, re\n\nsearch_roots = [\n    pathlib.Path('/usr/local/lib/node_modules'),\n    pathlib.Path('/usr/lib/node_modules'),\n    pathlib.Path('/opt/node_modules'),\n    pathlib.Path('/root/.local/share/openclaw'),\n    pathlib.Path('/app'),\n]\n\ntarget = None\nfor root in search_roots:\n    if not root.exists():\n        continue\n    hits = list(root.rglob('providers/openai-completions.js'))\n    if hits:\n        target = hits[0]\n        break\n\nif target is None:\n    print('[patch-usage] openai-completions.js not found, skipping', file=sys.stderr)\n    sys.exit(0)\n\ntext = target.read_text(encoding='utf-8')\n\nnew_text = re.sub(\n    r'compat\\.supportsUsageInStreaming\\s*!==\\s*false',\n    'true /* patched: always enable streaming usage */',\n    text,\n)\n\nif new_text == text:\n    print(f'[patch-usage] already patched or pattern not found in {target}', file=sys.stderr)\nelse:\n    target.write_text(new_text, encoding='utf-8')\n    print(f'[patch-usage] patched {target}', file=sys.stderr)\nPY"
    r = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', patch_cmd], capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning('[%s] streaming-usage patch failed: %s', task_id, r.stderr)
    else:
        logger.info('[%s] streaming-usage patch: %s', task_id, r.stderr.strip() or r.stdout.strip())

def _patch_openclaw_overflow_recovery(task_id: str) -> None:
    """Install the shared OpenAI-provider overflow recovery patch."""
    import base64
    patch_path = Path(__file__).resolve().parents[1] / 'scripts' / 'patch_openclaw_overflow_recovery.py'
    script = patch_path.read_text(encoding='utf-8')
    encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
    command = f"printf %s '{encoded}' | base64 -d | python3 -"
    result = subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', command], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'[{task_id}] overflow recovery patch failed:\n{result.stdout}\n{result.stderr}')
    logger.info('[%s] overflow recovery patch: %s', task_id, (result.stderr or result.stdout).strip()[:500])

def _install_openclaw_search_plugin(task_id: str, plugin_id: str, plugin_dir: Path, api_key_name: str) -> None:
    """Copy a search plugin into the container and install it."""
    container_plugin_path = f'/root/{plugin_id}-plugin'
    subprocess.run(['docker', 'exec', task_id, 'mkdir', '-p', container_plugin_path], capture_output=True, text=True, timeout=60)
    cp_r = subprocess.run(['docker', 'cp', f'{plugin_dir}/.', f'{task_id}:{container_plugin_path}/'], capture_output=True, text=True, timeout=60)
    if cp_r.returncode != 0:
        raise RuntimeError(f'{plugin_id} plugin copy failed:\n{cp_r.stderr}')
    disable_builtin_cmd = 'python3 - <<\'PY\'\nimport json, pathlib\np = pathlib.Path(\'/root/.openclaw/openclaw.json\')\nc = json.loads(p.read_text()) if p.exists() else {}\nc.setdefault(\'tools\', {}).setdefault(\'web\', {})[\'search\'] = {"enabled": False}\np.write_text(json.dumps(c, indent=2))\nPY'
    subprocess.run(['docker', 'exec', task_id, '/bin/bash', '-c', disable_builtin_cmd], capture_output=True, text=True, timeout=60)
    install_openclaw_plugin(task_id, container_plugin_path)
    logger.info('[%s] Installed %s plugin (%s)', task_id, plugin_id, api_key_name)

def _copy_dir_from_container(task_id: str, src: str, dest: str) -> bool:
    r = subprocess.run(['docker', 'cp', f'{task_id}:{src}', dest], capture_output=True, text=True)
    if r.returncode == 0:
        logger.info('[%s] Collected container directory %s → %s', task_id, src, dest)
        return True
    return False
