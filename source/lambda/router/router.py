"""LLM router: AgentCore Gateway REQUEST interceptor.

Resolves the virtual model id "auto" to a concrete target-qualified model using
a pluggable decision engine (heuristic | bedrock | jev) + deterministic policy.
Fail-open: any engine error/timeout -> DEFAULT_MODEL (frontier).
"""
import base64
import json
import logging
import os
import time
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

VIRTUAL_MODEL = "auto"
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "bedrock/anthropic.claude-opus-4-8")
ENGINE = os.environ.get("DECISION_ENGINE", "bedrock")
CONF_FLOOR = float(os.environ.get("CONF_FLOOR", "0.5"))

# API-family-aware tier tables — STANDARDIZED ON bedrock-runtime.
# Placement informed by model cards + pricing ($/M in|out):
#   Luna  0.20|1.20  "fast, OpenAI's lowest cost, high-volume"        -> fast
#   Terra 2|12       "balanced, GPT-5.5-competitive at half cost"     -> balanced
#   Kimi K3 (reasoning model, agentic/coding strength)                -> coding
#   Sol   4|20       "most capable, frontier reasoning, 1M ctx"       -> frontier
# GPT-5.6 dialect: max_completion_tokens (normalize_params handles it).
TIERS = {
    "openai": {   # /v1/chat/completions callers
        "fast":     "runtime/global.openai.gpt-5.6-luna",
        "balanced": "runtime/global.openai.gpt-5.6-terra",
        "coding":   "runtime/us.moonshotai.kimi-k3",
        "frontier": "runtime/global.openai.gpt-5.6-sol",
    },
    "anthropic": {  # /v1/messages callers
        "fast":     "runtime/us.anthropic.claude-sonnet-5",
        "balanced": "runtime/us.anthropic.claude-sonnet-5",
        "coding":   "runtime/us.anthropic.claude-sonnet-5",
        "frontier": "runtime/us.anthropic.claude-opus-4-8",
    },
}

# models whose OpenAI dialect requires max_completion_tokens instead of max_tokens
MCT_MODELS = ("gpt-5.6",)


def normalize_params(payload, model, cfg):
    mct = cfg.get("param_normalization", {}).get("max_completion_tokens_models", list(MCT_MODELS))
    if any(k in model for k in mct) and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    return payload


def api_family(payload):
    return "anthropic" if "anthropic_version" in payload else "openai"

_secrets = {}
def secret(name):
    if name not in _secrets:
        sm = boto3.client("secretsmanager")
        _secrets[name] = json.loads(sm.get_secret_value(SecretId=name)["SecretString"])
    return _secrets[name]

CONFIG_S3 = os.environ.get("ROUTER_CONFIG_S3", "")  # set by the stack
_cfg_cache = {"cfg": None, "ts": 0}
_CFG_TTL = 60

def _validate(cfg):
    tiers = cfg["tiers"]
    for fam in tiers.values():
        assert isinstance(fam, dict) and fam
    for t in cfg["task_base"].values():
        if t.startswith("_"): continue
        for fam in tiers.values():
            assert t in fam, f"task_base tier {t} missing from a family ladder"
    for src, dst in cfg["escalation"].items():
        if src.startswith("_"): continue
        for fam in tiers.values():
            assert dst in fam, f"escalation target {dst} missing"
    assert cfg["questions"]["task"]["criteria"]
    return cfg

def config():
    """Admin config from S3, hot-reloaded (TTL 60s). Invalid -> last-known-good."""
    now = time.time()
    if _cfg_cache["cfg"] and now - _cfg_cache["ts"] < _CFG_TTL:
        return _cfg_cache["cfg"]
    try:
        bucket, _, key = CONFIG_S3.replace("s3://", "").partition("/")
        raw = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        def _strip(x):
            if isinstance(x, dict):
                return {k: _strip(v) for k, v in x.items() if not str(k).startswith("_")}
            return x
        cfg = _validate(_strip(json.loads(raw)))
        _cfg_cache["cfg"], _cfg_cache["ts"] = cfg, now
    except Exception as e:  # noqa: BLE001
        logger.warning("config load failed (%s); using %s", str(e)[:120],
                       "last-known-good" if _cfg_cache["cfg"] else "baked-in defaults")
        if _cfg_cache["cfg"] is None:
            _cfg_cache["cfg"] = {"virtual_model": VIRTUAL_MODEL, "engine": ENGINE,
                "confidence_floor": CONF_FLOOR,
                "complexity_escalation_threshold": {"jev": 2.2, "bedrock": 2.6, "embedding": 2.6, "heuristic": 2.6},
                "tiers": TIERS, "task_base": TASK_BASE, "escalation": ESCALATE,
                "questions": QUESTIONS,
                "param_normalization": {"max_completion_tokens_models": list(MCT_MODELS)}}
        _cfg_cache["ts"] = now
    return _cfg_cache["cfg"]


