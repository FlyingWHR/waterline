"""Which GPU model is this? Nearest spec in core/gpu_specs.json, from measured features only (docs/METRICS.md).

Never reads the driver's name, SM count or memory size. Confusable models that the measurements can't tell
apart are reported together in `ambiguous_with` instead of guessed.
"""
import math

from core.specs import MODELS, host_link, partners

EFF = {"mem": 0.97, "bw": 0.86, "int8": 0.72, "h2d": 0.83}  # expected measured / spec (calibration knobs)
SM_TOL = 2.0                                     # SMs per unit of distance
LOG_TOL = {"mem": math.log(1.06), "bw": math.log(1.12), "int8": math.log(1.30), "ratio": math.log(1.25),
           "h2d": math.log(2.5)}  # host link: wide, so a Gen5 card in a Gen4 slot stays close; C2C vs PCIe is ~7x
FP8_MISMATCH = 5.0
FP8_REAL = 1.3   # FP8 counts as supported only at >= 1.3x the measured BF16 rate (tensor cores run 2x)
MARGIN = 1.0     # candidates within this distance of the best are ambiguous with it
POOR_FIT = 3.0


def _val(metrics, key):
    v = (metrics or {}).get(key)
    v = v.get("value") if isinstance(v, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def features(probes=None, metrics=None) -> dict:
    """Measured features from the commit probes and the profiler's metrics (metrics win where both exist)."""
    probes = probes or {}
    sms = _val(metrics, "sm_count") or (probes.get("sms") or None)
    fp8m, bf16, int8 = _val(metrics, "fp8_tflops"), _val(metrics, "bf16_tflops"), _val(metrics, "int8_tops")
    fp8_src = (metrics or {}).get("fp8_tflops")
    if isinstance(fp8_src, dict) and (fp8m or fp8_src.get("supported") is False):
        fp8 = bool(fp8m) and (bf16 is None or fp8m >= FP8_REAL * bf16)
    else:
        fp8 = probes.get("fp8") if isinstance(probes.get("fp8"), bool) else None
    bw = _val(metrics, "hbm_copy_tbs") or (probes.get("bw_tbs") or None)
    return {"sms": sms, "fp8": fp8, "mem_gib": _val(metrics, "mem_alloc_gib"), "bw_tbs": bw, "int8_tops": int8,
            "fp8_int8": fp8m / int8 if fp8m and int8 else None, "h2d_gbs": _val(metrics, "h2d_gbs")}


def expected(model_id) -> dict:
    m = MODELS[model_id]
    i8, f8 = m["dense"].get("int8_tops"), m["dense"].get("fp8_tflops")
    return {"sms": m["sms"], "fp8": bool(m["fp8"]), "mem_gib": EFF["mem"] * m["mem_gb"], "bw_tbs": EFF["bw"] * m["bw_tbs"],
            "int8_tops": EFF["int8"] * i8 if i8 else None, "fp8_int8": f8 / i8 if f8 and i8 else None,
            "h2d_gbs": EFF["h2d"] * host_link(model_id)[0] if host_link(model_id)[0] else None}


def _terms(f, e):
    """(z, phrase) per feature measured on both sides."""
    out = []
    if f["sms"] and e["sms"]:
        out.append(((f["sms"] - e["sms"]) / SM_TOL, f"{f['sms']:.0f} SMs (expected {e['sms']})"))
    if f["fp8"] is not None:
        yn = lambda b: "FP8" if b else "no FP8"  # noqa: E731
        out.append((0.0 if f["fp8"] == e["fp8"] else FP8_MISMATCH, f"{yn(f['fp8'])} (expected {yn(e['fp8'])})"))
    for k, tol, fmt in (("mem_gib", "mem", "{:.1f} GiB usable (expected about {:.0f})"),
                        ("bw_tbs", "bw", "{:.2f} TB/s copy (expected about {:.2f})"),
                        ("int8_tops", "int8", "{:.0f} INT8 TOPS (expected about {:.0f})"),
                        ("fp8_int8", "ratio", "FP8/INT8 ratio {:.2f} (expected {:.2f})"),
                        ("h2d_gbs", "h2d", "{:.0f} GB/s host-to-device (expected about {:.0f})")):
        if f[k] and e[k]:
            out.append((math.log(f[k] / e[k]) / LOG_TOL[tol], fmt.format(f[k], e[k])))
    return out


def _why(terms):
    ok = [p for z, p in terms if abs(z) <= 1]
    bad = [p for z, p in terms if abs(z) > 1]
    return "; ".join(filter(None, ["matches " + ", ".join(ok) if ok else "", "differs: " + ", ".join(bad) if bad else ""]))


def match(f: dict, claimed=None, top=5) -> dict:
    """Rank every model by distance to the features. `claimed`: a model id or a list of acceptable ids."""
    claimed_ids = [claimed] if isinstance(claimed, str) else list(claimed or [])
    ranked = []
    for mid in MODELS:
        t = _terms(f, expected(mid))
        ranked.append((math.sqrt(sum(z * z for z, _ in t)), mid, t))
    ranked.sort(key=lambda r: r[0])
    if not f.get("sms"):  # the anchor: without it too many models tie, and a tie must not pass as a match
        return {"best_match": None, "name": None, "candidates": [], "ambiguous_with": [], "claimed": claimed,
                "consistent": False, "fit": None, "features": f,
                "why": "The SM count could not be measured, so the model is unknown."}
    best_d, best, best_t = ranked[0]
    amb = [mid for d, mid, _ in ranked[1:] if d <= best_d + MARGIN]
    cands = [{"id": mid, "name": MODELS[mid]["name"], "distance": round(d, 3), "why": _why(t),
              "declared_confusable": mid in partners(best)} for d, mid, t in ranked[:max(top, len(amb) + 1)]]
    return {"best_match": best, "name": MODELS[best]["name"], "candidates": cands, "ambiguous_with": amb,
            "claimed": claimed, "consistent": bool(set(claimed_ids) & {best, *amb}),
            "fit": "good" if best_d <= POOR_FIT else "poor", "features": f, "why": _why(best_t)}


def classification(probes=None, metrics=None, claimed=None) -> dict:
    """features() + match(): the report's `classification` object."""
    return match(features(probes, metrics), claimed)


def evidence(f: dict) -> str:
    """Short plain-words list of what was measured, e.g. '108 SMs, no FP8, 39.4 GiB, 1.34 TB/s'."""
    parts = [f"{f['sms']:.0f} SMs" if f.get("sms") else None,
             None if f.get("fp8") is None else ("FP8" if f["fp8"] else "no FP8"),
             f"{f['mem_gib']:.1f} GiB" if f.get("mem_gib") else None,
             f"{f['bw_tbs']:.2f} TB/s" if f.get("bw_tbs") else None,
             f"{f['int8_tops']:.0f} INT8 TOPS" if f.get("int8_tops") else None]
    return ", ".join(p for p in parts if p)


def models_table() -> list[dict]:
    """Every model: id, name, sms, fp8, mem_gb, bw_tbs and dense int8/bf16/fp8 ratings."""
    return [{"id": mid, "name": m["name"], "sms": m["sms"], "fp8": m["fp8"], "mem_gb": m["mem_gb"],
             "bw_tbs": m["bw_tbs"], "int8_tops": m["dense"].get("int8_tops"),
             "bf16_tflops": m["dense"].get("bf16_tflops"), "fp8_tflops": m["dense"].get("fp8_tflops")}
            for mid, m in MODELS.items()]
