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

DOCKER_IMAGE  = os.environ.get("DOCKER_IMAGE",   "acebench-openclaw:v1.0")
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE",  "/tmp_workspace")

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
SERP_API_KEY = os.environ.get("SERP_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")  
# SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "brave")
# SEARCH_API_KEY_VAR = os.environ.get("SEARCH_API_KEY_VAR", "BRAVE_API_KEY")
# SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "gemini")
# SEARCH_API_KEY_VAR = os.environ.get("SEARCH_API_KEY_VAR", "GEMINI_API_KEY")
SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "serp")
SEARCH_API_KEY_VAR = os.environ.get("SEARCH_API_KEY_VAR", "SERP_API_KEY")
#########

JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", "")
JUDGE_API_KEY = os.environ.get("JUDGE_API_KEY", "")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "")
#########

def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)

def start_container(task_id: str, workspace_path: str, extra_env: str = "",
                    tmp_path: str = "", lobster_env: list[str] | None = None) -> None:
    proxy_http = os.environ.get('HTTP_PROXY_INNER', '')
    proxy_https = os.environ.get('HTTPS_PROXY_INNER', '')
    env_args = [
        "-e", f"http_proxy={proxy_http}",
        "-e", f"https_proxy={proxy_https}",
        "-e", f"HTTP_PROXY={proxy_http}",
        "-e", f"HTTPS_PROXY={proxy_https}",
        "-e", f"BRAVE_API_KEY={BRAVE_API_KEY}",
        "-e", f"SERP_API_KEY={SERP_API_KEY}",  
        "-e", f"SERPER_API_KEY={SERPER_API_KEY}",  
        "-e", f"SEARCH_PROVIDER={SEARCH_PROVIDER}", 
        "-e", f"{SEARCH_API_KEY_VAR}={os.environ.get(SEARCH_API_KEY_VAR, '')}",
        "-e", f"no_proxy={'' if not proxy_http else os.environ.get('NO_PROXY_INNER', '')}",
        "-e", f"PRIVACY_JUDGE={os.environ.get('PRIVACY_JUDGE', '')}",
        "-e", f"EDGE_CLOUD_MODE={os.environ.get('EDGE_CLOUD_MODE', '')}",
        "-e", f"PRIVACY_JUDGE_MODE={os.environ.get('PRIVACY_JUDGE_MODE', 'inline')}",
    ]
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith("#"):
            continue
        value = os.environ.get(key, "")
        env_args += ["-e", f"{key}={value}"]
        masked = (value[:4] + "***") if value else "(empty)"
        logger.info("[%s] Injecting env var: %s=%s", task_id, key, masked)

    for key in (lobster_env or []):
        value = os.environ.get(key, "")
        if not value:
            logger.warning("[%s] Lobster env key %s not found in environment, skipping", task_id, key)
            continue
        env_args += ["-e", f"{key}={value}"]
        masked = value[:4] + "***"
        logger.info("[%s] Injecting lobster env: %s=%s", task_id, key, masked)

    if JUDGE_BASE_URL:
        env_args += ["-e", f"JUDGE_BASE_URL={JUDGE_BASE_URL}"]
        logger.info("[%s] Injecting JUDGE_BASE_URL=%s", task_id, JUDGE_BASE_URL)
    if JUDGE_API_KEY:
        env_args += ["-e", f"JUDGE_API_KEY={JUDGE_API_KEY}"]
        logger.info("[%s] Injecting JUDGE_API_KEY", task_id)
    if JUDGE_MODEL:
        env_args += ["-e", f"JUDGE_MODEL={JUDGE_MODEL}"]
        logger.info("[%s] Injecting JUDGE_MODEL=%s", task_id, JUDGE_MODEL)

    cmd = [
        "docker", "run", "-d",
        "--name", task_id,
        *env_args,
        "-v", f"{workspace_path}:/app:ro",
        DOCKER_IMAGE,
        "/bin/bash", "-c", "tail -f /dev/null",
    ]
    logger.info("[%s] Starting container, mounting %s → /app (ro)", task_id, workspace_path)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Container startup failed:\n{r.stderr}")
    logger.info("[%s] Container ID: %s", task_id, r.stdout.strip()[:12])

    if tmp_path and os.path.exists(tmp_path):
        mkdir_cmd = ["docker", "exec", task_id, "mkdir", "-p", "/tmp_workspace/tmp"]
        subprocess.run(mkdir_cmd, capture_output=True)

        cp_cmd = ["docker", "cp", f"{tmp_path}/.", f"{task_id}:/tmp_workspace/tmp/"]
        
        logger.info("[%s] Copying temp files: %s → /tmp_workspace/tmp", task_id, tmp_path)
        cp_r = subprocess.run(cp_cmd, capture_output=True, text=True)
        
        if cp_r.returncode != 0:
            logger.error("[%s] File copy failed: %s", task_id, cp_r.stderr)
        else:
            logger.info("[%s] Temp file copy complete", task_id)

