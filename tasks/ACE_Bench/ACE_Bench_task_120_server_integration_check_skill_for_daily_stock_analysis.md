---
id: ACE_Bench_task_120_server_integration_check_skill_for_daily_stock_analysis
name: '[QCB] Server Integration Check Skill for Daily Stock Analysis'
source: QwenClawBench
original_id: task_00034_server_integration_check_skill_for_daily_stock_analysis
category: ACE_Bench
timeout_seconds: 1800
qwen_category: System Operations and Administration
qwen_subcategory: Software and Environment Management
qwen_grading_type: hybrid
qwen_grading_weights:
  automated: 0.4
  llm_judge: 0.6
qwen_input_modality: text-only
qwen_external_dependency: none
agent_category: "Automation"
---
## Prompt

Hey, I need a reusable skill for checking whether a given project or service has been integrated/deployed on a server. Something that can inspect deployment configs, cron jobs, systemd units, docker-compose files, and service lists to determine the integration status of any named service. Create it as a `skills/server-integration-checker/SKILL.md` in the workspace — think of it like a "server-integration-checker" skill, similar in spirit to how homeassistant-cli wraps Home Assistant control or how rose-docker-build-skill wraps Docker builds.

Once you've created the skill, use it to answer this question from a colleague on Feishu (message_id: om_x100b579d716838a4b21fbbb75b6b038, from user ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa03):

"Has daily_stock_analysis been integrated into the server?"

Check the workspace under `projects/daily_stock_analysis/` and `server/` for deployment artifacts, cron entries, docker-compose files, and any service lists. Then write your findings to `integration_report.md` and reply to the Feishu message with a clear status summary.

## Expected Behavior

The agent should:

1. **Create a SKILL.md** file at `skills/server-integration-checker/SKILL.md` in the workspace that defines a reusable "server-integration-checker" skill. The skill should have YAML frontmatter with `name` and `description` fields, and markdown sections describing how to check server integration status by inspecting deployment configs, cron jobs, docker-compose files, systemd units, and service lists. This is inspired by the referenced skills (coala, rose-docker-build-skill, homeassistant-cli) which each wrap a specific operational capability.

2. **Investigate the workspace** by reading:
   - `projects/daily_stock_analysis/README.md` (which shows server integration is pending)
   - `server/deploy/status.yaml` (which explicitly lists `daily_stock_analysis` under `pending_deployments` with `status: not_deployed`)
   - `server/cron/crontab.txt` (which has the daily_stock_analysis cron entry commented out as "NOT yet added")
   - `server/deploy/docker-compose.yml` (which has the daily_stock_analysis service commented out under "NOT YET DEPLOYED")

3. **Write `integration_report.md`** summarizing the findings: daily_stock_analysis is NOT currently integrated into the server. The report should mention what was checked and the current status.

4. **Reply to the Feishu message** (or indicate intent to reply) with a clear answer: daily_stock_analysis has NOT been integrated into the server yet. The reply should mention that local development and Docker image are complete but server deployment (systemd/cron/docker-compose) is still pending.

## Grading Criteria

