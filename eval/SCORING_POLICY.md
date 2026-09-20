# Routing Evaluation — Scoring Policy v2 (scope-corrected, locked 2026-09-19)

## Scope correction (supersedes v1)
The router's ONLY job is **intelligent model routing**: understand the prompt and
route to the best-fit model on capability/cost/latency. It is NOT a guardrail or
security layer — Amazon Bedrock Guardrails handles financial/legal/medical advice,
profanity, prompt attacks, etc., as a separate in-path layer on the same gateway.
Consequences:
- The `sensitive` question is REMOVED from all engines and from `resolve_tier`.
- The v1 "sensitive" dataset cell (30 rows) is EXCLUDED from scoring: its
  expected_tier=frontier labels encoded sensitivity, not capability need.
- v1 results (incl. the jev-vs-bedrock dangerous-downgrade verdict, which rested
  on sensitivity detection) are superseded; kept in artifacts for transparency.

## Ground truth
270 prompts (v1 dataset minus sensitive cell), labels = cheapest tier that
handles the prompt WELL: fast / balanced / coding / frontier.

## Correctness
1. exact: predicted == expected.
2. acceptable: exact OR one tier up the cost ladder.
3. **capability downgrade (safety-critical, target 0): expected=frontier,
   predicted=fast.** (Two-tier drop: a frontier-hard task on the cheapest model.)
4. overshoot: predicted above expected (cost waste).

## Engines under test (four-way)
- heuristic: input-size thresholds (baseline)
- bedrock: Haiku 4.5 tool-forced JSON classifier (in-account)
- jev: TypeSafe Jev via OpenRouter /api/alpha/decisions
- embedding: Bifrost-style semantic router — Titan Text Embeddings v2, kNN over
  reference phrases (12/tier sampled from the TUNE split, per Bifrost's own
  guidance to draw phrases from real traffic; sampling from tune = legitimate
  tuning, zero holdout leakage). Below-floor similarity -> frontier (fail-up).

## Metrics
exact, acceptable, capability_downgrades, overshoot_rate, decision latency
p50/p99, decision $/1k, projected blended savings vs always-frontier
(Luna 0.20|1.20, Terra 2|12, K3 0.60|2.50, Sol 4|20 per M; 300 in/500 out per req).

## Discipline
Same 70/30 split (seed 42). All four engines re-run on tune under v2 policy;
threshold/phrase selection on tune only; single holdout pass for all four
(reported side by side; per-engine holdout is single-touch under v2).
