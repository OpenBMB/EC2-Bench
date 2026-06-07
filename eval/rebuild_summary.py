#!/usr/bin/env python3
"""
Rebuild summary.json from existing task result directories.

Usage:
  # Auto-detect model subdirectory name
  python eval/rebuild_summary.py \
      --dir output/07_Privacy_qwen35_27b_89task_0416/07_Privacy-20260416_2122

  # Specify model label for the summary filename
  python eval/rebuild_summary.py \
      --dir output/07_Privacy_qwen35_27b_89task_0416/07_Privacy-20260416_2122 \
      --model-label vllm_local_qwen35_27b_Qwen_Qwen3.5-27B

  # Custom output path
  python eval/rebuild_summary.py \
      --dir output/.../07_Privacy-20260416_2122 \
      --out  output/.../07_Privacy-20260416_2122/summary_custom.json
"""
import argparse, json, re, sys
from pathlib import Path
from datetime import datetime
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.grading import (
    extract_cloud_usage,
    _cloud_input_as_chat_usage,
    _cloud_raw_input,
    _edge_usage_as_total_usage,
    _is_step_router_model,
    _normalize_step_router_total_usage,
    _usage_note,
)


def _load_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def scan_results(run_dir: Path) -> list[dict]:
    """Scan run_dir/07_Privacy_task_*/<model>/ and reconstruct result dicts."""
    results = []
    task_dirs = sorted(run_dir.glob("07_Privacy*task_*"), key=lambda p: (
        int(m.group(1)) if (m := re.search(r'task_(\d+)', p.name)) else 999, p.name
    ))

    for td in task_dirs:
        if not td.is_dir():
            continue
        model_dirs = [d for d in td.iterdir() if d.is_dir() and d.name != "task_output"]
        if not model_dirs:
            continue
        for md in model_dirs:
            score = _load_json(md / "score.json")
            usage = _load_json(md / "usage.json") or {}
            route = _load_json(md / "task_route.json")

            task_id_parts = [td.name.replace("07_Privacy_", "07_"), md.name]
            run_id_candidates = list(md.glob("*.log"))
            task_id = f"{'_'.join(task_id_parts)}"

            existing_summaries = list(run_dir.glob("summary_*.json"))
            if existing_summaries:
                old_summary = _load_json(existing_summaries[0])
                if old_summary and old_summary.get("results"):
                    for r in old_summary["results"]:
                        task_num_match = re.search(r'task_(\d+)', r["task_id"])
                        cur_num_match = re.search(r'task_(\d+)', td.name)
                        if task_num_match and cur_num_match and task_num_match.group(1) == cur_num_match.group(1):
                            task_id = r["task_id"]
                            break

            result = {
                "task_id": task_id,
                "scores": score if score else {},
                "error": None if score else "no score.json found",
                "usage": usage,
            }
            cloud = extract_cloud_usage(md)
            if cloud:
                result["cloud_usage"] = cloud
            if route:
                result["task_route"] = route.get("decision")
            results.append(result)

    return results


def make_bar(val: float) -> str:
    n = max(0, min(10, int(val * 10)))
    return "█" * n + "░" * (10 - n)


