---
id: ACE_Bench_task_127_feishu_task_progress_reporting_skill
name: '[QCB] Feishu Task Progress Reporting Skill'
source: QwenClawBench
original_id: task_00081_feishu_task_progress_reporting_skill
category: ACE_Bench
timeout_seconds: 1800
qwen_category: Communication and Scheduling
qwen_subcategory: Task and Plan Management
qwen_grading_type: hybrid
qwen_grading_weights:
  automated: 0.4
  llm_judge: 0.6
qwen_input_modality: text-only
qwen_external_dependency: none
agent_category: "Automation"
---
## Prompt

Hey, Wang Jun (sender: ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa04) just DMed me on Feishu asking "How is the progress on the three tasks?" — can you help me reply?

Before you write anything, please read through **all the workspace files** carefully, especially `feishu-message-cache.json` which has today's messages. Something about TASK-001 might need special attention.

I need you to do two things:

1. Create a reusable skill at `workspace/skills/task-progress-reporter/SKILL.md`. It should define a "task progress reporter" skill that reads structured task data, cross-references the message cache for late-breaking updates, detects blockers or schedule risks, and produces a Feishu-ready summary. Include sections for: when to use this skill, required inputs, processing steps (including how to handle conflicting data across sources), and output format.

2. Then use that skill: write a ready-to-send reply to `progress_report.md`. For each task include: title, assignee, status, progress %, due date, and a risk/blocker flag where relevant. Base the report on what you actually find across **all** the files — do not simply repeat numbers you've seen in one place; cross-check everything.

## Expected Behavior

The agent should:

1. **Read all seven workspace files**:
   - `tasks.json`: three in-progress tasks (TASK-001 72%, TASK-002 45%, TASK-003 30%), with task notes mentioning "MQ setup ETA Feb 11" for TASK-001's blocker.
   - `feishu-message-cache.json`: contains a **critical update from this morning** (2026-02-10T09:15) where Zhang Wei reports the RabbitMQ ETA has slipped to **Feb 13** (not Feb 11 as in tasks.json notes). Wang Jun then estimates "70% done overall" — which conflicts with the actual average.
   - `meeting-notes-2026-02-06.md`: notes from Feb 6 confirm the MQ dependency risk was already flagged ("If MQ setup slips, migration could miss Feb 14 deadline").
   - `project-metadata.json`: identifies sender `ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa04` as Wang Jun, Project Manager.
   - `team-contacts.csv`: 6 members total — 2 (Zhao Ling, Sun Hao) are **not** in project-metadata.json's team list.
   - `.env.feishu` and `feishu-config.json`: Feishu API context.

2. **Identify three data conflicts requiring resolution**:
   - **Conflict A (MQ timeline)**: `tasks.json` notes say "ETA Feb 11" but Zhang Wei's 09:15 message today says "new ETA is Feb 13". The message is more recent → use Feb 13 as authoritative.
   - **Conflict B (overall progress estimate)**: Wang Jun's 09:22 message says "about 70% done overall". Actual computed average: (72 + 45 + 30) / 3 = **49%**. The 70% figure is Wang Jun's uninformed guess; the report should use the correct 49% figure and not parrot the 70% estimate.
   - **Conflict C (team membership)**: `team-contacts.csv` has 6 people but `project-metadata.json` only lists 4 on the project team. Agent should use project-metadata.json as the authoritative team list for this project.

3. **Flag TASK-001 as AT RISK**: Due Feb 14; MQ (required dependency) now not ready until Feb 13. That leaves only 1 day for notification endpoint migration + API docs + integration testing — this is a critical timeline risk.

4. **Create `workspace/skills/task-progress-reporter/SKILL.md`** with:
   - YAML frontmatter (name, description fields)
   - A **Triggers / When to Use** section
   - An **Inputs** section listing required data sources (task JSON, message cache, project metadata)
   - A **Steps** section with numbered steps including: read task data → check message cache for late-breaking updates → resolve conflicts (latest message overrides task notes) → compute correct averages → identify blockers/risks → compose report
   - An **Output Format** section showing the report structure (per-task breakdown + risk flags + overall summary)

