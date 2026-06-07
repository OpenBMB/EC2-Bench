---
id: ACE_Bench_task_110_paper_to_poster
name: Academic Paper to Conference Poster
source: WildClawBench
original_id: 05_Creative_Synthesis_task_7_paper_to_poster
original_category: 05_Creative_Synthesis
selection_reason: both_low_max_lt_0.4
category: ACE_Bench
timeout_seconds: 1800
agent_category: "Information Search & Gathering"
---
## Prompt

工作目录中有一篇论文 PDF `/tmp_workspace/paper.pdf`。

请基于该论文制作一份学术会议风格的单页海报 `/tmp_workspace/results/poster.png`。要求：
- 单张图片，横向排版，适合学术会议张贴展示
- 分辨率足够高（最大边 ≥ 3000 像素）
- 包含论文标题、作者信息等核心板块
- 包含不少于 3 张图表（架构图、结果可视化、定量对比等）
- 设计风格统一且专业，配色协调、排版清晰、信息层次分明

如果需要图像理解或多模态生成能力，可以调用 OpenRouter API（base_url 通过环境变量 `OPENROUTER_BASE_URL` 获取，API Key 通过环境变量 `OPENROUTER_API_KEY` 获取）。

## Expected Behavior

Agent 需要自主阅读论文 PDF 并理解其核心贡献与技术细节，也可参考项目主页或代码仓库获取补充信息（如架构图、实验结果图等）。Agent 需合理规划海报版面布局，获取或生成图表素材，并输出高质量的 PNG 学术海报。具体实现路径（内容编排、图表来源与制作方式、设计风格、工具与模型选择）由 agent 自行决定。

## Grading Criteria

- [ ] **[Gating]** `poster.png` 存在且文件大小 > 100KB，尺寸为海报级别（最大边 ≥ 3000px），VLM 确认包含 "SeC" 标题、作者信息、≥ 3 张图表。任一项不通过则所有评分归零。
- [ ] 内容覆盖度（VLM 评估）：poster 上能否清晰传达论文核心内容（动机、方法、实验、结论），内容层次是否分明
- [ ] 可读性（VLM 评估）：文字大小是否适合阅读、信息密度是否合理、图表标注是否清晰
- [ ] 视觉美学（VLM 评估）：配色协调性、排版专业度、留白与构图平衡、整体设计品味

## Automated Checks

