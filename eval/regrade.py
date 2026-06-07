#!/usr/bin/env python3
"""
Re-grade tasks that have chat.jsonl but no score.json (or score with errors).

Reuses existing agent output — only re-runs warmup + grading in a fresh
container, so it's fast and doesn't cost LLM inference tokens (except for
tasks that use LLM-judge grading).

Usage:
    python3 eval/regrade.py output/07_Privacy-20260415_1028
    python3 eval/regrade.py output/07_Privacy-20260415_1028 --dry-run
    python3 eval/regrade.py output/07_Privacy-20260415_1028 --force  # re-grade even if score.json exists
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from utils.task_parser import parse_task_md
from utils.docker_utils import (
    DOCKER_IMAGE, TMP_WORKSPACE,
    remove_container, start_container, run_warmup,
)
from utils.grading import run_grading, format_scores

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT_DIR / os.environ.get("TASKS_SUBDIR", "tasks")


def find_task_md(task_id_ori: str) -> Path | None:
    """Find the task.md file for a given task_id_ori like '07_Privacy_task_36_job_failure_triage'."""
    # Flat layout: task .md directly under TASKS_DIR.
    flat_candidate = TASKS_DIR / f"{task_id_ori}.md"
    if flat_candidate.exists():
        return flat_candidate
    for category_dir in TASKS_DIR.iterdir():
        if not category_dir.is_dir():
            continue
        candidate = category_dir / f"{task_id_ori}.md"
        if candidate.exists():
            return candidate
    return None


def needs_regrade(output_dir: Path, force: bool = False) -> bool:
    has_chat = (output_dir / "chat.jsonl").exists()
    has_agent = (output_dir / "agent.log").exists()
    agent_size = (output_dir / "agent.log").stat().st_size if has_agent else 0
    has_score = (output_dir / "score.json").exists()

    if force and (has_chat or agent_size > 0):
        return True

    if not has_chat and agent_size == 0:
        return False

    if has_score:
        try:
            scores = json.loads((output_dir / "score.json").read_text())
            if "error" in scores:
                return True
        except Exception:
            return True
        return False

    return True


def regrade_one(task_id_ori: str, model_dir: Path, dry_run: bool = False) -> dict | None:
    task_file = find_task_md(task_id_ori)
    if not task_file:
        logger.error("Task file not found for: %s", task_id_ori)
        return None

    task = parse_task_md(task_file)

    if not task.get("automated_checks"):
        logger.info("[%s] No automated_checks, skipping", task_id_ori)
        return None

    if dry_run:
        logger.info("[DRY-RUN] Would regrade: %s", task_id_ori)
        return {"task_id": task_id_ori, "dry_run": True}

    workspace_path = task["workspace_path"]
    exec_path = os.path.join(workspace_path, "exec")
    tmp_path = os.path.join(workspace_path, "tmp")
    gt_path = os.path.join(workspace_path, "gt")
    os.makedirs(exec_path, exist_ok=True)

    container_id = f"regrade_{uuid.uuid4().hex[:8]}"

    try:
        start_container(container_id, exec_path, extra_env=task.get("env", ""),
                        tmp_path=tmp_path)

        warmup = task.get("warmup", "")
        if warmup:
            run_warmup(container_id, warmup)

        if os.path.isdir(gt_path):
            subprocess.run(
                ["docker", "cp", gt_path, f"{container_id}:{TMP_WORKSPACE}/gt"],
                capture_output=True, text=True,
            )
            logger.info("[%s] Copied gt → container", task_id_ori)

        task_output_ws = model_dir / "task_output" / "workspace"
        if task_output_ws.is_dir():
            subprocess.run(
                ["docker", "exec", container_id, "mkdir", "-p", f"{TMP_WORKSPACE}/results"],
                capture_output=True,
            )
            subprocess.run(
                ["docker", "cp", f"{task_output_ws}/.", f"{container_id}:{TMP_WORKSPACE}/"],
                capture_output=True, text=True,
            )
            logger.info("[%s] Copied task_output/workspace → container", task_id_ori)

        chat_host = model_dir / "chat.jsonl"
        if chat_host.exists():
            chat_container = "/root/.openclaw/agents/main/sessions/chat.jsonl"
            subprocess.run(
                ["docker", "exec", container_id, "mkdir", "-p",
                 "/root/.openclaw/agents/main/sessions"],
                capture_output=True,
            )
            subprocess.run(
                ["docker", "cp", str(chat_host), f"{container_id}:{chat_container}"],
                capture_output=True, text=True,
            )
            logger.info("[%s] Copied chat.jsonl → container", task_id_ori)

        scores = run_grading(
            task_id=container_id,
            automated_checks=task["automated_checks"],
            output_dir=model_dir,
        )

        print(format_scores(task_id_ori, scores))
        logger.info("[%s] Re-grading complete → %s", task_id_ori, model_dir / "score.json")
        return {"task_id": task_id_ori, "scores": scores}

    except Exception as exc:
        logger.error("[%s] Re-grading failed: %s", task_id_ori, exc)
        return {"task_id": task_id_ori, "error": str(exc)}

    finally:
        remove_container(container_id)


def main():
    parser = argparse.ArgumentParser(description="Re-grade tasks without re-running agents")
    parser.add_argument("output_root", type=Path, help="Output directory to scan")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--force", action="store_true",
                        help="Re-grade even if score.json exists (unless it has no errors)")
    parser.add_argument("--parallel", "-p", type=int, default=1,
                        help="Number of parallel re-grades (default: 1)")
    parser.add_argument(
        "--task", "-t", action="append", default=[],
        help="Only re-grade tasks whose dir name contains the given substring "
             "(repeatable). E.g. --task task_13 --task task_14",
    )
    parser.add_argument(
        "--judge-failed-only", action="store_true",
        help="Only re-grade tasks whose existing score.json has "
             "'llm_judge_failed' in any *_reason field (implies --force)",
    )
    args = parser.parse_args()

    if not args.output_root.is_dir():
        logger.error("Not a directory: %s", args.output_root)
        sys.exit(1)

    to_regrade: list[tuple[str, Path]] = []
    force_eff = args.force or args.judge_failed_only

    def _judge_failed(score_path: Path) -> bool:
        try:
            d = json.loads(score_path.read_text())
        except Exception:
            return False
        for k, v in d.items():
            if k.endswith("_reason") and isinstance(v, str) and "llm_judge_failed" in v:
                return True
        return False

    for task_dir in sorted(args.output_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id_ori = task_dir.name
        if args.task and not any(t in task_id_ori for t in args.task):
            continue
        for model_dir in sorted(task_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            if args.judge_failed_only:
                sp = model_dir / "score.json"
                if not (sp.exists() and _judge_failed(sp)):
                    continue
            if needs_regrade(model_dir, force=force_eff):
                to_regrade.append((task_id_ori, model_dir))

    if not to_regrade:
        print("No tasks need re-grading.")
        return

    print(f"\nTasks to re-grade: {len(to_regrade)}")
    for tid, md in to_regrade:
        has_chat = "chat" if (md / "chat.jsonl").exists() else "no-chat"
        has_score = "has-score" if (md / "score.json").exists() else "no-score"
        print(f"  {tid}  [{has_chat}] [{has_score}]")
    print()

    success = 0
    fail = 0
    skip = 0

    if args.parallel <= 1:
        for task_id_ori, model_dir in to_regrade:
            result = regrade_one(task_id_ori, model_dir, dry_run=args.dry_run)
            if result is None:
                skip += 1
            elif result.get("error"):
                fail += 1
            else:
                success += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(regrade_one, tid, md, args.dry_run): tid
                for tid, md in to_regrade
            }
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    skip += 1
                elif result.get("error"):
                    fail += 1
                else:
                    success += 1

    print(f"\nDone: {success} success, {fail} fail, {skip} skip")


if __name__ == "__main__":
    main()