_brt = None
def brt():
    global _brt
    if _brt is None:
        _brt = boto3.client("bedrock-runtime")
    return _brt


# ---------------- decision engines ----------------
# Task taxonomy grounded in enterprise LLM usage research (2026): content &
# translation, search/RAG, structured-data tasks, coding, agentic/reasoning.
QUESTIONS = {
    "task": {"type": "choice", "instructions": "What kind of task is this request?",
             "criteria": {
                 "simple_qa": "Simple factual question or short chat",
                 "classification": "Categorize, tag, label or detect sentiment/intent of given text",
                 "extraction": "Extract structured fields or data points from provided text",
                 "summarization": "Summarize, condense or produce key points of provided content",
                 "rewriting": "Edit, proofread, rephrase or change tone of provided text",
                 "translation": "Translate text between languages",
                 "customer_support": "Draft a helpful response to a customer inquiry or complaint",
                 "rag_qa": "Answer a question using provided documents/context/knowledge base",
                 "content_generation": "Create marketing copy, emails, posts, docs or other business content",
                 "analysis": "Multi-step analysis, comparison or synthesis",
                 "creative_writing": "Fiction, stories, poetry or other creative prose",
                 "code_simple": "Explain or comment code, small snippets, regex, formatting, simple SQL, mechanical transforms",
                 "coding": "Write or modify functions/features, tests, standard debugging, moderate SQL, IaC, code review",
                 "code_architecture": "System/schema design, tech tradeoffs, novel algorithms, performance/concurrency engineering, migration strategy",
                 "math": "Mathematical or quantitative problem solving",
                 "reasoning_planning": "Deep multi-step reasoning, strategy or complex planning",
                 "sysadmin_scripting": "Automation scripts: shell/bash/PowerShell, cron, log parsing, deploy/backup scripts",
                 "product_requirements": "Requirements, user stories with acceptance criteria, PRDs, epics, feature specs",
                 "agentic": "Orchestrating multi-step workflows, tools or autonomous task execution"}},
    "complexity": {"type": "score", "instructions": "How complex is this request for an AI assistant?",
                   "criteria": ["Trivial", "Easy", "Moderate", "Hard", "Frontier-level"]},
    "needs_reasoning": {"type": "noul", "instructions": "Answering well requires multi-step reasoning"},
}
# SCOPE (2026-09-19): routing only. Sensitivity/safety screening is Bedrock
# Guardrails' job (separate layer on this gateway) — not the router's.


def engine_heuristic(text):
    n = len(text)
    return {"task": "simple_qa" if n < 2000 else "analysis" if n < 8000 else "reasoning",
            "complexity": 0.5 if n < 2000 else 2.0 if n < 8000 else 3.5,
            "needs_reasoning": 0.0 if n < 8000 else 1.0,
            "confidence": 1.0, "engine": "heuristic"}


def engine_jev(text):
    """System One decision engine. Endpoint is configurable: OpenRouter
    (/api/alpha/decisions) or TypeSafe native (/v1/systemone) - same contract."""
    cfg = config()
    jc = cfg.get("decision_engines", {}).get("jev", {})
    endpoint = jc.get("endpoint") or os.environ.get("JEV_ENDPOINT", "https://openrouter.ai/api/alpha/decisions")
    model = jc.get("model") or os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
    secret_name = jc.get("secret_name") or os.environ.get("JEV_SECRET_NAME", "llm-router/jev-api-key")
    key = secret(secret_name)["apiKey"]
    body = {"model": model, "state": text[:32000], "questions": cfg["questions"]}
    req = urllib.request.Request(endpoint,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=float(jc.get("timeout_s", 3))) as r:
        a = json.loads(r.read())["answers"]
    return {"task": a["task"]["choice"], "complexity": a["complexity"]["score"],
            "needs_reasoning": a["needs_reasoning"]["noul"],
            "confidence": min(a["task"]["confidence"], a["complexity"]["confidence"]),
            "engine": "jev"}


