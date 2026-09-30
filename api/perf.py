"""Performance profile on the API side (docs/METRICS.md): spec, %-of-spec, expected ranges and flags per metric;
the verified INT8 metric; cohort percentiles; leaderboard; compare. Pure functions, no I/O.

Reports passed to cohort/compare/leaderboard are the stored report dicts: `metrics` (annotated), `cloud`,
`verdict`, `classification.best_match` (falls back to `claimed_model`).
"""
import statistics

from core.specs import MODELS, host_link

# metric -> (spec field, trust). Trust is re-stamped here: the pod can't upgrade its own grade.
SPEC = {"int8_tops_verified": ("int8_tops", "verified"), "int8_tops": ("int8_tops", "measured"),
        "bf16_tflops": ("bf16_tflops", "measured"), "fp8_tflops": ("fp8_tflops", "measured"),
        "hbm_copy_tbs": ("bw_tbs", "measured"), "hbm_read_tbs": ("bw_tbs", "measured"),
        "h2d_gbs": ("host_link", "measured"), "d2h_gbs": ("host_link", "measured"),
        "launch_us": (None, "measured"), "mem_alloc_gib": ("mem_gb", "measured"),
        "sm_count": ("sms", "measured"), "stability_cv": (None, "measured")}
# Expected % of spec: heuristics from common practice, NOT calibrated on our pods (docs/METRICS.md).
EXPECTED = {"int8_tops": [60, 85], "bf16_tflops": [60, 85], "fp8_tflops": [55, 85], "hbm_copy_tbs": [80, 92],
            "hbm_read_tbs": [85, 95], "h2d_gbs": [75, 90], "d2h_gbs": [75, 90], "mem_alloc_gib": [90, 101],
            "sm_count": [100, 100]}
C2C_EXPECTED = [30, 90]         # NVLink-C2C host copies: little field data
CONSUMER_HI = 105               # GeForce / workstation boost above the spec clock; their tensor specs are derived
UNSTABLE_CV = 5.0               # % trial-to-trial CV above which a metric is flagged unstable
STABILITY_MAX = 3.0             # % burn CV above which stability_cv is flagged unstable
LOWER_IS_BETTER = {"launch_us", "stability_cv"}
COHORT_MIN = 5


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _sig(x, d=4):
    return None if x is None else float(f"{x:.{d}g}")


def spec_for(key, model_id):
    """(spec value, plain-words source) for a metric on a model, or (None, None)."""
    field = SPEC.get(key, (None,))[0]
    m = MODELS.get(model_id)
    if not field or not m:
        return None, None
    if field == "host_link":
        v, label = host_link(model_id)
        return v, label and f"{m['name']}: {label}, per direction (core/gpu_specs.json interconnect)"
    d = m["dense"]
    if field in d:
        v, name = d[field], f"dense.{field}"
        if v is None and field == "bf16_tflops" and d.get("fp16_tflops"):
            v, name = d["fp16_tflops"], "dense.fp16_tflops (no BF16 on this part)"
    else:
        v, name = m.get(field), field
    if v is None:
        return None, None
    src = f"{m['name']}: {name} from {(m.get('sources') or ['core/gpu_specs.json'])[0]}"
    if field in d and m.get("derived_from_sparse"):
        src += " (sparse figure halved)"
    if any(u in (field, f"dense.{field}") for u in m.get("unverified") or []):
        src += " (unverified)"
    return v, src


def expected_for(key, model_id):
    m = MODELS.get(model_id)
    if key not in EXPECTED or not m:
        return None
    if key in ("h2d_gbs", "d2h_gbs") and "C2C" in (m.get("interconnect") or ""):
        return C2C_EXPECTED
    lo, hi = EXPECTED[key]
    form = (m.get("form") or "").lower()
    if key in ("int8_tops", "bf16_tflops", "fp8_tflops") and ("consumer" in form or "workstation" in form):
        hi = CONSUMER_HI
    return [lo, hi]