def build_summary(results: list[dict], category: str, model_name: str) -> dict:
    all_scores: dict[str, float] = {}
    for r in results:
        tid = r["task_id"]
        scores = r.get("scores", {})
        if r.get("error") or not scores or "error" in scores:
            continue
        numeric = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            continue
        all_scores[tid] = numeric.get("overall_score", sum(numeric.values()) / len(numeric))

    total_tasks = len(results)
    scored_tasks = len(all_scores)
    global_avg = sum(all_scores.values()) / total_tasks if total_tasks else 0.0

    privacy_list = [
        r["scores"]["privacy_score"]
        for r in results
        if r.get("scores") and isinstance(r["scores"].get("privacy_score"), (int, float))
    ]
    privacy_avg = sum(privacy_list) / len(privacy_list) if privacy_list else None

    total_output_tokens = sum(r.get("usage", {}).get("output_tokens", 0) for r in results)
    total_cost_usd = sum(r.get("usage", {}).get("cost_usd", 0.0) for r in results)

    total_turns_list = [r.get("usage", {}).get("n_turns", r.get("usage", {}).get("request_count", 0)) for r in results]
    total_turns = sum(total_turns_list)

    rb_raw_input = sum(r.get("usage", {}).get("input_tokens", 0) for r in results)
    rb_cache_read = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in results)
    total_usage = {
        "total_input_tokens": rb_raw_input,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": rb_cache_read,
        "total_cache_write_tokens": sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in results),
        "total_actual_input_tokens": rb_raw_input + rb_cache_read,
        "total_request_count": sum(r.get("usage", {}).get("request_count", 0) for r in results),
        "total_turns": total_turns,
        "avg_turns_per_task": round(total_turns / total_tasks, 2) if total_tasks else 0,
        "max_turns": max(total_turns_list) if total_turns_list else 0,
        "min_turns": min(total_turns_list) if total_turns_list else 0,
        "total_elapsed_time": round(sum(r.get("usage", {}).get("elapsed_time", 0.0) for r in results), 2),
        "total_cost_usd": round(total_cost_usd, 6),
        "_note": _usage_note(include_cache_write=False),
    }

    # edge / cloud aggregation
    cloud_tasks = [r for r in results if r.get("cloud_usage")]
    task_routed_cloud = [r for r in results if r.get("task_route") == "CLOUD"]

    total_cloud_usage = None
    if cloud_tasks:
        tp = sum(r["cloud_usage"].get("prompt_tokens", 0) for r in cloud_tasks)
        tc = sum(r["cloud_usage"].get("completion_tokens", 0) for r in cloud_tasks)
        tt = sum(r["cloud_usage"].get("total_tokens", 0) for r in cloud_tasks)
        tcr = sum(r["cloud_usage"].get("cache_read_tokens", 0) for r in cloud_tasks)
        tcw = sum(r["cloud_usage"].get("cache_write_tokens", 0) for r in cloud_tasks)
        tr_ = sum(r["cloud_usage"].get("request_count", 0) for r in cloud_tasks)
        tl = sum(r["cloud_usage"].get("total_latency_ms", 0) for r in cloud_tasks)
        nc = len(cloud_tasks)
        total_cloud_raw = _cloud_raw_input(tp, tcr, tcw)
        total_cloud_usage = {
            "tasks_with_cloud_calls": nc, "total_tasks": total_tasks,
            "cloud_call_rate": round(nc / total_tasks, 4) if total_tasks else 0,
            "total_request_count": tr_, "avg_cloud_calls_per_task": round(tr_ / total_tasks, 2) if total_tasks else 0,
            "total_prompt_tokens": tp,
            "total_raw_input_tokens": total_cloud_raw,
            "total_cache_read_tokens": tcr, "total_cache_write_tokens": tcw,
            "total_completion_tokens": tc, "total_tokens": tt,
            "cloud_token_share": 0,
            "cache_hit_rate": round(tcr / tp, 4) if tp else 0,
            "cache_write_rate": round(tcw / tp, 4) if tp else 0,
            "total_latency_ms": tl, "avg_latency_ms": round(tl / tr_) if tr_ else 0,
            "source": "audit_jsonl",
        }
    elif task_routed_cloud:
        ci  = sum(r.get("usage", {}).get("input_tokens", 0) for r in task_routed_cloud)
        ccr = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in task_routed_cloud)
        ccw = sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in task_routed_cloud)
        co  = sum(r.get("usage", {}).get("output_tokens", 0) for r in task_routed_cloud)
        cr  = sum(r.get("usage", {}).get("request_count", 0) for r in task_routed_cloud)
        ce  = sum(r.get("usage", {}).get("elapsed_time", 0) for r in task_routed_cloud)
        cp  = ci + ccr + ccw
        ct  = cp + co
        nc  = len(task_routed_cloud)
        total_cloud_usage = {
            "tasks_with_cloud_calls": nc, "total_tasks": total_tasks,
            "cloud_call_rate": round(nc / total_tasks, 4) if total_tasks else 0,
            "total_request_count": cr, "avg_cloud_calls_per_task": round(cr / total_tasks, 2) if total_tasks else 0,
            "total_prompt_tokens": cp,
            "total_raw_input_tokens": ci,
            "total_cache_read_tokens": ccr,
            "total_cache_write_tokens": ccw,
            "total_completion_tokens": co,
            "total_tokens": ct,
            "cloud_token_share": 0,  # 下游 L241 重算
            "total_elapsed_time": round(ce, 2), "source": "task_route",
        }

    final_scores_table = {}
    for tid, score in sorted(all_scores.items()):
        entry = {"overall_score": score, "overall_bar": make_bar(score)}
        for r in results:
            if r["task_id"] == tid and r.get("scores"):
                ps = r["scores"].get("privacy_score")
                if isinstance(ps, (int, float)):
                    entry["privacy_score"] = ps
                    entry["privacy_bar"] = make_bar(ps)
                break
        final_scores_table[tid] = entry

    summary = {
        "category": category,
        "model": model_name,
        "total_tasks": total_tasks,
        "scored_tasks": scored_tasks,
        "overall_average": round(global_avg, 4),
        "overall_bar": make_bar(global_avg),
        "privacy_average": round(privacy_avg, 4) if privacy_avg is not None else None,
        "privacy_bar": make_bar(privacy_avg) if privacy_avg is not None else None,
        "usage": total_usage,
    }

    if total_cloud_usage:
        is_step_router = _is_step_router_model(model_name)
        cloud_source = total_cloud_usage.get("source")
        if cloud_source == "task_route":
            ei = total_usage["total_input_tokens"] - total_cloud_usage.get("total_raw_input_tokens", 0)
            ecr = total_usage["total_cache_read_tokens"] - total_cloud_usage.get("total_cache_read_tokens", 0)
            ecw = total_usage["total_cache_write_tokens"] - total_cloud_usage.get("total_cache_write_tokens", 0)
        elif is_step_router:
            cloud_chat_input = _cloud_input_as_chat_usage(
                total_cloud_usage.get("total_prompt_tokens", 0),
                total_cloud_usage.get("total_cache_read_tokens", 0),
            )
            ei = total_usage["total_input_tokens"] - cloud_chat_input
            ecr = total_usage["total_cache_read_tokens"] - total_cloud_usage.get("total_cache_read_tokens", 0)
            ecw = total_usage["total_cache_write_tokens"] - total_cloud_usage.get("total_cache_write_tokens", 0)
        else:
            ei = total_usage["total_input_tokens"]
            ecr = total_usage["total_cache_read_tokens"]
            ecw = total_usage["total_cache_write_tokens"]
        eo = total_usage["total_output_tokens"] - total_cloud_usage.get("total_completion_tokens", 0)
        er = total_usage["total_request_count"] - total_cloud_usage.get("total_request_count", 0)
        ei, ecr, ecw, eo, er = (max(ei, 0), max(ecr, 0), max(ecw, 0), max(eo, 0), max(er, 0))
        et = ei + ecr + ecw + eo
        lpc = et + total_cloud_usage.get("total_tokens", 0)
        total_cloud_usage["cloud_token_share"] = round(total_cloud_usage.get("total_tokens", 0) / lpc, 4) if lpc else 0
        summary["edge_usage"] = {
            "total_request_count": er,
            "total_input_tokens": ei,
            "total_raw_input_tokens": ei,
            "total_cache_read_tokens": ecr,
            "total_cache_write_tokens": ecw,
            "total_output_tokens": eo,
            "total_tokens": et,
            "edge_token_share": round(et / lpc, 4) if lpc else 0,
            "cache_hit_rate": round(ecr / (ei + ecr + ecw), 4) if (ei + ecr + ecw) else 0,
        }
        if is_step_router and cloud_source == "audit_jsonl":
            summary["combined_usage"] = _normalize_step_router_total_usage(
                total_usage,
                summary["edge_usage"],
                total_cloud_usage,
            )
            summary["usage"] = _edge_usage_as_total_usage(
                total_usage,
                summary["edge_usage"],
                model_name,
                total_tasks,
            )
        summary["cloud_usage"] = total_cloud_usage
    else:
        has_routing = any(r.get("task_route") for r in results)
        if has_routing:
            ei = total_usage["total_input_tokens"]
            ecr = total_usage["total_cache_read_tokens"]
            ecw = total_usage["total_cache_write_tokens"]
            eo = total_usage["total_output_tokens"]
            er = total_usage["total_request_count"]
            et = ei + ecr + ecw + eo
            eit = ei + ecr + ecw
            summary["edge_usage"] = {
                "total_request_count": er,
                "total_input_tokens": ei,
                "total_raw_input_tokens": ei,
                "total_cache_read_tokens": ecr,
                "total_cache_write_tokens": ecw,
                "total_output_tokens": eo,
                "total_tokens": et,
                "edge_token_share": 1.0,
                "cache_hit_rate": round(ecr / eit, 4) if eit else 0,
                "note": "all tasks routed LOCAL — no cloud calls",
            }
            summary["cloud_usage"] = None
        else:
            summary["edge_usage"] = None
            summary["cloud_usage"] = None
            summary["_routing_note"] = "single-model run, no edge/cloud split"
    summary["final_scores"] = final_scores_table
    summary["results"] = results
    return summary


