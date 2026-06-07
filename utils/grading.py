from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE", "/tmp_workspace")
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", 0.7))

# ---- Model pricing (USD per million tokens) ----
MODEL_PRICING: dict[str, dict] = {
    "qwen3.5-9b":  {"input": 0.10, "cache_read": 0.10, "output": 0.15},
    "qwen3.5-27b": {"input": 0.30, "cache_read": 0.30, "output": 2.40},
    "gpt-5.4": {
        "tiers": [
            {"max_context": 272_000, "input": 2.50, "cache_read": 0.25, "output": 15.0},
            {"max_context": float("inf"), "input": 5.00, "cache_read": 0.50, "output": 22.5},
        ]
    },
    "sonnet-4.6": {"input": 3.00, "cache_read": 0.30, "output": 15.0},
}

def _match_pricing(model_name: str) -> dict | None:
    """Fuzzy-match a model name to its pricing entry."""
    import re
    normalized = re.sub(r'[\s_\-]+', '', model_name.lower())
    _PATTERNS: list[tuple[str, list[str]]] = [
        ("qwen3.5-9b",  ["qwen3.59b", "qwen359b"]),
        ("qwen3.5-27b", ["qwen3.527b", "qwen3527b"]),
        ("gpt-5.4",     ["gpt5.4", "gpt54"]),
        ("sonnet-4.6",  ["sonnet4.6", "sonnet46", "claude4.6"]),
    ]
    for key, patterns in _PATTERNS:
        for pat in patterns:
            if pat in normalized:
                return MODEL_PRICING[key]
    return None

def _estimate_message_cost(pricing: dict, input_tokens: int,
                           cache_read: int, output_tokens: int) -> float:
    """Estimate cost for a single API call in USD."""
    p = pricing
    if "tiers" in p:
        context = input_tokens + cache_read
        for tier in p["tiers"]:
            if context <= tier["max_context"]:
                p = tier
                break
    return (input_tokens * p["input"]
            + cache_read * p["cache_read"]
            + output_tokens * p["output"]) / 1_000_000


_PRIVACY_AUDIT_SRC_PATH = Path(__file__).resolve().parent / "privacy_audit.py"


def _load_privacy_audit_module_src() -> str:
    """Load privacy_audit.py and wrap it into an inline module so task graders
    can do ``from privacy_audit import compute_privacy_score`` inside the
    container without needing a real import path."""
    if not _PRIVACY_AUDIT_SRC_PATH.exists():
        return ""
    src = _PRIVACY_AUDIT_SRC_PATH.read_text(encoding="utf-8")
    return (
        "# --- injected utils/privacy_audit.py ---\n"
        "import sys as _sys, types as _types\n"
        "_pa_mod = _types.ModuleType('privacy_audit')\n"
        "_pa_src = r'''" + src.replace("'''", "\\'\\'\\'") + "'''\n"
        "exec(compile(_pa_src, '<privacy_audit>', 'exec'), _pa_mod.__dict__)\n"
        "_sys.modules['privacy_audit'] = _pa_mod\n"
        "# --- end privacy_audit ---\n"
    )


_LLM_JUDGE_LOG_PATCH = r'''
import threading as _threading, time as _time, json as _json_log, pathlib as _pathlib_log

_judge_log_path = _pathlib_log.Path("/tmp/llm_judge.jsonl")
_judge_log_lock = _threading.Lock()
_judge_context = _threading.local()

def _set_judge_type(t: str):
    _judge_context.type = t

def _get_judge_type() -> str:
    return getattr(_judge_context, "type", "completion")

import builtins as _builtins
_builtins._set_judge_type = _set_judge_type

def _patch_openai_judge_logging():
    try:
        from openai.resources.chat.completions import Completions
    except ImportError:
        return
    _orig_create = Completions.create
    def _truncate_content(text, max_words=100):
        if not text or not isinstance(text, str):
            return text
        words = text.split()
        if len(words) <= max_words * 2:
            return text
        return " ".join(words[:max_words]) + " [...TRUNCATED...] " + " ".join(words[-max_words:])

    def _truncate_messages(msgs):
        out = []
        for m in msgs:
            mc = dict(m)
            if isinstance(mc.get("content"), str):
                mc["content"] = _truncate_content(mc["content"])
            out.append(mc)
        return out

    def _logged_create(self, *args, **kwargs):
        model = kwargs.get("model", "")
        messages = kwargs.get("messages", [])
        t0 = _time.time()
        error_str = None
        output_content = None
        try:
            resp = _orig_create(self, *args, **kwargs)
            output_content = resp.choices[0].message.content if resp.choices else None
            return resp
        except Exception as _e:
            error_str = str(_e)
            raise
        finally:
            latency_ms = int((_time.time() - t0) * 1000)
            entry = {
                "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()),
                "type": _get_judge_type(),
                "model": model,
                "messages": _truncate_messages(messages),
                "output": _truncate_content(output_content),
                "error": error_str,
                "latency_ms": latency_ms,
            }
            with _judge_log_lock:
                with open(_judge_log_path, "a", encoding="utf-8") as _f:
                    _f.write(_json_log.dumps(entry, ensure_ascii=False, default=str) + "\n")
    Completions.create = _logged_create

_patch_openai_judge_logging()
'''


