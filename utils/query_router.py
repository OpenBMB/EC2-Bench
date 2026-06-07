"""
Route table file format
-----------------------
.. code-block:: json
    {
        "version": 1,
        "router": "routellm-mf",
        "model_id": "routellm/mf_gpt4_augmented",
        "threshold": 0.11593,
        "edge_label": "Qwen3.5-9B",
        "cloud_label": "gpt-5.4",
        "missing_policy": "fail",   // fail | local | cloud
        "generated_at": "2026-05-06T07:14:00Z",
        "routes": {
            "task_1_xxx": {
                "decision": "LOCAL",
                "score": 0.0421,
                "threshold": 0.11593,
                "reason": "mf_score<=tau"
            }
        }
    }

``score`` is whatever the underlying router returned (RouteLLM MF returns the
"strong-model win-rate"). ``decision`` is the materialized binary route. We
keep both so a downstream re-calibration only needs to re-binarize, not
re-score.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

# Default location run_batch.py looks at when --query-router-table is unset.
DEFAULT_ROUTE_TABLE = Path(__file__).resolve().parent / "query_router_routes.json"

# RouteLLM's published MF checkpoints. Augmented variant is the one used in
# the paper's main results table; non-augmented is included for ablation.
DEFAULT_MF_MODEL = "routellm/mf_gpt4_augmented"
ALT_MF_MODELS = (
    "routellm/mf_gpt4_augmented",
    "routellm/mf_gpt4",
)

# Threshold calibrated by RouteLLM authors for ~50% cloud ratio on Arena;
# we expose it only as a starting point — recalibrate per benchmark via
# :func:`calibrate_threshold_for_target_ratio`.
DEFAULT_THRESHOLD = 0.11593


# ---------------------------------------------------------------------------
# Runtime helpers (imported by run_batch.py — keep stdlib-only)
# ---------------------------------------------------------------------------


def load_route_table(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Load the offline route table.

    Raises ``FileNotFoundError`` if the file is missing, ``ValueError`` if the
    schema is broken. We intentionally do *not* silently fall back, mirroring
    the failure-loud philosophy used by ``step-router``.
    """
    if path is None:
        path = DEFAULT_ROUTE_TABLE
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"query-router route table not found: {p}. "
            f"Generate it first with `python -m utils.query_router build ...`."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"query-router route table is not valid JSON ({p}): {exc}") from exc
    if not isinstance(data, dict) or "routes" not in data or not isinstance(data["routes"], dict):
        raise ValueError(
            f"query-router route table missing 'routes' dict ({p}); got keys={list(data) if isinstance(data, dict) else type(data).__name__}"
        )
    data.setdefault("missing_policy", "fail")
    data.setdefault("threshold", DEFAULT_THRESHOLD)
    data.setdefault("router", "routellm-mf")
    return data


def _normalize_task_id(task_id: str) -> str:
    """Normalize a task id for lookup.

    run_batch.py passes the full task id (e.g.
    ``07_Privacy_0423_task_42_email_chat_triage``). We keep the full id as the
    primary key but also tolerate the shortened ``<category-prefix>_task_<n>``
    form to make ad-hoc table editing easier.
    """
    return (task_id or "").strip()