def engine_bedrock(text):
    """Haiku 4.5 as an in-account System One stand-in (tool-forced JSON)."""
    tool = {"toolSpec": {"name": "decision", "description": "Report the routing decision.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "task": {"type": "string", "enum": list(config()["questions"]["task"]["criteria"])},
            "task_confidence": {"type": "number"},
            "complexity": {"type": "number", "description": "0=Trivial..4=Frontier-level"},
            "complexity_confidence": {"type": "number"},
            "needs_reasoning": {"type": "number"}},
            "required": ["task", "task_confidence", "complexity", "complexity_confidence",
                          "needs_reasoning"]}}}}
    classifier_model = config().get("decision_engines", {}).get("bedrock", {}).get(
        "model_id", os.environ.get("CLASSIFIER_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"))
    r = brt().converse(modelId=classifier_model,
        messages=[{"role": "user", "content": [{"text":
            "Classify this request for LLM routing. Honest confidences (0-1).\n\nREQUEST:\n" + text[:16000]}]}],
        toolConfig={"tools": [tool], "toolChoice": {"tool": {"name": "decision"}}},
        inferenceConfig={"maxTokens": 300})
    args = next(b["toolUse"]["input"] for b in r["output"]["message"]["content"] if "toolUse" in b)
    return {"task": args["task"], "complexity": float(args["complexity"]),
            "needs_reasoning": float(args["needs_reasoning"]),
            "confidence": min(float(args["task_confidence"]), float(args["complexity_confidence"])),
            "engine": "bedrock"}


_emb_cache = {"phrases": None}


def _titan_embed(texts):
    out = []
    for t in texts:
        r = brt().invoke_model(modelId="amazon.titan-embed-text-v2:0",
            body=json.dumps({"inputText": t[:8000]}))
        out.append(json.loads(r["body"].read())["embedding"])
    return out


def engine_embedding(text):
    """Bifrost-style semantic router: kNN over per-tier reference phrases
    (loaded from S3-free local bundle env REF_PHRASES_JSON or packaged file)."""
    if _emb_cache["phrases"] is None:
        path = os.environ.get("REF_PHRASES_PATH", os.path.join(os.path.dirname(__file__), "ref_phrases.json"))
        ref = json.load(open(path))  # {tier: [phrase, ...]}
        vecs = {}
        for tier, phrases in ref.items():
            vecs[tier] = list(zip(phrases, _titan_embed(phrases)))
        _emb_cache["phrases"] = vecs
    q = _titan_embed([text])[0]
    import math
    qn = math.sqrt(sum(x * x for x in q))
    best_tier, best_sim = None, -1.0
    for tier, items in _emb_cache["phrases"].items():
        for _, v in items:
            dot = sum(a * b for a, b in zip(q, v))
            sim = dot / (qn * math.sqrt(sum(x * x for x in v)) + 1e-9)
            if sim > best_sim:
                best_sim, best_tier = sim, tier
    # map matched tier to feature-shape so resolve_tier semantics stay uniform:
    tier_feats = {"fast": (0.5, 0.0, "simple_qa"), "balanced": (2.0, 0.6, "analysis"),
                  "coding": (2.0, 0.5, "coding"), "frontier": (3.5, 0.9, "reasoning")}
    cx, nr, task = tier_feats[best_tier]
    return {"task": task, "complexity": cx, "needs_reasoning": nr,
            "confidence": max(best_sim, 0.0), "engine": "embedding"}


ENGINES = {"heuristic": engine_heuristic, "jev": engine_jev, "bedrock": engine_bedrock,
           "embedding": engine_embedding}


# ---------------- policy ----------------
# thresholds tuned on the eval tune split 2026-09-19 (zero dangerous downgrades,
# 86.6% exact for both engines): balanced band gated on complexity alone —
# analysis tasks legitimately score high needs_reasoning, so it only gates fast.
C_LO = float(os.environ.get("COMPLEXITY_LO", "1.0"))
C_HI = float(os.environ.get("COMPLEXITY_HI", "2.6"))
NR_FAST = float(os.environ.get("NR_FAST", "0.5"))


