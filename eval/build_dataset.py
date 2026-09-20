"""Phase 4: build the labeled routing dataset (~300 prompts)."""
import json
import random

import boto3
from botocore.config import Config

random.seed(42)
R = "us-east-1"
ROOT = "/home/ec2-user/projects/agentcoreTest/llm-intelligent-routing"
brt = boto3.client("bedrock-runtime", region_name=R, config=Config(read_timeout=300))

CELLS = [
    # (task, expected_tier, n, spec)
    ("simple_qa", "fast", 35,
     "Trivial factual questions or short chat (capitals, definitions, conversions, greetings)."),
    ("classification", "fast", 30,
     "Categorize/tag/label provided text: sentiment of a review (include the short review inline), "
     "intent of a message, topic tagging, spam detection, priority triage."),
    ("extraction", "fast", 30,
     "Extract structured fields (dates, names, amounts, entities) from a short inline text into JSON/list."),
    ("summarization", "fast", 30,
     "Summarize/condense a SHORT provided text (2-5 sentences inline). Simple transformation."),
    ("rewriting", "fast", 30,
     "Edit/proofread/rephrase provided text (include it inline): fix grammar, change tone, shorten, "
     "make more formal/casual."),
    ("translation", "fast", 30,
     "Translate a provided sentence/short paragraph between languages (include the text inline; "
     "vary language pairs)."),
    ("customer_support", "balanced", 30,
     "Draft a helpful, empathetic response to a customer inquiry/complaint (include the customer's "
     "message inline): billing issues, product problems, service questions."),
    ("rag_qa", "balanced", 30,
     "Answer a question using provided context (include a 3-6 sentence context passage inline plus "
     "a question about it that requires combining details)."),
    ("content_generation", "balanced", 30,
     "Create business content: marketing copy, product descriptions, emails, LinkedIn posts, "
     "job descriptions, press-release drafts. Moderate length and quality expectations."),
    ("analysis", "balanced", 35,
     "Multi-step analysis or synthesis of moderate difficulty: compare options with criteria, "
     "explain tradeoffs, interpret a described trend, structured plans."),
    ("creative_writing", "balanced", 30,
     "Creative prose: short stories, poems, dialogue scenes, creative descriptions. Moderate ambition."),
    ("coding", "coding", 35,
     "Programming tasks: write/debug/refactor functions, explain code, tests, algorithms, SQL queries."),
    ("math", "frontier", 30,
     "Genuine mathematical/quantitative problem solving: multi-step word problems, probability, "
     "optimization, financial math requiring careful calculation chains."),
    ("reasoning_planning", "frontier", 35,
     "Hard multi-step reasoning: intricate system design under constraints, multi-constraint "
     "optimization, strategy under uncertainty, long-horizon planning with tradeoff justification."),
    ("agentic", "frontier", 30,
     "Requests to orchestrate multi-step workflows: 'research X then compare Y then produce Z', "
     "multi-tool task sequences, autonomous multi-stage jobs with dependencies."),
]


def gen(spec, n):
    out = []
    for chunk in ([n] if n <= 30 else [n - n // 2, n // 2]):
        r = brt.converse(
            modelId="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": [{"text":
                f"Generate exactly {chunk} distinct realistic user prompts for this category:\n{spec}\n"
                f"Vary length, phrasing, domain and formality. Output ONLY a JSON array of {chunk} strings."}]}],
            inferenceConfig={"maxTokens": 8000})
        txt = r["output"]["message"]["content"][0]["text"]
        out += json.loads(txt[txt.index("["):txt.rindex("]") + 1])
    return out


def main():
    rows = []
    for task, tier, n, spec in CELLS:
        prompts = gen(spec, n)
        print(f"{task}: {len(prompts)}")
        for p in prompts[:n]:
            rows.append({"prompt": p, "task": task, "expected_tier": tier})
    # stratified split
    from collections import defaultdict
    strata = defaultdict(list)
    for r_ in rows:
        strata[(r_["task"], r_["expected_tier"])].append(r_)
    for grp in strata.values():
        random.shuffle(grp)
        cut = round(len(grp) * 0.7)
        for i, r_ in enumerate(grp):
            r_["split"] = "tune" if i < cut else "holdout"
    with open(f"{ROOT}/eval/dataset_v2.jsonl", "w") as fh:
        for i, r_ in enumerate(rows):
            r_["id"] = f"r{i:04d}"
            fh.write(json.dumps(r_, ensure_ascii=False) + "\n")
    from collections import Counter
    print("total:", len(rows), Counter(r_["split"] for r_ in rows),
          Counter(r_["expected_tier"] for r_ in rows))


if __name__ == "__main__":
    main()