def decide(task_id: str, table: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return ``(decision, reason, info)`` for ``task_id``.

    ``decision`` is either ``"LOCAL"`` or ``"CLOUD"``. ``reason`` is a short
    human-readable string. ``info`` carries the raw route entry (score,
    threshold, etc.) for logging.

    Behaviour when ``task_id`` is missing from the table is governed by
    ``table["missing_policy"]``:
        * ``"fail"``  (default) — raises :class:`KeyError`.
        * ``"local"`` — returns ``LOCAL`` with reason ``"missing_default_local"``.
        * ``"cloud"`` — returns ``CLOUD`` with reason ``"missing_default_cloud"``.
    """
    routes = table.get("routes") or {}
    tid = _normalize_task_id(task_id)
    entry = routes.get(tid)

    if entry is None:
        # Be lenient on the short form so users can hand-edit the table.
        for k, v in routes.items():
            if k.endswith(tid) or tid.endswith(k):
                entry = v
                break

    if entry is None:
        policy = (table.get("missing_policy") or "fail").lower()
        if policy == "local":
            return "LOCAL", "missing_default_local", {"task_id": tid, "table_threshold": table.get("threshold")}
        if policy == "cloud":
            return "CLOUD", "missing_default_cloud", {"task_id": tid, "table_threshold": table.get("threshold")}
        raise KeyError(
            f"query-router: task_id {tid!r} not found in route table "
            f"({len(routes)} entries, missing_policy=fail). "
            f"Either rebuild the table or set missing_policy=local|cloud."
        )

    raw_decision = str(entry.get("decision", "")).upper().strip()
    if raw_decision not in ("LOCAL", "CLOUD"):
        raise ValueError(
            f"query-router: route entry for {tid!r} has invalid decision "
            f"{entry.get('decision')!r}; expected LOCAL or CLOUD."
        )
    reason = str(entry.get("reason") or f"routellm-mf score={entry.get('score')!r} τ={entry.get('threshold', table.get('threshold'))!r}")
    return raw_decision, reason, dict(entry)


def build_router_usage_record(decision: str, reason: str, info: dict[str, Any]) -> dict[str, Any]:
    """Build the per-task ``router_usage.json`` for query-router.

    Same envelope as the step-router judge bucket (``judge`` / ``redact`` /
    ``total``) so downstream tooling (grading.py / rebuild_summary.py) can
    ingest it without a special-case branch. Query-router makes *no* online
    calls at run time, so all token counts are zero — the cost of this
    router is paid offline at build time and is recorded inside the
    route-table file itself.
    """
    zero = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "judge": dict(zero),
        "redact": dict(zero),
        "total": dict(zero),
        "router": "routellm-mf-query-level",
        "decision": decision,
        "reason": reason,
        "score": info.get("score"),
        "threshold": info.get("threshold"),
        "note": "Query-level routing decision pre-computed offline; "
                "no per-task cloud/judge call at run time.",
    }


# ---------------------------------------------------------------------------
# Offline builders (lazy imports for routellm / torch; safe to import this
# module from run_batch.py even if those packages are not installed)
# ---------------------------------------------------------------------------


def calibrate_threshold_for_target_ratio(
    scores: Sequence[float],
    target_cloud_ratio: float,
) -> float:
    """Pick the threshold τ such that exactly ``target_cloud_ratio`` of the
    queries score *above* τ (i.e., would be routed to CLOUD).

    This is the same calibration RouteLLM uses for its public benchmarks: the
    raw MF score has no calibrated meaning across model pairs, so we pin τ at
    the empirical quantile that yields the desired cloud-call rate.
    """
    if not 0.0 < target_cloud_ratio < 1.0:
        raise ValueError(
            f"target_cloud_ratio must be in (0, 1), got {target_cloud_ratio!r}"
        )
    sorted_scores = sorted(float(s) for s in scores)
    if not sorted_scores:
        raise ValueError("calibrate_threshold_for_target_ratio: empty scores")
    # We want fraction strictly > τ to equal target, so τ is the
    # (1-target)-quantile.
    idx = int(round((1.0 - target_cloud_ratio) * (len(sorted_scores) - 1)))
    idx = max(0, min(len(sorted_scores) - 1, idx))
    return sorted_scores[idx]


def build_route_table_from_scores(
    task_scores: Iterable[tuple[str, float]],
    *,
    threshold: float | None = None,
    target_cloud_ratio: float | None = None,
    edge_label: str = "edge",
    cloud_label: str = "cloud",
    router_name: str = "routellm-mf",
    model_id: str = DEFAULT_MF_MODEL,
    missing_policy: str = "fail",
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize a route table dict from ``(task_id, score)`` pairs.

    Either ``threshold`` *or* ``target_cloud_ratio`` must be set (not both).
    ``score > threshold`` ⇒ CLOUD, else LOCAL — matches RouteLLM's binary
    route convention where the MF score represents the strong model's
    estimated win-rate.
    """
    if (threshold is None) == (target_cloud_ratio is None):
        raise ValueError(
            "build_route_table_from_scores: pass exactly one of threshold or "
            "target_cloud_ratio"
        )

    pairs = [(str(tid), float(s)) for tid, s in task_scores]
    if not pairs:
        raise ValueError("build_route_table_from_scores: no (task_id, score) inputs")

    if target_cloud_ratio is not None:
        threshold = calibrate_threshold_for_target_ratio(
            [s for _, s in pairs], target_cloud_ratio
        )

    routes: dict[str, dict[str, Any]] = {}
    n_cloud = 0
    for tid, score in pairs:
        decision = "CLOUD" if score > threshold else "LOCAL"
        if decision == "CLOUD":
            n_cloud += 1
        routes[tid] = {
            "decision": decision,
            "score": score,
            "threshold": threshold,
            "reason": f"score{'>' if decision == 'CLOUD' else '<='}tau",
        }

    table: dict[str, Any] = {
        "version": 1,
        "router": router_name,
        "model_id": model_id,
        "threshold": threshold,
        "edge_label": edge_label,
        "cloud_label": cloud_label,
        "missing_policy": missing_policy,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "n_total": len(pairs),
            "n_cloud": n_cloud,
            "cloud_ratio": n_cloud / len(pairs),
        },
        "routes": routes,
    }
    if extra_meta:
        table["meta"] = dict(extra_meta)
    return table