def setup_workspace(task_id: str, thinking: str | None = None) -> None:
    logger.info("[%s] Copying /app → %s", task_id, TMP_WORKSPACE)
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cp -r /app/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Workspace copy failed:\n{r.stderr}")


    patch_cmd = (
        r"""sed -i 's/if (params\.modelOverride?\.trim()) addRaw(params\.modelOverride)/"""
        r"""if (params.modelOverride\?.trim() \&\& params.modelOverride.trim() !== "default") addRaw(params.modelOverride)/g' """
        "/usr/lib/node_modules/openclaw/dist/compact-*.js 2>/dev/null"
    )
    patch_r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", patch_cmd],
        capture_output=True, text=True, timeout=60,
    )
    if patch_r.returncode == 0:
        logger.info("[%s] Patched OpenClaw to ignore model='default' override", task_id)
    else:
        logger.warning("[%s] OpenClaw model-override patch failed: %s", task_id, patch_r.stderr)

    if thinking is not None:
        logger.info("[%s] Setting thinkingDefault to %s", task_id, thinking)
        thinking_result = subprocess.run(
            ["docker", "exec", task_id,
             "openclaw", "config", "set", "agents.defaults.thinkingDefault", thinking],
            capture_output=True, text=True, timeout=60,
        )
        if thinking_result.returncode != 0:
            raise RuntimeError(
                f"Failed to set thinkingDefault to {thinking}:\n{thinking_result.stderr}"
            )

    # Symlink OpenClaw workspace → TMP_WORKSPACE so the image tool's
    # media-local-roots check allows reading files under /tmp_workspace.
    subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"rm -rf /root/.openclaw/workspace && ln -s {TMP_WORKSPACE} /root/.openclaw/workspace"],
        capture_output=True, text=True, timeout=60,
    )

    _patch_openclaw_streaming_usage(task_id)
    if SEARCH_PROVIDER in ("serp", "serper"):  
        _install_serp_search_plugin(task_id)
    elif SEARCH_PROVIDER != "brave" or SEARCH_API_KEY_VAR != "BRAVE_API_KEY":
        search_inject_cmd = f"""python3 - <<'PY'
import json, pathlib
p = pathlib.Path('/root/.openclaw/openclaw.json')
c = json.loads(p.read_text())
c.setdefault('tools', {{}}).setdefault('web', {{}})['search'] = {{
    "enabled": True,
    "provider": "{SEARCH_PROVIDER}",
    "apiKey": "${{{SEARCH_API_KEY_VAR}}}"
}}
p.write_text(json.dumps(c, indent=2))
PY"""
        subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", search_inject_cmd],
            capture_output=True, text=True,
        )
        logger.info("[%s] Search provider set to %s (key var: %s)", task_id, SEARCH_PROVIDER, SEARCH_API_KEY_VAR)

def setup_skills(task_id: str, skills: str, skills_path: str) -> None:
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        subprocess.run(
            ["docker", "exec", task_id,
             "mkdir", "-p", f"/root/skills/{line}"],
            capture_output=True, text=True, timeout=60,
        )
        r = subprocess.run(
            ["docker", "cp",
             f"{skills_path}/{line}", f"{task_id}:/root/skills"],
            capture_output=True, text=True, timeout=60,
        )


def setup_plugin_source(task_id: str, host_src_dir: str, container_dst: str,
                        timeout: int = 120) -> None:
    """Copy a plugin source directory into the container at an arbitrary path
    (typically /root/openclaw_plugins/<name>/), without touching /root/skills/.

    Caller is expected to invoke install_openclaw_plugin(task_id, container_dst)
    afterwards.
    """
    parent = os.path.dirname(container_dst.rstrip("/")) or "/"
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", parent],
        capture_output=True, text=True, timeout=timeout,
    )
    subprocess.run(
        ["docker", "exec", task_id, "rm", "-rf", container_dst],
        capture_output=True, text=True, timeout=timeout,
    )
    r = subprocess.run(
        ["docker", "cp", host_src_dir, f"{task_id}:{container_dst}"],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"setup_plugin_source: docker cp {host_src_dir} -> {task_id}:{container_dst} failed:\n"
            f"stderr: {r.stderr}\nstdout: {r.stdout}"
        )
#########


