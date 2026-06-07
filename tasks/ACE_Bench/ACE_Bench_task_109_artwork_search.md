---
id: ACE_Bench_task_109_artwork_search
name: Confirm the location of an artwork.
source: WildClawBench
original_id: 04_Search_Retrieval_task_9_artwork_search
original_category: 04_Search_Retrieval
selection_reason: both_low_max_lt_0.4
category: ACE_Bench
timeout_seconds: 1800
agent_category: "Information Search & Gathering"
---
## Prompt

It is now mid-July 2025. Where can I go to view this painting?

- The image path is: `/tmp_workspace/04_Search_Retrieval_task_10_artwork_search/painting.jpg`

- Save the results into `/tmp_workspace/results/results.md`.

If you need image understanding or multimodal capabilities, you can call the OpenRouter API (base_url available via the `OPENROUTER_BASE_URL` environment variable, API key available via the `OPENROUTER_API_KEY` environment variable).

## Expected Behavior

The agent should complete the following tasks:

1. The agent should first determine the specific details of the painting (possibly with the help of search).

2. The agent should then further verify the location of the artwork in mid-July 2025.

## Grading Criteria

- [ ] Points are awarded if the position is right.

## Automated Checks

```python
def grade(**kwargs) -> dict:
    """
    04_Search_Retrieval_task_9_artwork_search

    Args:

    Returns:
        0 or 1
    """
    import os
    import json
    import logging
    from pathlib import Path

    log = logging.getLogger("04_Search_Retrieval_task_9_artwork_search Grading Start!")
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

        gt_description = "Museum of Art Pudong, Shanghai（浦东美术馆）"
        judge_prompt = f"""你是一位评分裁判。请你依据标准答案和待评估回答进行打分。

具体要求是：
1. 如果待评估回答中预测的位置和标准答案不一样，就返回 score 为 0。
2. 如果待评估回答中预测的位置和标准答案一样，就返回 score 为 1。

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
workspace/ACE_Bench/task_109_artwork_search
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