def build_route_table_with_routellm(
    tasks: Sequence[tuple[str, str]],
    *,
    strong_model: str,
    weak_model: str,
    router: str = "mf",
    mf_checkpoint: str = DEFAULT_MF_MODEL,
    threshold: float | None = None,
    target_cloud_ratio: float | None = None,
    routellm_config: dict[str, Any] | None = None,
    edge_label: str | None = None,
    cloud_label: str | None = None,
    missing_policy: str = "fail",
) -> dict[str, Any]:
    """Score each ``(task_id, prompt)`` with a RouteLLM ``Controller`` and
    produce a route table.

    ``routellm`` is imported lazily so this module stays importable on hosts
    without that dependency. To use this builder install:
        pip install "routellm[serve,eval]"
    """
    try:
        from routellm.controller import Controller  # type: ignore
    except Exception as exc:  # pragma: no cover - environmental
        raise RuntimeError(
            "build_route_table_with_routellm requires the `routellm` package. "
            "Install with: pip install 'routellm[serve,eval]'\n"
            f"Original import error: {exc!r}"
        ) from exc

    cfg = dict(routellm_config or {})
    cfg.setdefault(router, {}).setdefault("checkpoint_path", mf_checkpoint)

    controller = Controller(
        routers=[router],
        strong_model=strong_model,
        weak_model=weak_model,
        config=cfg,
    )

    task_scores: list[tuple[str, float]] = []
    routed_pairs: list[tuple[str, str]] = []
    # RouteLLM exposes a per-router scoring callable on the controller. The
    # exact attribute name has shifted across versions; try the documented
    # paths in order.
    score_fn = None
    for attr in ("calculate_strong_win_rate", "win_rate"):
        if hasattr(controller, attr):
            score_fn = getattr(controller, attr)
            break
    if score_fn is None:
        # Fallback: emulate via ``route`` at a sweep of thresholds. This is
        # slow but provider-agnostic; only used when the routellm API differs.
        logger.warning(
            "routellm Controller has no win_rate/calculate_strong_win_rate; "
            "falling back to 0/1 scores via route()."
        )

        def _binary_score(prompt: str) -> float:
            chosen = controller.route(prompt=prompt, router=router, threshold=0.5)
            return 1.0 if chosen == strong_model else 0.0

        score_fn = _binary_score

    for tid, prompt in tasks:
        try:
            s = float(score_fn(prompt))
        except TypeError:
            # Some routellm versions take (router, prompt) ordering.
            s = float(score_fn(router, prompt))
        task_scores.append((tid, s))
        routed_pairs.append((tid, ""))  # placeholder for parity, decision filled later

    table = build_route_table_from_scores(
        task_scores,
        threshold=threshold,
        target_cloud_ratio=target_cloud_ratio,
        edge_label=edge_label or weak_model,
        cloud_label=cloud_label or strong_model,
        router_name=f"routellm-{router}",
        model_id=mf_checkpoint,
        missing_policy=missing_policy,
        extra_meta={
            "strong_model": strong_model,
            "weak_model": weak_model,
            "routellm_router": router,
        },
    )
    return table


# ---------------------------------------------------------------------------
# CLI — `python -m utils.query_router build ...`
# ---------------------------------------------------------------------------