5. **Create `progress_report.md`** that:
   - Addresses Wang Jun by name
   - Lists all three tasks with title, assignee, status, progress %, due date
   - Flags TASK-001 as **AT RISK** with the Feb 13 MQ blocker and Feb 14 deadline noted
   - States the correct overall average (~49%), NOT Wang Jun's 70% estimate
   - Is friendly and concise — suitable for pasting into a Feishu DM reply

## Grading Criteria

- [ ] SKILL.md exists at `workspace/skills/task-progress-reporter/SKILL.md` with YAML frontmatter (name, description)
- [ ] SKILL.md contains a conflict-resolution step (latest message overrides stale task notes)
- [ ] progress_report.md exists and covers all three tasks with correct data (72%, 45%, 30%; correct due dates)
- [ ] progress_report.md flags TASK-001 as AT RISK / blocked (due to MQ delay)
- [ ] progress_report.md uses the MQ ETA of Feb 13 (not Feb 11 from tasks.json)
- [ ] progress_report.md does NOT parrot Wang Jun's incorrect 70% estimate; uses ~49% or correctly computed per-task figures
- [ ] Agent correctly identifies Wang Jun as the sender using project-metadata.json

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    import re
    import glob
    from pathlib import Path

    ws = Path(workspace_path)
    scores = {
        "skill_md_structured": 0.0,
        "progress_report_data_accurate": 0.0,
        "mq_conflict_resolved": 0.0,
        "risk_flag_for_task001": 0.0,
        "progress_report_all_tasks": 0.0,
    }

    # --- Locate SKILL.md ---
    skill_path = None
    preferred = ws / "skills" / "task-progress-reporter" / "SKILL.md"
    if preferred.exists():
        skill_path = preferred
    else:
        matches = sorted(glob.glob(str(ws / "skills" / "*" / "SKILL.md")))
        if matches:
            skill_path = Path(matches[0])

    report_path = ws / "progress_report.md"

    if skill_path is None and not report_path.exists():
        return scores

    # --- SKILL.md check ---
    if skill_path is not None and skill_path.exists():
        try:
            skill_content = skill_path.read_text(encoding="utf-8", errors="replace")
            fm_match = re.search(r"^---\s*\n(.*?)\n---", skill_content, re.DOTALL)
            if fm_match:
                fm_text = fm_match.group(1)
                has_name = bool(re.search(r"(?i)name\s*:", fm_text))
                has_desc = bool(re.search(r"(?i)description\s*:", fm_text))
                body = skill_content[fm_match.end():]
                body_lower = body.lower()
                # Check for key sections
                has_steps = bool(re.search(r"step|procedure|process|workflow", body_lower))
                has_conflict = bool(re.search(r"conflict|override|latest|prioriti|cross.?ref|reconcil", body_lower))
                has_output = bool(re.search(r"output|format|template|report", body_lower))
                section_score = sum([has_steps, has_conflict, has_output])
                if has_name and has_desc and len(body.strip()) >= 100 and section_score >= 2:
                    scores["skill_md_structured"] = 1.0
                elif has_name and has_desc and len(body.strip()) >= 50:
                    scores["skill_md_structured"] = 0.6
                elif fm_match:
                    scores["skill_md_structured"] = 0.3
        except Exception:
            pass

    if not report_path.exists():
        return scores

    try:
        report = report_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return scores

    if len(report.strip()) < 20:
        return scores

    report_lower = report.lower()

    # --- All three tasks mentioned ---
    task_patterns = [
        r"TASK-001|Backend API migration|api.*v3|v3.*api",
        r"TASK-002|Mobile app UI|mobile.*redesign|UI redesign",
        r"TASK-003|User analytics|analytics dashboard",
    ]
    tasks_found = sum(1 for p in task_patterns if re.search(p, report, re.IGNORECASE))
    scores["progress_report_all_tasks"] = tasks_found / 3.0

    # --- Data accuracy: correct percentages and due dates ---
    accuracy = 0.0
    pct_checks = sum(1 for pct in ["72", "45", "30"] if re.search(rf"\b{pct}\s*%", report))
    accuracy += (pct_checks / 3.0) * 0.5
    date_checks = sum(1 for d in ["2026-02-14", "2026-02-20", "2026-02-25"] if d in report)
    accuracy += (date_checks / 3.0) * 0.5
    scores["progress_report_data_accurate"] = accuracy

    # --- KEY CHECK: Does the report use Feb 13 (not Feb 11) for MQ ETA? ---
    # And does it NOT repeat Wang Jun's wrong 70% as overall progress?
    has_feb13 = bool(re.search(r"feb\.?\s*13|2026-02-13|february\s+13", report_lower))
    has_feb11_only = bool(re.search(r"feb\.?\s*11|2026-02-11", report_lower)) and not has_feb13
    wrong_70_parroted = bool(re.search(r"(overall|average|avg|total).{0,40}\b70\s*%|\b70\s*%.{0,40}(overall|average|avg|total)", report_lower)) and not bool(
        re.search(r"(incorrect|wrong|not accurate|actually|correct[^i]|real|49\s*%|avg.*49|average.*49)", report_lower)
    )
    if has_feb13 and not wrong_70_parroted:
        scores["mq_conflict_resolved"] = 1.0
    elif has_feb13:
        scores["mq_conflict_resolved"] = 0.6
    elif not has_feb11_only and not wrong_70_parroted:
        scores["mq_conflict_resolved"] = 0.3

    # --- Risk flag for TASK-001 ---
    # Check for risk/blocker language near TASK-001 context
    risk_keywords = ["at risk", "risk", "blocked", "blocker", "critical", "deadline", "slip", "tight", "delay"]
    task001_context = re.search(
        r"(TASK-001|backend api|api.*v3|v3.*api).{0,400}",
        report, re.IGNORECASE | re.DOTALL
    )
    if task001_context:
        context_text = task001_context.group(0).lower()
        risk_found = sum(1 for kw in risk_keywords if kw in context_text)
        if risk_found >= 2:
            scores["risk_flag_for_task001"] = 1.0
        elif risk_found == 1:
            scores["risk_flag_for_task001"] = 0.5

    return scores