- [ ] SKILL.md exists at `skills/server-integration-checker/SKILL.md` with proper YAML frontmatter (name and description fields)
- [ ] SKILL.md contains meaningful instruction sections for checking server integration
- [ ] integration_report.md exists and states daily_stock_analysis is NOT integrated
- [ ] integration_report.md references specific evidence (cron, docker-compose, service list, or status.yaml)
- [ ] Agent correctly identifies the NOT-integrated status in its reply
- [ ] Agent provides actionable context (what is done vs what remains)

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    import os
    import re

    scores = {
        "skill_md_exists_and_structured": 0.0,
        "skill_md_content_quality": 0.0,
        "integration_report_exists": 0.0,
        "integration_report_content": 0.0,
        "correct_status_in_transcript": 0.0,
    }

    # Early return if primary output file is absent
    report_path = os.path.join(workspace_path, "integration_report.md")
    if not os.path.isfile(report_path):
        return scores

    # 1. Check SKILL.md exists and has proper YAML frontmatter
    #    Canonical location: skills/server-integration-checker/SKILL.md
    #    Also accept any skills/<name>/SKILL.md as fallback
    skill_content = ""
    canonical_skill_path = os.path.join(
        workspace_path, "skills", "server-integration-checker", "SKILL.md"
    )
    if os.path.isfile(canonical_skill_path):
        skill_path_found = canonical_skill_path
    else:
        # Fallback: scan skills/ subdirectories
        skill_path_found = None
        skills_dir = os.path.join(workspace_path, "skills")
        if os.path.isdir(skills_dir):
            for entry in os.listdir(skills_dir):
                candidate = os.path.join(skills_dir, entry, "SKILL.md")
                if os.path.isfile(candidate):
                    skill_path_found = candidate
                    break

    if skill_path_found:
        try:
            with open(skill_path_found, "r", encoding="utf-8") as f:
                skill_content = f.read()
        except Exception:
            skill_content = ""

        if skill_content.strip():
            has_frontmatter = skill_content.strip().startswith("---")
            has_name = bool(re.search(r'(?i)name\s*:', skill_content[:500]))
            has_description = bool(re.search(r'(?i)description\s*:', skill_content[:800]))
            if has_frontmatter and has_name and has_description:
                scores["skill_md_exists_and_structured"] = 1.0
            elif has_frontmatter and (has_name or has_description):
                scores["skill_md_exists_and_structured"] = 0.5
            elif has_frontmatter:
                scores["skill_md_exists_and_structured"] = 0.25
            else:
                scores["skill_md_exists_and_structured"] = 0.1

    # 2. Check SKILL.md content quality
    if skill_content:
        keywords = ["cron", "systemd", "docker", "service", "deploy", "integrat", "check", "server"]
        matches = sum(1 for kw in keywords if kw.lower() in skill_content.lower())
        if matches >= 5:
            scores["skill_md_content_quality"] = 1.0
        elif matches >= 3:
            scores["skill_md_content_quality"] = 0.7
        elif matches >= 1:
            scores["skill_md_content_quality"] = 0.3
        else:
            scores["skill_md_content_quality"] = 0.0

    # 3. Check integration_report.md exists (already confirmed above)
    report_content = ""
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report_content = f.read()
    except Exception:
        report_content = ""
    if report_content.strip():
        scores["integration_report_exists"] = 1.0

    # 4. Check integration_report.md content
    if report_content:
        content_lower = report_content.lower()
        not_integrated = any(phrase in content_lower for phrase in [
            "not integrated", "not yet integrated", "not deployed", "not yet deployed",
            "pending", "not found", "not included", "has not been", "hasn't been",
            "no integration", "not currently"
        ])
        evidence_keywords = ["cron", "docker-compose", "docker compose", "service", "systemd",
                             "deployed_services", "status.yaml", "not_deployed"]
        evidence_count = sum(1 for ek in evidence_keywords if ek.lower() in content_lower)

        if not_integrated and evidence_count >= 2:
            scores["integration_report_content"] = 1.0
        elif not_integrated and evidence_count >= 1:
            scores["integration_report_content"] = 0.7
        elif not_integrated:
            scores["integration_report_content"] = 0.5
        elif evidence_count >= 1:
            scores["integration_report_content"] = 0.25
        else:
            scores["integration_report_content"] = 0.0

    # 5. Check transcript for correct status communication
    for event in transcript:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("role") == "assistant":
            text = msg.get("content", "")
            if isinstance(text, str):
                text_lower = text.lower()
                not_phrases = [
                    "not integrated", "not yet integrated", "not deployed",
                    "not yet deployed", "hasn't been", "has not been",
                    "pending", "not currently", "still pending"
                ]
                if any(p in text_lower for p in not_phrases):
                    scores["correct_status_in_transcript"] = 1.0
                    break

    return scores
