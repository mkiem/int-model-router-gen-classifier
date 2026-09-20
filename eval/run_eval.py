"""Phase 4 eval runner. Usage: python3.11 eval/run_eval.py <engine|ALL> <tune|holdout> [conc]"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = "/home/ec2-user/projects/agentcoreTest/llm-intelligent-routing"
sys.path.insert(0, f"{ROOT}/interceptor")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
import router  # production code under test

ORDER = {"fast": 0, "coding": 1, "balanced": 1, "frontier": 2}
# ladder prices $/M (in, out) for blended-cost projection (openai family ladder)
PRICE = {"fast": (0.20, 1.20), "balanced": (2, 12), "coding": (0.60, 2.50), "frontier": (4, 20)}
ASSUMED_IN, ASSUMED_OUT = 300, 500  # tokens per request for cost projection


def load(split):
    return [r for l in open(f"{ROOT}/eval/dataset_v4.jsonl") for r in [json.loads(l)] if r["split"] == split]


def run_engine(engine, rows, conc=8):
    fn = router.ENGINES[engine]
    cfg = router.config()
    results = {}
    def one(r):
        t0 = time.monotonic()
        try:
            d = fn(r["prompt"])
            tier, reason = router.resolve_tier(d, cfg, engine)
            return r["id"], {"tier": tier, "reason": reason, "conf": d["confidence"],
                             "lat_ms": int((time.monotonic() - t0) * 1000), "err": None}
        except Exception as e:  # noqa: BLE001
            return r["id"], {"tier": "frontier", "reason": "fail-open", "conf": 0,
                             "lat_ms": int((time.monotonic() - t0) * 1000), "err": str(e)[:100]}
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for fut in as_completed([ex.submit(one, r) for r in rows]):
            rid, res = fut.result()
            results[rid] = res
    return results


def score(rows, results):
    exact = acc = dangerous = overshoot = errs = 0
    lat = []
    cost_routed = cost_frontier = 0.0
    by_tier = {}
    for r in rows:
        res = results[r["id"]]
        if res["err"]:
            errs += 1
        p, e = res["tier"], r["expected_tier"]
        lat.append(res["lat_ms"])
        exact += (p == e)
        up_ok = ORDER[p] >= ORDER[e] and ORDER[p] - ORDER[e] <= 1
        acc += (p == e) or up_ok
        if e == "frontier" and p == "fast":
            dangerous += 1
        if ORDER[p] > ORDER[e]:
            overshoot += 1
        pi, po = PRICE[p]
        cost_routed += (ASSUMED_IN * pi + ASSUMED_OUT * po) / 1e6
        fi, fo = PRICE["frontier"]
        cost_frontier += (ASSUMED_IN * fi + ASSUMED_OUT * fo) / 1e6
        bt = by_tier.setdefault(e, {"n": 0, "exact": 0})
        bt["n"] += 1
        bt["exact"] += (p == e)
    n = len(rows)
    lat.sort()
    return {"n": n, "exact": round(exact / n, 4), "acceptable": round(acc / n, 4),
            "dangerous": dangerous, "overshoot_rate": round(overshoot / n, 4),
            "errors": errs, "lat_p50": lat[n // 2], "lat_p99": lat[min(int(n * .99), n - 1)],
            "savings_vs_frontier": round(1 - cost_routed / cost_frontier, 4),
            "by_tier": {k: round(v["exact"] / v["n"], 3) for k, v in by_tier.items()}}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "ALL"
    split = sys.argv[2] if len(sys.argv) > 2 else "tune"
    rows = load(split)
    engines = list(router.ENGINES) if which == "ALL" else [which]
    out = {}
    for e in engines:
        res = run_engine(e, rows)
        json.dump(res, open(f"{ROOT}/artifacts/eval_{e}_{split}.json", "w"))
        m = score(rows, res)
        out[e] = m
        print(f"[{e:9s}|{split}] exact={m['exact']:.3f} acceptable={m['acceptable']:.3f} "
              f"dangerous={m['dangerous']} overshoot={m['overshoot_rate']:.3f} "
              f"savings={m['savings_vs_frontier']:.1%} lat_p50={m['lat_p50']}ms errs={m['errors']}")
        print(f"           by expected tier: {m['by_tier']}")
    json.dump(out, open(f"{ROOT}/artifacts/metrics_{split}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
