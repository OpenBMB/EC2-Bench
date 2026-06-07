---
id: ACE_Bench_task_105_repo_to_slides
name: GitHub Repository to Project Presentation
source: WildClawBench
original_id: 05_Creative_Synthesis_task_9_repo_to_slides
original_category: 05_Creative_Synthesis
selection_reason: stable_gpt_win
category: ACE_Bench
timeout_seconds: 1800
agent_category: "Office & Daily Tasks"
---
## Prompt

请为 Meta 的开源项目 SAM 3: Segment Anything with Concepts（https://github.com/facebookresearch/sam3/）制作一份 8 页的项目介绍演示文稿。

输出文件为 `/tmp_workspace/results/output.pdf`，要求：
- 恰好 8 页
- 设计风格统一且专业

如果需要图像理解或多模态生成能力，可以调用 OpenRouter API（base_url 通过环境变量 `OPENROUTER_BASE_URL` 获取，API Key 通过环境变量 `OPENROUTER_API_KEY` 获取）。

## Expected Behavior

Agent 需要自主访问 GitHub 仓库了解 SAM 3 项目的技术细节与核心贡献，合理组织内容结构，获取或生成所需图片素材，并输出 PDF 格式的演示文稿。具体实现路径（内容编排、图片来源、设计风格、工具与模型选择）由 agent 自行决定。

## Grading Criteria

- [ ] **[Gating]** `output.pdf` 存在。不通过则所有评分归零。
- [ ] 文本内容涵盖项目关键主题（首页标题含 SAM 3、架构、结果、数据集、创新点）（VLM 评估）
- [ ] 视觉设计质量：配色统一、版式专业、信息层次清晰（VLM 评估）

## Automated Checks