```python
def grade(**kwargs) -> dict:
    from pathlib import Path
    import json
    import os
    import re
    import base64
    import io
    import time
    from openai import OpenAI

    ALL_CRITERIA = [
        "basic_requirements",
        "content_coverage",
        "readability",
        "visual_aesthetics",
        "overall_score",
    ]

    OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
    VLM_MODEL = os.environ.get("JUDGE_MODEL", "openai/gpt-5.4")
    OPENROUTER_BASE_URL = os.environ["OPENROUTER_BASE_URL"]

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    def _call_vlm(messages, model=None, max_tokens=2048, retries=2):
        if model is None:
            model = VLM_MODEL
        for attempt in range(retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0,
                )
                return resp.choices[0].message.content
            except Exception:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                return None

    def _extract_json(text):
        if text is None:
            return None
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1)
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            m2 = re.search(r"\{.*\}", text, re.DOTALL)
            if m2:
                try:
                    return json.loads(m2.group(0))
                except json.JSONDecodeError:
                    pass
            return None

    def _load_poster_b64(img_path):
        """Load poster PNG and encode to base64 JPEG for VLM evaluation."""
        from PIL import Image
        img = Image.open(img_path).convert("RGB")
        max_dim = max(img.size)
        if max_dim > 2000:
            scale = 2000 / max_dim
            img = img.resize(
                (int(img.width * scale), int(img.height * scale)),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()

    workspace = Path("/tmp_workspace/results/")
    scores = {}

    # ── 1. Basic requirements (GATING – all must pass) ────────────────

    png_file = workspace / "poster.png"
    if not png_file.exists() or png_file.stat().st_size < 100_000:
        return {k: 0.0 for k in ALL_CRITERIA}

    try:
        from PIL import Image
        img = Image.open(png_file)
        img_w, img_h = img.size
    except Exception:
        return {k: 0.0 for k in ALL_CRITERIA}

    checks = {}

    max_dim = max(img_w, img_h)
    checks["poster_size"] = max_dim >= 3000

    try:
        img_b64 = _load_poster_b64(png_file)
    except Exception:
        return {k: 0.0 for k in ALL_CRITERIA}

    gate_msg = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        },
        {
            "type": "text",
            "text": (
                "You are checking whether this image is a valid academic poster "
                "for the paper 'SeC: Advancing Complex Video Object Segmentation "
                "via Progressive Concept Construction'.\n\n"
                "Answer these 3 yes/no questions by looking at the image:\n"
                '1. has_title: Does the poster visually contain the paper title '
                'with "SeC" or "Concept Construction" clearly readable?\n'
                "2. has_authors: Does the poster show author names (e.g. Zhang, "
                "Ding, Dong, or similar)?\n"
                "3. has_figures: Does the poster contain at least 3 distinct "
                "figures, charts, tables, or diagrams (not just decorative elements)?\n\n"
                "Return ONLY valid JSON:\n"
                '{"has_title": true/false, "has_authors": true/false, '
                '"has_figures": true/false}'
            ),
        },
    ]
    gate_result = _call_vlm([{"role": "user", "content": gate_msg}], max_tokens=256)
    gate_data = _extract_json(gate_result)
    if gate_data:
        checks["has_title"] = bool(gate_data.get("has_title", False))
        checks["has_authors"] = bool(gate_data.get("has_authors", False))
        checks["has_figures"] = bool(gate_data.get("has_figures", False))
    else:
        checks["has_title"] = False
        checks["has_authors"] = False
        checks["has_figures"] = False

    gate_pass = all(checks.values())
    scores["basic_requirements"] = 1.0 if gate_pass else round(
        sum(checks.values()) / len(checks), 2,
    )

    if not gate_pass:
        scores.update({k: 0.0 for k in ALL_CRITERIA if k not in scores})
        scores["overall_score"] = 0.0
        return scores

    # ── 2. Content coverage (VLM judges poster IMAGE) ────────────────

    content_msg = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        },
        {
            "type": "text",
            "text": (
                "You are a strict evaluator for academic conference posters.\n\n"
                "This poster is for the paper 'SeC: Advancing Complex Video "
                "Object Segmentation via Progressive Concept Construction'.\n\n"
                "Look at the POSTER IMAGE and evaluate whether the following "
                "5 topics are VISUALLY PRESENT and READABLE on the poster "
                "(not just mentioned — they must be clearly conveyed):\n"
                "1. Problem motivation (why complex VOS is hard)\n"
                "2. Method overview (progressive concept construction, architecture)\n"
                "3. Key quantitative results (benchmark numbers on MOSE/LVOS/MeViS/SeCVOS)\n"
                "4. Qualitative results or meaningful visualizations (not decorative)\n"
                "5. Conclusion or key takeaways\n\n"
                "Scoring rules — be strict:\n"
                "- Count how many of the 5 topics are clearly presented AND readable.\n"
                "- 5/5 clearly readable → 1.0\n"
                "- 4/5 clearly readable → 0.75\n"
                "- 3/5 clearly readable → 0.55\n"
                "- 2/5 → 0.35\n"
                "- 1/5 → 0.15\n"
                "- 0/5 → 0.0\n"
                "- Deduct 0.1 if content is present but too small/dense to read comfortably.\n"
                "- Deduct 0.1 if figures are placeholder/decorative rather than informative.\n\n"
                "Return ONLY valid JSON:\n"
                '{"content_coverage": <float>, "reasoning": "<1-2 sentences>"}'
            ),
        },
    ]
    cc_result = _call_vlm([{"role": "user", "content": content_msg}], max_tokens=512)
    cc_data = _extract_json(cc_result)
    scores["content_coverage"] = round(
        min(1.0, max(0.0, float(cc_data.get("content_coverage", 0)))) if cc_data else 0.0, 2,
    )

    # ── 3. Readability (VLM judges poster IMAGE) ─────────────────────

    read_msg = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        },
        {
            "type": "text",
            "text": (
                "You are a strict evaluator of READABILITY for academic posters.\n"
                "Imagine this poster is printed at standard conference size "
                "(~90cm × 120cm) and viewed from 1-2 meters away.\n\n"
                "Evaluate these specific aspects:\n"
                "1. Title & headings: Are they large enough to read from 2m?\n"
                "2. Body text: Is it large enough to read from 1m? (≥24pt equivalent)\n"
                "3. Information density: Is there reasonable whitespace, or is it a wall of text?\n"
                "4. Figure labels & captions: Can chart axes, legends, table text be read?\n"
                "5. Visual hierarchy: Can a viewer quickly identify sections and reading order?\n\n"
                "Scoring rules — be harsh on small/dense text:\n"
                "- 1.0 = All text comfortably readable, excellent whitespace, clear hierarchy\n"
                "- 0.7 = Mostly readable but some sections slightly too dense or small\n"
                "- 0.5 = Mixed — headings OK but body text or figure labels too small\n"
                "- 0.3 = Most text too small/dense, would struggle to read at a conference\n"
                "- 0.1 = Barely readable, extremely dense text dump\n"
                "- 0.0 = Unreadable\n\n"
                "Common failure: poster has lots of correct content but crammed into tiny "
                "font sizes — this should score LOW (0.2-0.4), not high.\n\n"
                "Return ONLY valid JSON:\n"
                '{"readability": <float>, "reasoning": "<1-2 sentences>"}'
            ),
        },
    ]
    rd_result = _call_vlm([{"role": "user", "content": read_msg}], max_tokens=512)
    rd_data = _extract_json(rd_result)
    scores["readability"] = round(
        min(1.0, max(0.0, float(rd_data.get("readability", 0)))) if rd_data else 0.0, 2,
    )

    # ── 4. Visual aesthetics (VLM judges poster IMAGE) ───────────────

    aes_msg = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        },
        {
            "type": "text",
            "text": (
                "You are a strict design critic evaluating an academic conference poster.\n\n"
                "Rate the VISUAL AESTHETICS from 0.0 to 1.0. Consider:\n"
                "1. Color scheme: Is the palette cohesive and professional? Or garish/clashing?\n"
                "2. Layout & composition: Balanced columns, intentional alignment, good use of space?\n"
                "3. Typography: Consistent font choices, proper heading hierarchy, professional feel?\n"
                "4. Figure integration: Are figures well-placed, properly sized, visually harmonious?\n"
                "5. Overall polish: Does it look like a carefully designed poster or an auto-generated template dump?\n\n"
                "Scoring — calibrate against real conference posters:\n"
                "- 1.0 = Award-worthy poster design (rare — requires exceptional design craft)\n"
                "- 0.8 = Professionally designed, minor nitpicks only\n"
                "- 0.6 = Competent design, looks intentional but not remarkable\n"
                "- 0.4 = Generic/template-like, functional but uninspired\n"
                "- 0.2 = Poor design choices (clashing colors, bad alignment, cluttered)\n"
                "- 0.0 = Broken or no styling at all\n\n"
                "Most auto-generated posters fall in 0.3-0.5 range. "
                "Do NOT give >0.6 unless the design genuinely impresses you.\n\n"
                "Return ONLY valid JSON:\n"
                '{"visual_aesthetics": <float>, "reasoning": "<1-2 sentences>"}'
            ),
        },
    ]
    aes_result = _call_vlm([{"role": "user", "content": aes_msg}], max_tokens=512)
    aes_data = _extract_json(aes_result)
    scores["visual_aesthetics"] = round(
        min(1.0, max(0.0, float(aes_data.get("visual_aesthetics", 0)))) if aes_data else 0.0, 2,
    )

    # ── Overall score ────────────────────────────────────────────────

    w = {"content_coverage": 1, "readability": 1, "visual_aesthetics": 1}
    total_w = sum(w.values())
    scores["overall_score"] = round(
        sum(scores.get(k, 0.0) * w.get(k, 1) for k in w) / total_w, 4,
    )

    return scores
```

## Workspace Path

```
workspace/ACE_Bench/task_110_paper_to_poster
```

## Skills

```
```

## Env

```
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
JUDGE_MODEL
```

## Warmup

```
pip install requests Pillow pymupdf
```
