---
id: ACE_Bench_task_104_paper_affiliation_search
name: Academic Paper and Affiliation Search
source: WildClawBench
original_id: 04_Search_Retrieval_task_8_paper_affiliation_search
original_category: 04_Search_Retrieval
selection_reason: stable_gpt_win
category: ACE_Bench
timeout_seconds: 3600
agent_category: "Information Search & Gathering"
---
## Prompt

Help me compile the Oral papers accepted at ICCV 2025, and determine how many of them have SJTU (Shanghai Jiao Tong University) as the first affiliation and how many have FDU (Fudan University) as the first affiliation. 

Please provide both the counts and the corresponding list of papers.

- Save the results into `/tmp_workspace/results/results.md`.

## Expected Behavior

The agent should complete the following tasks:

1. The agent should first find the list of Oral papers for ICCV 2025.

2. The agent should then examine each paper to identify those whose first affiliation is SJTU or FDU.

3. Finally, it should count the number of such papers and return both the counts and the corresponding paper titles.

## Grading Criteria

- [ ] Point is awarded if both the paper count and titles are entirely correct; otherwise, no point is given.

## Automated Checks

```python
def grade(**kwargs) -> dict:
    """
    04_Search_Retrieval_task_8_paper_affiliation_search

    Args:

    Returns:
        0 or 1
    """
    import os
    import json
    import logging
    from pathlib import Path

    log = logging.getLogger("04_Search_Retrieval_task_8_paper_affiliation_search Grading Start!")
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    description_file = Path("/tmp_workspace/results/results.md")
    scores = {}

    if not description_file.exists():
        log.warning("results.md not found: %s", description_file)
        scores["overall_score"] = 0.0
        return scores

    pred_description = description_file.read_text(encoding="utf-8").strip()
    if not pred_description:
        log.warning("results.md is empty")
        scores["overall_score"] = 0.0
        return scores

    log.info("Content in MD: %s", pred_description[:200])

    llm_succeeded = False
    last_error = None

    try:
        import time
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=os.environ["OPENROUTER_BASE_URL"],
        )

        gt_description = "\n".join([
            "SJTU: 4",
            "FixTalk: Taming Identity Leakage for High-Quality Talking Head Generation in Extreme Cases",
            "Learning Streaming Video Representation via Multitask Training",
            "Knowledge Distillation for Learned Image Compression",
            "ROAR: Reducing Inversion Error in Generative Image Watermarking",
            "FDU: 0"
        ])
        judge_prompt = f"""你是一位评分裁判。请你依据标准答案和待评估回答进行打分。

具体要求是：
1. 待评估回答会给出以 SJTU 和 FDU 两个大学为第一单位的 ICCV 2025 中稿的 Oral论文 数量和论文标题。
2. 如果论文数量和标题完全正确，返回 score 为 1。
3. 如果论文数量和标题包含任何错误，返回 score 为 0。

【标准答案】
{gt_description}

【待评估回答】
{pred_description}

请只返回一个 JSON 对象，格式：{{"score": <0或1>, "reason": "<简要理由>"}}"""

        max_retries = 3
        for attempt in range(max_retries):
            log.info("LLM Judge request %d/%d...", attempt + 1, max_retries)
            try:
                response = client.chat.completions.create(
                    model=os.environ.get("JUDGE_MODEL", "openai/gpt-5.4"),
                    messages=[{"role": "user", "content": judge_prompt}],
                    temperature=0,
                )

                result_text = response.choices[0].message.content.strip()
                log.info("LLM raw response: %s", result_text[:300])

                if result_text.startswith("```"):
                    result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result_json = json.loads(result_text)
                raw_score = float(result_json.get("score", 0))
                scores["overall_score"] = raw_score
                scores["judge_reason"] = result_json.get("reason", "")
                llm_succeeded = True
                log.info("LLM Judge succeeded — score: %s, reason: %s",
                         raw_score, scores["judge_reason"])
                break

            except Exception as e:
                last_error = e
                log.warning("LLM Judge attempt %d failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

    except Exception as e:
        last_error = e
        log.error("OpenAI client initialization failed: %s", e)

    if not llm_succeeded and last_error:
        scores["judge_error"] = str(last_error)

    # ---- Fall back to keyword matching when all LLM attempts fail ----
    if not llm_succeeded:
        log.warning("LLM Judge failed all 3 attempts, scoring 0")
        scores["overall_score"] = 0

    log.info("Final score: overall_score=%s",
             scores["overall_score"])

    return scores
```
## Workspace Path

```
workspace/ACE_Bench/task_104_paper_affiliation_search
```

## Skills

```
agent-browser
```

## Env

```
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
JUDGE_MODEL
```

## Warmup

```
npm install -g agent-browser
```

