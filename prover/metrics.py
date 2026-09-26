"""Metric objects, statistics, SM staircase detection and the CPU-mode simulation. Pure Python (no CUDA).

Shape and methods: docs/INTERFACES.md "Performance profile" and docs/METRICS.md.
"""
import random
import statistics

from core.specs import MODELS, host_link

METHODS = {
    "int8_tops": ("TOPS", "torch._int_mm INT8 8192x8192, CUDA events, L2 flushed, warm-up 5, median of 20"),
    "bf16_tflops": ("TFLOPS", "torch.matmul BF16 8192x8192, CUDA events, L2 flushed, warm-up 5, median of 20"),
    "fp8_tflops": ("TFLOPS", "torch._scaled_mm e4m3 -> bf16 8192x8192, CUDA events, L2 flushed, warm-up 5, median of 20"),
    "hbm_copy_tbs": ("TB/s", "device copy of a 4 GiB buffer, (read+write bytes)/time, CUDA events, L2 flushed, "
                             "warm-up 3, median of 20"),
    "hbm_read_tbs": ("TB/s", "int32 sum over a 4 GiB buffer, bytes/time, CUDA events, L2 flushed, warm-up 3, median of 20"),
    "h2d_gbs": ("GB/s", "pinned 1 GiB host->device copy, CUDA events, warm-up 2, median of 10"),
    "d2h_gbs": ("GB/s", "pinned 1 GiB device->host copy, CUDA events, warm-up 2, median of 10"),
    "launch_us": ("us", "empty kernel launch + stream sync, host clock round trip, warm-up 50, median of 1000"),
    "mem_alloc_gib": ("GiB", "1 GiB then 64 MiB chunks until allocation fails, per-chunk pattern written and read back"),
    "sm_count": ("SMs", "spin-kernel staircase 32..256 blocks, one block per SM, median-filtered first sustained 1.5x step"),
    "stability_cv": ("%", "coefficient of variation of per-second BF16 TFLOPS over the health burn"),
}
API_KEYS = ("spec", "pct_of_spec", "spec_source", "expected_pct", "flag")  # filled by api/perf.annotate


def sig(x, digits=5):
    return None if x is None else float(f"{x:.{digits}g}")


def summary(xs):
    """n, median, p10, p90 and CV (percent) of a sample list."""
    xs = [float(x) for x in xs]
    if not xs:
        return {"n": 0, "median": None, "p10": None, "p90": None, "cv": None}
    med = statistics.median(xs)
    if len(xs) == 1:
        return {"n": 1, "median": sig(med), "p10": sig(med), "p90": sig(med), "cv": 0.0}
    q = statistics.quantiles(xs, n=10, method="inclusive")
    mean = statistics.fmean(xs)
    return {"n": len(xs), "median": sig(med), "p10": sig(q[0]), "p90": sig(q[-1]),
            "cv": sig(100 * statistics.pstdev(xs) / mean, 3) if mean else None}


def metric(key, samples, trust="measured", **extra):
    unit, method = METHODS[key]
    s = summary(samples)
    return {"value": s["median"], "unit": unit, "method": method, "trust": trust, **s,
            **dict.fromkeys(API_KEYS), **extra}


def missing(key, **extra):
    """A metric that has no value (unsupported, skipped, or errored). extra says which."""
    return metric(key, []) | extra


def sm_from_staircase(times):
    """SM count from {blocks: seconds}: median-filter (window 5), baseline = first 8 points, SMs = k - 1 where k
    starts the first run of 3 points above 1.5x baseline. None if no step is found."""
    ks = sorted(times)
    t = [times[k] for k in ks]
    f = [statistics.median(t[max(0, i - 2):i + 3]) for i in range(len(t))]
    if len(f) < 11:
        return None
    base = statistics.median(f[:8])
    return next((ks[i] - 1 for i in range(8, len(f) - 2) if all(x > 1.5 * base for x in f[i:i + 3])), None)


def sm_metric(stair, **extra):
    sms = sm_from_staircase(stair)
    return metric("sm_count", [sms] if sms else [], swept=[min(stair), max(stair)], **extra)


def stability_metric(per_second, **extra):
    if len(per_second or []) < 2:
        return missing("stability_cv", note="no burn")
    cv = summary(per_second)["cv"]
    return metric("stability_cv", [cv], seconds=len(per_second), **extra)


# ---- CPU mode ------------------------------------------------------------------------------------------------
EFF = {"int8_tops": 0.72, "bf16_tflops": 0.74, "fp8_tflops": 0.68, "hbm_copy_tbs": 0.86, "hbm_read_tbs": 0.90,
       "h2d_gbs": 0.83, "d2h_gbs": 0.82}  # mid-points of the docs/METRICS.md heuristic ranges


def sim_staircase(sms, lo=32, hi=256):
    """Synthetic staircase in seconds: ceil(k / sms) waves of ~2 ms plus deterministic jitter."""
    return {k: 0.002 * -(-k // sms) + 0.00002 * ((k * 37) % 7) for k in range(lo, hi + 1)}


def simulate(model_id, per_second=None, seed=7):
    """A plausible full metric set for a model: spec x typical efficiency, a few percent of per-GPU offset and
    ~0.6 % trial noise. Every metric is marked simulated."""
    m, rnd = MODELS[model_id], random.Random(f"{model_id}-{seed}")
    d = m["dense"]
    spec = {"int8_tops": d.get("int8_tops"), "bf16_tflops": d.get("bf16_tflops") or d.get("fp16_tflops"),
            "fp8_tflops": d.get("fp8_tflops"), "hbm_copy_tbs": m["bw_tbs"], "hbm_read_tbs": m["bw_tbs"],
            "h2d_gbs": host_link(model_id)[0], "d2h_gbs": host_link(model_id)[0]}
    trials = {"hbm_copy_tbs": 20, "hbm_read_tbs": 20, "h2d_gbs": 10, "d2h_gbs": 10}
    out = {}
    for key, s in spec.items():
        if s is None:
            out[key] = missing(key, supported=False) if key == "fp8_tflops" else missing(key, note="no spec")
            continue
        mid = s * EFF[key] * rnd.uniform(0.94, 1.04)  # per-card spread, as on real fleets
        out[key] = metric(key, [mid * rnd.gauss(1, 0.006) for _ in range(trials.get(key, 20))])
    out["launch_us"] = metric("launch_us", [rnd.gauss(5.5, 0.4) for _ in range(1000)])
    out["mem_alloc_gib"] = metric("mem_alloc_gib", [round(m["mem_gb"] * 0.985, 2)], chunks=m["mem_gb"], bad_chunks=0)
    out["sm_count"] = sm_metric(sim_staircase(m["sms"]))
    if per_second:
        out["stability_cv"] = stability_metric(per_second)
    for v in out.values():
        v |= {"simulated": True, "method": "Simulated (CPU mode): " + v["method"]}
    return out
