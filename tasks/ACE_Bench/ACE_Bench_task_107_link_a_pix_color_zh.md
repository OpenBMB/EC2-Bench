---
id: ACE_Bench_task_107_link_a_pix_color_zh
name: Link-a-Pix Color — Solve Puzzle and Recognize Filled Pattern
source: WildClawBench
original_id: 02_Code_Intelligence_task_8_link_a_pix_color_zh
original_category: 02_Code_Intelligence
selection_reason: both_low_max_lt_0.4
category: ACE_Bench
timeout_seconds: 3600
agent_category: "Development & Operations"
---
## Prompt

这是一道**连数字**谜题（也叫 Link-a-Pix / Paint by Pairs）。请你完成以下任务（注意：时间有限，尽量15分钟内完成，请注意高效）：

**游戏规则：**
每道谜题都由一个网格组成，网格的一些方格中包含了彩色的提示数字。每个提示数字（除了"1"以外）都是成对儿出现的——相同颜色、相同数值的数字恰好出现两次。游戏的目的是：通过水平或垂直的路径连接每一对提示数字，使得连线的路径长度（包含两端含有提示数字的方格）与提示数字的值一致。提示数字"1"表示该方格自身即为一个长度为 1 的路径。所有连线路径不能交叉，且每个方格最多只被一条路径经过。当所有数字对都被正确连线后，被路径覆盖的方格会按对应的颜色填充，从而还原出一幅隐藏在网格背后的像素画。

**你的任务：**
1. 分析输入图片，识别网格中所有提示数字的位置、数值和颜色。
2. 编写代码，按照上述规则求解谜题——为每一对同色同值的数字找到一条长度匹配的连线路径。
3. 将所有路径对应的方格按颜色填充，得到最终的像素画，并保存为图片。
4. 观察填色完成后的像素画，判断它画出来的是什么东西或场景。

**输入图片路径：**
`/tmp_workspace/input/origin.png`

**要求：**
1. 将填色完成后的像素画保存为：`/tmp_workspace/results/result.png`（按游戏规则求解并填色后的完整网格图片）
2. 将你对「填色后图中是什么」的**中文**描述写入：`/tmp_workspace/results/description.txt`（纯文本，必须使用中文，一两句即可，尽量全面描述填色后呈现的物体或场景）。

如果需要图像理解或多模态生成能力，可以调用 OpenRouter API（base_url 通过环境变量 `OPENROUTER_BASE_URL` 获取，API Key 通过环境变量 `OPENROUTER_API_KEY` 获取）。

## Expected Behavior

评测方预期智能体会：

1. 读取 `/tmp_workspace/input/origin.png` 中的彩色连数字网格图。
2. 借助视觉/OCR 等方式识别各数字的位置（行、列）、数值和颜色。
3. 使用 Python（如 PIL、matplotlib、OpenCV 等）编写求解算法，为每一对同色同值的数字找到合法的连线路径。
4. 对单独的"1"，直接将其所在方格填上对应颜色。
5. 将所有路径覆盖的方格按颜色填充，生成像素画并保存为 `/tmp_workspace/results/result.png`。
6. 分析填色结果，正确描述画面呈现的物体或场景，并写入 `/tmp_workspace/results/description.txt`。

## Grading Criteria

评分由两部分组成，各占 50%：

- **图片完成度（50%）**：VLM 对比 `results/result.png` 与标准答案 `gt/gt.png` 的填色相似度，打分 0-10 分后归一化
- **描述准确度（50%）**：LLM 评估 `description.txt` 与标准答案描述的语义相似度，打分 0-10 分后归一化

`overall_score = 0.5 × image_score + 0.5 × description_score`

## Automated Checks