_QWENCLAW_GRADING_TYPE = "hybrid"
_QWENCLAW_AUTO_WEIGHT = 0.4
_QWENCLAW_LLM_WEIGHT = 0.6
_QWENCLAW_TASK_PROMPT = "Hey, Wang Jun (sender: ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa04) just DMed me on Feishu asking \"How is the progress on the three tasks?\" — can you help me reply?\n\nBefore you write anything, please read through **all the workspace files** carefully, especially `feishu-message-cache.json` which has today's messages. Something about TASK-001 might need special attention.\n\nI need you to do two things:\n\n1. Create a reusable skill at `workspace/skills/task-progress-reporter/SKILL.md`. It should define a \"task progress reporter\" skill that reads structured task data, cross-references the message cache for late-breaking updates, detects blockers or schedule risks, and produces a Feishu-ready summary. Include sections for: when to use this skill, required inputs, processing steps (including how to handle conflicting data across sources), and output format.\n\n2. Then use that skill: write a ready-to-send reply to `progress_report.md`. For each task include: title, assignee, status, progress %, due date, and a risk/blocker flag where relevant. Base the report on what you actually find across **all** the files — do not simply repeat numbers you've seen in one place; cross-check everything."
_QWENCLAW_EXPECTED_BEHAVIOR = "The agent should:\n\n1. **Read all seven workspace files**:\n   - `tasks.json`: three in-progress tasks (TASK-001 72%, TASK-002 45%, TASK-003 30%), with task notes mentioning \"MQ setup ETA Feb 11\" for TASK-001's blocker.\n   - `feishu-message-cache.json`: contains a **critical update from this morning** (2026-02-10T09:15) where Zhang Wei reports the RabbitMQ ETA has slipped to **Feb 13** (not Feb 11 as in tasks.json notes). Wang Jun then estimates \"70% done overall\" — which conflicts with the actual average.\n   - `meeting-notes-2026-02-06.md`: notes from Feb 6 confirm the MQ dependency risk was already flagged (\"If MQ setup slips, migration could miss Feb 14 deadline\").\n   - `project-metadata.json`: identifies sender `ou_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa04` as Wang Jun, Project Manager.\n   - `team-contacts.csv`: 6 members total — 2 (Zhao Ling, Sun Hao) are **not** in project-metadata.json's team list.\n   - `.env.feishu` and `feishu-config.json`: Feishu API context.\n\n2. **Identify three data conflicts requiring resolution**:\n   - **Conflict A (MQ timeline)**: `tasks.json` notes say \"ETA Feb 11\" but Zhang Wei's 09:15 message today says \"new ETA is Feb 13\". The message is more recent → use Feb 13 as authoritative.\n   - **Conflict B (overall progress estimate)**: Wang Jun's 09:22 message says \"about 70% done overall\". Actual computed average: (72 + 45 + 30) / 3 = **49%**. The 70% figure is Wang Jun's uninformed guess; the report should use the correct 49% figure and not parrot the 70% estimate.\n   - **Conflict C (team membership)**: `team-contacts.csv` has 6 people but `project-metadata.json` only lists 4 on the project team. Agent should use project-metadata.json as the authoritative team list for this project.\n\n3. **Flag TASK-001 as AT RISK**: Due Feb 14; MQ (required dependency) now not ready until Feb 13. That leaves only 1 day for notification endpoint migration + API docs + integration testing — this is a critical timeline risk.\n\n4. **Create `workspace/skills/task-progress-reporter/SKILL.md`** with:\n   - YAML frontmatter (name, description fields)\n   - A **Triggers / When to Use** section\n   - An **Inputs** section listing required data sources (task JSON, message cache, project metadata)\n   - A **Steps** section with numbered steps including: read task data → check message cache for late-breaking updates → resolve conflicts (latest message overrides task notes) → compute correct averages → identify blockers/risks → compose report\n   - An **Output Format** section showing the report structure (per-task breakdown + risk flags + overall summary)\n\n5. **Create `progress_report.md`** that:\n   - Addresses Wang Jun by name\n   - Lists all three tasks with title, assignee, status, progress %, due date\n   - Flags TASK-001 as **AT RISK** with the Feb 13 MQ blocker and Feb 14 deadline noted\n   - States the correct overall average (~49%), NOT Wang Jun's 70% estimate\n   - Is friendly and concise — suitable for pasting into a Feishu DM reply"
_QWENCLAW_LLM_RUBRIC = "### Multi-Source Conflict Resolution (Weight: 35%)\nEvaluates whether the agent identifies and correctly resolves the three data conflicts across workspace files.\n\n- **1.0**: Correctly resolves all three conflicts: (A) uses MQ ETA of Feb 13 from Zhang Wei's morning message (not Feb 11 from tasks.json notes); (B) uses the actual computed average of ~49% and explicitly corrects or ignores Wang Jun's 70% estimate; (C) uses project-metadata.json team list as authoritative (4 members, not 6 from team-contacts.csv).\n- **0.75**: Resolves conflicts A and B correctly; C may be missed or handled implicitly.\n- **0.5**: Resolves conflict A (MQ ETA) correctly but accepts Wang Jun's 70% estimate without challenge, or vice versa.\n- **0.25**: Notices some inconsistency but doesn't clearly resolve it — output contains mixed/ambiguous data.\n- **0.0**: Ignores the message cache entirely; reports Feb 11 or parrots 70% as the overall progress; no cross-referencing.\n\n### TASK-001 Risk Assessment (Weight: 25%)\nEvaluates whether TASK-001 is correctly identified and flagged as AT RISK.\n\n- **1.0**: Flags TASK-001 as AT RISK or blocked, explicitly notes the MQ ETA of Feb 13 vs Feb 14 deadline, explains only 1 day for remaining work (notification migration + API docs + integration testing), and recommends escalation or deadline review.\n- **0.75**: Flags TASK-001 as risky and mentions the Feb 13/Feb 14 conflict, but doesn't detail the remaining work or recommend action.\n- **0.5**: Notes that TASK-001 has a blocker but doesn't connect it to the deadline risk or uses the old Feb 11 ETA.\n- **0.25**: Mentions notification endpoints are blocked but doesn't flag the timeline as at risk.\n- **0.0**: No risk assessment; reports TASK-001 as normal in-progress without noting the blocker or deadline risk.\n\n### Progress Report Accuracy and Completeness (Weight: 20%)\nEvaluates whether progress_report.md accurately reflects all three tasks with correct data.\n\n- **1.0**: All three tasks present with correct titles, progress percentages (72%, 45%, 30%), due dates (2026-02-14, 2026-02-20, 2026-02-25), assignees, and representative notes. Report correctly does not state 70% as overall average.\n- **0.75**: All three tasks present with mostly correct data; one minor omission or note missing.\n- **0.5**: All three tasks mentioned but one has incorrect data, or overall average is stated as 70% without correction.\n- **0.25**: Only one or two tasks covered, or significant inaccuracies.\n- **0.0**: Missing, empty, or fabricated task data.\n\n### Skill Definition Quality (Weight: 20%)\nEvaluates the SKILL.md quality.\n\n- **1.0**: SKILL.md has proper YAML frontmatter (name, description), and clearly defined sections for: when to use the skill, required inputs (listing task JSON, message cache, project metadata), numbered processing steps that include a conflict-resolution rule (latest message overrides stale task notes), and an output format template.\n- **0.75**: SKILL.md has frontmatter and most required sections, but the conflict-resolution step is absent or vague.\n- **0.5**: SKILL.md exists with frontmatter and some content but is generic, missing conflict handling or output format.\n- **0.25**: SKILL.md exists but is minimal or boilerplate with no skill-specific logic.\n- **0.0**: SKILL.md missing, empty, or unrelated to task progress reporting."


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

