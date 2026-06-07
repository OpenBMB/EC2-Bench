"""Edge-cloud collaboration helpers (prompts, plan generators, etc).

Each collaboration mode lives in its own clearly-named section below.
Generic helpers that can be reused across modes (skill manifest builder,
SKILL.md frontmatter parser) live in the SHARED section near the top.

Currently registered modes:
  * pipeline-plan-executor  — cloud LLM drafts a task-level skeleton from
                              the instruction alone; edge agent executes it
                              with local context. Inspired by CoGenesis
                              (ACL 2024) sketch-based variant.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ============================================================================
# SHARED — helpers reusable across collaboration modes
# ============================================================================

def _extract_skill_description(content: str) -> str:
    """Extract a one-line description from a SKILL.md (mirrors advisor logic).

    Try YAML frontmatter ``description:`` first, then fall back to the first
    non-empty, non-heading, non-frontmatter-fence line. Cap at 200 chars to
    keep prompts compact and to discourage callers from pasting SKILL.md
    bodies into the description field.
    """
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        desc_match = re.search(
            r'^description:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE,
        )
        if desc_match:
            return desc_match.group(1).strip()[:200]
    for line in content.splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("---"):
            continue
        return t[:200]
    return ""


def _build_skill_manifest(
    skills: Iterable[str] | None,
    skills_path: str | Path | None,
) -> str:
    """Return a "## Skills available" block, or "" when no skills apply.

    Same anti-hallucination framing as advisor: the cloud model sees only
    skill names + one-line descriptions, NOT SKILL.md contents, and is
    instructed to defer to the executor for actual paths/commands.
    """
    if not skills or not skills_path:
        return ""
    base = Path(skills_path)
    if not base.is_dir():
        return ""
    entries: list[str] = []
    for name in skills:
        if not name or name.startswith("cloud-advisor"):
            continue
        skill_md = base / name / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read %s: %s", skill_md, exc)
            continue
        desc = _extract_skill_description(content)
        entries.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
    if not entries:
        return ""
    header = (
        "The executor has the following skills available "
        "(name + one-line description):\n"
    )
    return header + "\n".join(entries)


# ============================================================================
# MODE: pipeline-plan-executor
# ----------------------------------------------------------------------------
# Cloud LLM drafts a task-level *sketch* (skeleton, not full content) from
# the task instruction alone. Edge agent then executes against its local
# context (files, tools, SKILL.md, runtime), using the sketch as a hint.
#
# The system prompt below follows CoGenesis (ACL 2024) Table 8 closely:
# "skeleton not full content, list of points, each point very short".
# We use < 10 words per point (their original was 3-5 words for writing
# tasks; agent tasks need slightly more semantic room per point).
# Skill manifest is injected as a plain tool inventory when present.
# ============================================================================

_PIPELINE_PLAN_PROMPT_HEAD = """You're an organizer responsible for only giving the skeleton for completing the task that an executor agent will perform."""

_PIPELINE_PLAN_PROMPT_TAIL = """Provide the skeleton in a list of points. Instead of writing a full sentence, each skeleton point should be very short with fewer than 10 words."""


def build_pipeline_plan_system_prompt(
    skills: Iterable[str] | None = None,
    skills_path: str | Path | None = None,
) -> str:
    """Compose the pipeline-plan-executor system prompt.

    Inserts the skill manifest (when ``skills`` is non-empty and at least one
    SKILL.md is readable) between the executor-environment section and the
    output rules. Returns the same prompt unchanged when no manifest applies.
    """
    manifest = _build_skill_manifest(skills, skills_path)
    middle = f"\n\n{manifest}\n" if manifest else ""
    return f"{_PIPELINE_PLAN_PROMPT_HEAD}{middle}\n\n{_PIPELINE_PLAN_PROMPT_TAIL}"


# Backward-compatible default prompt (no skill manifest). Prefer
# ``build_pipeline_plan_system_prompt(skills, skills_path)`` for task-aware
# prompts so the cloud planner can reference available skills.
PIPELINE_PLAN_SYSTEM_PROMPT = build_pipeline_plan_system_prompt()


def generate_pipeline_plan(
    task_prompt: str,
    base_url: str,
    api_key: str,
    model_id: str,
    skills: Iterable[str] | None = None,
    skills_path: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Ask the cloud model for a task-level plan and return (plan_text, audit_entry).

    ``skills`` + ``skills_path`` enable advisor style skill-manifest
    injection (names + frontmatter descriptions, no SKILL.md contents).
    """
    if not base_url:
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Intentionally do NOT fall back to JUDGE_API_KEY: the privacy/utility
        # judge and the cloud collaborator are conceptually separate roles
        # (evaluator vs. system-under-test), must be billable / auditable
        # independently, and may even live behind different providers.
        raise RuntimeError(
            "pipeline-plan-executor requires a cloud planner API key — set "
            "OPENROUTER_API_KEY (or pass api_key explicitly). JUDGE_API_KEY "
            "is reserved for the evaluator and is not used here."
        )

    system_prompt = build_pipeline_plan_system_prompt(
        skills=skills, skills_path=skills_path,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]


    payload = json.dumps({
        "model": model_id,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    t0 = time.time()
    # Thinking models can spend 60-120s reasoning on harder tasks; leave
    # generous headroom so a slow plan doesn't fail the whole task.
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read())
    latency_ms = int((time.time() - t0) * 1000)
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    # Read ONLY ``message.content``. The plan must be the model's final
    # answer, not its scratchpad — ``reasoning`` / ``reasoning_content`` is
    # chain-of-thought and is intentionally ignored. If content is empty
    # (e.g. ``max_tokens`` was eaten by thinking before the model could
    # finalize), raise so the caller surfaces the problem rather than
    # silently injecting raw reasoning into the executor's prompt.
    plan_text = (msg.get("content") or "").strip()
    if not plan_text:
        finish = (data.get("choices") or [{}])[0].get("finish_reason")
        raise RuntimeError(
            "pipeline-plan-executor cloud planner returned empty content "
            f"(finish_reason={finish!r}); raise max_tokens or shorten the prompt."
        )
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    # ``prompt_tokens_details`` may be explicitly null on some backends (e.g.
    # vLLM); ``dict.get(key, default)`` does NOT swap null for the default,
    # so we use ``or {}`` to coerce.
    prompt_details = usage.get("prompt_tokens_details") or {}
    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "mode": "pipeline-plan-executor",
        "session_id": "pipeline_plan",
        "input": messages,
        "query": "",
        "output": plan_text,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": usage.get("total_tokens", prompt_tokens + completion_tokens),
        "cache_read_tokens": prompt_details.get("cached_tokens", 0),
        "cache_write_tokens": 0,
        "latency_ms": latency_ms,
        "model": model_id,
    }
    return plan_text, audit_entry