_QWENCLAW_GRADING_TYPE = "hybrid"
_QWENCLAW_AUTO_WEIGHT = 0.4
_QWENCLAW_LLM_WEIGHT = 0.6
_QWENCLAW_TASK_PROMPT = "Hey, I need a reusable skill for checking whether a given project or service has been integrated/deployed on a server. Something that can inspect deployment configs, cron jobs, systemd units, docker-compose files, and service lists to determine the integration status of any named service. Create it as a `skills/server-integration-checker/SKILL.md` in the workspace — think of it like a \"server-integration-checker\" skill, similar in spirit to how homeassistant-cli wraps Home Assistant control or how rose-docker-build-skill wraps Docker builds.\n\nOnce you've created the skill, use it to answer this question from a colleague on Feishu (message_id: om_x100b579d716838a4b21fbbb75b6b038, from user ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa03):\n\n\"Has daily_stock_analysis been integrated into the server?\"\n\nCheck the workspace under `projects/daily_stock_analysis/` and `server/` for deployment artifacts, cron entries, docker-compose files, and any service lists. Then write your findings to `integration_report.md` and reply to the Feishu message with a clear status summary."
_QWENCLAW_EXPECTED_BEHAVIOR = "The agent should:\n\n1. **Create a SKILL.md** file at `skills/server-integration-checker/SKILL.md` in the workspace that defines a reusable \"server-integration-checker\" skill. The skill should have YAML frontmatter with `name` and `description` fields, and markdown sections describing how to check server integration status by inspecting deployment configs, cron jobs, docker-compose files, systemd units, and service lists. This is inspired by the referenced skills (coala, rose-docker-build-skill, homeassistant-cli) which each wrap a specific operational capability.\n\n2. **Investigate the workspace** by reading:\n   - `projects/daily_stock_analysis/README.md` (which shows server integration is pending)\n   - `server/deploy/status.yaml` (which explicitly lists `daily_stock_analysis` under `pending_deployments` with `status: not_deployed`)\n   - `server/cron/crontab.txt` (which has the daily_stock_analysis cron entry commented out as \"NOT yet added\")\n   - `server/deploy/docker-compose.yml` (which has the daily_stock_analysis service commented out under \"NOT YET DEPLOYED\")\n\n3. **Write `integration_report.md`** summarizing the findings: daily_stock_analysis is NOT currently integrated into the server. The report should mention what was checked and the current status.\n\n4. **Reply to the Feishu message** (or indicate intent to reply) with a clear answer: daily_stock_analysis has NOT been integrated into the server yet. The reply should mention that local development and Docker image are complete but server deployment (systemd/cron/docker-compose) is still pending."
_QWENCLAW_LLM_RUBRIC = "### Evidence and Workspace Grounding (Weight: 30%)\nThe agent must base its conclusions on actual workspace files (`README.md`, `server/deploy/status.yaml`, `server/cron/crontab.txt`, `server/deploy/docker-compose.yml`) rather than guessing or hallucinating.\n- 1.0: Explicitly references multiple workspace files and draws correct conclusions from each\n- 0.75: References at least two workspace files with correct interpretation\n- 0.5: References at least one workspace file correctly\n- 0.25: Vague references to workspace content without specifics\n- 0.0: Ignores workspace files entirely, hallucinates facts, or skips the investigation\n\n### Skill Creation Quality (Weight: 25%)\nThe agent should create a well-structured, reusable `skills/server-integration-checker/SKILL.md` that encapsulates the server-integration-checking capability, inspired by the referenced skills (coala, rose-docker-build-skill, homeassistant-cli).\n- 1.0: `skills/server-integration-checker/SKILL.md` has proper YAML frontmatter, clear sections, and is genuinely reusable for checking any service's integration status\n- 0.75: SKILL.md is well-structured but somewhat specific to this one case rather than being fully reusable\n- 0.5: SKILL.md exists with basic structure but lacks depth or reusability\n- 0.25: SKILL.md exists but is minimal or poorly structured\n- 0.0: No SKILL.md created, or the file is empty/irrelevant, or placed at wrong path (e.g., workspace root)\n\n### Correctness of Integration Status (Weight: 25%)\nThe agent must correctly determine that daily_stock_analysis is NOT integrated into the server.\n- 1.0: Clearly states not integrated and explains what is complete (local dev, Docker) vs what is missing (systemd/cron/docker-compose deployment)\n- 0.75: Correctly states not integrated with some supporting detail\n- 0.5: States not integrated but with minimal or slightly inaccurate detail\n- 0.25: Ambiguous answer that doesn't clearly confirm or deny integration\n- 0.0: Incorrectly states it IS integrated, or provides no answer\n\n### Communication and Deliverables (Weight: 20%)\nThe agent should produce an `integration_report.md` and reply to the Feishu message with a clear, helpful summary.\n- 1.0: Both `integration_report.md` and Feishu reply are clear, well-organized, and actionable (e.g., offers next steps)\n- 0.75: Both deliverables present with good quality but minor gaps\n- 0.5: One deliverable present and adequate, or both present but low quality\n- 0.25: Only a brief transcript reply with no report file\n- 0.0: No report file and no meaningful reply addressing the question"


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