def annotate_one(key, m, model_id):
    """A clean copy of one metric with spec, pct_of_spec, spec_source, expected_pct and flag set."""
    m = dict(m)
    value = _num(m.get("value"))
    spec, src = spec_for(key, model_id)
    pct = _sig(100 * value / spec) if value is not None and spec else None
    exp = expected_for(key, model_id)
    cv = _num(m.get("cv"))
    flag = None
    if value is not None:
        # run-to-run spread is only meaningful for throughput; launch latency and one-shot readings jitter by nature
        noisy_ok = key in ("launch_us", "mem_alloc_gib", "sm_count")
        if (key == "stability_cv" and value > STABILITY_MAX) or (cv is not None and cv > UNSTABLE_CV and not noisy_ok):
            flag = "unstable"
        elif pct is not None and exp and pct < exp[0]:
            flag = "low"
        elif pct is not None and exp and pct > exp[1]:
            flag = "high"
    return m | {"value": value, "trust": SPEC[key][1], "spec": spec, "pct_of_spec": pct, "spec_source": src,
                "expected_pct": exp, "flag": flag}


def annotate(metrics: dict | None, model_id: str | None) -> dict:
    """Annotate the pod's metrics against a model: the CLAIMED one (what was paid for; for class 3, any A100 id).
    Unknown keys and any `int8_tops_verified` from the pod are dropped: only the API makes verified metrics."""
    return {k: annotate_one(k, v, model_id) for k, v in (metrics or {}).items()
            if k in SPEC and k != "int8_tops_verified" and isinstance(v, dict)}


def verified_int8(n: int, steps: int, elapsed_s: float, model_id: str | None) -> dict:
    """int8_tops_verified: the challenge's 2 n^3 steps INT8 ops over the API's start->commit seconds."""
    v = _sig(2 * n**3 * steps / elapsed_s / 1e12, 5) if elapsed_s and elapsed_s > 0 else None
    m = {"value": v, "unit": "TOPS", "trust": "verified", "n": 1 if v else 0, "median": v, "p10": v, "p90": v,
         "cv": 0.0 if v else None, "ops_total": 2 * n**3 * steps, "elapsed_s": elapsed_s,
         "method": "2*n^3*steps INT8 ops of the graded challenge / API-clock seconds from start to commit "
                   "(lower bound: includes generation, hashing and network)"}
    return annotate_one("int8_tops_verified", m, model_id)


def percentile_rank(value, past) -> float | None:
    """Percent of past values below `value` (ties count half). None when fewer than 5 past values."""
    xs = [x for x in map(_num, past or []) if x is not None]
    if value is None or len(xs) < COHORT_MIN:
        return None
    below = sum(x < value for x in xs) + 0.5 * sum(x == value for x in xs)
    return round(100 * below / len(xs), 1)


def _model(r):
    return ((r.get("classification") or {}).get("best_match")) or r.get("claimed_model")


def _value(r, key):
    return _num(((r.get("metrics") or {}).get(key) or {}).get("value"))


def cohort(report: dict, past: list[dict]) -> dict:
    """Percentile of each metric among past reports with the same best match. 'Better than X %': flipped for
    metrics where lower is better. Needs at least 5 past reports."""
    mid = _model(report)
    same = [r for r in past if _model(r) == mid and r.get("report_id") != report.get("report_id")]
    if len(same) < COHORT_MIN:
        return {"n": len(same), "note": "not enough checks yet"}
    pct = {}
    for key in (report.get("metrics") or {}):
        if key == "sm_count":
            continue
        p = percentile_rank(_value(report, key), [_value(r, key) for r in same])
        if p is not None:
            pct[key] = round(100 - p, 1) if key in LOWER_IS_BETTER else p
    return {"model_id": mid, "n": len(same), "percentiles": pct}


def compare(report: dict, past: list[dict]) -> dict:
    """Vs spec, spec values of the relevant models, cohort percentiles. (app.compare serves the route.)"""
    metrics = report.get("metrics") or {}
    cls = report.get("classification") or {}
    ids = [cls.get("best_match"), report.get("claimed_model"), *cls.get("ambiguous_with", []),
           *(c["id"] for c in cls.get("candidates", []))]
    ids = [i for i in dict.fromkeys(ids) if i in MODELS][:5]
    return {"vs_spec": {k: m.get("pct_of_spec") for k, m in metrics.items()},
            "vs_models": [{"id": i, "name": MODELS[i]["name"],
                           **{k: spec_for(k, i)[0] for k in SPEC if SPEC[k][0] and k != "int8_tops_verified"}}
                          for i in ids],
            "cohort": cohort(report, past)}