### Multi-Source Conflict Resolution (Weight: 35%)
Evaluates whether the agent identifies and correctly resolves the three data conflicts across workspace files.

- **1.0**: Correctly resolves all three conflicts: (A) uses MQ ETA of Feb 13 from Zhang Wei's morning message (not Feb 11 from tasks.json notes); (B) uses the actual computed average of ~49% and explicitly corrects or ignores Wang Jun's 70% estimate; (C) uses project-metadata.json team list as authoritative (4 members, not 6 from team-contacts.csv).
- **0.75**: Resolves conflicts A and B correctly; C may be missed or handled implicitly.
- **0.5**: Resolves conflict A (MQ ETA) correctly but accepts Wang Jun's 70% estimate without challenge, or vice versa.
- **0.25**: Notices some inconsistency but doesn't clearly resolve it — output contains mixed/ambiguous data.
- **0.0**: Ignores the message cache entirely; reports Feb 11 or parrots 70% as the overall progress; no cross-referencing.

### TASK-001 Risk Assessment (Weight: 25%)
Evaluates whether TASK-001 is correctly identified and flagged as AT RISK.

- **1.0**: Flags TASK-001 as AT RISK or blocked, explicitly notes the MQ ETA of Feb 13 vs Feb 14 deadline, explains only 1 day for remaining work (notification migration + API docs + integration testing), and recommends escalation or deadline review.
- **0.75**: Flags TASK-001 as risky and mentions the Feb 13/Feb 14 conflict, but doesn't detail the remaining work or recommend action.
- **0.5**: Notes that TASK-001 has a blocker but doesn't connect it to the deadline risk or uses the old Feb 11 ETA.
- **0.25**: Mentions notification endpoints are blocked but doesn't flag the timeline as at risk.
- **0.0**: No risk assessment; reports TASK-001 as normal in-progress without noting the blocker or deadline risk.