def inject_openclaw_models(task_id: str, models_config: dict) -> None:
    """Inject custom models into ~/.openclaw/openclaw.json."""
    container_tmp_path = "/tmp/openclaw_models.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp_file:
        json.dump(models_config, tmp_file, indent=2)
        tmp_file_path = tmp_file.name

    try:
        cp_r = subprocess.run(
            ["docker", "cp", tmp_file_path, f"{task_id}:{container_tmp_path}"],
            capture_output=True, text=True, timeout=60,
        )
        if cp_r.returncode != 0:
            raise RuntimeError(f"Failed to copy models config into container:\n{cp_r.stderr}")

        inject_cmd = f"""python3 - <<'PY'
import json
import pathlib

config_path = pathlib.Path('/root/.openclaw/openclaw.json')
models_path = pathlib.Path('{container_tmp_path}')

config = json.loads(config_path.read_text()) if config_path.exists() else {{}}
models = json.loads(models_path.read_text())
config['models'] = models

config_path.write_text(json.dumps(config, indent=2))
PY"""
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Failed to inject models config:\n{r.stderr}")
    finally:
        Path(tmp_file_path).unlink(missing_ok=True)

    logger.info("[%s] Injected custom models config", task_id)


def run_warmup(task_id: str, warmup: str) -> None:
    """Execute warmup bash commands line by line inside the container (skip blank lines and comments)."""
    if not warmup.strip():
        return
    commands = [
        line.strip()
        for line in warmup.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not commands:
        return

    logger.info("[%s] Running warmup (%d commands)", task_id, len(commands))
    for cmd in commands:
        logger.info("[%s] warmup: %s", task_id, cmd)
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Warmup command failed: {cmd!r}\n{r.stderr}")


def run_background(task_id: str, bash_cmd: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cd {TMP_WORKSPACE} && {bash_cmd}"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    proc._log_file = log_file
    logger.info("[%s] Started process PID=%s → %s", task_id, proc.pid, log_path)
    return proc


def close_proc_log(proc: subprocess.Popen) -> None:
    """Close the log file handle created by run_background."""
    log_file = getattr(proc, "_log_file", None)
    if log_file and not log_file.closed:
        log_file.close()


def collect_output_from_container(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/.

    Collection strategy:
      1. All files under /tmp/openclaw/ (agent session logs, etc.)
      2. Task output files under /tmp_workspace/results/
    """
    task_output_dir = output_dir / "task_output"
    task_output_dir.mkdir(parents=True, exist_ok=True)

    _copy_dir_from_container(task_id, "/tmp/openclaw/.", str(task_output_dir))

    results_out = task_output_dir / "workspace" / "results"
    results_out.mkdir(parents=True, exist_ok=True)
    ok = _copy_dir_from_container(
        task_id, f"{TMP_WORKSPACE}/results/.", str(results_out),
    )
    if not ok:
        logger.warning("[%s] results/ directory does not exist or is empty", task_id)


def inject_lobster_workspace(task_id: str, workspace_path: str) -> None:
    """Copy the entire lobster workspace into /root/ (the OpenClaw workspace in the image).

    This brings in everything: SOUL.md, USER.md, MEMORY.md, memory/, skills/, etc.
    """
    r = subprocess.run(
        ["docker", "cp", f"{workspace_path}/.", f"{task_id}:/root/"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.error("[%s] Lobster workspace copy failed: %s", task_id, r.stderr)
    else:
        logger.info("[%s] Lobster workspace copied: %s → /root/", task_id, workspace_path)


def patch_openclaw_config(task_id: str, tools_also_allow: list[str] | None = None,
                          model_compat: dict | None = None) -> None:
    """Patch ~/.openclaw/openclaw.json: add tools.alsoAllow and model compat settings."""
    patches = []
    if tools_also_allow:
        patches.append(
            f"c.setdefault('tools', {{}}).setdefault('alsoAllow', [])\n"
            f"for t in {tools_also_allow!r}:\n"
            f"    if t not in c['tools']['alsoAllow']: c['tools']['alsoAllow'].append(t)"
        )
    if model_compat:
        patches.append(
            f"for prov in c.get('models', {{}}).get('providers', {{}}).values():\n"
            f"    for m in prov.get('models', []):\n"
            f"        m.setdefault('compat', {{}}).update({model_compat!r})"
        )
    if not patches:
        return
    script = (
        "import json, pathlib\n"
        "p = pathlib.Path('/root/.openclaw/openclaw.json')\n"
        "c = json.loads(p.read_text())\n"
        + "\n".join(patches) + "\n"
        "p.write_text(json.dumps(c, indent=2))\n"
    )
    r = subprocess.run(
        ["docker", "exec", task_id, "python3", "-c", script],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"patch_openclaw_config failed:\n{r.stderr}")
    applied = []
    if tools_also_allow:
        applied.append(f"tools.alsoAllow={tools_also_allow}")
    if model_compat:
        applied.append(f"model_compat={model_compat}")
    logger.info("[%s] OpenClaw config patched: %s", task_id, ", ".join(applied))


def install_openclaw_plugin(task_id: str, container_plugin_path: str,
                            timeout: int = 300, retries: int = 3) -> None:
    """Install an OpenClaw plugin from a directory already inside the container."""
    subprocess.run(
        ["docker", "exec", task_id, "chmod", "-R", "755", container_plugin_path],
        capture_output=True, text=True, timeout=60,
    )
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            install_r = subprocess.run(
                ["docker", "exec", task_id, "openclaw", "plugins", "install", container_plugin_path],
                capture_output=True, text=True, timeout=timeout,
            )
            if install_r.returncode == 0:
                logger.info("[%s] OpenClaw plugin installed: %s", task_id, container_plugin_path)
                return
            last_err = (
                f"Plugin install failed ({container_plugin_path}):\n"
                f"stderr: {install_r.stderr}\nstdout: {install_r.stdout}"
            )
        except subprocess.TimeoutExpired:
            last_err = f"Plugin install timed out after {timeout}s ({container_plugin_path})"
        logger.warning("[%s] Plugin install attempt %d/%d failed: %s",
                       task_id, attempt, retries, last_err)
    raise RuntimeError(last_err)


SERP_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "skills" / "serp-search"


def _patch_openclaw_streaming_usage(task_id: str) -> None:
    """Patch OpenClaw's openai-completions adapter so it always sends
    stream_options.include_usage=true for openai-completions providers.

    OpenClaw hardcodes supportsUsageInStreaming=false for non-native providers
    (see github.com/openclaw/openclaw/issues/41963), causing all vLLM/llama.cpp
    token counts to be reported as zero.  We search for the compiled JS file and
    replace the guard condition so usage is always requested.
    """
    patch_cmd = r"""python3 - <<'PY'
import pathlib, sys, re

search_roots = [
    pathlib.Path('/usr/local/lib/node_modules'),
    pathlib.Path('/usr/lib/node_modules'),
    pathlib.Path('/opt/node_modules'),
    pathlib.Path('/root/.local/share/openclaw'),
    pathlib.Path('/app'),
]

target = None
for root in search_roots:
    if not root.exists():
        continue
    hits = list(root.rglob('providers/openai-completions.js'))
    if hits:
        target = hits[0]
        break

if target is None:
    print('[patch-usage] openai-completions.js not found, skipping', file=sys.stderr)
    sys.exit(0)

text = target.read_text(encoding='utf-8')

new_text = re.sub(
    r'compat\.supportsUsageInStreaming\s*!==\s*false',
    'true /* patched: always enable streaming usage */',
    text,
)

if new_text == text:
    print(f'[patch-usage] already patched or pattern not found in {target}', file=sys.stderr)
else:
    target.write_text(new_text, encoding='utf-8')
    print(f'[patch-usage] patched {target}', file=sys.stderr)
PY"""
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", patch_cmd],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.warning("[%s] streaming-usage patch failed: %s", task_id, r.stderr)
    else:
        logger.info("[%s] streaming-usage patch: %s", task_id, r.stderr.strip() or r.stdout.strip())



def _install_serp_search_plugin(task_id: str) -> None:
    """Copy serp-search plugin into the container and install it, replacing built-in web_search."""
    container_plugin_path = "/root/serp-search-plugin"
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", container_plugin_path],
        capture_output=True, text=True, timeout=60,
    )
    cp_r = subprocess.run(
        ["docker", "cp", f"{SERP_PLUGIN_DIR}/.", f"{task_id}:{container_plugin_path}/"],
        capture_output=True, text=True, timeout=60,
    )
    if cp_r.returncode != 0:
        raise RuntimeError(f"serp-search plugin copy failed:\n{cp_r.stderr}")

    disable_builtin_cmd = """python3 - <<'PY'
import json, pathlib
p = pathlib.Path('/root/.openclaw/openclaw.json')
c = json.loads(p.read_text()) if p.exists() else {}
c.setdefault('tools', {}).setdefault('web', {})['search'] = {"enabled": False}
p.write_text(json.dumps(c, indent=2))
PY"""
    subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", disable_builtin_cmd],
        capture_output=True, text=True, timeout=60,
    )

    install_openclaw_plugin(task_id, container_plugin_path)


    logger.info("[%s] Replaced built-in web_search with serp-search plugin (SERP_API_KEY)", task_id)


def _copy_dir_from_container(task_id: str, src: str, dest: str) -> bool:
    r = subprocess.run(
        ["docker", "cp", f"{task_id}:{src}", dest],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        logger.info("[%s] Collected container directory %s → %s", task_id, src, dest)
        return True
    return False