def run_grading(task_id: str, automated_checks: str, output_dir: Path) -> dict:
    logger.info("[%s] Starting in-container grading...", task_id)

    env_remap = (
        "import os\n"
        "if os.environ.get('JUDGE_BASE_URL'):\n"
        "    os.environ['OPENROUTER_BASE_URL'] = os.environ['JUDGE_BASE_URL']\n"
        "if os.environ.get('JUDGE_API_KEY'):\n"
        "    os.environ['OPENROUTER_API_KEY'] = os.environ['JUDGE_API_KEY']\n"
    )
    privacy_audit_src = _load_privacy_audit_module_src()
    runner_code = "\n".join([
        "import json, sys",
        env_remap,
        _LLM_JUDGE_LOG_PATCH,
        privacy_audit_src,
        automated_checks,
        "",
        f'result = grade(transcript=[], workspace_path="{TMP_WORKSPACE}")',
        "print(json.dumps(result))",
    ]) + "\n"
    #########

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(runner_code)
        tmp_host = f.name

    try:
        r = subprocess.run(
            ["docker", "cp", tmp_host, f"{task_id}:/tmp/_grade_runner.py"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("[%s] docker cp failed: %s", task_id, r.stderr)
            return {"error": f"docker cp failed: {r.stderr}"}

        subprocess.run(
            ["docker", "exec", task_id, "pip", "install", "-q", "json_repair"],
            capture_output=True, text=True, timeout=30,
        )

        r = subprocess.run(
            ["docker", "exec", task_id, "python3", "/tmp/_grade_runner.py"],
            capture_output=True, text=True,
            timeout=1200,
        )
        if r.returncode != 0:
            logger.error("[%s] Grading script execution failed: %s", task_id, r.stderr)
            return {"error": f"grade script failed: {r.stderr}"}

        try:
            scores = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            scores = None
            for line in reversed(r.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        scores = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if scores is None:
                logger.error("[%s] Failed to parse grading result, no valid JSON found in stdout\nstdout: %s", task_id, r.stdout[:500])
                return {"error": f"json parse failed: no valid JSON in stdout"}

    finally:
        Path(tmp_host).unlink(missing_ok=True)

    score_path = output_dir / "score.json"
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[%s] Grading results written to → %s", task_id, score_path)

    judge_log_host = output_dir / "llm_judge.jsonl"
    r_jl = subprocess.run(
        ["docker", "cp", f"{task_id}:/tmp/llm_judge.jsonl", str(judge_log_host)],
        capture_output=True, text=True,
    )
    if r_jl.returncode == 0 and judge_log_host.exists():
        logger.info("[%s] Collected llm_judge.jsonl (%d bytes)", task_id, judge_log_host.stat().st_size)

    deferred_privacy_host = output_dir / "privacy_judge_deferred.json"
    r_dp = subprocess.run(
        ["docker", "cp", f"{task_id}:/tmp/privacy_judge_deferred.json", str(deferred_privacy_host)],
        capture_output=True, text=True,
    )
    if r_dp.returncode == 0 and deferred_privacy_host.exists():
        logger.info("[%s] Collected privacy_judge_deferred.json (%d bytes)", task_id, deferred_privacy_host.stat().st_size)

    return scores


def format_scores(task_id: str, scores: dict) -> str:
    if "error" in scores:
        return f"[{task_id}] Grading error: {scores['error']}"
    lines = [f"\n{'='*60}", f"  {task_id}", f"{'='*60}"]

    _SKIP_KEYS = {"tool_get_calls", "tool_send_calls", "tool_list_calls"}
    for k, v in scores.items():
        if k in _SKIP_KEYS:
            continue
        if isinstance(v, (int, float)):
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            lines.append(f"  {bar} {v:.2f}  {k}")

    lines.append("=" * 60)
    overall = scores.get("overall_score")
    privacy = scores.get("privacy_score")
    o_str = f"{overall * 100:.2f}" if isinstance(overall, (int, float)) else "N/A"
    p_str = f"{privacy * 100:.2f}" if isinstance(privacy, (int, float)) else "N/A"
    lines.append(f"  overall_score\tprivacy_score")
    lines.append(f"  {o_str}\t{p_str}")

    return "\n".join(lines)

def print_summary(results: list[dict], category: str, output_dir: Path, model_name: str,
                   expected_tasks: int = 0) -> None:
    print(f"\n{'#'*60}")
    print(f"  Summary Report — {category}")
    print(f"{'#'*60}")

    all_scores: dict[str, float] = {}
    for r in results:
        task_id = r["task_id"]
        if r.get("error"):
            print(f"  ✗ {task_id}: {r['error']}")
            continue
        scores = r['scores']
        if not scores:
            print(f"  - {task_id}: No scores")
            continue
        if "error" in scores:
            print(f"  ✗ {task_id}: Grading error {scores['error']}")
            continue
        numeric_dict = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        
        if not numeric_dict:
            print(f"  - {task_id}: No valid numeric scores")
            continue

        avg = sum(numeric_dict.values()) / len(numeric_dict)
        print(f"  ✓ {task_id}: avg {avg:.2f}  ({len(numeric_dict)} items)")

        final_score_val = numeric_dict.get('overall_score', avg)
        all_scores[task_id] = final_score_val

    if all_scores:
        print(f"\n  Final scores per task (overall / privacy):")
        for k, score in sorted(all_scores.items()):
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            # Find privacy_score for this task
            priv = ""
            for r in results:
                if r["task_id"] == k and r.get("scores"):
                    ps = r["scores"].get("privacy_score")
                    if ps is not None:
                        pbar = "█" * int(ps * 10) + "░" * (10 - int(ps * 10))
                        priv = f"  privacy: {pbar} {ps:.2f}"
                    break
            print(f"    {bar} {score:.2f}  {k}{priv}")

    print(f"\n  Token usage and cost per task:")
    print(f"    {'Task ID':<55} {'Output Tokens':>12} {'Cost(USD)':>12}")
    print(f"    {'-'*55} {'-'*12} {'-'*12}")
    total_output_tokens = 0
    total_cost_usd = 0.0
    for r in sorted(results, key=lambda x: x["task_id"]):
        usage = r.get("usage", {})
        out_tok = usage.get("output_tokens", 0)
        cost = usage.get("cost_usd", 0.0)
        total_output_tokens += out_tok
        total_cost_usd += cost
        print(f"    {r['task_id']:<55} {out_tok:>12} {cost:>11.4f}$")
    print(f"    {'Total':<55} {total_output_tokens:>12} {total_cost_usd:>11.4f}$")

    from datetime import datetime
    date_tag = datetime.now().strftime("%Y%m%d")
    summary_path = output_dir / category / f"0_summary_{model_name}_{date_tag}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    def make_bar(val):
        return "█" * int(val * 10) + "░" * (10 - int(val * 10))

    final_scores_table = {}
    pass_count = 0
    for tid, score in sorted(all_scores.items()):
        passed = score >= PASS_THRESHOLD
        if passed:
            pass_count += 1
        entry = {"overall_score": score, "overall_bar": make_bar(score), "pass": passed}
        for r in results:
            if r["task_id"] == tid and r.get("scores"):
                ps = r["scores"].get("privacy_score")
                if ps is not None:
                    entry["privacy_score"] = ps
                    entry["privacy_bar"] = make_bar(ps)
                break
        final_scores_table[tid] = entry

    # ---- Aggregate stats (same as print_global_summary) ----
    returned_tasks = len(results)
    scored_tasks = len(all_scores)
    total_tasks = max(expected_tasks, returned_tasks)
    missing_score_tasks = total_tasks - scored_tasks
    global_avg = sum(all_scores.values()) / total_tasks if total_tasks > 0 else 0.0
    pass_rate = pass_count / total_tasks if total_tasks else 0.0
    privacy_scores_list = [
        r["scores"]["privacy_score"]
        for r in results
        if r.get("scores") and r["scores"].get("privacy_score") is not None
    ]
    privacy_avg = sum(privacy_scores_list) / len(privacy_scores_list) if privacy_scores_list else None

    if all_scores:
        bar = make_bar(global_avg)
        print(f"\n  Completed tasks: {scored_tasks} / {total_tasks}  (returned {returned_tasks})")
        if missing_score_tasks > 0:
            print(f"  Tasks without valid score: {missing_score_tasks}")
        print(f"  Overall average:  {bar} {global_avg:.4f}")
        print(f"  pass (>={PASS_THRESHOLD}): {pass_count}/{total_tasks} ({pass_rate:.2%})")
        if privacy_avg is not None:
            pbar = make_bar(privacy_avg)
            print(f"  Privacy average:  {pbar} {privacy_avg:.4f}")

    turns_list = [r.get("usage", {}).get("n_turns", r.get("usage", {}).get("request_count", 0)) for r in results]
    total_turns = sum(turns_list)
    raw_input = sum(r.get("usage", {}).get("input_tokens", 0) for r in results)
    cache_read = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in results)
    total_usage = {
        "total_input_tokens": raw_input,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": cache_read,
        "total_cache_write_tokens": sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in results),
        "total_actual_input_tokens": raw_input + cache_read,
        "total_request_count": sum(r.get("usage", {}).get("request_count", 0) for r in results),
        "total_turns": total_turns,
        "avg_turns_per_task": round(total_turns / total_tasks, 2) if total_tasks else 0,
        "max_turns": max(turns_list) if turns_list else 0,
        "min_turns": min(turns_list) if turns_list else 0,
        "total_elapsed_time": round(sum(r.get("usage", {}).get("elapsed_time", 0.0) for r in results), 2),
        "total_cost_usd": round(total_cost_usd, 6),
        "_note": _usage_note(include_cache_write=False),
    }

    # Source 1: cloud_assistant_audit.jsonl (advisor / step-router 等)
    cloud_tasks = [r for r in results if r.get("cloud_usage")]
    # Source 2: task_route decision (query-router 等 task-level 路由)
    task_routed_cloud = [r for r in results if r.get("task_route") == "CLOUD"]
    task_routed_local = [r for r in results if r.get("task_route") == "LOCAL"]

    total_cloud_usage = None
    if cloud_tasks:
        total_prompt = sum(r["cloud_usage"].get("prompt_tokens", 0) for r in cloud_tasks)
        total_completion = sum(r["cloud_usage"].get("completion_tokens", 0) for r in cloud_tasks)
        total_cloud_tokens = sum(r["cloud_usage"].get("total_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_read = sum(r["cloud_usage"].get("cache_read_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_write = sum(r["cloud_usage"].get("cache_write_tokens", 0) for r in cloud_tasks)
        total_cloud_req = sum(r["cloud_usage"].get("request_count", 0) for r in cloud_tasks)
        total_latency = sum(r["cloud_usage"].get("total_latency_ms", 0) for r in cloud_tasks)
        n_cloud_tasks = len(cloud_tasks)
        # raw = prompt - read - write (兼容 OpenAI/Anthropic; OpenAI write=0 时退化为旧公式)
        total_cloud_raw = _cloud_raw_input(total_prompt, total_cloud_cache_read, total_cloud_cache_write)
        total_cloud_usage = {
            "tasks_with_cloud_calls": n_cloud_tasks,
            "total_tasks": total_tasks,
            "cloud_call_rate": round(n_cloud_tasks / total_tasks, 4) if total_tasks else 0,
            "total_request_count": total_cloud_req,
            "avg_cloud_calls_per_task": round(total_cloud_req / total_tasks, 2) if total_tasks else 0,
            "total_prompt_tokens": total_prompt,
            "total_raw_input_tokens": total_cloud_raw,
            "total_cache_read_tokens": total_cloud_cache_read,
            "total_cache_write_tokens": total_cloud_cache_write,
            "total_completion_tokens": total_completion,
            "total_tokens": total_cloud_tokens,
            "cloud_token_share": 0,
            "cache_hit_rate": round(total_cloud_cache_read / total_prompt, 4) if total_prompt else 0,
            "cache_write_rate": round(total_cloud_cache_write / total_prompt, 4) if total_prompt else 0,
            "total_latency_ms": total_latency,
            "avg_latency_ms": round(total_latency / total_cloud_req) if total_cloud_req else 0,
            "source": "audit_jsonl",
        }
    elif task_routed_cloud:
        # 拿到 0 退化成 edge_input = 全量，造成 task-level 路由的 edge/cloud 拆分错误。
        cloud_in_raw    = sum(r.get("usage", {}).get("input_tokens", 0) for r in task_routed_cloud)
        cloud_cache_r   = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in task_routed_cloud)
        cloud_cache_w   = sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in task_routed_cloud)
        cloud_out       = sum(r.get("usage", {}).get("output_tokens", 0) for r in task_routed_cloud)
        cloud_req       = sum(r.get("usage", {}).get("request_count", 0) for r in task_routed_cloud)
        cloud_elapsed   = sum(r.get("usage", {}).get("elapsed_time", 0) for r in task_routed_cloud)
        cloud_prompt    = cloud_in_raw + cloud_cache_r + cloud_cache_w  # chat.jsonl 口径 prompt
        cloud_tok       = cloud_prompt + cloud_out
        n_cloud         = len(task_routed_cloud)
        total_cloud_usage = {
            "tasks_with_cloud_calls": n_cloud,
            "total_tasks": total_tasks,
            "cloud_call_rate": round(n_cloud / total_tasks, 4) if total_tasks else 0,
            "total_request_count": cloud_req,
            "avg_cloud_calls_per_task": round(cloud_req / total_tasks, 2) if total_tasks else 0,
            "total_prompt_tokens": cloud_prompt,
            "total_raw_input_tokens": cloud_in_raw,
            "total_cache_read_tokens": cloud_cache_r,
            "total_cache_write_tokens": cloud_cache_w,
            "total_completion_tokens": cloud_out,
            "total_tokens": cloud_tok,
            "cloud_token_share": 0,  # 下面 L520 会按 edge_tokens+cloud_tokens 重新算
            "total_elapsed_time": round(cloud_elapsed, 2),
            "source": "task_route",
        }
        #########
    #########

    summary_data = {
        "category": category,
        "model": model_name,
        "total_tasks": total_tasks,
        "returned_tasks": returned_tasks,
        "scored_tasks": scored_tasks,
        "overall_average": round(global_avg, 4),
        "overall_bar": make_bar(global_avg),
        "pass_threshold": PASS_THRESHOLD,
        "pass_count": pass_count,
        "pass_rate": round(pass_rate, 4),
        "privacy_average": round(privacy_avg, 4) if privacy_avg is not None else None,
        "privacy_bar": make_bar(privacy_avg) if privacy_avg is not None else None,
        "usage": total_usage,
    }
    if total_cloud_usage:
        cloud_source = total_cloud_usage.get("source", "audit_jsonl")
        is_step_router = _is_step_router_model(model_name)
        if cloud_source == "task_route":
            edge_input = max(total_usage["total_input_tokens"] - total_cloud_usage.get("total_raw_input_tokens", 0), 0)
            edge_cache_read = max(total_usage["total_cache_read_tokens"] - total_cloud_usage.get("total_cache_read_tokens", 0), 0)
            edge_cache_write = max(total_usage["total_cache_write_tokens"] - total_cloud_usage.get("total_cache_write_tokens", 0), 0)
            edge_output = max(total_usage["total_output_tokens"] - total_cloud_usage.get("total_completion_tokens", 0), 0)
            edge_req = max(total_usage["total_request_count"] - total_cloud_usage.get("total_request_count", 0), 0)
        elif is_step_router:
            cloud_chat_input = _cloud_input_as_chat_usage(
                total_cloud_usage.get("total_prompt_tokens", 0),
                total_cloud_usage.get("total_cache_read_tokens", 0),
            )
            edge_input = max(total_usage["total_input_tokens"] - cloud_chat_input, 0)
            edge_cache_read = max(total_usage["total_cache_read_tokens"] - total_cloud_usage.get("total_cache_read_tokens", 0), 0)
            edge_cache_write = max(total_usage["total_cache_write_tokens"] - total_cloud_usage.get("total_cache_write_tokens", 0), 0)
            edge_output = max(total_usage["total_output_tokens"] - total_cloud_usage.get("total_completion_tokens", 0), 0)
            edge_req = max(total_usage["total_request_count"] - total_cloud_usage.get("total_request_count", 0), 0)
        else:
            edge_input = total_usage["total_input_tokens"]
            edge_cache_read = total_usage["total_cache_read_tokens"]
            edge_cache_write = total_usage["total_cache_write_tokens"]
            edge_output = total_usage["total_output_tokens"]
            edge_req = total_usage["total_request_count"]
        edge_tokens = edge_input + edge_cache_read + edge_cache_write + edge_output
        local_plus_cloud = edge_tokens + total_cloud_usage.get("total_tokens", 0)
        total_cloud_usage["cloud_token_share"] = round(total_cloud_usage.get("total_tokens", 0) / local_plus_cloud, 4) if local_plus_cloud else 0
        summary_data["edge_usage"] = {
            "total_request_count": edge_req,
            "total_input_tokens": edge_input,
            "total_raw_input_tokens": edge_input,
            "total_cache_read_tokens": edge_cache_read,
            "total_cache_write_tokens": edge_cache_write,
            "total_output_tokens": edge_output,
            "total_tokens": edge_tokens,
            "edge_token_share": round(edge_tokens / local_plus_cloud, 4) if local_plus_cloud else 0,
            "cache_hit_rate": round(edge_cache_read / (edge_input + edge_cache_read + edge_cache_write), 4) if (edge_input + edge_cache_read + edge_cache_write) else 0,
        }
        if is_step_router and cloud_source == "audit_jsonl":
            combined_usage = _normalize_step_router_total_usage(
                total_usage, summary_data["edge_usage"], total_cloud_usage
            )
            summary_data["combined_usage"] = combined_usage
            summary_data["usage"] = _edge_usage_as_total_usage(
                total_usage, summary_data["edge_usage"], model_name, total_tasks
            )
        #########
        summary_data["cloud_usage"] = total_cloud_usage
    else:
        has_routing = any(r.get("task_route") for r in results)
        if has_routing:
            edge_input  = total_usage["total_input_tokens"]
            edge_cr     = total_usage["total_cache_read_tokens"]
            edge_cw     = total_usage["total_cache_write_tokens"]
            edge_out    = total_usage["total_output_tokens"]
            edge_req    = total_usage["total_request_count"]
            edge_tok    = edge_input + edge_cr + edge_cw + edge_out
            edge_input_total = edge_input + edge_cr + edge_cw
            edge_cache_hit_rate = round(edge_cr / edge_input_total, 4) if edge_input_total else 0
            summary_data["edge_usage"] = {
                "total_request_count": edge_req,
                "total_input_tokens": edge_input,
                "total_raw_input_tokens": edge_input,
                "total_cache_read_tokens": edge_cr,
                "total_cache_write_tokens": edge_cw,
                "total_output_tokens": edge_out,
                "total_tokens": edge_tok,
                "edge_token_share": 1.0,
                "cache_hit_rate": edge_cache_hit_rate,
                "note": "all tasks routed LOCAL — no cloud calls",
            }
            summary_data["cloud_usage"] = None
            #########
        else:
            summary_data["edge_usage"] = None
            summary_data["cloud_usage"] = None
            summary_data["_routing_note"] = "single-model run, no edge/cloud split"
    summary_data["final_scores"] = final_scores_table
    summary_data["results"] = results
    summary_path.write_text(
        json.dumps(summary_data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n  Summary written to → {summary_path}")
    print("#" * 60)

def extract_usage_from_jsonl(jsonl_path: Path, model_name: str = "") -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
    }
    if not jsonl_path.exists():
        return totals
    pricing = _match_pricing(model_name) if model_name else None
    estimated_cost = 0.0
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        totals["request_count"] += 1
        usage = msg.get("usage", {})
        raw_input   = usage.get("input",       0)
        cache_read  = usage.get("cacheRead",   0)
        cache_write = usage.get("cacheWrite",  0)
        output_tok  = usage.get("output",      0)
        totals["input_tokens"]       += raw_input
        totals["output_tokens"]      += output_tok
        totals["cache_read_tokens"]  += cache_read
        totals["cache_write_tokens"] += cache_write
        totals["total_tokens"]       += usage.get("totalTokens", 0)
        cost = usage.get("cost", {})
        totals["cost_usd"] += cost.get("total", 0.0)
        if pricing:
            estimated_cost += _estimate_message_cost(
                pricing, raw_input, cache_read, output_tok)
    if totals["cost_usd"] == 0.0 and estimated_cost > 0:
        totals["cost_usd"] = estimated_cost
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["n_turns"] = totals["request_count"]
    return totals



def extract_cloud_usage(output_dir: Path) -> dict | None:
    """Parse cloud_assistant_audit.jsonl to aggregate cloud model token usage."""
    audit_path = output_dir / "cloud_assistant_audit.jsonl"
    if not audit_path.exists() or audit_path.stat().st_size == 0:
        return None
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "request_count": 0,
        "total_latency_ms": 0,
    }
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["request_count"] += 1
        totals["prompt_tokens"] += entry.get("prompt_tokens", 0)
        totals["completion_tokens"] += entry.get("completion_tokens", 0)
        totals["total_tokens"] += entry.get("total_tokens", 0)
        totals["cache_read_tokens"] += entry.get("cache_read_tokens", 0)
        totals["cache_write_tokens"] += entry.get("cache_write_tokens", 0)
        totals["total_latency_ms"] += entry.get("latency_ms", 0)
    if totals["request_count"] == 0:
        return None
    totals["avg_latency_ms"] = round(totals["total_latency_ms"] / totals["request_count"])
    return totals
#########


def _is_step_router_model(model_name: str) -> bool:
    return "step-router" in (model_name or "").lower()


def _actual_input_tokens(raw_input: int, cache_read: int, cache_write: int = 0) -> int:
    return raw_input + cache_read + cache_write


def _cloud_raw_input(prompt_tokens: int, cache_read: int, cache_write: int) -> int:
    # Cloud prompt_tokens includes raw + cache_read + cache_write.
    return max(prompt_tokens - cache_read - cache_write, 0)


def _cloud_input_as_chat_usage(prompt_tokens: int, cache_read: int) -> int:
    # OpenClaw chat usage exposes provider input roughly as prompt - cache_read.
    # For Claude this still includes cache creation/write tokens, so step-router
    # must subtract this from mixed chat usage before adding normalized cloud usage.
    return max(prompt_tokens - cache_read, 0)


def _usage_note(include_cache_write: bool) -> str:
    if include_cache_write:
        return (
            "total_input_tokens counts non-cached raw tokens; "
            "total_actual_input_tokens = total_input_tokens + total_cache_read_tokens + total_cache_write_tokens"
        )
    return "total_input_tokens counts only non-cached new tokens; total_actual_input_tokens = total_input_tokens + total_cache_read_tokens"


def _normalize_step_router_total_usage(total_usage: dict, edge_usage: dict, cloud_usage: dict) -> dict:
    """Return provider-normalized total usage for step-router summaries.

    In step-router, chat.jsonl contains a mixed stream of edge and cloud-routed
    turns. Claude-compatible providers often report cache creation as input in
    chat usage, while cloud_assistant_audit.jsonl has the correct
    raw/cache_read/cache_write split. Normalize totals as edge + audited cloud.
    """
    out = dict(total_usage)
    raw_input = edge_usage.get("total_raw_input_tokens", edge_usage.get("total_input_tokens", 0)) + cloud_usage.get("total_raw_input_tokens", 0)
    cache_read = edge_usage.get("total_cache_read_tokens", 0) + cloud_usage.get("total_cache_read_tokens", 0)
    cache_write = edge_usage.get("total_cache_write_tokens", 0) + cloud_usage.get("total_cache_write_tokens", 0)
    output = edge_usage.get("total_output_tokens", 0) + cloud_usage.get("total_completion_tokens", 0)
    request_count = edge_usage.get("total_request_count", 0) + cloud_usage.get("total_request_count", 0)

    out["total_input_tokens"] = raw_input
    out["total_output_tokens"] = output
    out["total_cache_read_tokens"] = cache_read
    out["total_cache_write_tokens"] = cache_write
    out["total_actual_input_tokens"] = _actual_input_tokens(raw_input, cache_read, cache_write)
    out["total_request_count"] = request_count
    out["_note"] = _usage_note(include_cache_write=True)
    out["_normalization"] = "step-router chat usage split into edge usage plus audited cloud raw/cache_read/cache_write"
    return out


def _edge_usage_as_total_usage(total_usage: dict, edge_usage: dict, model_name: str, denom_tasks: int) -> dict:
    """Return the default top-level usage for step-router summaries.

    For step-router we report executor/edge usage in the top-level ``usage``
    field and keep cloud usage separate in ``cloud_usage``. A separate
    ``combined_usage`` can be used when edge + cloud totals are needed.
    """
    out = dict(total_usage)
    raw_input = edge_usage.get("total_raw_input_tokens", edge_usage.get("total_input_tokens", 0))
    cache_read = edge_usage.get("total_cache_read_tokens", 0)
    cache_write = edge_usage.get("total_cache_write_tokens", 0)
    output = edge_usage.get("total_output_tokens", 0)
    request_count = edge_usage.get("total_request_count", 0)

    out["total_input_tokens"] = raw_input
    out["total_output_tokens"] = output
    out["total_cache_read_tokens"] = cache_read
    out["total_cache_write_tokens"] = cache_write
    out["total_actual_input_tokens"] = _actual_input_tokens(raw_input, cache_read, cache_write)
    out["total_request_count"] = request_count
    out["total_turns"] = request_count
    out["avg_turns_per_task"] = round(request_count / denom_tasks, 2) if denom_tasks else 0

    pricing = _match_pricing(model_name)
    if pricing:
        out["total_cost_usd"] = round(
            _estimate_message_cost(pricing, raw_input + cache_write, cache_read, output),
            6,
        )
    out["_note"] = (
        "step-router top-level usage is edge/executor only; "
        "cloud model usage is reported separately in cloud_usage"
    )
    out["_normalization"] = "step-router usage=edge_usage; combined_usage=edge_usage+cloud_usage"
    return out


def print_global_summary(results: list[dict], output_dir: Path, model_name: str, repeat: int = 1) -> None:
    print(f"\n{'#'*60}")
    print(f"  Global Summary Report — ALL CATEGORIES")
    print(f"{'#'*60}")

    total_tasks = len(results)
    scored_tasks = 0
    missing_score_tasks = 0
    total_score = 0.0
    for r in results:
        scores = r.get("scores", {})
        if r.get("error") or not scores or "error" in scores:
            missing_score_tasks += 1
            continue
        numeric = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            missing_score_tasks += 1
            continue
        final = numeric.get("overall_score", sum(numeric.values()) / len(numeric))
        total_score += final
        scored_tasks += 1

    global_avg = 0.0
    if total_tasks > 0:
        global_avg = total_score / total_tasks
        bar = "█" * int(global_avg * 10) + "░" * (10 - int(global_avg * 10))
        print(f"\n  Completed tasks: {scored_tasks} / {total_tasks}")
        print(f"  Tasks without a valid score.json: {missing_score_tasks}")
        if missing_score_tasks > 0:
            print("  Possible causes: task execution failed, such as OOM, or grading failed.")
        print(f"  Global average: {bar} {global_avg:.4f}")
    else:
        print("  No tasks found")

    turns_list = [r.get("usage", {}).get("n_turns", r.get("usage", {}).get("request_count", 0)) for r in results]
    total_turns = sum(turns_list)
    g_raw_input = sum(r.get("usage", {}).get("input_tokens", 0) for r in results)
    g_cache_read = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in results)
    total_usage = {
        "total_input_tokens":       g_raw_input,
        "total_output_tokens":      sum(r.get("usage", {}).get("output_tokens", 0) for r in results),
        "total_cache_read_tokens":  g_cache_read,
        "total_cache_write_tokens": sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in results),
        "total_actual_input_tokens": g_raw_input + g_cache_read,
        "total_request_count":      sum(r.get("usage", {}).get("request_count", 0) for r in results),
        "total_turns": total_turns,
        "avg_turns_per_task": round(total_turns / total_tasks, 2) if total_tasks else 0,
        "max_turns": max(turns_list) if turns_list else 0,
        "min_turns": min(turns_list) if turns_list else 0,
        "total_elapsed_time":       round(sum(r.get("usage", {}).get("elapsed_time", 0.0) for r in results), 2),
        "total_cost_usd":           round(sum(r.get("usage", {}).get("cost_usd", 0.0) for r in results), 6),
        "_note": "total_input_tokens counts only non-cached new tokens; total_actual_input_tokens = total_input_tokens + total_cache_read_tokens",
    }
    print(f"\n  [Model Usage]")
    print(f"  Total input tokens:  {total_usage['total_input_tokens']}  (non-cached)")
    print(f"  Total actual input:  {total_usage['total_actual_input_tokens']}  (non-cached + cache_read)")
    print(f"  Total output tokens: {total_usage['total_output_tokens']}")
    print(f"  Total cache read:    {total_usage['total_cache_read_tokens']}")
    print(f"  Total cache write:   {total_usage['total_cache_write_tokens']}")
    print(f"  Total requests:      {total_usage['total_request_count']}")
    print(f"  Total turns:         {total_turns}  (avg {total_usage['avg_turns_per_task']}/task, range {total_usage['min_turns']}~{total_usage['max_turns']})")
    print(f"  Total elapsed time:  {total_usage['total_elapsed_time']:.2f}s")
    print(f"  Total cost:          ${total_usage['total_cost_usd']:.4f}")

    cloud_tasks = [r for r in results if r.get("cloud_usage")]
    total_cloud_usage = None
    if cloud_tasks:
        total_prompt = sum(r["cloud_usage"].get("prompt_tokens", 0) for r in cloud_tasks)
        total_completion = sum(r["cloud_usage"].get("completion_tokens", 0) for r in cloud_tasks)
        total_cloud_tokens = sum(r["cloud_usage"].get("total_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_read = sum(r["cloud_usage"].get("cache_read_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_write = sum(r["cloud_usage"].get("cache_write_tokens", 0) for r in cloud_tasks)
        total_cloud_req = sum(r["cloud_usage"].get("request_count", 0) for r in cloud_tasks)
        total_latency = sum(r["cloud_usage"].get("total_latency_ms", 0) for r in cloud_tasks)
        n_cloud_tasks = len(cloud_tasks)
        edge_tokens = (total_usage["total_input_tokens"] + total_usage["total_output_tokens"]) - (total_prompt + total_completion)
        denom = edge_tokens + total_cloud_tokens
        total_cloud_raw = max(total_prompt - total_cloud_cache_read - total_cloud_cache_write, 0)
        cache_hit_rate = round(total_cloud_cache_read / total_prompt, 4) if total_prompt else 0
        cache_write_rate = round(total_cloud_cache_write / total_prompt, 4) if total_prompt else 0
        total_cloud_usage = {
            "tasks_with_cloud_calls": n_cloud_tasks,
            "total_tasks": total_tasks,
            "cloud_call_rate": round(n_cloud_tasks / total_tasks, 4) if total_tasks else 0,
            "total_request_count": total_cloud_req,
            "avg_cloud_calls_per_task": round(total_cloud_req / total_tasks, 2) if total_tasks else 0,
            "total_prompt_tokens": total_prompt,
            "total_raw_input_tokens": total_cloud_raw,
            "total_cache_read_tokens": total_cloud_cache_read,
            "total_cache_write_tokens": total_cloud_cache_write,
            "total_completion_tokens": total_completion,
            "total_tokens": total_cloud_tokens,
            "cloud_token_share": round(total_cloud_tokens / denom, 4) if denom else 0,
            "cache_hit_rate": cache_hit_rate,
            "cache_write_rate": cache_write_rate,
            "total_latency_ms": total_latency,
            "avg_latency_ms": round(total_latency / total_cloud_req) if total_cloud_req else 0,
        }
        print(f"\n  [Cloud Model Usage]")
        print(f"  Tasks with cloud calls:  {n_cloud_tasks}/{total_tasks} ({total_cloud_usage['cloud_call_rate']*100:.1f}%)")
        print(f"  Total cloud requests:    {total_cloud_req}")
        print(f"  Avg cloud calls / task:  {total_cloud_usage['avg_cloud_calls_per_task']}")
        print(f"  Prompt tokens:           {total_prompt}  =  raw {total_cloud_raw} + cache_read {total_cloud_cache_read} + cache_write {total_cloud_cache_write}  (read {cache_hit_rate*100:.1f}%, write {cache_write_rate*100:.1f}%)")
        print(f"  Completion tokens:       {total_completion}")
        print(f"  Cloud token share:       {total_cloud_usage['cloud_token_share']*100:.2f}% (cloud / (local+cloud))")
        print(f"  Avg latency per call:    {total_cloud_usage['avg_latency_ms']}ms")
    #########

    from datetime import datetime
    date_tag = datetime.now().strftime("%Y%m%d")
    repeat_tag = f"_avg@{repeat}" if repeat > 1 else ""
    summary_path = output_dir / f"0_summary_all_{model_name}{repeat_tag}_{date_tag}.json"
    summary_path.write_text(
        json.dumps(
            {"global_avg": global_avg if total_tasks else None,
             "repeat": repeat,
             "task_count": total_tasks,
             "scored_task_count": scored_tasks,
             "missing_score_task_count": missing_score_tasks,
             **total_usage,
             "cloud_usage": total_cloud_usage,
             "edge_usage": {
                 "total_request_count": max(total_usage["total_request_count"] - (total_cloud_usage or {}).get("total_request_count", 0), 0),
                 "total_raw_input_tokens": max(total_usage["total_input_tokens"] - (total_cloud_usage or {}).get("total_raw_input_tokens", 0), 0),
                 "total_cache_read_tokens": max(total_usage["total_cache_read_tokens"] - (total_cloud_usage or {}).get("total_cache_read_tokens", 0), 0),
                 "total_cache_write_tokens": max(total_usage["total_cache_write_tokens"] - (total_cloud_usage or {}).get("total_cache_write_tokens", 0), 0),
                 "total_output_tokens": max(total_usage["total_output_tokens"] - (total_cloud_usage or {}).get("total_completion_tokens", 0), 0),
                 "total_tokens": max((total_usage["total_input_tokens"] + total_usage["total_cache_read_tokens"] + total_usage["total_output_tokens"]) - (total_cloud_usage or {}).get("total_tokens", 0), 0),
             } if total_cloud_usage else None,
             "results": results},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n  Global summary written to → {summary_path}")
    print("#" * 60)


def print_avg_summary(
    per_run_results: list[list[dict]],
    category: str,
    output_dir: Path,
    model_name: str,
    repeat: int,
) -> None:
    """Aggregate N runs into an avg@N summary with mean and std per task."""
    import statistics

    print(f"\n{'#'*60}")
    print(f"  avg@{repeat} Summary — {category}")
    print(f"{'#'*60}")

    def _extract_overall(r: dict) -> float | None:
        scores = r.get("scores", {})
        if r.get("error") or not scores or "error" in scores:
            return None
        numeric = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            return None
        return numeric.get("overall_score", sum(numeric.values()) / len(numeric))

    def _extract_task_key(r: dict) -> str:
        import re
        return re.sub(r'_run\d+', '', r["task_id"]).rsplit("_", 2)[0]

    task_runs: dict[str, list[float]] = {}
    task_privacy_runs: dict[str, list[float]] = {}

    for results in per_run_results:
        seen_this_run: set[str] = set()
        for r in results:
            tkey = _extract_task_key(r)
            seen_this_run.add(tkey)
            ov = _extract_overall(r)
            task_runs.setdefault(tkey, []).append(ov if ov is not None else 0.0)
            ps = (r.get("scores") or {}).get("privacy_score")
            if ps is not None:
                task_privacy_runs.setdefault(tkey, []).append(ps)
        for tkey in task_runs:
            if tkey not in seen_this_run:
                task_runs[tkey].append(0.0)

    def make_bar(val):
        return "█" * int(val * 10) + "░" * (10 - int(val * 10))

    final_scores_table = {}
    all_task_avgs = []
    pass_at_n_count = 0
    pass_pow_n_count = 0
    print(f"\n  Per-task avg@{repeat} scores:")

    for tkey in sorted(task_runs.keys()):
        runs = task_runs[tkey]
        avg_val = statistics.mean(runs)
        std_val = statistics.stdev(runs) if len(runs) > 1 else 0.0
        all_task_avgs.append(avg_val)

        any_passed = any(v >= PASS_THRESHOLD for v in runs)
        all_passed = all(v >= PASS_THRESHOLD for v in runs) and len(runs) == repeat
        if any_passed:
            pass_at_n_count += 1
        if all_passed:
            pass_pow_n_count += 1

        entry = {
            "overall_avg": round(avg_val, 4),
            "overall_std": round(std_val, 4),
            "overall_bar": make_bar(avg_val),
            f"pass@{repeat}": any_passed,
            f"pass^{repeat}": all_passed,
            "runs": [round(v, 4) for v in runs],
            "n_runs": len(runs),
        }
        p_runs = task_privacy_runs.get(tkey)
        if p_runs:
            entry["privacy_avg"] = round(statistics.mean(p_runs), 4)
            entry["privacy_std"] = round(statistics.stdev(p_runs) if len(p_runs) > 1 else 0.0, 4)
            entry["privacy_runs"] = [round(v, 4) for v in p_runs]

        final_scores_table[tkey] = entry
        bar = make_bar(avg_val)
        runs_str = ", ".join(f"{v:.2f}" for v in runs)
        pass_mark = "✓✓" if all_passed else ("✓" if any_passed else "✗")
        priv_str = ""
        if p_runs:
            priv_str = f"  privacy_avg: {entry['privacy_avg']:.2f}"
        print(f"    {bar} {avg_val:.4f} ±{std_val:.4f}  {tkey}  [{runs_str}] {pass_mark}{priv_str}")

    total_tasks = len(task_runs)
    global_avg = statistics.mean(all_task_avgs) if all_task_avgs else 0.0
    global_std = statistics.stdev(all_task_avgs) if len(all_task_avgs) > 1 else 0.0
    pass_at_n_rate = pass_at_n_count / total_tasks if total_tasks else 0.0
    pass_pow_n_rate = pass_pow_n_count / total_tasks if total_tasks else 0.0

    privacy_avgs = [e["privacy_avg"] for e in final_scores_table.values() if "privacy_avg" in e]
    privacy_global = statistics.mean(privacy_avgs) if privacy_avgs else None

    print(f"\n  Tasks: {total_tasks}")
    print(f"  Overall avg@{repeat}:  {make_bar(global_avg)} {global_avg:.4f} ±{global_std:.4f}")
    print(f"  pass@{repeat} (>={PASS_THRESHOLD}): {pass_at_n_count}/{total_tasks} ({pass_at_n_rate:.2%})")
    print(f"  pass^{repeat} (>={PASS_THRESHOLD}): {pass_pow_n_count}/{total_tasks} ({pass_pow_n_rate:.2%})")
    if privacy_global is not None:
        print(f"  Privacy avg@{repeat}:  {make_bar(privacy_global)} {privacy_global:.4f}")

    all_flat = [r for run in per_run_results for r in run]
    total_usage = {}
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "request_count", "cost_usd"):
        vals = [r.get("usage", {}).get(key, 0) for r in all_flat]
        total_usage[f"total_{key}"] = round(sum(vals), 6) if "cost" in key else sum(vals)
    total_usage["total_actual_input_tokens"] = total_usage["total_input_tokens"] + total_usage["total_cache_read_tokens"]
    total_usage["total_elapsed_time"] = round(
        sum(r.get("usage", {}).get("elapsed_time", 0.0) for r in all_flat), 2
    )
    turns_list = [r.get("usage", {}).get("n_turns", r.get("usage", {}).get("request_count", 0)) for r in all_flat]
    total_usage["total_turns"] = sum(turns_list)
    total_usage["avg_turns_per_task"] = round(sum(turns_list) / len(turns_list), 2) if turns_list else 0
    total_usage["_note"] = _usage_note(include_cache_write=False)

    avg_usage = {
        "avg_input_tokens": round(total_usage["total_input_tokens"] / repeat, 2),
        "avg_output_tokens": round(total_usage["total_output_tokens"] / repeat, 2),
        "avg_cache_read_tokens": round(total_usage["total_cache_read_tokens"] / repeat, 2),
        "avg_cache_write_tokens": round(total_usage["total_cache_write_tokens"] / repeat, 2),
        "avg_request_count": round(total_usage["total_request_count"] / repeat, 2),
        "avg_turns_per_task": round(total_usage["total_turns"] / repeat / total_tasks, 2) if total_tasks else 0,
        "avg_cost_usd": round(total_usage["total_cost_usd"] / repeat, 6),
        "avg_elapsed_time": round(total_usage["total_elapsed_time"] / repeat, 2),
    }

    from datetime import datetime
    date_tag = datetime.now().strftime("%Y%m%d")
    summary_path = output_dir / category / f"0_summary_{model_name}_avg@{repeat}_{date_tag}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    summary_data = {
        "category": category,
        "model": model_name,
        "repeat": repeat,
        "total_tasks": total_tasks,
        f"overall_avg@{repeat}": round(global_avg, 4),
        "overall_std": round(global_std, 4),
        "overall_bar": make_bar(global_avg),
        "pass_threshold": PASS_THRESHOLD,
        f"pass@{repeat}": pass_at_n_count,
        f"pass@{repeat}_rate": round(pass_at_n_rate, 4),
        f"pass^{repeat}": pass_pow_n_count,
        f"pass^{repeat}_rate": round(pass_pow_n_rate, 4),
        f"privacy_avg@{repeat}": round(privacy_global, 4) if privacy_global is not None else None,
        "usage": {
            "per_run_avg": avg_usage,
            "all_runs_total": total_usage,
        },
    }

    # Aggregate cloud_usage across all runs if any task has it.
    cloud_tasks = [r for r in all_flat if r.get("cloud_usage")]
    if cloud_tasks:
        total_cloud_prompt = sum(r["cloud_usage"].get("prompt_tokens", 0) for r in cloud_tasks)
        total_cloud_completion = sum(r["cloud_usage"].get("completion_tokens", 0) for r in cloud_tasks)
        total_cloud_tokens = sum(r["cloud_usage"].get("total_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_read = sum(r["cloud_usage"].get("cache_read_tokens", 0) for r in cloud_tasks)
        total_cloud_cache_write = sum(r["cloud_usage"].get("cache_write_tokens", 0) for r in cloud_tasks)
        total_cloud_req = sum(r["cloud_usage"].get("request_count", 0) for r in cloud_tasks)
        total_cloud_latency = sum(r["cloud_usage"].get("total_latency_ms", 0) for r in cloud_tasks)
        n_cloud_tasks = len(cloud_tasks)
        total_cloud_raw = _cloud_raw_input(total_cloud_prompt, total_cloud_cache_read, total_cloud_cache_write)
        cache_hit_rate_cloud = round(total_cloud_cache_read / total_cloud_prompt, 4) if total_cloud_prompt else 0
        cache_write_rate_cloud = round(total_cloud_cache_write / total_cloud_prompt, 4) if total_cloud_prompt else 0
        sample_source = (cloud_tasks[0].get("cloud_usage") or {}).get("source", "audit_jsonl")
        is_step_router = _is_step_router_model(model_name)
        if sample_source == "task_route":
            edge_only_input = max(total_usage["total_input_tokens"] - total_cloud_raw, 0)
            edge_only_cache_read = max(total_usage["total_cache_read_tokens"] - total_cloud_cache_read, 0)
            edge_only_cache_write = max(total_usage["total_cache_write_tokens"] - total_cloud_cache_write, 0)
            edge_only_output = max(total_usage["total_output_tokens"] - total_cloud_completion, 0)
            edge_only_req = max(total_usage["total_request_count"] - total_cloud_req, 0)
        elif is_step_router:
            cloud_chat_input = _cloud_input_as_chat_usage(total_cloud_prompt, total_cloud_cache_read)
            edge_only_input = max(total_usage["total_input_tokens"] - cloud_chat_input, 0)
            edge_only_cache_read = max(total_usage["total_cache_read_tokens"] - total_cloud_cache_read, 0)
            edge_only_cache_write = max(total_usage["total_cache_write_tokens"] - total_cloud_cache_write, 0)
            edge_only_output = max(total_usage["total_output_tokens"] - total_cloud_completion, 0)
            edge_only_req = max(total_usage["total_request_count"] - total_cloud_req, 0)
        else:
            edge_only_input = total_usage["total_input_tokens"]
            edge_only_cache_read = total_usage["total_cache_read_tokens"]
            edge_only_cache_write = total_usage["total_cache_write_tokens"]
            edge_only_output = total_usage["total_output_tokens"]
            edge_only_req = total_usage["total_request_count"]
        edge_only_tokens = edge_only_input + edge_only_cache_read + edge_only_cache_write + edge_only_output
        denom = edge_only_tokens + total_cloud_tokens
        cache_hit_rate = cache_hit_rate_cloud  # 兼容下方原引用
        #########
        avg_cloud_tasks = round(n_cloud_tasks / repeat, 2)
        cloud_call_rate = round(avg_cloud_tasks / total_tasks, 4) if total_tasks else 0
        avg_cloud_calls_per_task = round(total_cloud_req / repeat / total_tasks, 2) if total_tasks else 0
        summary_data["cloud_usage"] = {
            "source": sample_source,
            "total_tasks": total_tasks,
            "cloud_call_rate": cloud_call_rate,
            "avg_cloud_calls_per_task": avg_cloud_calls_per_task,
            "all_runs_total": {
                "tasks_with_cloud_calls": n_cloud_tasks,
                "total_request_count": total_cloud_req,
                "total_prompt_tokens": total_cloud_prompt,
                "total_raw_input_tokens": total_cloud_raw,
                "total_cache_read_tokens": total_cloud_cache_read,
                "total_cache_write_tokens": total_cloud_cache_write,
                "total_completion_tokens": total_cloud_completion,
                "total_tokens": total_cloud_tokens,
                "cloud_token_share": round(total_cloud_tokens / denom, 4) if denom else 0,
                "cache_hit_rate": cache_hit_rate,
                "cache_write_rate": cache_write_rate_cloud,
                "total_latency_ms": total_cloud_latency,
                "avg_latency_ms": round(total_cloud_latency / total_cloud_req) if total_cloud_req else 0,
            },
            "per_run_avg": {
                "avg_tasks_with_cloud_calls": avg_cloud_tasks,
                "avg_request_count": round(total_cloud_req / repeat, 2),
                "avg_prompt_tokens": round(total_cloud_prompt / repeat, 2),
                "avg_raw_input_tokens": round(total_cloud_raw / repeat, 2),
                "avg_cache_read_tokens": round(total_cloud_cache_read / repeat, 2),
                "avg_cache_write_tokens": round(total_cloud_cache_write / repeat, 2),
                "avg_output_tokens": round(total_cloud_completion / repeat, 2),
                "avg_tokens": round(total_cloud_tokens / repeat, 2),
                "avg_cloud_calls_per_task": avg_cloud_calls_per_task,
                "avg_latency_ms": round(total_cloud_latency / total_cloud_req) if total_cloud_req else 0,
                "cloud_token_share": round(total_cloud_tokens / denom, 4) if denom else 0,
                "cache_hit_rate": cache_hit_rate,
                "cache_write_rate": cache_write_rate_cloud,
            },
        }
        edge_share = round(edge_only_tokens / denom, 4) if denom else 0
        edge_cache_hit_rate = round(edge_only_cache_read / (edge_only_input + edge_only_cache_read + edge_only_cache_write), 4) if (edge_only_input + edge_only_cache_read + edge_only_cache_write) else 0
        summary_data["edge_usage"] = {
            "all_runs_total": {
                "total_request_count": edge_only_req,
                "total_input_tokens": edge_only_input,
                "total_raw_input_tokens": edge_only_input,
                "total_cache_read_tokens": edge_only_cache_read,
                "total_cache_write_tokens": edge_only_cache_write,
                "total_output_tokens": edge_only_output,
                "total_tokens": edge_only_tokens,
                "edge_token_share": edge_share,
                "cache_hit_rate": edge_cache_hit_rate,
            },
            "per_run_avg": {
                "avg_request_count": round(edge_only_req / repeat, 2),
                "avg_raw_input_tokens": round(edge_only_input / repeat, 2),
                "avg_cache_read_tokens": round(edge_only_cache_read / repeat, 2),
                "avg_cache_write_tokens": round(edge_only_cache_write / repeat, 2),
                "avg_output_tokens": round(edge_only_output / repeat, 2),
                "avg_tokens": round(edge_only_tokens / repeat, 2),
                "edge_token_share": edge_share,
                "cache_hit_rate": edge_cache_hit_rate,
            },
        }
        if is_step_router and sample_source == "audit_jsonl":
            combined_usage = _normalize_step_router_total_usage(
                total_usage,
                summary_data["edge_usage"]["all_runs_total"],
                summary_data["cloud_usage"]["all_runs_total"],
            )
            edge_total_usage = _edge_usage_as_total_usage(
                total_usage,
                summary_data["edge_usage"]["all_runs_total"],
                model_name,
                total_tasks * repeat,
            )
            summary_data["usage"] = {
                "per_run_avg": {
                    "avg_input_tokens": round(edge_total_usage["total_input_tokens"] / repeat, 2),
                    "avg_output_tokens": round(edge_total_usage["total_output_tokens"] / repeat, 2),
                    "avg_cache_read_tokens": round(edge_total_usage["total_cache_read_tokens"] / repeat, 2),
                    "avg_cache_write_tokens": round(edge_total_usage["total_cache_write_tokens"] / repeat, 2),
                    "avg_request_count": round(edge_total_usage["total_request_count"] / repeat, 2),
                    "avg_turns_per_task": round(edge_total_usage["total_turns"] / repeat / total_tasks, 2) if total_tasks else 0,
                    "avg_cost_usd": round(edge_total_usage["total_cost_usd"] / repeat, 6),
                    "avg_elapsed_time": round(edge_total_usage["total_elapsed_time"] / repeat, 2),
                },
                "all_runs_total": edge_total_usage,
            }
            summary_data["combined_usage"] = {
                "per_run_avg": {
                    "avg_input_tokens": round(combined_usage["total_input_tokens"] / repeat, 2),
                    "avg_output_tokens": round(combined_usage["total_output_tokens"] / repeat, 2),
                    "avg_cache_read_tokens": round(combined_usage["total_cache_read_tokens"] / repeat, 2),
                    "avg_cache_write_tokens": round(combined_usage["total_cache_write_tokens"] / repeat, 2),
                    "avg_request_count": round(combined_usage["total_request_count"] / repeat, 2),
                    "avg_turns_per_task": round(combined_usage["total_turns"] / repeat / total_tasks, 2) if total_tasks else 0,
                    "avg_cost_usd": round(combined_usage["total_cost_usd"] / repeat, 6),
                    "avg_elapsed_time": round(combined_usage["total_elapsed_time"] / repeat, 2),
                },
                "all_runs_total": combined_usage,
            }
    elif (task_routed_cloud := [r for r in all_flat if r.get("task_route") == "CLOUD"]):
        cloud_in_raw    = sum(r.get("usage", {}).get("input_tokens", 0) for r in task_routed_cloud)
        cloud_cache_r   = sum(r.get("usage", {}).get("cache_read_tokens", 0) for r in task_routed_cloud)
        cloud_cache_w   = sum(r.get("usage", {}).get("cache_write_tokens", 0) for r in task_routed_cloud)
        cloud_out       = sum(r.get("usage", {}).get("output_tokens", 0) for r in task_routed_cloud)
        cloud_req       = sum(r.get("usage", {}).get("request_count", 0) for r in task_routed_cloud)
        cloud_elapsed   = sum(r.get("usage", {}).get("elapsed_time", 0) for r in task_routed_cloud)
        cloud_prompt    = cloud_in_raw + cloud_cache_r + cloud_cache_w
        cloud_tok       = cloud_prompt + cloud_out
        n_cloud         = len(task_routed_cloud)  # 跨 repeat 累计
        avg_cloud_tasks = round(n_cloud / repeat, 2)
        cloud_call_rate = round(avg_cloud_tasks / total_tasks, 4) if total_tasks else 0
        avg_cloud_calls_per_task = round(cloud_req / repeat / total_tasks, 2) if total_tasks else 0

        # task_route 模式下，edge/cloud 不重叠（每个 task 整段要么云要么端），直接相减
        edge_only_input       = max(total_usage["total_input_tokens"]        - cloud_in_raw,  0)
        edge_only_cache_read  = max(total_usage["total_cache_read_tokens"]   - cloud_cache_r, 0)
        edge_only_cache_write = max(total_usage["total_cache_write_tokens"]  - cloud_cache_w, 0)
        edge_only_output      = max(total_usage["total_output_tokens"]       - cloud_out,     0)
        edge_only_req         = max(total_usage["total_request_count"]       - cloud_req,     0)
        edge_only_tokens      = edge_only_input + edge_only_cache_read + edge_only_cache_write + edge_only_output
        denom                 = edge_only_tokens + cloud_tok
        cloud_token_share     = round(cloud_tok / denom, 4) if denom else 0
        edge_token_share      = round(edge_only_tokens / denom, 4) if denom else 0
        cloud_cache_hit_rate  = round(cloud_cache_r / cloud_prompt, 4) if cloud_prompt else 0
        cloud_cache_write_rate = round(cloud_cache_w / cloud_prompt, 4) if cloud_prompt else 0
        edge_input_total      = edge_only_input + edge_only_cache_read + edge_only_cache_write
        edge_cache_hit_rate   = round(edge_only_cache_read / edge_input_total, 4) if edge_input_total else 0

        summary_data["cloud_usage"] = {
            "source": "task_route",
            "total_tasks": total_tasks,
            "cloud_call_rate": cloud_call_rate,
            "avg_cloud_calls_per_task": avg_cloud_calls_per_task,
            "all_runs_total": {
                "tasks_with_cloud_calls": n_cloud,
                "total_request_count": cloud_req,
                "total_prompt_tokens": cloud_prompt,
                "total_raw_input_tokens": cloud_in_raw,
                "total_cache_read_tokens": cloud_cache_r,
                "total_cache_write_tokens": cloud_cache_w,
                "total_completion_tokens": cloud_out,
                "total_tokens": cloud_tok,
                "cloud_token_share": cloud_token_share,
                "cache_hit_rate": cloud_cache_hit_rate,
                "cache_write_rate": cloud_cache_write_rate,
                "total_elapsed_time": round(cloud_elapsed, 2),
            },
            "per_run_avg": {
                "avg_tasks_with_cloud_calls": avg_cloud_tasks,
                "avg_request_count": round(cloud_req / repeat, 2),
                "avg_prompt_tokens": round(cloud_prompt / repeat, 2),
                "avg_raw_input_tokens": round(cloud_in_raw / repeat, 2),
                "avg_cache_read_tokens": round(cloud_cache_r / repeat, 2),
                "avg_cache_write_tokens": round(cloud_cache_w / repeat, 2),
                "avg_output_tokens": round(cloud_out / repeat, 2),
                "avg_tokens": round(cloud_tok / repeat, 2),
                "avg_cloud_calls_per_task": avg_cloud_calls_per_task,
                "cloud_token_share": cloud_token_share,
                "cache_hit_rate": cloud_cache_hit_rate,
                "cache_write_rate": cloud_cache_write_rate,
                "avg_elapsed_time": round(cloud_elapsed / repeat, 2),
            },
        }
        summary_data["edge_usage"] = {
            "all_runs_total": {
                "total_request_count": edge_only_req,
                "total_input_tokens": edge_only_input,
                "total_raw_input_tokens": edge_only_input,
                "total_cache_read_tokens": edge_only_cache_read,
                "total_cache_write_tokens": edge_only_cache_write,
                "total_output_tokens": edge_only_output,
                "total_tokens": edge_only_tokens,
                "edge_token_share": edge_token_share,
                "cache_hit_rate": edge_cache_hit_rate,
            },
            "per_run_avg": {
                "avg_request_count": round(edge_only_req / repeat, 2),
                "avg_raw_input_tokens": round(edge_only_input / repeat, 2),
                "avg_cache_read_tokens": round(edge_only_cache_read / repeat, 2),
                "avg_cache_write_tokens": round(edge_only_cache_write / repeat, 2),
                "avg_output_tokens": round(edge_only_output / repeat, 2),
                "avg_tokens": round(edge_only_tokens / repeat, 2),
                "edge_token_share": edge_token_share,
                "cache_hit_rate": edge_cache_hit_rate,
            },
        }
    else:
        has_routing = any(r.get("task_route") for r in all_flat)
        if has_routing:
            edge_input  = total_usage["total_input_tokens"]
            edge_cr     = total_usage["total_cache_read_tokens"]
            edge_cw     = total_usage["total_cache_write_tokens"]
            edge_out    = total_usage["total_output_tokens"]
            edge_req    = total_usage["total_request_count"]
            edge_tok    = edge_input + edge_cr + edge_cw + edge_out
            edge_input_total = edge_input + edge_cr + edge_cw
            edge_cache_hit_rate = round(edge_cr / edge_input_total, 4) if edge_input_total else 0
            summary_data["edge_usage"] = {
                "all_runs_total": {
                    "total_request_count": edge_req,
                    "total_input_tokens": edge_input,
                    "total_raw_input_tokens": edge_input,
                    "total_cache_read_tokens": edge_cr,
                    "total_cache_write_tokens": edge_cw,
                    "total_output_tokens": edge_out,
                    "total_tokens": edge_tok,
                    "edge_token_share": 1.0,
                    "cache_hit_rate": edge_cache_hit_rate,
                    "note": "all tasks routed LOCAL — no cloud calls",
                },
                "per_run_avg": {
                    "avg_request_count": round(edge_req / repeat, 2),
                    "avg_raw_input_tokens": round(edge_input / repeat, 2),
                    "avg_cache_read_tokens": round(edge_cr / repeat, 2),
                    "avg_cache_write_tokens": round(edge_cw / repeat, 2),
                    "avg_output_tokens": round(edge_out / repeat, 2),
                    "avg_tokens": round(edge_tok / repeat, 2),
                },
            }
            summary_data["cloud_usage"] = None
        else:
            summary_data["edge_usage"] = None
            summary_data["cloud_usage"] = None
            summary_data["_routing_note"] = "single-model run, no edge/cloud split"
    summary_data["final_scores"] = final_scores_table
    summary_path.write_text(
        json.dumps(summary_data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n  avg@{repeat} summary written to → {summary_path}")
    print("#" * 60)
#########