def _cli_collect_tasks(tasks_dir: Path, category: str | None,
                       task_filter: str | None) -> list[tuple[str, str]]:
    """Walk ``tasks/`` like run_batch does and return ``[(task_id, prompt)]``."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from utils.task_parser import parse_task_md  # local import to avoid cycles

    if category and category.lower() != "all":
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
        for md in sorted(root.glob("*task_*.md")):
            if pat and not pat.search(md.stem):
                continue
            try:
                t = parse_task_md(md)
            except Exception as exc:
                logger.warning("skip %s: %s", md, exc)
                continue
            out.append((t["task_id"], t["prompt"]))
    return out


def _cli_build(args: argparse.Namespace) -> int:
    tasks_dir = Path(args.tasks_dir).expanduser().resolve()
    if not tasks_dir.is_dir():
        logger.error("tasks-dir not found: %s", tasks_dir)
        return 2

    pairs = _cli_collect_tasks(tasks_dir, args.category, args.task_filter)
    if not pairs:
        logger.error("No tasks matched (category=%s, filter=%s)",
                     args.category, args.task_filter)
        return 2
    logger.info("Collected %d tasks from %s", len(pairs), tasks_dir)

    # --------------------------------------------------------------------
    # Optional: calibrate the threshold on a SEPARATE set of tasks (typically
    # the training tasks) instead of the test tasks given by ``--tasks-dir``.
    # Use case: pick τ such that 20% of training tasks would be routed CLOUD,
    # then apply that fixed τ to the test set (where the actual cloud ratio
    # may differ due to distribution shift).
    # --------------------------------------------------------------------
    def _score_with_irt(score_pairs):
        from utils.irt_router.train import predict_with_irt_router  # lazy
        return predict_with_irt_router(
            args.irt_checkpoint, score_pairs,
            strong_model=args.strong_model, weak_model=args.weak_model,
        )

    def _score_with_local_mf(score_pairs):
        from utils.irt_router.routellm_mf.train import predict_with_mf_router  # lazy
        return predict_with_mf_router(
            args.local_mf_checkpoint, score_pairs,
            strong_model=args.strong_model, weak_model=args.weak_model,
        )

    def _calibrate_on_tasks(score_pairs):
        """Run the chosen scorer on calibration tasks, return τ such that
        ``target_cloud_ratio`` of them would be CLOUD."""
        if args.irt_checkpoint:
            cal_scored = _score_with_irt(score_pairs)
        elif args.local_mf_checkpoint:
            cal_scored = _score_with_local_mf(score_pairs)
        elif args.scores_json:
            score_map = json.loads(
                Path(args.scores_json).expanduser().read_text(encoding="utf-8")
            )
            cal_scored = [(tid, float(score_map[tid]))
                          for tid, _ in score_pairs if tid in score_map]
            if len(cal_scored) < len(score_pairs):
                logger.warning(
                    "calibration: scores-json missing %d/%d tasks",
                    len(score_pairs) - len(cal_scored), len(score_pairs),
                )
        else:
            raise RuntimeError(
                "--calibration-tasks-dir not supported with the RouteLLM "
                "scoring path yet (only --irt-checkpoint / "
                "--local-mf-checkpoint / --scores-json)."
            )
        cal_scores = [s for _, s in cal_scored]
        if not cal_scores:
            raise RuntimeError("calibration scorer produced no scores")
        return calibrate_threshold_for_target_ratio(cal_scores, args.target_cloud_ratio), cal_scores

    cal_threshold = None
    cal_meta: dict[str, Any] = {}
    if args.calibration_tasks_dir:
        if args.target_cloud_ratio is None:
            logger.error("--calibration-tasks-dir requires --target-cloud-ratio "
                         "(τ is calibrated to hit that ratio on the calibration set).")
            return 2
        if args.threshold is not None:
            logger.error("--calibration-tasks-dir is incompatible with explicit "
                         "--threshold (the calibration step is what computes τ).")
            return 2
        cal_dir = Path(args.calibration_tasks_dir).expanduser().resolve()
        if not cal_dir.is_dir():
            logger.error("calibration-tasks-dir not found: %s", cal_dir)
            return 2
        cal_pairs = _cli_collect_tasks(
            cal_dir, args.calibration_category or "all", None
        )
        if not cal_pairs:
            logger.error("No calibration tasks matched (cat=%s)",
                         args.calibration_category)
            return 2
        logger.info("Calibrating τ on %d tasks from %s (target=%.0f%% CLOUD) ...",
                    len(cal_pairs), cal_dir, args.target_cloud_ratio * 100)
        cal_threshold, cal_scores = _calibrate_on_tasks(cal_pairs)
        n_cloud_cal = sum(1 for s in cal_scores if s >= cal_threshold)
        logger.info("Calibration: τ=%.5f → %d/%d cloud (%.1f%%) on calibration set",
                    cal_threshold, n_cloud_cal, len(cal_scores),
                    100 * n_cloud_cal / len(cal_scores))
        cal_meta = {
            "calibration_tasks_dir": str(cal_dir),
            "calibration_category": args.calibration_category or "all",
            "calibration_n_tasks": len(cal_scores),
            "calibration_target_ratio": args.target_cloud_ratio,
            "calibration_n_cloud": n_cloud_cal,
            "calibration_actual_ratio": n_cloud_cal / len(cal_scores),
        }

    # Effective threshold/ratio passed downstream: if we calibrated, force
    # the fixed τ and clear the ratio (downstream will not re-binary-search).
    eff_threshold = cal_threshold if cal_threshold is not None else args.threshold
    eff_target_ratio = None if cal_threshold is not None else args.target_cloud_ratio

    if args.irt_checkpoint:
        # Score every task with a locally-trained MIRT (IRT-Router) checkpoint.
        # See utils/irt_router/train.py.
        if not args.strong_model or not args.weak_model:
            logger.error(
                "--irt-checkpoint requires --strong-model and --weak-model "
                "(which two models do you want to route between?)."
            )
            return 2
        logger.info("scoring %d tasks with IRT-Router checkpoint %s ...",
                    len(pairs), args.irt_checkpoint)
        scored = _score_with_irt(pairs)
        ckpt_path = Path(args.irt_checkpoint).expanduser().resolve()
        meta_path = ckpt_path.with_suffix(ckpt_path.suffix + ".meta.json")
        try:
            ckpt_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            ckpt_meta = {}
        table = build_route_table_from_scores(
            scored,
            threshold=eff_threshold,
            target_cloud_ratio=eff_target_ratio,
            edge_label=args.edge_label or args.weak_model,
            cloud_label=args.cloud_label or args.strong_model,
            router_name=args.router_name or "irt-router-mirt",
            model_id=str(ckpt_path),
            missing_policy=args.missing_policy,
            extra_meta={
                "source": "irt-router-mirt",
                "irt_checkpoint": str(ckpt_path),
                "encoder": ckpt_meta.get("encoder"),
                "latent_dim": ckpt_meta.get("latent_dim"),
                "strong_model": args.strong_model,
                "weak_model": args.weak_model,
                "score_meaning": "P(strong correct) - P(weak correct)",
                **cal_meta,
            },
        )
    elif args.local_mf_checkpoint:
        # Score every task with a locally-trained RouteLLM-MF checkpoint.
        # See utils/irt_router/routellm_mf/train.py.
        if not args.strong_model or not args.weak_model:
            logger.error(
                "--local-mf-checkpoint requires --strong-model and "
                "--weak-model (which two models do you want to route between?)."
            )
            return 2
        logger.info("scoring %d tasks with local MF checkpoint %s ...",
                    len(pairs), args.local_mf_checkpoint)
        scored = _score_with_local_mf(pairs)
        ckpt_path = Path(args.local_mf_checkpoint).expanduser().resolve()
        meta_path = ckpt_path.with_suffix(ckpt_path.suffix + ".meta.json")
        try:
            ckpt_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            ckpt_meta = {}
        table = build_route_table_from_scores(
            scored,
            threshold=eff_threshold,
            target_cloud_ratio=eff_target_ratio,
            edge_label=args.edge_label or args.weak_model,
            cloud_label=args.cloud_label or args.strong_model,
            router_name=args.router_name or "routellm-mf-local",
            model_id=str(ckpt_path),
            missing_policy=args.missing_policy,
            extra_meta={
                "source": "routellm-mf-local",
                "local_mf_checkpoint": str(ckpt_path),
                "encoder": ckpt_meta.get("encoder"),
                "dim": ckpt_meta.get("dim"),
                "strong_model": args.strong_model,
                "weak_model": args.weak_model,
                "score_meaning": "sigmoid(delta_strong - delta_weak) - 0.5",
                **cal_meta,
            },
        )
    elif args.scores_json:
        scores_path = Path(args.scores_json).expanduser()
        score_map = json.loads(scores_path.read_text(encoding="utf-8"))
        if not isinstance(score_map, dict):
            logger.error("--scores-json must be a {task_id: score} object")
            return 2
        scored = [(tid, float(score_map[tid])) for tid, _ in pairs if tid in score_map]
        missing = [tid for tid, _ in pairs if tid not in score_map]
        if missing:
            logger.warning("scores file missing %d tasks (will be excluded): %s",
                           len(missing), missing[:5])
        table = build_route_table_from_scores(
            scored,
            threshold=eff_threshold,
            target_cloud_ratio=eff_target_ratio,
            edge_label=args.edge_label or args.weak_model or "edge",
            cloud_label=args.cloud_label or args.strong_model or "cloud",
            router_name=args.router_name or "scores-json",
            model_id=args.mf_checkpoint,
            missing_policy=args.missing_policy,
            extra_meta={"source": "scores_json", "scores_path": str(scores_path),
                        **cal_meta},
        )
    else:
        if not args.strong_model or not args.weak_model:
            logger.error(
                "Without --scores-json you must pass --strong-model and "
                "--weak-model so RouteLLM can score the tasks."
            )
            return 2
        table = build_route_table_with_routellm(
            pairs,
            strong_model=args.strong_model,
            weak_model=args.weak_model,
            router=args.routellm_router,
            mf_checkpoint=args.mf_checkpoint,
            threshold=args.threshold,
            target_cloud_ratio=args.target_cloud_ratio,
            edge_label=args.edge_label,
            cloud_label=args.cloud_label,
            missing_policy=args.missing_policy,
        )

    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, indent=2, ensure_ascii=False), encoding="utf-8")
    stats = table.get("stats", {})
    logger.info(
        "Wrote route table → %s | n_total=%s n_cloud=%s ratio=%.3f τ=%.5f",
        out, stats.get("n_total"), stats.get("n_cloud"),
        stats.get("cloud_ratio") or 0.0, table.get("threshold") or 0.0,
    )
    return 0


# ---------------------------------------------------------------------------
# `collect-scores` CLI — turn benchmark output dirs into router training CSV
# ---------------------------------------------------------------------------

# Patterns that distinguish "pure single-model" runs from collaborative ones.
# We only collect single-model runs because their score is a clean per-model
# performance signal; mixed modes (advisor/router) reflect collaboration
# strategy, not raw model capability.
_SINGLE_MODEL_MODE_SUFFIXES = ("cloud-only", "local-only", "edge-only")


def _parse_model_subdir(name: str) -> tuple[str, str] | None:
    """Parse a per-task subdir name like ``gpt-5.4_cloud-only_run2`` into
    ``(model_name, mode)``. Returns ``None`` if the dir doesn't match a
    pure single-model run (e.g. it's an advisor / router subdir).
    """
    import re as _re
    n = _re.sub(r"_run\d+$", "", name).strip()
    for suffix in _SINGLE_MODEL_MODE_SUFFIXES:
        if n.endswith("_" + suffix):
            return n[: -(len(suffix) + 1)], suffix
    # 兜底：纯 model-name 目录（旧版 local-only 不带后缀），例如 `Qwen3.5-9B/`
    # 这里只在调用方明确允许 bare 目录时使用，所以默认返回 None。
    return None


def _read_score_json(score_path: Path) -> float | None:
    """Read ``score.json`` and return the canonical ``overall_score``."""
    try:
        data = json.loads(score_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("score.json unreadable %s: %s", score_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    s = data.get("overall_score")
    if s is None:
        return None
    try:
        s = float(s)
    except (TypeError, ValueError):
        return None
    if 0.0 <= s <= 1.0:
        return s
    # Some grading paths emit 0..100 → normalize.
    if 0.0 <= s <= 100.0:
        return s / 100.0
    logger.warning("score out of expected range: %s (%s)", s, score_path)
    return None


def _collect_scores_from_run(run_dir: Path,
                             allow_bare: bool,
                             bare_model_filter: set[str] | None,
                             ) -> list[tuple[str, str, float]]:
    """Walk one ``07_Privacy_0423-<ts>/`` style run dir and yield
    ``(task_id, model, score)`` triples for every per-task score.json.
    """
    out: list[tuple[str, str, float]] = []
    if not run_dir.is_dir():
        return out

    for task_dir in sorted(run_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name  # 已确认 == YAML frontmatter id
        for sub in sorted(task_dir.iterdir()):
            if not sub.is_dir():
                continue
            parsed = _parse_model_subdir(sub.name)
            if parsed is None:
                if allow_bare and (
                    bare_model_filter is None or sub.name in bare_model_filter
                ):
                    model = sub.name
                else:
                    continue
            else:
                model, _mode = parsed
            sj = sub / "score.json"
            if not sj.is_file():
                continue
            s = _read_score_json(sj)
            if s is None:
                continue
            out.append((task_id, model, s))
    return out



def _build_short_to_canonical_task_map(tasks_dir: Path) -> dict[str, str]:
    """Walk ``tasks_dir`` and build ``{cat_prefix}_task_{n}`` → canonical ``id``.

    Matches the short form used by ``summary_all_*.json`` (e.g. ``01_task_2``)
    against the YAML ``id`` field of each task md (e.g.
    ``01_Productivity_Flow_task_2_table_tex_download``). Category prefix is the
    leading numeric segment of the directory name (``01`` from
    ``01_Productivity_Flow``).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from utils.task_parser import parse_task_md  # local import

    import re as _re
    out: dict[str, str] = {}
    if not tasks_dir.is_dir():
        return out
    for cat_dir in sorted(tasks_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        m = _re.match(r"^(\d+)_", cat_dir.name)
        if not m:
            continue
        cat_prefix = m.group(1)
        for md in sorted(cat_dir.glob("*task_*.md")):
            tnum = _re.search(r"_task_(\d+)_", md.stem)
            if not tnum:
                continue
            short = f"{cat_prefix}_task_{tnum.group(1)}"
            try:
                t = parse_task_md(md)
                out[short] = t["task_id"]
            except Exception as exc:
                logger.warning("skip %s: %s", md, exc)
    return out


def _parse_orig_result_task_id(raw: str, model_hint: str | None) -> str | None:
    """Extract the canonical short task id (``01_task_2``) from a
    summary_all result's ``task_id`` field, which looks like
    ``01_task_2_glm-5.1_20260428_1028_2917ad``.

    Approach: regex-match the leading ``\\d+_task_\\d+`` prefix.
    """
    import re as _re
    m = _re.match(r"^(\d+_task_\d+)(?:_|$)", raw or "")
    return m.group(1) if m else None


def _collect_scores_from_orig_summary(
    summary_path: Path, model_name: str
) -> list[tuple[str, str, float]]:
    try:
        d = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("can't parse %s: %s", summary_path, exc)
        return []
    results = d.get("results") or []
    out: list[tuple[str, str, float]] = []
    skipped = 0
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("error"):
            skipped += 1
            continue
        scores = r.get("scores") or {}
        if not isinstance(scores, dict):
            skipped += 1
            continue
        s = scores.get("overall_score")
        if s is None:
            skipped += 1
            continue
        try:
            s = float(s)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not (0.0 <= s <= 1.0):
            if 0.0 <= s <= 100.0:
                s = s / 100.0
            else:
                skipped += 1
                continue
        short = _parse_orig_result_task_id(str(r.get("task_id") or ""), model_name)
        if short is None:
            skipped += 1
            continue
        out.append((short, model_name, s))
    if skipped:
        logger.info("  (%d results skipped: error/null score/unparseable)", skipped)
    return out


def _cli_collect_scores(args: argparse.Namespace) -> int:
    import csv as _csv

    bare_filter: set[str] | None = None
    if args.allow_bare_model_dirs:
        if args.bare_model_filter:
            bare_filter = {m.strip() for m in args.bare_model_filter.split(",") if m.strip()}
        else:
            bare_filter = None  # take any subdir as a model name

    triples: list[tuple[str, str, float]] = []
    for scan_root in (args.scan_dir or []):
        root = Path(scan_root).expanduser().resolve()
        if not root.is_dir():
            logger.warning("scan-dir not found, skipping: %s", root)
            continue
        # A scan_dir may itself be one run dir (contains task subdirs) or a
        # parent directory that contains many ``<run-tag>/`` subdirs. Auto-
        # detect: if any direct child looks like a task dir (ends with
        # ``_task_<n>_*``), treat root as a run dir.
        children = [p for p in root.iterdir() if p.is_dir()]
        looks_like_run = any("_task_" in c.name for c in children)
        run_dirs = [root] if looks_like_run else children

        for rd in run_dirs:
            new_triples = _collect_scores_from_run(
                rd, args.allow_bare_model_dirs, bare_filter
            )
            if new_triples:
                logger.info("  %s → +%d triples", rd, len(new_triples))
            triples.extend(new_triples)

    orig_short_triples: list[tuple[str, str, float]] = []
    for spec in (args.orig_summary or []):
        if "::" not in spec:
            logger.error("--orig-summary expects 'PATH::MODEL_NAME', got: %s", spec)
            return 2
        path_str, model_name = spec.rsplit("::", 1)
        sp = Path(path_str).expanduser().resolve()
        if not sp.is_file():
            logger.warning("orig-summary not found, skipping: %s", sp)
            continue
        new_triples = _collect_scores_from_orig_summary(sp, model_name.strip())
        logger.info("  %s [%s] → +%d triples", sp.name, model_name, len(new_triples))
        orig_short_triples.extend(new_triples)

    if not triples and not orig_short_triples:
        logger.error("No (task, model, score) triples collected. "
                     "Check --scan-dir / --orig-summary.")
        return 2

    # ---- merge orig (short-form task ids) into triples after canonicalizing ----
    tasks_dir = Path(args.tasks_dir).expanduser().resolve()
    if not tasks_dir.is_dir():
        logger.error("tasks-dir not found: %s", tasks_dir)
        return 2
    if orig_short_triples:
        short2canon = _build_short_to_canonical_task_map(tasks_dir)
        logger.info("orig short→canonical map size: %d", len(short2canon))
        unmatched_shorts: set[str] = set()
        for short, m, s in orig_short_triples:
            canon = short2canon.get(short)
            if canon is None:
                unmatched_shorts.add(short)
                continue
            triples.append((canon, m, s))
        if unmatched_shorts:
            logger.warning("dropped %d orig triples with no canonical task_id: %s",
                           len(unmatched_shorts), sorted(unmatched_shorts)[:5])

    # Optional aggregation: same (task, model) appears multiple times across
    # runs (avg@N) → average the scores.
    if args.aggregate == "mean":
        agg: dict[tuple[str, str], list[float]] = {}
        for tid, m, s in triples:
            agg.setdefault((tid, m), []).append(s)
        triples = [(tid, m, sum(vs) / len(vs)) for (tid, m), vs in agg.items()]
        logger.info("Aggregated to %d unique (task,model) pairs by mean.", len(triples))

    prompt_pairs = _cli_collect_tasks(tasks_dir, args.category, args.task_filter)
    prompt_lookup = {tid: pr for tid, pr in prompt_pairs}

    rows: list[dict[str, Any]] = []
    skipped_no_prompt = 0
    for tid, m, s in triples:
        prompt = prompt_lookup.get(tid)
        if prompt is None:
            skipped_no_prompt += 1
            continue
        rows.append({"task_id": tid, "llm": m, "question": prompt, "performance": s})
    if skipped_no_prompt:
        logger.warning("Dropped %d triples (task_id not found in tasks_dir).",
                       skipped_no_prompt)
    if not rows:
        logger.error("All triples had unmatched task_ids; nothing to write.")
        return 2

    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["task_id", "llm", "question", "performance"])
        w.writeheader()
        w.writerows(rows)

    n_models = len({r["llm"] for r in rows})
    n_tasks = len({r["task_id"] for r in rows})
    logger.info(
        "Wrote %d rows → %s (%d unique tasks × %d models)",
        len(rows), out, n_tasks, n_models,
    )
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="utils.query_router",
        description="Reproduce RouteLLM's MF Router at the query/task level "
                    "and materialize routing decisions into an offline JSON "
                    "table consumed by `run_batch.py --run-mode query-router`.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- collect-scores ----
    c = sub.add_parser(
        "collect-scores",
        help="Walk benchmark output dirs and produce a `(task_id, llm, "
             "question, performance)` CSV ready for IRT-Router training.",
    )
    c.add_argument("--scan-dir", action="append", default=None,
                   help="Output directory to scan (per-task subdir layout, "
                        "modified AceBench style). May be a single run dir "
                        "(e.g. .../07_Privacy_0423-2026.../) or a parent "
                        "containing many such run dirs. Pass multiple times.")
    c.add_argument("--orig-summary", action="append", default=None,
                   help="Path::MODEL_NAME spec for an upstream-style "
                        "summary_all_*.json file (orig AceBench layout). "
                        "Example: --orig-summary "
                        "'output/0_3x_timeout/summary_all_yeysai_glm-5.1_avg@3.json::glm-5.1'. "
                        "Pass multiple times. At least one of --scan-dir or "
                        "--orig-summary is required.")
    c.add_argument("--tasks-dir",
                   default=str(Path(__file__).resolve().parent.parent / "tasks"),
                   help="Path to the tasks/ directory (used to look up prompts).")
    c.add_argument("--category", default="all",
                   help="Category subdir under tasks-dir, or 'all'.")
    c.add_argument("--task-filter", default=None,
                   help="Optional regex on task md filename stem.")
    c.add_argument("--output", required=True,
                   help="Path for the resulting CSV.")
    c.add_argument("--aggregate", choices=["mean", "none"], default="mean",
                   help="When the same (task,model) appears in multiple runs "
                        "(e.g. avg@N), average the scores. Default: mean.")
    c.add_argument("--allow-bare-model-dirs", action="store_true",
                   help="Treat task subdirectories without a *_cloud-only/"
                        "_local-only/_edge-only suffix as bare model dirs "
                        "(legacy local-only layout). Use with care.")
    c.add_argument("--bare-model-filter", default=None,
                   help="Comma-separated whitelist of bare model dir names; "
                        "implies --allow-bare-model-dirs.")
    c.set_defaults(func=_cli_collect_scores)

    # ---- build ----
    b = sub.add_parser("build", help="Build a route table for a task set.")
    b.add_argument("--tasks-dir", default=str(Path(__file__).resolve().parent.parent / "tasks"))
    b.add_argument("--category", default="all")
    b.add_argument("--task-filter", default=None)
    b.add_argument("--output", required=True, help="Where to write the route table JSON.")
    b.add_argument("--threshold", type=float, default=None,
                   help="MF score threshold τ. Mutually exclusive with --target-cloud-ratio.")
    b.add_argument("--target-cloud-ratio", type=float, default=None,
                   help="Calibrate τ so the resulting cloud-call ratio equals this value (e.g. 0.20). "
                        "By default τ is calibrated on the same tasks as --tasks-dir; pass "
                        "--calibration-tasks-dir to calibrate on a separate (training) set instead.")
    b.add_argument("--calibration-tasks-dir", default=None,
                   help="Optional separate tasks directory used to calibrate τ. When set, the "
                        "router scores those tasks, picks τ to hit --target-cloud-ratio there, "
                        "then applies the fixed τ to --tasks-dir. Use this to avoid leaking "
                        "test-set statistics into threshold selection (calibrate on train, "
                        "evaluate on test).")
    b.add_argument("--calibration-category", default=None,
                   help="Category subdir filter for --calibration-tasks-dir (default: 'all').")
    b.add_argument("--scores-json", default=None,
                   help="Optional path to a precomputed {task_id: score} JSON; "
                        "skips RouteLLM inference. Useful for offline analysis "
                        "or when running this script on a host without GPUs.")
    b.add_argument("--irt-checkpoint", default=None,
                   help="Path to a MIRT checkpoint produced by "
                        "`python -m utils.irt_router.train train`. When set, "
                        "scores tasks via IRT-Router (replaces RouteLLM MF). "
                        "Requires --strong-model and --weak-model.")
    b.add_argument("--local-mf-checkpoint", default=None,
                   help="Path to a RouteLLM-MF checkpoint produced by "
                        "`python -m utils.irt_router.routellm_mf.train train`. "
                        "When set, scores tasks via a *locally-trained* MF "
                        "(distinct from --mf-checkpoint, which points at the "
                        "upstream HuggingFace MF). Requires --strong-model "
                        "and --weak-model.")
    b.add_argument("--strong-model", default=None,
                   help="Strong-side model id (cloud label). For IRT-Router this "
                        "must be one of the model names seen during training.")
    b.add_argument("--weak-model", default=None,
                   help="Weak-side model id (edge label). For IRT-Router this "
                        "must be one of the model names seen during training.")
    b.add_argument("--routellm-router", default="mf",
                   help="RouteLLM router name (default: mf).")
    b.add_argument("--mf-checkpoint", default=DEFAULT_MF_MODEL,
                   help=f"HuggingFace MF checkpoint (default: {DEFAULT_MF_MODEL}).")
    b.add_argument("--edge-label", default=None)
    b.add_argument("--cloud-label", default=None)
    b.add_argument("--router-name", default=None,
                   help="Override the 'router' tag in the output table. "
                        "Defaults: 'irt-router-mirt' (--irt-checkpoint), "
                        "'routellm-mf-local' (--local-mf-checkpoint), "
                        "'routellm-mf' otherwise.")
    b.add_argument("--missing-policy", default="fail",
                   choices=["fail", "local", "cloud"],
                   help="Behaviour at run time when a task_id is not in the table.")
    b.set_defaults(func=_cli_build)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