### Progress Report Accuracy and Completeness (Weight: 20%)
Evaluates whether progress_report.md accurately reflects all three tasks with correct data.

- **1.0**: All three tasks present with correct titles, progress percentages (72%, 45%, 30%), due dates (2026-02-14, 2026-02-20, 2026-02-25), assignees, and representative notes. Report correctly does not state 70% as overall average.
- **0.75**: All three tasks present with mostly correct data; one minor omission or note missing.
- **0.5**: All three tasks mentioned but one has incorrect data, or overall average is stated as 70% without correction.
- **0.25**: Only one or two tasks covered, or significant inaccuracies.
- **0.0**: Missing, empty, or fabricated task data.

### Skill Definition Quality (Weight: 20%)
Evaluates the SKILL.md quality.

- **1.0**: SKILL.md has proper YAML frontmatter (name, description), and clearly defined sections for: when to use the skill, required inputs (listing task JSON, message cache, project metadata), numbered processing steps that include a conflict-resolution rule (latest message overrides stale task notes), and an output format template.
- **0.75**: SKILL.md has frontmatter and most required sections, but the conflict-resolution step is absent or vague.
- **0.5**: SKILL.md exists with frontmatter and some content but is generic, missing conflict handling or output format.
- **0.25**: SKILL.md exists but is minimal or boilerplate with no skill-specific logic.
- **0.0**: SKILL.md missing, empty, or unrelated to task progress reporting.

## Workspace Path

```
workspace/ACE_Bench/task_127_feishu_task_progress_reporting_skill
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
