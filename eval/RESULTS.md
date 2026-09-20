# LLM Intelligent Routing — Consolidated Evaluation Findings

**Date:** 2026-09-19 · **Final config:** v5 (19 task classes) · **Final dataset:** v4 (585 labeled prompts)
Region us-east-1 · Measured against live infrastructure: AgentCore Gateway inference target + interceptor Lambda + `routing_config.json` (S3, hot-reload).

## 1. What was evaluated

A gateway-intercepting router: callers send `model: "auto"`; a decision engine
classifies the prompt (task type, complexity, reasoning-need + calibrated
confidence); deterministic policy maps the classification to a model tier.
Scope: **intelligent model routing only** (content safety is out of scope —
separate concern).

**Four decision engines, identical harness and dataset:**
1. **jev** — TypeSafe Jev 1.13 (System One decision model) via OpenRouter `/api/alpha/decisions`
2. **bedrock** — Claude Haiku 4.5 tool-forced JSON classifier (in-account)
3. **embedding** — Bifrost-style semantic router (Titan Embed v2, kNN over reference phrases)
4. **heuristic** — input-size thresholds (baseline)

## 2. Final task taxonomy (19 classes) and routing map

| Base tier → model (OpenAI-family ladder) | Task classes |
|---|---|
| **fast** → GPT-5.6 Luna ($0.20/$1.20 per M) | simple_qa, classification, extraction, summarization, rewriting, translation, **code_simple** |
| **balanced** → GPT-5.6 Terra ($2/$12) | customer_support, rag_qa, content_generation, analysis, creative_writing, **product_requirements** |
| **coding** → Kimi K3 | coding, **sysadmin_scripting** |
| **frontier** → GPT-5.6 Sol ($4/$20) | math, reasoning_planning, agentic, **code_architecture** |

(Anthropic-family callers: Sonnet 5 / Opus 4.8 ladder.) Plus per-prompt
complexity escalation (one tier up; code_simple→coding) and a confidence floor
(<0.5 → frontier, fail-up). Everything above is operator-editable in
`routing_config.json` with ~60s hot-reload — the coding split and both new
classes (sysadmin_scripting, product_requirements) shipped without code changes.

## 3. Final results — holdout, 177 prompts, single pass

| Engine | Exact | Acceptable* | Dangerous downgrades | Overshoot | Blended savings vs always-frontier | Decision p50 | Decision cost /1k |
|---|---|---|---|---|---|---|---|
| **jev** | **94.9%** | **97.7%** | **0** | 2.8% | **56.1%** | **224ms** | **~$0.02** |
| bedrock | 93.8% | 97.2% | 0 | 3.4% | 55.3% | 958ms | ~$1 |
| embedding | 22.0% | 62.7% | 0 | 77.4% | 0.8% | 82ms | ~$0.02 |
| heuristic | 36.7% | 36.7% | **38** | 0% | — | ~0ms | $0 |

*Acceptable = exact or one tier up (up-routing spends more, never degrades quality).
Dangerous = frontier-labeled prompt routed to the cheapest tier. Savings assume
300-in/500-out tokens per request over the eval mix.

**Per-task (jev, holdout):** 15 of 19 classes at 100% (incl. both new classes
9/9: sysadmin_scripting, product_requirements; and code_simple 9/9,
reasoning_planning 11/11, math 9/9). Below 100%: analysis 7/11 (escalated
upward — cost-safe), coding 7/9 (2 trivial-looking test asks → fast),
code_architecture 8/9 (1 → K3, still code-capable), rag_qa & agentic 8/9.
**Zero downgrades of hard prompts to cheap models anywhere in the series.**

## 4. Key findings

1. **Decision-model routing works and pays for itself immediately.** ~56% token-
   spend reduction; a single Sol→Luna downgrade saves ~500x the routing decision's
   cost. Router overhead: 224ms (jev).
2. **Semantic-similarity routing (Bifrost's method) empirically fails — and
   more examples don't fix it.** At 12 phrases/tier: 22% holdout exact. Re-tested
   at Bifrost's shipped density (50/tier, 192 total, drawn from real traffic per
   their guidance): tune accuracy jumped to 59% but **holdout stayed at 23%** —
   a classic memorization signature. Added phrases teach the engine to recognize
   those specific prompts, not to judge difficulty; unseen traffic (the only kind
   production sees) gains nothing. Embeddings measure what a prompt is ABOUT,
   not how HARD it is. (Also: kNN latency scales with phrase count — 82ms → 213ms.)
3. **Jev leads on every dimension simultaneously** under a rich taxonomy
   (accuracy, 4x latency, 50x decision cost vs the Haiku classifier); the
   19-way typed Choice with calibrated confidence is its home turf. The Haiku
   engine remains a close-accuracy in-account fallback for data-sovereignty
   requirements (config swap).
4. **Taxonomy granularity improved accuracy** rather than degrading it
   (5 classes: 87.8% → 15: 97.9% → 19: 94.9% on progressively harder datasets)
   — precise task definitions give the classifier sharper boundaries and the
   policy sharper routes (e.g. regex-explain → Luna at 1/12th K3's price;
   schema design → Sol instead of under-served by K3).
5. **Errors skew upward by construction.** The fail-up design (confidence floor,
   escalation-only complexity modifier) means classification mistakes cost
   pennies, not quality. Overshoot: 2.8%.

## 5. Method (for scrutiny)

- 585 prompts across 19 cells, Sonnet-4.6-generated from per-cell specs
  (labels = generation intent), incl. deliberate boundary cases.
- 70/30 stratified tune/holdout; thresholds and reference phrases selected on
  tune only; holdout single-pass per configuration.
- Scoring policy locked before evaluation (eval/SCORING_POLICY.md, v2
  scope-corrected). Engines evaluated through the production router code path.
- Known limitation: synthetic prompts. Recommended next validation: shadow mode
  on real traffic (log-only decisions), then re-score against this harness.

## 6. Artifact map

`source/config/routing_config.json` (operator config) · `eval/dataset_v4.jsonl`
(585 labeled prompts) · `eval/run_eval.py` + `eval/SCORING_POLICY.md` (harness +
locked scoring rules) · `eval/build_dataset.py` (dataset generator) ·
`source/lambda/router/router.py` (all four engines + policy — the production
code path the eval exercises). Deploy your own instance with
`deployment/deploy.sh` and inspect decisions in the stack's DynamoDB table.
