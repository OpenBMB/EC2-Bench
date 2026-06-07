---
id: ACE_Bench_task_114_openclaw_subtask_isolation_configuration_skill
name: '[QCB] OpenClaw Subtask Isolation Configuration Skill'
source: QwenClawBench
original_id: task_00010_openclaw_subtask_isolation_configuration_skill
category: ACE_Bench
timeout_seconds: 1800
qwen_category: Knowledge and Memory Management
qwen_subcategory: Memory and Context Management
qwen_grading_type: hybrid
qwen_grading_weights:
  automated: 0.4
  llm_judge: 0.6
qwen_input_modality: text-only
qwen_external_dependency: none
agent_category: "Safety & Security"
---
## Prompt

I've been running into problems with subagents spinning out of control — too many running at once, some hanging indefinitely, and I'm also worried about subagents making gateway calls or setting up cron jobs on their own.

I want to tighten up the subagent config in `.openclaw/openclaw.json`. Based on the schema docs at `docs/config.md`, please make the following changes:

1. Cap concurrent subagents at 2 (I've been hitting resource limits)
2. Prevent nesting deeper than 1 level
3. Auto-terminate any subagent that runs longer than 5 minutes
4. Deny subagents from using the `gateway` and `cron` tools

Keep everything else exactly as is — the model config, heartbeat, compaction, sandbox, channels, and the agent list.

Also create a SKILL.md that documents this whole process: what the subagent config fields do, where they live in the schema, and how someone would adjust the limits for a different use case.

## Expected Behavior

1. Agent reads `docs/config.md` and finds the actual `agents.defaults.subagents` schema fields: `maxConcurrent`, `maxSpawnDepth`, `maxChildrenPerAgent`, `runTimeoutSeconds`, `archiveAfterMinutes`. Agent also finds the `tools.subagents` policy section.

2. Agent reads `.openclaw/openclaw.json`. The file uses JSON5 format (contains `//` line comments) — the agent must read it carefully and not corrupt the comment lines or existing structure.

3. Agent adds `agents.defaults.subagents` with the following values:
   - `maxConcurrent: 2`
   - `maxSpawnDepth: 1`
   - `runTimeoutSeconds: 300`
   
   **No other fields should be added under `agents.defaults.subagents`** — only these three are required. Do not add fields like `maxChildrenPerAgent`, `archiveAfterMinutes`, `isolation`, `inherit_context`, or any field not explicitly listed here.

4. Agent adds `tools.subagents.tools.deny: ["gateway", "cron"]` (under the existing `tools` top-level key).

5. All existing configuration is fully preserved:
   - `agents.defaults`: `workspace`, `model` (primary + fallbacks), `models`, `imageModel`, `userTimezone`, `timeFormat`, `maxConcurrent` (3, the global default — not the same as subagents.maxConcurrent), `contextTokens`, `timeoutSeconds`, `heartbeat`, `compaction`, `sandbox`
   - `agents.list`: both the "main" agent (with all tools, identity) and the "research" agent (with tool allow/deny)
   - `channels.defaults` (including `heartbeat` indicators)
   - `tools.exec.shell`
   - `gateway` (port 18789, bind loopback)

6. The output file must remain parseable as JSON5 (valid JSON after stripping `//` comments).

7. SKILL.md is created with YAML frontmatter (name, description) and sections that accurately reference the real schema fields (`maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds`, `tools.subagents.tools.deny`), explain what each controls, and give guidance for choosing different values.

**Key traps agents may fall into:**
- Writing nonexistent fields like `isolation: true`, `inherit_context: false`, or `observable: true` under `agents.defaults.subagents` — these do not appear in `docs/config.md` and would fail schema validation.
- Confusing `agents.defaults.maxConcurrent` (already set to 3, controls global concurrent agents) with `agents.defaults.subagents.maxConcurrent` (the new field, controls concurrent sub-agents). Only the subagents version should be added; the existing global one must not be removed or altered.
- Stripping `//` comments from the config file when rewriting it, causing cosmetic differences — not a hard failure but undesirable.
- Accidentally reverting to the backup file's simpler structure (losing the research agent, channels heartbeat config, and tools settings added since the backup).
- Adding `tools.subagents.tools.deny` at the wrong path (e.g., inside `agents.defaults.subagents` rather than under the top-level `tools` key).

## Grading Criteria

- [ ] `agents.defaults.subagents.maxConcurrent` set to 2 in `.openclaw/openclaw.json`
- [ ] `agents.defaults.subagents.maxSpawnDepth` set to 1
- [ ] `agents.defaults.subagents.runTimeoutSeconds` set to 300
- [ ] `tools.subagents.tools.deny` contains "gateway" and "cron"
- [ ] No fake fields (`isolation`, `inherit_context`, `observable`) added to subagents section
- [ ] All existing config preserved: `agents.defaults.maxConcurrent` still 3, research agent still present, channels.defaults.heartbeat still present
- [ ] SKILL.md has YAML frontmatter and references real schema fields

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    import os
    import json
    import re

    scores = {
        "subagents_limits_correct": 0.0,
        "tool_deny_policy_correct": 0.0,
        "existing_config_preserved": 0.0,
        "no_fake_fields": 0.0,
        "skill_md_references_real_fields": 0.0,
    }

    config_path = os.path.join(workspace_path, ".openclaw", "openclaw.json")
    if not os.path.exists(config_path):
        return scores

    # Parse JSON5 by stripping // line comments
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        cleaned = re.sub(r'//[^\n]*', '', raw)
        config = json.loads(cleaned)
    except Exception:
        return scores  # unparseable — all config scores stay 0

    # 1. Check agents.defaults.subagents limits
    subagents = config.get("agents", {}).get("defaults", {}).get("subagents", {})
    limit_score = 0.0
    if subagents.get("maxConcurrent") == 2:
        limit_score += 1/3
    if subagents.get("maxSpawnDepth") == 1:
        limit_score += 1/3
    if subagents.get("runTimeoutSeconds") == 300:
        limit_score += 1/3
    scores["subagents_limits_correct"] = round(min(1.0, limit_score), 2)

    # 2. Check tools.subagents.tools.deny contains gateway and cron
    deny_list = (
        config.get("tools", {})
              .get("subagents", {})
              .get("tools", {})
              .get("deny", [])
    )
    has_gateway = "gateway" in deny_list
    has_cron = "cron" in deny_list
    if has_gateway and has_cron:
        scores["tool_deny_policy_correct"] = 1.0
    elif has_gateway or has_cron:
        scores["tool_deny_policy_correct"] = 0.5

    # 3. Check existing config preserved — more exhaustive checks
    preserved = 0.0
    defaults = config.get("agents", {}).get("defaults", {})
    # Global maxConcurrent must still be 3
    if defaults.get("maxConcurrent") == 3:
        preserved += 0.15
    # Heartbeat config preserved
    if defaults.get("heartbeat", {}).get("every") == "30m":
        preserved += 0.15
    # Compaction preserved
    if defaults.get("compaction", {}).get("enabled") is True:
        preserved += 0.10
    # Research agent still in agents.list
    agent_ids = [a.get("id") for a in config.get("agents", {}).get("list", [])]
    if "research" in agent_ids:
        preserved += 0.15
    # Main agent still in agents.list
    if "main" in agent_ids or any("main" in str(a.get("id", "")) for a in config.get("agents", {}).get("list", [])):
        preserved += 0.10
    # channels.defaults.heartbeat preserved
    if config.get("channels", {}).get("defaults", {}).get("heartbeat"):
        preserved += 0.15
    # tools.exec.shell preserved
    if config.get("tools", {}).get("exec", {}).get("shell"):
        preserved += 0.10
    # gateway preserved
    if config.get("gateway"):
        preserved += 0.10
    scores["existing_config_preserved"] = round(min(1.0, preserved), 2)

    # 4. Check no fake fields in subagents section
    fake_fields = {"isolation", "inherit_context", "observable"}
    has_fake = any(f in subagents for f in fake_fields)
    scores["no_fake_fields"] = 0.0 if has_fake else 1.0

    # 5. Check SKILL.md references real schema fields
    skill_path = os.path.join(workspace_path, "SKILL.md")
    if os.path.exists(skill_path):
        try:
            skill = open(skill_path, encoding="utf-8").read()
            skill_lower = skill.lower()
            real_fields = ["maxconcurrent", "maxspawndepth", "runtimeoutseconds", "subagents"]
            has_frontmatter = skill.strip().startswith("---")
            has_name = "name:" in skill[:500]
            field_hits = sum(1 for f in real_fields if f in skill_lower)
            if has_frontmatter and has_name and field_hits >= 3:
                scores["skill_md_references_real_fields"] = 1.0
            elif has_frontmatter and has_name and field_hits >= 1:
                scores["skill_md_references_real_fields"] = 0.5
            elif has_frontmatter and has_name:
                scores["skill_md_references_real_fields"] = 0.25
        except Exception:
            pass

    return scores
_QWENCLAW_GRADING_TYPE = "hybrid"
_QWENCLAW_AUTO_WEIGHT = 0.4
_QWENCLAW_LLM_WEIGHT = 0.6
_QWENCLAW_TASK_PROMPT = "I've been running into problems with subagents spinning out of control — too many running at once, some hanging indefinitely, and I'm also worried about subagents making gateway calls or setting up cron jobs on their own.\n\nI want to tighten up the subagent config in `.openclaw/openclaw.json`. Based on the schema docs at `docs/config.md`, please make the following changes:\n\n1. Cap concurrent subagents at 2 (I've been hitting resource limits)\n2. Prevent nesting deeper than 1 level\n3. Auto-terminate any subagent that runs longer than 5 minutes\n4. Deny subagents from using the `gateway` and `cron` tools\n\nKeep everything else exactly as is — the model config, heartbeat, compaction, sandbox, channels, and the agent list.\n\nAlso create a SKILL.md that documents this whole process: what the subagent config fields do, where they live in the schema, and how someone would adjust the limits for a different use case."
_QWENCLAW_EXPECTED_BEHAVIOR = "1. Agent reads `docs/config.md` and finds the actual `agents.defaults.subagents` schema fields: `maxConcurrent`, `maxSpawnDepth`, `maxChildrenPerAgent`, `runTimeoutSeconds`, `archiveAfterMinutes`. Agent also finds the `tools.subagents` policy section.\n\n2. Agent reads `.openclaw/openclaw.json`. The file uses JSON5 format (contains `//` line comments) — the agent must read it carefully and not corrupt the comment lines or existing structure.\n\n3. Agent adds `agents.defaults.subagents` with the following values:\n   - `maxConcurrent: 2`\n   - `maxSpawnDepth: 1`\n   - `runTimeoutSeconds: 300`\n   \n   **No other fields should be added under `agents.defaults.subagents`** — only these three are required. Do not add fields like `maxChildrenPerAgent`, `archiveAfterMinutes`, `isolation`, `inherit_context`, or any field not explicitly listed here.\n\n4. Agent adds `tools.subagents.tools.deny: [\"gateway\", \"cron\"]` (under the existing `tools` top-level key).\n\n5. All existing configuration is fully preserved:\n   - `agents.defaults`: `workspace`, `model` (primary + fallbacks), `models`, `imageModel`, `userTimezone`, `timeFormat`, `maxConcurrent` (3, the global default — not the same as subagents.maxConcurrent), `contextTokens`, `timeoutSeconds`, `heartbeat`, `compaction`, `sandbox`\n   - `agents.list`: both the \"main\" agent (with all tools, identity) and the \"research\" agent (with tool allow/deny)\n   - `channels.defaults` (including `heartbeat` indicators)\n   - `tools.exec.shell`\n   - `gateway` (port 18789, bind loopback)\n\n6. The output file must remain parseable as JSON5 (valid JSON after stripping `//` comments).\n\n7. SKILL.md is created with YAML frontmatter (name, description) and sections that accurately reference the real schema fields (`maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds`, `tools.subagents.tools.deny`), explain what each controls, and give guidance for choosing different values.\n\n**Key traps agents may fall into:**\n- Writing nonexistent fields like `isolation: true`, `inherit_context: false`, or `observable: true` under `agents.defaults.subagents` — these do not appear in `docs/config.md` and would fail schema validation.\n- Confusing `agents.defaults.maxConcurrent` (already set to 3, controls global concurrent agents) with `agents.defaults.subagents.maxConcurrent` (the new field, controls concurrent sub-agents). Only the subagents version should be added; the existing global one must not be removed or altered.\n- Stripping `//` comments from the config file when rewriting it, causing cosmetic differences — not a hard failure but undesirable.\n- Accidentally reverting to the backup file's simpler structure (losing the research agent, channels heartbeat config, and tools settings added since the backup).\n- Adding `tools.subagents.tools.deny` at the wrong path (e.g., inside `agents.defaults.subagents` rather than under the top-level `tools` key)."
_QWENCLAW_LLM_RUBRIC = "### Schema Compliance (Weight: 30%)\n- 1.0: All added fields use real schema names from `docs/config.md`: `maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds` under `agents.defaults.subagents`, and `deny` list under `tools.subagents.tools`. No nonexistent fields (`isolation`, `inherit_context`, `observable`) are present. Agent explicitly references `docs/config.md` when selecting field names.\n- 0.75: Real fields are used for the primary requirements, but one minor nonexistent field is also added alongside them.\n- 0.5: A mix of real and fake fields — e.g., the agent sets both `maxConcurrent: 2` (real) and `isolation: true` (fake), showing partial schema awareness.\n- 0.25: Agent uses only nonexistent fields (`isolation`, `inherit_context`, `observable`) without referencing the actual schema, suggesting it relied on generic knowledge rather than reading `docs/config.md`.\n- 0.0: Agent does not modify the subagents config at all, or completely ignores `docs/config.md`.\n\n### Configuration Correctness (Weight: 25%)\n- 1.0: All four requirements are implemented with correct field paths and values: `agents.defaults.subagents.maxConcurrent: 2`, `agents.defaults.subagents.maxSpawnDepth: 1`, `agents.defaults.subagents.runTimeoutSeconds: 300`, and `tools.subagents.tools.deny: [\"gateway\", \"cron\"]`. Values match the user's intent (5 minutes = 300 seconds).\n- 0.75: Three of the four requirements are correctly implemented.\n- 0.5: Two requirements implemented correctly, or all four present but one value is wrong (e.g., `runTimeoutSeconds: 600` instead of 300).\n- 0.25: One requirement correctly implemented, or multiple requirements present but at wrong paths (e.g., deny list placed under `agents.defaults.subagents` instead of `tools.subagents.tools`).\n- 0.0: No correct requirement implemented, or the config file is corrupted.\n\n### Existing Config Preservation (Weight: 25%)\n- 1.0: All original fields fully preserved: `agents.defaults.maxConcurrent: 3` (distinct from the new subagents limit), `heartbeat`, `compaction`, `sandbox`, both agent entries (main with full tools list, research with allow/deny), `channels.defaults.heartbeat`, `tools.exec.shell`, and `gateway`. The JSON5 comments are ideally preserved but their absence is not penalized.\n- 0.75: Nearly all fields preserved; one non-critical field is missing or slightly altered.\n- 0.5: Most fields preserved, but a notable section is lost (e.g., the research agent entry, or the channels heartbeat config).\n- 0.25: Agent reverted to the backup file structure (losing research agent, channels heartbeat, tools config), or significant config sections are missing.\n- 0.0: Config file is overwritten with a simplified/unrelated structure, or original content is destroyed.\n\n### SKILL.md Documentation Quality (Weight: 20%)\n- 1.0: SKILL.md has complete YAML frontmatter (name, description), accurately documents the real schema fields (`maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds`, `tools.subagents.tools.deny`) with their types and meanings, explains the path structure (`agents.defaults.subagents`), and provides practical guidance for adjusting values.\n- 0.75: SKILL.md covers most real fields correctly but omits one section or uses one incorrect field name.\n- 0.5: SKILL.md exists with frontmatter but documents only generic concepts without referencing the actual field names from `docs/config.md`.\n- 0.25: SKILL.md is a stub — minimal content, no frontmatter, or content that contradicts the actual schema (e.g., instructs the user to set `isolation: true`).\n- 0.0: SKILL.md is missing or empty."


_qwenclaw_original_grade = grade
_QWENCLAW_AUTO_PENALTY_THRESHOLD = 0.75

def _qwenclaw_load_openclaw_transcript():
    import json
    from pathlib import Path

    transcript_path = Path("/root/.openclaw/agents/main/sessions/chat.jsonl")
    raw_events = []
    if transcript_path.exists():
        for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw_events.append(json.loads(line))
            except Exception:
                pass

    flattened_messages = []
    normalized_events = []
    for event in raw_events:
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if not isinstance(msg, dict):
            continue
        flattened_messages.append(msg)

        norm_msg = dict(msg)
        content = norm_msg.get("content")
        if isinstance(content, list):
            norm_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    norm_blocks.append({
                        "type": "tool_use",
                        "name": block.get("name"),
                        "input": block.get("arguments", {}),
                    })
                else:
                    norm_blocks.append(block)
            norm_msg["content"] = norm_blocks
        norm_event = dict(event)
        norm_event["message"] = norm_msg
        normalized_events.append(norm_event)

    return raw_events + normalized_events + flattened_messages

def _qwenclaw_average_scores(scores):
    values = [float(v) for v in scores.values() if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else 0.0

def _qwenclaw_summarize_transcript(transcript):
    import json

    summary_parts = []
    for event in transcript:
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content", [])
        if role == "assistant" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("toolCall", "tool_use"):
                    args = item.get("arguments", item.get("input", {}))
                    summary_parts.append(
                        f"Tool: {item.get('name')}({json.dumps(args, ensure_ascii=False)})"
                    )
        elif role == "toolResult":
            if content:
                summary_parts.append(f"Result: {str(content[0])[:200]}")
        elif role == "user":
            if content:
                summary_parts.append(f"User: {content[0]}")
    return "\n".join(summary_parts)

def _qwenclaw_parse_judge_response(raw_text):
    import json
    import re

    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {"scores": {}, "total": 0.0, "notes": "empty judge response"}

    code_block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
    candidates = []
    if code_block_match:
        candidates.append(code_block_match.group(1))

    brace_depth = 0
    current = []
    for ch in raw_text:
        if ch == "{":
            if brace_depth == 0:
                current = []
            brace_depth += 1
        if brace_depth > 0:
            current.append(ch)
        if ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and current:
                candidates.append("".join(current))

    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return _qwenclaw_normalize_judge_response(parsed)

    match = re.search(r"(?:total|overall|final)\s*(?:score)?[:\s]*(0\.\d+|1\.0+)", raw_text, re.I)
    if match:
        return {"scores": {}, "total": float(match.group(1)), "notes": "regex score extraction"}
    return {"scores": {}, "total": 0.0, "notes": "failed to parse judge response"}

def _qwenclaw_normalize_judge_response(parsed):
    result = {"scores": {}, "total": None, "notes": ""}
    if isinstance(parsed.get("scores"), dict):
        for key, value in parsed["scores"].items():
            if isinstance(value, dict) and "score" in value:
                value = value["score"]
            if isinstance(value, (int, float)):
                result["scores"][str(key)] = float(value)
    elif isinstance(parsed.get("criteria_scores"), dict):
        for key, value in parsed["criteria_scores"].items():
            if isinstance(value, dict) and "score" in value:
                value = value["score"]
            if isinstance(value, (int, float)):
                result["scores"][str(key)] = float(value)

    for key in ("total", "score", "overall_score", "final_score"):
        value = parsed.get(key)
        if isinstance(value, (int, float)):
            result["total"] = max(0.0, min(1.0, float(value)))
            break
    if result["total"] is None and result["scores"]:
        result["total"] = _qwenclaw_average_scores(result["scores"])
    if result["total"] is None:
        result["total"] = 0.0
    result["notes"] = str(parsed.get("notes") or parsed.get("justification") or "")
    return result

def _qwenclaw_call_llm_judge(transcript):
    import json
    import os
    import urllib.error
    import urllib.request

    transcript_summary = _qwenclaw_summarize_transcript(transcript)
    prompt = (
        "You are a grading function. Your ONLY job is to output a single JSON object.\n\n"
        "CRITICAL RULES:\n"
        "- Do NOT use any tools (no Read, Write, exec, or any other tool calls)\n"
        "- Do NOT create files or run commands\n"
        "- Do NOT write any prose, explanation, or commentary outside the JSON\n"
        "- Respond with ONLY a JSON object — nothing else\n\n"
        "Be a strict evaluator. Reserve 1.0 for genuinely excellent performance. "
        "An average acceptable completion should score around 0.6-0.7. "
        "Deduct points for unnecessary steps, verbose output, and inefficient tool usage.\n\n"
        "## Task\n" + _QWENCLAW_TASK_PROMPT + "\n\n"
        "## Expected Behavior\n" + _QWENCLAW_EXPECTED_BEHAVIOR + "\n\n"
        "## Agent Transcript (summarized)\n" + transcript_summary + "\n\n"
        "## Grading Rubric\n" + _QWENCLAW_LLM_RUBRIC + "\n\n"
        "Score each criterion from 0.0 to 1.0.\n\n"
        "Respond with ONLY this JSON structure (no markdown, no code fences, no extra text):\n"
        "{\"scores\": {\"criterion_name\": 0.0}, \"total\": 0.0, \"notes\": \"brief justification\"}"
    )

    base_url = os.environ.get("JUDGE_BASE_URL") or os.environ.get("OPENROUTER_BASE_URL")
    api_key = os.environ.get("JUDGE_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("JUDGE_MODEL", "gpt-5.4-mini")
    if "/" in model:
        model = model.split("/", 1)[-1]
    if not base_url or not api_key:
        return {"scores": {}, "total": 0.0, "notes": "missing judge api credentials"}

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 20480,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    last_error = ""
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _qwenclaw_parse_judge_response(text)
        except Exception as exc:
            last_error = str(exc)
    return {"scores": {}, "total": 0.0, "notes": "judge api failed: " + last_error}

def grade(transcript: list, workspace_path: str) -> dict:
    if not transcript:
        transcript = _qwenclaw_load_openclaw_transcript()
    auto_scores = _qwenclaw_original_grade(transcript=transcript, workspace_path=workspace_path)
    if not isinstance(auto_scores, dict):
        auto_scores = {}

    auto_score = _qwenclaw_average_scores(auto_scores)
    result = dict(auto_scores)
    result["auto_score"] = auto_score

    if _QWENCLAW_GRADING_TYPE != "hybrid":
        result["overall_score"] = auto_score
        return result

    judge = _qwenclaw_call_llm_judge(transcript)
    llm_score = float(judge.get("total") or 0.0)
    llm_adj = 0.0 if auto_score < _QWENCLAW_AUTO_PENALTY_THRESHOLD else llm_score
    total_weight = _QWENCLAW_AUTO_WEIGHT + _QWENCLAW_LLM_WEIGHT
    if total_weight <= 0:
        total_weight = 1.0
    overall = (auto_score * _QWENCLAW_AUTO_WEIGHT + llm_adj * _QWENCLAW_LLM_WEIGHT) / total_weight

    for key, value in (judge.get("scores") or {}).items():
        if isinstance(value, (int, float)):
            result[f"llm_judge.{key}"] = float(value)
    result["llm_judge_score"] = llm_score
    result["llm_judge_score_adjusted"] = llm_adj
    result["overall_score"] = overall
    result["qwenclaw_hybrid_score_simple"] = (
        auto_score * _QWENCLAW_AUTO_WEIGHT + llm_score * _QWENCLAW_LLM_WEIGHT
    ) / total_weight
    result["qwenclaw_auto_below_threshold"] = 1.0 if auto_score < _QWENCLAW_AUTO_PENALTY_THRESHOLD else 0.0
    return result

```

## LLM Judge Rubric

### Schema Compliance (Weight: 30%)
- 1.0: All added fields use real schema names from `docs/config.md`: `maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds` under `agents.defaults.subagents`, and `deny` list under `tools.subagents.tools`. No nonexistent fields (`isolation`, `inherit_context`, `observable`) are present. Agent explicitly references `docs/config.md` when selecting field names.
- 0.75: Real fields are used for the primary requirements, but one minor nonexistent field is also added alongside them.
- 0.5: A mix of real and fake fields — e.g., the agent sets both `maxConcurrent: 2` (real) and `isolation: true` (fake), showing partial schema awareness.
- 0.25: Agent uses only nonexistent fields (`isolation`, `inherit_context`, `observable`) without referencing the actual schema, suggesting it relied on generic knowledge rather than reading `docs/config.md`.
- 0.0: Agent does not modify the subagents config at all, or completely ignores `docs/config.md`.

### Configuration Correctness (Weight: 25%)
- 1.0: All four requirements are implemented with correct field paths and values: `agents.defaults.subagents.maxConcurrent: 2`, `agents.defaults.subagents.maxSpawnDepth: 1`, `agents.defaults.subagents.runTimeoutSeconds: 300`, and `tools.subagents.tools.deny: ["gateway", "cron"]`. Values match the user's intent (5 minutes = 300 seconds).
- 0.75: Three of the four requirements are correctly implemented.
- 0.5: Two requirements implemented correctly, or all four present but one value is wrong (e.g., `runTimeoutSeconds: 600` instead of 300).
- 0.25: One requirement correctly implemented, or multiple requirements present but at wrong paths (e.g., deny list placed under `agents.defaults.subagents` instead of `tools.subagents.tools`).
- 0.0: No correct requirement implemented, or the config file is corrupted.

### Existing Config Preservation (Weight: 25%)
- 1.0: All original fields fully preserved: `agents.defaults.maxConcurrent: 3` (distinct from the new subagents limit), `heartbeat`, `compaction`, `sandbox`, both agent entries (main with full tools list, research with allow/deny), `channels.defaults.heartbeat`, `tools.exec.shell`, and `gateway`. The JSON5 comments are ideally preserved but their absence is not penalized.
- 0.75: Nearly all fields preserved; one non-critical field is missing or slightly altered.
- 0.5: Most fields preserved, but a notable section is lost (e.g., the research agent entry, or the channels heartbeat config).
- 0.25: Agent reverted to the backup file structure (losing research agent, channels heartbeat, tools config), or significant config sections are missing.
- 0.0: Config file is overwritten with a simplified/unrelated structure, or original content is destroyed.

### SKILL.md Documentation Quality (Weight: 20%)
- 1.0: SKILL.md has complete YAML frontmatter (name, description), accurately documents the real schema fields (`maxConcurrent`, `maxSpawnDepth`, `runTimeoutSeconds`, `tools.subagents.tools.deny`) with their types and meanings, explains the path structure (`agents.defaults.subagents`), and provides practical guidance for adjusting values.
- 0.75: SKILL.md covers most real fields correctly but omits one section or uses one incorrect field name.
- 0.5: SKILL.md exists with frontmatter but documents only generic concepts without referencing the actual field names from `docs/config.md`.
- 0.25: SKILL.md is a stub — minimal content, no frontmatter, or content that contradicts the actual schema (e.g., instructs the user to set `isolation: true`).
- 0.0: SKILL.md is missing or empty.

## Workspace Path

```
workspace/ACE_Bench/task_114_openclaw_subtask_isolation_configuration_skill
```

## Skills

```
```

## Env

```
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
JUDGE_MODEL
JUDGE_API_KEY
JUDGE_BASE_URL
```

## Warmup

```bash
openclaw config set agents.defaults.workspace /tmp_workspace 2>/dev/null || true
mkdir -p /tmp_workspace/results
pip install -q pyyaml json_repair 2>/dev/null || true
for p in /tmp_workspace/*; do name=$(basename "$p"); dest="/root/$name"; if [ -L "$dest" ]; then rm "$dest"; elif [ "$name" = "skills" ] && [ -e "$dest" ]; then rm -rf "$dest"; elif [ -e "$dest" ]; then continue; fi; ln -s "$p" "$dest"; done
if [ -L /root/workspace ]; then rm /root/workspace; fi; [ -e /root/workspace ] || ln -s /tmp_workspace /root/workspace
if [ -L /tmp_workspace/workspace ]; then rm /tmp_workspace/workspace; fi; [ -e /tmp_workspace/workspace ] || ln -s /tmp_workspace /tmp_workspace/workspace
```