```python
def grade(**kwargs) -> dict:
    import base64
    import io
    import json
    import os
    import re
    import time
    from openai import OpenAI
    from pathlib import Path

    OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
    VLM_MODEL = os.environ.get("JUDGE_MODEL", "openai/gpt-5.4")
    OPENROUTER_BASE_URL = os.environ["OPENROUTER_BASE_URL"]

    ALL_CRITERIA = [
        "basic_requirements",
        "content_coverage",
        "visual_quality",
        "overall_score",
    ]


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
            except Exception as e:
                print(f"  [VLM call attempt {attempt + 1} failed: {e}]")
                if attempt < retries:
                    time.sleep(2 ** attempt)
                else:
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

    workspace = Path("/tmp_workspace/results")
    scores = {}

    pdf_file = workspace / "output.pdf"
    if not pdf_file.exists():
        print(f"[FAIL] output.pdf not found ({pdf_file})")
        return {k: 0.0 for k in ALL_CRITERIA}

    try:
        import fitz
    except ImportError:
        print("[FAIL] PyMuPDF not installed (pip install pymupdf)")
        return {k: 0.0 for k in ALL_CRITERIA}

    try:
        doc = fitz.open(str(pdf_file))
    except Exception as e:
        print(f"[FAIL] Cannot open PDF: {e}")
        return {k: 0.0 for k in ALL_CRITERIA}

    n_pages = len(doc)
    print(f"\n=== PDF Info ===")
    print(f"  Pages: {n_pages}, Size: {pdf_file.stat().st_size:,} bytes")

    gate_pass = n_pages == 8
    scores["basic_requirements"] = 1.0 if gate_pass else 0.0
    print(f"  Page count: {n_pages} (need 8) -> {'OK' if gate_pass else 'FAIL'}")
    print(f"  basic_requirements: {scores['basic_requirements']}")

    if not gate_pass:
        doc.close()
        scores.update({k: 0.0 for k in ALL_CRITERIA if k not in scores})
        scores["overall_score"] = 0.0
        return scores

    print(f"\n=== Content Coverage (VLM Judge) ===")
    content_score = 0.0
    try:
        images_b64 = []
        for i in range(n_pages):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(2, 2))
            images_b64.append(base64.b64encode(pix.tobytes("png")).decode())

        if images_b64:
            from PIL import Image

            pil_images = [Image.open(io.BytesIO(base64.b64decode(b))) for b in images_b64]
            w, h = pil_images[0].size
            cols = 2
            rows = (len(pil_images) + cols - 1) // cols
            grid = Image.new("RGB", (w * cols, h * rows), "white")
            for idx, img in enumerate(pil_images):
                grid.paste(img, ((idx % cols) * w, (idx // cols) * h))

            buf = io.BytesIO()
            grid.save(buf, "JPEG", quality=85)
            grid_b64 = base64.b64encode(buf.getvalue()).decode()

            vlm_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {
                    "type": "text",
                    "text": (
                        "You are evaluating a presentation about SAM 3 (Segment Anything with "
                        "Concepts) by Meta. The image shows all 8 slides in a 2x4 grid.\n\n"
                        "Rate content coverage from 0.0 to 1.0 based on these criteria:\n"
                        "1. Title slide clearly shows 'SAM 3' project name\n"
                        "2. Project overview / introduction\n"
                        "3. Model architecture\n"
                        "4. Key results / benchmarks\n"
                        "5. SA-Co dataset\n"
                        "6. Innovation over SAM 2\n\n"
                        "Return ONLY valid JSON:\n"
                        '{"content_coverage": <float>}'
                    ),
                },
            ]
            result = _call_vlm([{"role": "user", "content": vlm_content}], max_tokens=512)
            data = _extract_json(result)
            if data:
                content_score = min(1.0, max(0.0, float(data.get("content_coverage", 0))))
    except Exception as e:
        print(f"  [VLM evaluation error: {e}]")

    scores["content_coverage"] = round(content_score, 2)
    print(f"  content_coverage: {scores['content_coverage']}")

    print(f"\n=== Visual Quality (VLM Judge) ===")
    visual_score = 0.0
    try:
        images_b64 = []
        for i in range(min(n_pages, 8)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(2, 2))
            images_b64.append(base64.b64encode(pix.tobytes("png")).decode())

        if images_b64:
            from PIL import Image

            pil_images = [Image.open(io.BytesIO(base64.b64decode(b))) for b in images_b64]
            w, h = pil_images[0].size
            cols = 2
            rows = (len(pil_images) + cols - 1) // cols
            grid = Image.new("RGB", (w * cols, h * rows), "white")
            for idx, img in enumerate(pil_images):
                grid.paste(img, ((idx % cols) * w, (idx // cols) * h))

            buf = io.BytesIO()
            grid.save(buf, "JPEG", quality=85)
            grid_b64 = base64.b64encode(buf.getvalue()).decode()

            vlm_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{grid_b64}"}},
                {
                    "type": "text",
                    "text": (
                        "Rate presentation visual design from 0.0 to 1.0:\n"
                        "Color consistency, layout, typography, image usage, polish.\n"
                        "1.0=conference-quality, 0.7=good, 0.5=plain, 0.3=problems, 0.0=broken\n\n"
                        "Return ONLY valid JSON:\n"
                        '{"visual_quality": <float>}'
                    ),
                },
            ]
            vlm_result = _call_vlm([{"role": "user", "content": vlm_content}], max_tokens=512)
            vlm_data = _extract_json(vlm_result)
            if vlm_data:
                visual_score = min(1.0, max(0.0, float(vlm_data.get("visual_quality", 0))))
    except Exception as e:
        print(f"  [VLM evaluation error: {e}]")

    doc.close()
    scores["visual_quality"] = round(visual_score, 2)
    print(f"  visual_quality: {scores['visual_quality']}")

    w = {"content_coverage": 1, "visual_quality": 2}
    total_w = sum(w.values())
    scores["overall_score"] = round(
        sum(scores.get(k, 0.0) * w.get(k, 1) for k in w) / total_w, 4,
    )

    return scores
```

## Workspace Path

```
workspace/ACE_Bench/task_105_repo_to_slides
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
pip install requests pymupdf Pillow
```