```python
def grade(**kwargs) -> dict:
    """
    Link-a-Pix (color pixel art) grading.

    - Image completeness (50%): VLM compares results/result.png with gt/gt.png
    - Description accuracy (50%): LLM evaluates semantic similarity to ground truth
    """
    import os
    import json
    import base64
    import logging
    import time
    from pathlib import Path

    log = logging.getLogger("grade_link_a_pix_color")
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    base = Path("/tmp_workspace")
    result_image = base / "results" / "result.png"
    description_file = base / "results" / "description.txt"
    gt_image = base / "gt" / "gt.png"

    gt_description = "在蓝天白云和太阳的背景下，一个穿着红色上衣、黑色裤子的人正站在绿色草地上骑滑板车。"

    scores = {}
    image_score = 0.0
    description_score = 0.0

    # ========== OpenAI client initialization ==========
    client = None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=os.environ["OPENROUTER_BASE_URL"],
        )
    except Exception as e:
        log.error("OpenAI client initialization failed: %s", e)

    # ========== 1. Image completeness score (50%) ==========
    if not result_image.exists():
        log.warning("result.png not found: %s", result_image)
        scores["image_score"] = 0.0
    elif not gt_image.exists():
        log.warning("gt.png not found: %s", gt_image)
        scores["image_score"] = 0.0
    else:
        try:
            pred_b64 = base64.b64encode(result_image.read_bytes()).decode("utf-8")
            gt_b64 = base64.b64encode(gt_image.read_bytes()).decode("utf-8")

            if client:
                vlm_prompt = (
                    "你是一位评分裁判。请比较以下两张图片的相似度和完成度。\n"
                    "第一张图是标准答案（正确解答的连数字像素画），第二张图是待评估的答案。\n\n"
                    "这是一道连数字（Link-a-Pix）谜题的解答结果。正确的解答应该是：\n"
                    "每对同色同值的数字之间用路径连接，路径覆盖的方格用对应颜色填充，最终形成一幅像素画。\n\n"
                    "评分标准（0-10 分）：\n"
                    "- 10分：填色结果与标准答案完全一致或几乎完全一致，像素画完整还原\n"
                    "- 7-9分：大部分路径正确连接并填色，像素画主体清晰可辨，但有少量区域填色错误或遗漏\n"
                    "- 4-6分：部分路径正确，能看出一些填色区域，但整体像素画不完整或有较多错误\n"
                    "- 1-3分：只有少量填色正确，整体与标准答案差距较大\n"
                    "- 0分：完全没有填色、图片为空、或与标准答案完全不相关\n\n"
                    '请只返回一个 JSON 对象，示例：\n'
                    '{"score": 7, "reason": "大部分填色正确但右下角区域有偏差"}'
                )

                max_retries = 3
                for attempt in range(max_retries):
                    log.info("VLM image comparison attempt %d/%d...", attempt + 1, max_retries)
                    try:
                        resp = client.chat.completions.create(
                            model=os.environ.get("JUDGE_MODEL", "openai/gpt-5.4"),
                            messages=[{"role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{gt_b64}"}},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pred_b64}"}},
                                {"type": "text", "text": vlm_prompt},
                            ]}],
                            temperature=0.0,
                            max_tokens=256,
                        )
                        raw = resp.choices[0].message.content.strip()
                        log.info("VLM response: %s", raw[:300])
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                        result_json = json.loads(raw)
                        raw_score = int(result_json["score"])
                        image_score = round(max(0.0, min(1.0, raw_score / 10.0)), 4)
                        scores["image_judge_reason"] = result_json.get("reason", "")
                        scores["image_judge_method"] = "vlm"
                        log.info("VLM image score: %d/10, reason: %s", raw_score, scores["image_judge_reason"])
                        break
                    except Exception as e:
                        log.warning("VLM image comparison attempt %d failed: %s", attempt + 1, e)
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)

                if "image_judge_method" not in scores:
                    log.warning("VLM image comparison failed all 3 attempts, image score 0")
            else:
                log.warning("OpenAI client unavailable, image score 0")

        except Exception as e:
            log.warning("Image read failed: %s", e)

    scores["image_score"] = image_score

    # ========== 2. Description accuracy score (50%) ==========
    if not description_file.exists():
        log.warning("description.txt not found: %s", description_file)
        scores["description_score"] = 0.0
    else:
        pred_description = description_file.read_text(encoding="utf-8").strip()
        if not pred_description:
            log.warning("description.txt is empty")
            scores["description_score"] = 0.0
        else:
            log.info("Read description for evaluation: %s", pred_description[:200])

            # ---- Fallback: keyword-based text matching ----
            def keyword_fallback(pred: str) -> float:
                pred_lower = pred.lower()
                primary_kws = [
                    "滑板车", "滑板", "scooter", "skateboard", "kickboard"
                ]
                person_kws = [
                    "人", "人物", "小孩", "男孩", "女孩", "少年",
                    "person", "boy", "girl", "kid", "child", "rider"
                ]
                clothing_kws = [
                    "红色衣服", "红色上衣", "红衣", "红色",
                    "黑色裤子", "黑裤", "黑色"
                ]
                background_kws = [
                    "草地", "绿地", "草坪", "草", "grass",
                    "天空", "蓝天", "sky",
                    "太阳", "sun", "阳光",
                    "云", "白云", "cloud"
                ]

                has_primary = any(kw in pred_lower for kw in primary_kws)
                has_person = any(kw in pred_lower for kw in person_kws)
                clothing_hits = sum(1 for kw in clothing_kws if kw in pred_lower)
                bg_hits = sum(1 for kw in background_kws if kw in pred_lower)

                log.info(
                    "Keyword match — primary(scooter): %s, person: %s, clothing hits: %d, background hits: %d",
                    has_primary, has_person, clothing_hits, bg_hits
                )

                if has_primary and has_person:
                    return min(1.0, 0.8 + 0.03 * clothing_hits + 0.02 * bg_hits)
                if has_primary:
                    return min(0.75, 0.55 + 0.03 * clothing_hits + 0.02 * bg_hits)
                if has_person and bg_hits >= 2:
                    return min(0.45, 0.25 + 0.03 * clothing_hits + 0.02 * bg_hits)
                return min(0.2, 0.03 * clothing_hits + 0.02 * bg_hits)

            # ---- Prefer LLM as Judge ----
            llm_succeeded = False

            if client:
                judge_prompt = f"""你是一位评分裁判。请比较以下两段描述的语义相似度。

【标准答案】
{gt_description}

【待评估回答】
{pred_description}

评分标准（0-10 分）：
- 10分：完全准确地描述了相同的物体/场景，关键要素齐全（人物、骑滑板车、红色上衣、黑色裤子、蓝天白云、太阳、绿色草地）
- 7-9分：正确识别出主体是一个人在骑滑板车，且提及了部分背景或服装细节，但有遗漏或不够准确
- 4-6分：大致方向正确（如识别出人物或运动场景），但未准确识别出滑板车或关键要素有较大偏差
- 1-3分：描述有少量相关元素，但整体偏差较大
- 0分：完全不相关或为空

请只返回一个 JSON 对象，示例：
{{
    "score": 7,
    "reason": "正确识别了主体但缺少部分细节"
}}"""

                max_retries = 3
                for attempt in range(max_retries):
                    log.info("LLM Judge attempt %d/%d...", attempt + 1, max_retries)
                    try:
                        response = client.chat.completions.create(
                            model=os.environ.get("JUDGE_MODEL", "openai/gpt-5.4"),
                            messages=[{"role": "user", "content": judge_prompt}],
                            temperature=0.0,
                            max_tokens=256,
                        )

                        result_text = response.choices[0].message.content.strip()
                        log.info("LLM raw response: %s", result_text[:300])

                        if result_text.startswith("```"):
                            result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                        result_json = json.loads(result_text)
                        raw_score = int(result_json["score"])
                        description_score = round(max(0.0, min(1.0, raw_score / 10.0)), 4)
                        scores["desc_judge_reason"] = result_json.get("reason", "")
                        scores["desc_judge_method"] = "llm"
                        scores["llm_attempts"] = attempt + 1
                        llm_succeeded = True
                        log.info(
                            "LLM Judge succeeded — score: %d/10, reason: %s",
                            raw_score, scores["desc_judge_reason"]
                        )
                        break

                    except Exception as e:
                        log.warning("LLM Judge attempt %d failed: %s", attempt + 1, e)
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)

            if not llm_succeeded:
                log.warning("LLM Judge failed, falling back to keyword matching")
                description_score = keyword_fallback(pred_description)
                scores["desc_judge_method"] = "keyword_fallback"
                log.info("Keyword fallback score: %s", description_score)

    scores["description_score"] = description_score

    # ========== Total score ==========
    scores["overall_score"] = round(0.5 * scores["image_score"] + 0.5 * scores["description_score"], 4)
    log.info(
        "Final scores: image=%.4f × 50%% + description=%.4f × 50%% = overall=%.4f",
        scores["image_score"], scores["description_score"], scores["overall_score"]
    )

    return scores
```
## Workspace Path

```
workspace/ACE_Bench/task_107_link_a_pix_color_zh
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
pip install openai Pillow
```
