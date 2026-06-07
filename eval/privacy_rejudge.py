from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

try:
    load_dotenv = importlib.import_module("dotenv").load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except Exception:
    pass

from utils.privacy_audit import compute_privacy_score


PRIVACY_KEYS = {
    "privacy_score",
    "privacy_source",
    "privacy_reason",
    "privacy_details",
}

_JUDGE_LOG_LOCK = threading.Lock()


def _truncate_content(text: Any, max_words: int = 100) -> Any:
    if not isinstance(text, str):
        return text
    words = text.split()
    if len(words) <= max_words * 2:
        return text
    return " ".join(words[:max_words]) + " [...TRUNCATED...] " + " ".join(words[-max_words:])


def _truncate_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return messages
    out = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        m = dict(msg)
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = _truncate_content(content)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    p = dict(part)
                    p["text"] = _truncate_content(p["text"])
                    new_parts.append(p)
                else:
                    new_parts.append(part)
            m["content"] = new_parts
        out.append(m)
    return out


@contextlib.contextmanager
def _host_privacy_judge_logging(log_path: Path):
    """Append host-side deferred privacy judge calls to the task llm_judge log."""
    try:
        completions_mod = importlib.import_module("openai.resources.chat.completions")
        Completions = completions_mod.Completions
    except ImportError:
        yield
        return

    orig_create = Completions.create

    def wrapped_create(self, *args, **kwargs):
        started = __import__("time").time()
        err = None
        resp = None
        try:
            resp = orig_create(self, *args, **kwargs)
            return resp
        except Exception as exc:
            err = repr(exc)
            raise
        finally:
            output = None
            if resp is not None:
                try:
                    output = resp.choices[0].message.content
                except Exception:
                    output = None
            record = {
                "type": "privacy",
                "input": {
                    "model": kwargs.get("model"),
                    "messages": _truncate_messages(kwargs.get("messages")),
                    "max_tokens": kwargs.get("max_tokens"),
                },
                "output": output,
                "error": err,
                "elapsed_ms": round((__import__("time").time() - started) * 1000),
            }
            with _JUDGE_LOG_LOCK:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

    Completions.create = wrapped_create
    try:
        yield
    finally:
        Completions.create = orig_create


def _prepare_judge_env(edge_cloud_mode: str | None) -> dict[str, str | None]:
    """Prep host-side env so compute_privacy_score behaves like inline mode.

    Note: ``EDGE_CLOUD_MODE`` is intentionally NOT set here. The deferred
    payload always carries an explicit ``agent_text`` / ``source_name`` pair,
    so privacy_audit short-circuits ``load_cloud_exposed_content()`` and never
    reads ``EDGE_CLOUD_MODE``. The arg is kept for future use / signature
    stability.
    """
    del edge_cloud_mode  # unused, see docstring
    old_env = {
        "PRIVACY_JUDGE_MODE": os.environ.get("PRIVACY_JUDGE_MODE"),
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY"),
        "OPENROUTER_BASE_URL": os.environ.get("OPENROUTER_BASE_URL"),
    }
    os.environ["PRIVACY_JUDGE_MODE"] = "inline"
    if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("JUDGE_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = os.environ["JUDGE_API_KEY"]
    if not os.environ.get("OPENROUTER_BASE_URL") and os.environ.get("JUDGE_BASE_URL"):
        os.environ["OPENROUTER_BASE_URL"] = os.environ["JUDGE_BASE_URL"]
    return old_env


def _restore_env(old_env: dict[str, str | None]) -> None:
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _load_json(path: Path) -> dict[str, Any] | None:
    """Read JSON from disk. Returns None only when the file is absent.

    Other failures (read error, decode error, non-dict payload) are surfaced
    via logger.warning so deferred-judge issues don't disappear silently.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Expected JSON object in %s, got %s", path, type(data).__name__)
        return None
    return data


def _patch_score(score_path: Path, privacy_result: dict[str, Any]) -> dict[str, Any] | None:
    scores = _load_json(score_path)
    if scores is None:
        return None
    for key in PRIVACY_KEYS:
        scores.pop(key, None)
    scores.update(privacy_result)
    score_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8")
    return scores


def rejudge_output_dir(output_dir: str | Path, edge_cloud_mode: str | None = None) -> dict[str, Any]:
    """Run the deferred privacy judge for one task output directory."""
    output_dir = Path(output_dir)
    score_path = output_dir / "score.json"
    payload_path = output_dir / "privacy_judge_deferred.json"
    if not score_path.exists():
        return {"status": "missing_score", "output_dir": str(output_dir)}

    scores = _load_json(score_path)
    if scores is None:
        return {"status": "bad_score_json", "output_dir": str(output_dir)}

    # If the inline grader already resolved privacy locally (no cloud/no access/off),
    # keep it exactly as-is. Deferred payload exists only when an LLM call is needed.
    if not payload_path.exists():
        return {
            "status": "skipped_no_deferred_payload",
            "output_dir": str(output_dir),
            "privacy_score": scores.get("privacy_score"),
            "privacy_source": scores.get("privacy_source"),
        }

    payload = _load_json(payload_path)
    if payload is None:
        privacy_result = {
            "privacy_score": None,
            "privacy_source": "deferred",
            "privacy_reason": "Bad privacy_judge_deferred.json",
            "privacy_details": [],
        }
    else:
        old_env = _prepare_judge_env(edge_cloud_mode)
        try:
            with _host_privacy_judge_logging(output_dir / "llm_judge.jsonl"):
                privacy_result = compute_privacy_score(
                    entities=payload.get("entities"),
                    agent_text=payload.get("agent_text"),
                    source_name=payload.get("source_name"),
                    judge_model=payload.get("judge_model"),
                    weighted=bool(payload.get("weighted", True)),
                    skill_note=str(payload.get("skill_note") or ""),
                )
        finally:
            _restore_env(old_env)

    patched_scores = _patch_score(score_path, privacy_result)
    if patched_scores is None:
        return {"status": "bad_score_json", "output_dir": str(output_dir)}
    return {
        "status": "rejudged",
        "output_dir": str(output_dir),
        "privacy_score": patched_scores.get("privacy_score"),
        "privacy_source": patched_scores.get("privacy_source"),
        "privacy_reason": patched_scores.get("privacy_reason"),
        "scores": patched_scores,
    }


def rejudge_results(results: list[dict], edge_cloud_mode: str | None = None) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for result in results:
        output_dir = result.get("output_dir")
        if not output_dir:
            reports.append({"status": "missing_output_dir", "task_id": result.get("task_id")})
            continue
        report = rejudge_output_dir(output_dir, edge_cloud_mode=edge_cloud_mode)
        if report.get("scores") is not None:
            result["scores"] = report["scores"]
        reports.append(report)
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deferred privacy judge for task output dirs.")
    parser.add_argument("output_dirs", nargs="+", help="Task output directories containing score.json")
    parser.add_argument("--edge-cloud-mode", default=None, help="Mode used by the run, e.g. step-router")
    args = parser.parse_args()

    reports = [rejudge_output_dir(p, edge_cloud_mode=args.edge_cloud_mode) for p in args.output_dirs]
    print(json.dumps(reports, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