# base tier per task (enterprise-taxonomy v3); complexity escalates one tier.
TASK_BASE = {
    "simple_qa": "fast", "classification": "fast", "extraction": "fast",
    "summarization": "fast", "rewriting": "fast", "translation": "fast",
    "customer_support": "balanced", "rag_qa": "balanced",
    "content_generation": "balanced", "analysis": "balanced",
    "creative_writing": "balanced",
    "code_simple": "fast", "coding": "coding", "code_architecture": "frontier",
    "sysadmin_scripting": "coding", "product_requirements": "balanced",
    "math": "frontier", "reasoning_planning": "frontier", "agentic": "frontier",
}
ESCALATE = {"fast": "balanced", "balanced": "frontier", "coding": "frontier",
            "code_simple": "coding", "frontier": "frontier"}


def resolve_tier(d, cfg, engine_name):
    """task-based base tier, escalated by complexity; fail-UP on uncertainty."""
    if d["confidence"] < float(cfg.get("confidence_floor", 0.5)):
        return "frontier", "low-confidence-escalation"
    base = cfg["task_base"].get(d["task"], "balanced")
    chi = float(cfg.get("complexity_escalation_threshold", {}).get(engine_name, 2.6))
    if d["complexity"] >= chi:
        return cfg["escalation"].get(base, "frontier"), "complexity-escalation"
    return base, "task-base"


def extract_text(payload):
    msgs = payload.get("messages") or []
    parts = []
    for m in msgs:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts += [b.get("text", "") for b in c if isinstance(b, dict)]
    if not parts and payload.get("input"):
        parts.append(str(payload["input"]))
    return "\n".join(parts)


def log_decision(rec):
    try:
        boto3.resource("dynamodb").Table(os.environ.get("DECISIONS_TABLE", "llm-router-decisions")) \
            .put_item(Item={**{k: (str(v) if isinstance(v, float) else v) for k, v in rec.items()},
                            "ttl": int(time.time()) + 86400})
    except Exception as e:  # noqa: BLE001
        logger.warning("decision log failed: %s", e)
    # EMF metric line
    print(json.dumps({"_aws": {"Timestamp": int(time.time()*1000), "CloudWatchMetrics": [{
        "Namespace": "LLMRouter", "Dimensions": [["engine", "tier"]],
        "Metrics": [{"Name": "overhead_ms"}, {"Name": "decisions"}]}]},
        "engine": rec["engine"], "tier": rec["tier"],
        "overhead_ms": rec["overhead_ms"], "decisions": 1}))


def lambda_handler(event, context):
    http = event.get("http", {})
    encoded = (http.get("gatewayRequest") or {}).get("body")
    if not encoded:
        return {"interceptorOutputVersion": "1.0", "http": {}}
    try:
        payload = json.loads(base64.b64decode(encoded))
    except Exception:  # noqa: BLE001
        return {"interceptorOutputVersion": "1.0", "http": {}}
    cfg = config()
    if not isinstance(payload, dict) or payload.get("model") != cfg.get("virtual_model", VIRTUAL_MODEL):
        return {"interceptorOutputVersion": "1.0", "http": {}}  # concrete ids pass through

    t0 = time.monotonic()
    text = extract_text(payload)
    family = api_family(payload)
    engine_name = os.environ.get("DECISION_ENGINE_OVERRIDE") or cfg.get("engine", ENGINE)
    tiers = cfg["tiers"].get(family) or list(cfg["tiers"].values())[0]
    try:
        d = ENGINES[engine_name](text)
    except Exception as e:  # noqa: BLE001
        logger.warning("engine %s failed (%s) - fail-open to default", engine_name, str(e)[:150])
        d = {"task": "?", "complexity": -1, "needs_reasoning": -1,
             "confidence": 0.0, "engine": f"{engine_name}-FAILED"}
        tier, reason = "frontier", "fail-open"
        model = tiers["frontier"]
    else:
        tier, reason = resolve_tier(d, cfg, engine_name)
        model = tiers[tier]
    overhead = int((time.monotonic() - t0) * 1000)

    payload["model"] = model
    payload = normalize_params(payload, model, cfg)
    log_decision({"pk": f"{int(time.time()*1000)}-{context.aws_request_id[:8]}",
                  "engine": d["engine"], "task": d["task"], "complexity": d["complexity"],
                  "needs_reasoning": d["needs_reasoning"],
                  "confidence": d["confidence"], "tier": tier, "reason": reason,
                  "model": model, "overhead_ms": overhead, "chars": len(text)})
    logger.info("routed auto -> %s (%s, %s, conf=%.2f, %dms)", model, tier, reason, d["confidence"], overhead)
    return {"interceptorOutputVersion": "1.0",
            "http": {"transformedGatewayRequest": {
                "body": base64.b64encode(json.dumps(payload).encode()).decode()}}}