def leaderboard(reports: list[dict], model: str | None = None) -> list[dict]:
    """Per (model, cloud): n, median verified INT8 TOPS, median pct of spec, pass rate. Best first.
    (app.leaderboard serves the route.)"""
    groups = {}
    for r in reports:
        mid = _model(r)
        if mid and (model is None or mid == model):
            groups.setdefault((mid, r.get("cloud")), []).append(r)
    out = []
    for (mid, cloud), rs in groups.items():
        tops = [x for x in (_value(r, "int8_tops_verified") for r in rs) if x is not None]
        pct = [x for x in (_num(((r.get("metrics") or {}).get("int8_tops_verified") or {}).get("pct_of_spec"))
                           for r in rs) if x is not None]
        out.append({"model": mid, "name": MODELS[mid]["name"] if mid in MODELS else mid, "cloud": cloud, "n": len(rs),
                    "median_tops_verified": _sig(statistics.median(tops), 5) if tops else None,
                    "median_pct_of_spec": _sig(statistics.median(pct)) if pct else None,
                    "pass_rate": round(sum(r.get("verdict") == "pass" for r in rs) / len(rs), 3)})
    return sorted(out, key=lambda x: -(x["median_tops_verified"] or 0))


# ---- delivery: what the machine around the GPU holds back ----------------------------------------------------------
# Thresholds are ours, applied API-side so the pod can't grade itself. Findings are advisory: they come from the
# machine's own report and never change the verdict (the exam decides that).
CPUS_PER_GPU = 8          # below this, data loading and preprocessing commonly starve a GPU
DISK_MIN_MBS = 200        # checkpoint and dataset loads crawl below this
NET_MIN_MBS = 25          # ~200 Mbit/s: a 70 GB model download takes over 45 minutes
SUSTAIN_DROP_PCT = 10     # first vs last window of a long burn
SLOWING = ("power cap", "HW slowdown", "SW thermal", "HW thermal", "HW power brake")


def delivery(health: dict | None) -> list[dict]:
    """What the machine around the GPU holds back (CPU, RAM, disk, network) and how much a long burn sagged:
    [{kind, text}]. GPU-side health (PCIe, ECC, MIG, throttle reasons) is flagged by the panel from the same report."""
    hr = health or {}
    host, burn, dev = hr.get("host") or {}, hr.get("burn") or {}, hr.get("device") or {}
    out = []
    fmt = lambda x: "?" if x is None else f"{x:g}"  # noqa: E731

    def add(kind, text):
        out.append({"kind": kind, "text": text})

    cpu, gpus = host.get("cpu") or {}, max(1, _num(host.get("gpus")) or 1)
    usable = _num(cpu.get("usable"))
    if usable is not None and usable / gpus < CPUS_PER_GPU:
        cap = f" (a {cpu['quota']:g}-core quota on a {cpu.get('visible')}-core host)" if cpu.get("quota") else ""
        add("cpu", f"{usable:g} CPU cores for {gpus:g} GPU{'s' if gpus > 1 else ''}{cap}: data loading can starve it.")
    ram, vram = _num(host.get("memory_gib")), _num(dev.get("memory_gib"))
    if ram is not None and vram and ram < vram * gpus:
        add("memory", f"{ram:g} GiB of RAM for {vram * gpus:g} GiB of GPU memory: large checkpoints won't fit in host memory.")
    d = host.get("disk") or {}
    w, r = _num(d.get("write_mbs")), _num(d.get("read_mbs"))
    if (w is not None and w < DISK_MIN_MBS) or (r is not None and r < DISK_MIN_MBS):
        add("disk", f"Disk at {fmt(w)} MB/s write, {fmt(r)} MB/s read in {d.get('path', 'the work directory')}: loading data and checkpoints will be slow.")
    net = _num(host.get("download_mbs"))
    if net is not None and net < NET_MIN_MBS:
        add("network", f"Downloads at {net:g} MB/s: pulling a 70 GB model takes {70e3 / net / 60:.0f} minutes.")
    slowed = [x for x in burn.get("reasons_seen") or [] if x in SLOWING]
    s = burn.get("sustained") or {}
    drop = _num(s.get("drop_pct"))
    if drop is not None and drop >= SUSTAIN_DROP_PCT:
        why = f", held back by {' and '.join(slowed)}" if slowed else ""
        add("sustained", f"Throughput fell {drop:g}% over a {burn.get('seconds', 0) / 60:.0f}-minute burn "
                         f"({s['first_tflops']:g} → {s['last_tflops']:g} TFLOPS){why}.")
    return out