def main():
    ap = argparse.ArgumentParser(description="Rebuild summary.json from task result directories")
    ap.add_argument("--dir", required=True, help="Path to the run directory, e.g. output/.../07_Privacy-20260416_2122")
    ap.add_argument("--model-label", default=None, help="Model label for summary filename (auto-detected if omitted)")
    ap.add_argument("--out", default=None, help="Custom output path for summary JSON (auto-generated if omitted)")
    args = ap.parse_args()

    run_dir = Path(args.dir).resolve()
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} is not a directory"); return

    category = run_dir.name

    # auto-detect model label from existing summary or subdirectory names
    model_label = args.model_label
    if not model_label:
        existing = list(run_dir.glob("summary_*.json"))
        if existing:
            m = re.match(r'summary_(.+?)_\d{8}\.json', existing[0].name)
            if m:
                model_label = m.group(1)
        if not model_label:
            model_dirs = set()
            for td in run_dir.glob("07_Privacy_task_*"):
                for md in td.iterdir():
                    if md.is_dir() and md.name != "task_output":
                        model_dirs.add(md.name)
            model_label = model_dirs.pop() if len(model_dirs) == 1 else "unknown_model"

    print(f"Scanning: {run_dir}")
    print(f"Model label: {model_label}")

    results = scan_results(run_dir)
    print(f"Found {len(results)} task results")

    scored = sum(1 for r in results if r.get("scores") and not r.get("error"))
    print(f"Scored: {scored}, Errors: {len(results) - scored}")

    summary = build_summary(results, category, model_label)

    if args.out:
        out_path = Path(args.out).resolve()
    else:
        date_tag = datetime.now().strftime("%Y%m%d")
        out_path = run_dir / f"summary_{model_label}_{date_tag}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"\nOverall average: {make_bar(summary['overall_average'])} {summary['overall_average']:.4f}")
    if summary.get("privacy_average") is not None:
        print(f"Privacy average: {make_bar(summary['privacy_average'])} {summary['privacy_average']:.4f}")
    print(f"\nSummary written to → {out_path}")


if __name__ == "__main__":
    main()