### Evidence and Workspace Grounding (Weight: 30%)
The agent must base its conclusions on actual workspace files (`README.md`, `server/deploy/status.yaml`, `server/cron/crontab.txt`, `server/deploy/docker-compose.yml`) rather than guessing or hallucinating.
- 1.0: Explicitly references multiple workspace files and draws correct conclusions from each
- 0.75: References at least two workspace files with correct interpretation
- 0.5: References at least one workspace file correctly
- 0.25: Vague references to workspace content without specifics
- 0.0: Ignores workspace files entirely, hallucinates facts, or skips the investigation

### Skill Creation Quality (Weight: 25%)
The agent should create a well-structured, reusable `skills/server-integration-checker/SKILL.md` that encapsulates the server-integration-checking capability, inspired by the referenced skills (coala, rose-docker-build-skill, homeassistant-cli).
- 1.0: `skills/server-integration-checker/SKILL.md` has proper YAML frontmatter, clear sections, and is genuinely reusable for checking any service's integration status
- 0.75: SKILL.md is well-structured but somewhat specific to this one case rather than being fully reusable
- 0.5: SKILL.md exists with basic structure but lacks depth or reusability
- 0.25: SKILL.md exists but is minimal or poorly structured
- 0.0: No SKILL.md created, or the file is empty/irrelevant, or placed at wrong path (e.g., workspace root)

### Correctness of Integration Status (Weight: 25%)
The agent must correctly determine that daily_stock_analysis is NOT integrated into the server.
- 1.0: Clearly states not integrated and explains what is complete (local dev, Docker) vs what is missing (systemd/cron/docker-compose deployment)
- 0.75: Correctly states not integrated with some supporting detail
- 0.5: States not integrated but with minimal or slightly inaccurate detail
- 0.25: Ambiguous answer that doesn't clearly confirm or deny integration
- 0.0: Incorrectly states it IS integrated, or provides no answer

### Communication and Deliverables (Weight: 20%)
The agent should produce an `integration_report.md` and reply to the Feishu message with a clear, helpful summary.
- 1.0: Both `integration_report.md` and Feishu reply are clear, well-organized, and actionable (e.g., offers next steps)
- 0.75: Both deliverables present with good quality but minor gaps
- 0.5: One deliverable present and adequate, or both present but low quality
- 0.25: Only a brief transcript reply with no report file
- 0.0: No report file and no meaningful reply addressing the question

## Workspace Path

```
workspace/ACE_Bench/task_120_server_integration_check_skill_for_daily_stock_analysis
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
