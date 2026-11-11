"""Task-level routing using the published RouteLLM matrix-factorization router."""
from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
logger = logging.getLogger(__name__)
DEFAULT_ROUTE_TABLE = None
DEFAULT_MF_MODEL = 'routellm/mf_gpt4_augmented'

def load_route_table(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Load the offline route table."""
    if path is None:
        raise ValueError('Supply an explicit external Query route table')
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f'query-router route table not found: {p}. Generate it first with `python -m utils.query_router build ...`.')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'query-router route table is not valid JSON ({p}): {exc}') from exc
    if not isinstance(data, dict) or 'routes' not in data or (not isinstance(data['routes'], dict)):
        raise ValueError(f"query-router route table missing 'routes' dict ({p}); got keys={(list(data) if isinstance(data, dict) else type(data).__name__)}")
    data.setdefault('missing_policy', 'fail')
    data.setdefault('router', 'routellm-mf')
    return data

def _normalize_task_id(task_id: str) -> str:
    """Normalize a task id for lookup."""
    return (task_id or '').strip()

def _task_lookup_key(task_id: str) -> str:
    """Return the task-number-and-name suffix used for route lookup."""
    match = re.search(r'(?:^|_)(task_\d+(?:_.*)?)$', task_id)
    return match.group(1) if match else task_id

def decide(task_id: str, table: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return ``(decision, reason, info)`` for ``task_id``."""
    routes = table.get('routes') or {}
    tid = _normalize_task_id(task_id)
    entry = routes.get(tid)
    if entry is None:
        lookup_key = _task_lookup_key(tid)
        for (k, v) in routes.items():
            if k.endswith(tid) or tid.endswith(k) or _task_lookup_key(k) == lookup_key:
                entry = v
                break
    if entry is None:
        policy = (table.get('missing_policy') or 'fail').lower()
        if policy == 'local':
            return ('LOCAL', 'missing_default_local', {'task_id': tid, 'table_threshold': table.get('threshold')})
        if policy == 'cloud':
            return ('CLOUD', 'missing_default_cloud', {'task_id': tid, 'table_threshold': table.get('threshold')})
        raise KeyError(f'query-router: task_id {tid!r} not found in route table ({len(routes)} entries, missing_policy=fail). Either rebuild the table or set missing_policy=local|cloud.')
    raw_decision = str(entry.get('decision', '')).upper().strip()
    if raw_decision not in ('LOCAL', 'CLOUD'):
        raise ValueError(f"query-router: route entry for {tid!r} has invalid decision {entry.get('decision')!r}; expected LOCAL or CLOUD.")
    reason = str(entry.get('reason') or f"routellm-mf score={entry.get('score')!r} τ={entry.get('threshold', table.get('threshold'))!r}")
    return (raw_decision, reason, dict(entry))

def build_router_usage_record(decision: str, reason: str, info: dict[str, Any]) -> dict[str, Any]:
    """Build the per-task ``router_usage.json`` for query-router."""
    zero = {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    return {'judge': dict(zero), 'total': dict(zero), 'router': 'routellm-mf-query-level', 'decision': decision, 'reason': reason, 'score': info.get('score'), 'threshold': info.get('threshold'), 'note': 'Query-level routing decision pre-computed offline; no per-task cloud/judge call at run time.'}

def calibrate_threshold_for_target_ratio(scores: Sequence[float], target_cloud_ratio: float) -> float:
    """Pick the threshold τ such that exactly ``target_cloud_ratio`` of the"""
    if not 0.0 < target_cloud_ratio < 1.0:
        raise ValueError(f'target_cloud_ratio must be in (0, 1), got {target_cloud_ratio!r}')
    sorted_scores = sorted((float(s) for s in scores))
    if not sorted_scores:
        raise ValueError('calibrate_threshold_for_target_ratio: empty scores')
    idx = int(round((1.0 - target_cloud_ratio) * (len(sorted_scores) - 1)))
    idx = max(0, min(len(sorted_scores) - 1, idx))
    return sorted_scores[idx]

def build_route_table_from_scores(task_scores: Iterable[tuple[str, float]], *, threshold: float | None=None, target_cloud_ratio: float | None=None, edge_label: str='edge', cloud_label: str='cloud', router_name: str='routellm-mf', model_id: str=DEFAULT_MF_MODEL, missing_policy: str='fail', extra_meta: dict[str, Any] | None=None) -> dict[str, Any]:
    """Materialize a route table dict from ``(task_id, score)`` pairs."""
    if (threshold is None) == (target_cloud_ratio is None):
        raise ValueError('build_route_table_from_scores: pass exactly one of threshold or target_cloud_ratio')
    pairs = [(str(tid), float(s)) for (tid, s) in task_scores]
    if not pairs:
        raise ValueError('build_route_table_from_scores: no (task_id, score) inputs')
    if target_cloud_ratio is not None:
        threshold = calibrate_threshold_for_target_ratio([s for (_, s) in pairs], target_cloud_ratio)
    routes: dict[str, dict[str, Any]] = {}
    n_cloud = 0
    for (tid, score) in pairs:
        decision = 'CLOUD' if score >= threshold else 'LOCAL'
        if decision == 'CLOUD':
            n_cloud += 1
        routes[tid] = {'decision': decision, 'score': score, 'threshold': threshold, 'reason': f"score{('>=' if decision == 'CLOUD' else '<')}tau"}
    table: dict[str, Any] = {'version': 1, 'router': router_name, 'model_id': model_id, 'threshold': threshold, 'edge_label': edge_label, 'cloud_label': cloud_label, 'missing_policy': missing_policy, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'stats': {'n_total': len(pairs), 'n_cloud': n_cloud, 'cloud_ratio': n_cloud / len(pairs)}, 'routes': routes}
    if extra_meta:
        table['meta'] = dict(extra_meta)
    return table

def _cli_collect_tasks(tasks_dir: Path, category: str | None, task_filter: str | None) -> list[tuple[str, str]]:
    """Walk ``tasks/`` like run_batch does and return ``[(task_id, prompt)]``."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from utils.task_parser import parse_task_md
    if category and category.lower() != 'all':
        roots = [tasks_dir / category]
    else:
        roots = sorted([p for p in tasks_dir.iterdir() if p.is_dir()])
    pat = None
    if task_filter:
        import re as _re
        pat = _re.compile(task_filter)
    out: list[tuple[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for md in sorted(root.glob('*task_*.md')):
            if pat and (not pat.search(md.stem)):
                continue
            try:
                t = parse_task_md(md)
            except Exception as exc:
                logger.warning('skip %s: %s', md, exc)
                continue
            out.append((t['task_id'], t['prompt']))
    return out

def score_with_routellm(tasks, checkpoint=DEFAULT_MF_MODEL):
    from routellm.routers.routers import MatrixFactorizationRouter
    router = MatrixFactorizationRouter(checkpoint_path=checkpoint)
    return [(tid, float(router.calculate_strong_win_rate(prompt))) for (tid, prompt) in tasks]

def main(argv=None):
    parser = argparse.ArgumentParser(description='Build an external Query Routing table with RouteLLM MF.')
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--tasks-dir', type=Path, required=True)
    build.add_argument('--category', default='all')
    build.add_argument('--task-filter', default=None)
    build.add_argument('--mf-checkpoint', default=DEFAULT_MF_MODEL)
    build.add_argument('--scores-json', type=Path, help='External map of task IDs to RouteLLM MF scores.')
    group = build.add_mutually_exclusive_group(required=True)
    group.add_argument('--threshold', type=float)
    group.add_argument('--target-cloud-ratio', type=float)
    build.add_argument('--calibration-tasks-dir', type=Path)
    build.add_argument('--calibration-category', default='all')
    build.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.target_cloud_ratio is not None and args.calibration_tasks_dir is None:
        parser.error('--target-cloud-ratio requires a separate --calibration-tasks-dir')
    pairs = _cli_collect_tasks(args.tasks_dir, args.category, args.task_filter)
    if not pairs:
        parser.error('No evaluation tasks matched')
    score_map = json.loads(args.scores_json.read_text()) if args.scores_json else None

    def score(tasks):
        if score_map is not None:
            return [(tid, float(score_map[tid])) for (tid, _) in tasks]
        return score_with_routellm(tasks, args.mf_checkpoint)
    threshold = args.threshold
    if args.target_cloud_ratio is not None:
        calibration = _cli_collect_tasks(args.calibration_tasks_dir, args.calibration_category, None)
        if not calibration:
            parser.error('No calibration tasks matched')
        if {tid for (tid, _) in pairs} & {tid for (tid, _) in calibration}:
            parser.error('Calibration and evaluation task IDs must be disjoint')
        threshold = calibrate_threshold_for_target_ratio([s for (_, s) in score(calibration)], args.target_cloud_ratio)
    table = build_route_table_from_scores(score(pairs), threshold=threshold, model_id=args.mf_checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(table, ensure_ascii=False, indent=2) + '\n')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
