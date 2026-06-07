#!/usr/bin/env python3
"""Write step-router summaries with normalized edge/cloud usage fields.

This is a metadata-only rebuild: it reads existing summary results,
usage.json-derived records, and cloud_assistant_audit usage already captured in
the run directories. It does not rerun tasks or grading.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.grading import (  # noqa: E402
    _cloud_input_as_chat_usage,
    _cloud_raw_input,
    _edge_usage_as_total_usage,
    _is_step_router_model,
    _normalize_step_router_total_usage,
    _usage_note,
)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def total_usage_from_results(results: list[dict[str, Any]], total_tasks: int, repeat: int) -> dict[str, Any]:
    turns = [
        r.get("usage", {}).get("n_turns", r.get("usage", {}).get("request_count", 0))
        for r in results
    ]
    raw = sum(r.get("usage", {}).get("input_tokens", 0) for r in results)
    cache_read = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in results)
    cache_write = sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in results)
    denom_tasks = total_tasks * repeat
    return {
        "total_input_tokens": raw,
        "total_output_tokens": sum(r.get("usage", {}).get("output_tokens", 0) for r in results),
        "total_cache_read_tokens": cache_read,
        "total_cache_write_tokens": cache_write,
        "total_actual_input_tokens": raw + cache_read,
        "total_request_count": sum(r.get("usage", {}).get("request_count", 0) for r in results),
        "total_cost_usd": round(sum(r.get("usage", {}).get("cost_usd", 0.0) for r in results), 6),
        "total_elapsed_time": round(sum(r.get("usage", {}).get("elapsed_time", 0.0) for r in results), 2),
        "total_turns": sum(turns),
        "avg_turns_per_task": round(sum(turns) / denom_tasks, 2) if denom_tasks else 0,
        "max_turns": max(turns) if turns else 0,
        "min_turns": min(turns) if turns else 0,
        "_note": _usage_note(include_cache_write=False),
    }


def split_usage(
    results: list[dict[str, Any]],
    model_name: str,
    total_tasks: int,
    repeat: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    total_usage = total_usage_from_results(results, total_tasks, repeat)
    cloud_tasks = [r for r in results if r.get("cloud_usage")]
    if not cloud_tasks:
        return total_usage, total_usage, None, None

    prompt = sum(r["cloud_usage"].get("prompt_tokens", 0) for r in cloud_tasks)
    completion = sum(r["cloud_usage"].get("completion_tokens", 0) for r in cloud_tasks)
    cloud_tokens = sum(r["cloud_usage"].get("total_tokens", 0) for r in cloud_tasks)
    cache_read = sum(r["cloud_usage"].get("cache_read_tokens", 0) for r in cloud_tasks)
    cache_write = sum(r["cloud_usage"].get("cache_write_tokens", 0) for r in cloud_tasks)
    cloud_req = sum(r["cloud_usage"].get("request_count", 0) for r in cloud_tasks)
    latency = sum(r["cloud_usage"].get("total_latency_ms", 0) for r in cloud_tasks)
    cloud_raw = _cloud_raw_input(prompt, cache_read, cache_write)
    cloud = {
        "source": "audit_jsonl",
        "total_tasks": total_tasks,
        "cloud_call_rate": round((len(cloud_tasks) / repeat) / total_tasks, 4) if total_tasks else 0,
        "avg_cloud_calls_per_task": round(cloud_req / repeat / total_tasks, 2) if total_tasks else 0,
        "tasks_with_cloud_calls": len(cloud_tasks),
        "total_request_count": cloud_req,
        "total_prompt_tokens": prompt,
        "total_raw_input_tokens": cloud_raw,
        "total_cache_read_tokens": cache_read,
        "total_cache_write_tokens": cache_write,
        "total_completion_tokens": completion,
        "total_tokens": cloud_tokens,
        "cloud_token_share": 0,
        "cache_hit_rate": round(cache_read / prompt, 4) if prompt else 0,
        "cache_write_rate": round(cache_write / prompt, 4) if prompt else 0,
        "total_latency_ms": latency,
        "avg_latency_ms": round(latency / cloud_req) if cloud_req else 0,
    }

    if _is_step_router_model(model_name):
        cloud_chat_input = _cloud_input_as_chat_usage(prompt, cache_read)
        edge_input = max(total_usage["total_input_tokens"] - cloud_chat_input, 0)
        edge_cache_read = max(total_usage["total_cache_read_tokens"] - cache_read, 0)
        edge_cache_write = max(total_usage["total_cache_write_tokens"] - cache_write, 0)
        edge_output = max(total_usage["total_output_tokens"] - completion, 0)
        edge_req = max(total_usage["total_request_count"] - cloud_req, 0)
    else:
        edge_input = total_usage["total_input_tokens"]
        edge_cache_read = total_usage["total_cache_read_tokens"]
        edge_cache_write = total_usage["total_cache_write_tokens"]
        edge_output = total_usage["total_output_tokens"]
        edge_req = total_usage["total_request_count"]

    edge_tokens = edge_input + edge_cache_read + edge_cache_write + edge_output
    denom = edge_tokens + cloud_tokens
    edge = {
        "total_request_count": edge_req,
        "total_input_tokens": edge_input,
        "total_raw_input_tokens": edge_input,
        "total_cache_read_tokens": edge_cache_read,
        "total_cache_write_tokens": edge_cache_write,
        "total_output_tokens": edge_output,
        "total_tokens": edge_tokens,
        "edge_token_share": round(edge_tokens / denom, 4) if denom else 0,
        "cache_hit_rate": round(edge_cache_read / (edge_input + edge_cache_read + edge_cache_write), 4)
        if (edge_input + edge_cache_read + edge_cache_write)
        else 0,
    }
    cloud["cloud_token_share"] = round(cloud_tokens / denom, 4) if denom else 0
    combined_usage = total_usage
    if _is_step_router_model(model_name):
        combined_usage = _normalize_step_router_total_usage(total_usage, edge, cloud)
        combined_usage["avg_turns_per_task"] = round(combined_usage["total_turns"] / (total_tasks * repeat), 2)
        default_usage = _edge_usage_as_total_usage(total_usage, edge, model_name, total_tasks * repeat)
    else:
        default_usage = total_usage
    return default_usage, combined_usage, edge, cloud


def per_run_usage(total: dict[str, Any], total_tasks: int, repeat: int) -> dict[str, Any]:
    return {
        "avg_input_tokens": round(total["total_input_tokens"] / repeat, 2),
        "avg_output_tokens": round(total["total_output_tokens"] / repeat, 2),
        "avg_cache_read_tokens": round(total["total_cache_read_tokens"] / repeat, 2),
        "avg_cache_write_tokens": round(total["total_cache_write_tokens"] / repeat, 2),
        "avg_request_count": round(total["total_request_count"] / repeat, 2),
        "avg_turns_per_task": round(total["total_turns"] / repeat / total_tasks, 2) if total_tasks else 0,
        "avg_cost_usd": round(total["total_cost_usd"] / repeat, 6),
        "avg_elapsed_time": round(total["total_elapsed_time"] / repeat, 2),
    }


def nest_avg(total: dict[str, Any], edge: dict[str, Any] | None, cloud: dict[str, Any] | None, total_tasks: int, repeat: int):
    usage = {"per_run_avg": per_run_usage(total, total_tasks, repeat), "all_runs_total": total}
    edge_n = None
    if edge:
        edge_n = {
            "all_runs_total": edge,
            "per_run_avg": {
                "avg_request_count": round(edge["total_request_count"] / repeat, 2),
                "avg_raw_input_tokens": round(edge["total_raw_input_tokens"] / repeat, 2),
                "avg_cache_read_tokens": round(edge["total_cache_read_tokens"] / repeat, 2),
                "avg_cache_write_tokens": round(edge["total_cache_write_tokens"] / repeat, 2),
                "avg_output_tokens": round(edge["total_output_tokens"] / repeat, 2),
                "avg_tokens": round(edge["total_tokens"] / repeat, 2),
                "edge_token_share": edge["edge_token_share"],
                "cache_hit_rate": edge["cache_hit_rate"],
            },
        }
    cloud_n = None
    if cloud:
        cloud_n = {
            "source": cloud["source"],
            "total_tasks": cloud["total_tasks"],
            "cloud_call_rate": cloud["cloud_call_rate"],
            "avg_cloud_calls_per_task": cloud["avg_cloud_calls_per_task"],
            "all_runs_total": {k: v for k, v in cloud.items() if k not in ("source", "total_tasks", "cloud_call_rate", "avg_cloud_calls_per_task")},
            "per_run_avg": {
                "avg_tasks_with_cloud_calls": round(cloud["tasks_with_cloud_calls"] / repeat, 2),
                "avg_request_count": round(cloud["total_request_count"] / repeat, 2),
                "avg_prompt_tokens": round(cloud["total_prompt_tokens"] / repeat, 2),
                "avg_raw_input_tokens": round(cloud["total_raw_input_tokens"] / repeat, 2),
                "avg_cache_read_tokens": round(cloud["total_cache_read_tokens"] / repeat, 2),
                "avg_cache_write_tokens": round(cloud["total_cache_write_tokens"] / repeat, 2),
                "avg_output_tokens": round(cloud["total_completion_tokens"] / repeat, 2),
                "avg_tokens": round(cloud["total_tokens"] / repeat, 2),
                "avg_cloud_calls_per_task": cloud["avg_cloud_calls_per_task"],
                "avg_latency_ms": cloud["avg_latency_ms"],
                "cloud_token_share": cloud["cloud_token_share"],
                "cache_hit_rate": cloud["cache_hit_rate"],
                "cache_write_rate": cloud["cache_write_rate"],
            },
        }
    return usage, edge_n, cloud_n


def collect_results_for_summary(path: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(summary.get("results"), list):
        return summary["results"]
    repeat = int(summary.get("repeat") or 1)
    if repeat <= 1:
        return []
    results: list[dict[str, Any]] = []
    target_model = str(summary.get("model", ""))
    for run_summary in sorted(path.parent.glob("0_summary_*_run*_*.json")):
        if run_summary.name.endswith("_normalized_usage.json"):
            continue
        data = load_json(run_summary)
        run_model = re.sub(r"_run\d+$", "", str((data or {}).get("model", "")))
        if data and run_model == target_model and isinstance(data.get("results"), list):
            results.extend(data["results"])
    return results


def normalize_summary(path: Path, overwrite: bool = False) -> Path | None:
    summary = load_json(path)
    if not summary or "step-router" not in str(summary.get("model", "")).lower():
        return None
    results = collect_results_for_summary(path, summary)
    if not results:
        return None
    repeat = int(summary.get("repeat") or 1)
    total_tasks = int(summary.get("total_tasks") or max(len(results) // repeat, 1))
    usage_total, combined_total, edge, cloud = split_usage(results, summary.get("model", ""), total_tasks, repeat)
    out = dict(summary)
    if "per_run_avg" in summary.get("usage", {}):
        out["usage"], out["edge_usage"], out["cloud_usage"] = nest_avg(usage_total, edge, cloud, total_tasks, repeat)
        if edge and cloud:
            out["combined_usage"] = {
                "per_run_avg": per_run_usage(combined_total, total_tasks, repeat),
                "all_runs_total": combined_total,
            }
    else:
        out["usage"], out["edge_usage"], out["cloud_usage"] = usage_total, edge, cloud
        if edge and cloud:
            out["combined_usage"] = combined_total
    target = path if overwrite else path.with_name(path.stem + "_normalized_usage.json")
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="Summary file or directory to normalize")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite original summary files")
    args = parser.parse_args()

    written: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        summaries = [path] if path.is_file() else sorted(path.glob("**/0_summary*.json"))
        for summary in summaries:
            if summary.name.endswith("_normalized_usage.json"):
                continue
            target = normalize_summary(summary, overwrite=args.overwrite)
            if target:
                written.append(target)
    print(f"wrote {len(written)} summary files")
    for target in written:
        print(target)


if __name__ == "__main__":
    main()
