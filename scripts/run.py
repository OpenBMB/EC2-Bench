"""Run a documented strategy against an external dataset."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]

def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    strategies = json.loads((ROOT / 'configs/strategies.json').read_text())
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('strategy', choices=list(strategies))
    p.add_argument('--data-root', type=Path, help='External dataset containing tasks/, workspace/ and optional skills/')
    p.add_argument('--category', default='EC2Bench')
    p.add_argument('--query-router-table', type=Path)
    p.add_argument('--runtime', choices=['e2b', 'docker'], default='e2b')
    p.add_argument('--edge-model', default='edge/Qwen/Qwen3.5-9B')
    p.add_argument('--cloud-model', default='cloud/GLM-5.3')
    p.add_argument('--models-config', type=Path, default=ROOT / 'configs/models.example.json')
    p.add_argument('--repeat', type=int, default=3)
    p.add_argument('--parallel', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--task-filter')
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--dry-run', action='store_true')
    (a, extra) = p.parse_known_args()
    if a.repeat < 1 or a.parallel < 1 or a.limit < 0:
        p.error('repeat/parallel must be positive and limit nonnegative')
    s = strategies[a.strategy]
    cmd = [sys.executable, str(ROOT / 'eval/run_batch.py'), '--category', a.category, '--run-mode', s['run_mode'], '--runtime', a.runtime, '--models-config', str(a.models_config.resolve()), '--repeat', str(a.repeat), '--parallel', str(a.parallel), '--output-dir', str((a.output_dir or ROOT / 'output' / a.strategy).resolve())]
    if a.strategy != 'cloud-only':
        cmd += ['--edge-model', a.edge_model]
    if a.strategy != 'local-only':
        cmd += ['--cloud-model', a.cloud_model]
    table = a.query_router_table or os.environ.get('QUERY_ROUTER_TABLE') or ROOT / 'configs/query_router_routes.json'
    if a.strategy == 'query-routing' and table:
        cmd += ['--query-router-table', str(Path(table).expanduser().resolve())]
    if a.limit:
        cmd += ['--limit', str(a.limit)]
    if a.task_filter:
        cmd += ['--task-filter', a.task_filter]
    cmd += extra
    if a.dry_run:
        print(shlex.join(cmd), flush=True)
        return
    if a.data_root is None:
        p.error('Supply --data-root PATH; this repository contains code only')
    data_root = a.data_root.expanduser().resolve()
    if not data_root.is_dir():
        p.error('External data root must be an existing directory')
    tasks_dir = data_root / 'tasks'
    if not tasks_dir.is_dir():
        tasks_dir = ROOT / 'tasks'
    if not tasks_dir.is_dir():
        p.error('Task definitions must be present in the repository or external data root')
    env = {**os.environ, 'EC2_DATA_ROOT': str(data_root), 'TASKS_SUBDIR': str(tasks_dir)}
    if a.strategy == 'per-step-routing':
        routing = json.loads((ROOT / 'configs/per_step_thresholds.json').read_text())
        top_k = str(routing['glimpse_top_k'])
        if not env.get('GLIMPSE_ENTROPY_THRESHOLD'):
            if env.get('GLIMPSE_TOP_K', top_k) != top_k:
                p.error('Set GLIMPSE_ENTROPY_THRESHOLD when overriding GLIMPSE_TOP_K')
            threshold = routing['entropy_thresholds'].get(a.edge_model.rsplit('/', 1)[-1])
            if threshold is None:
                p.error('Set GLIMPSE_ENTROPY_THRESHOLD for this edge model')
            env['GLIMPSE_ENTROPY_THRESHOLD'] = str(threshold)
        env.setdefault('GLIMPSE_TOP_K', top_k)
    env.setdefault('ACEBENCH_SKILLS_PATH', str(data_root / 'skills'))
    print(shlex.join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    raise SystemExit(result.returncode)
if __name__ == '__main__':
    main()
