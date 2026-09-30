"""Calibrate the exam on this pod: seconds per INT8 step at n=16384 -> steps, deadline and the env to set.

  python3 -m prover.calibrate                  # on the pod: GPU self-check, then timing
  python3 -m prover.calibrate --cpu            # no GPU: a clearly labelled SIMULATED estimate from the datasheet

Picks the steps at which this GPU's answer lands at ~70% of the deadline for its own model (the API's formula,
docs/METRICS.md "Deadline"), and says what an A100 would need for the same work (datasheet INT8 ratio).
"""
import argparse
import statistics
import sys
import time

from core.challenge import Params, leaf_hash
from core.specs import MODELS
from prover.run import MODEL_BY_SMS

N, TARGET, SIM_EFF = 16384, 0.70, 0.6
# Same formula and constants as api/check.deadline_s (the pod has no api/; tests keep the two equal).
BASE_S, MIN_EFF, MIN_S = 3.0, 0.25, 5.0
CLASS = {"h100-sxm": 1, "h100-pcie": 2, "a100-sxm-80": 3, "a100-sxm-40": 3, "a100-pcie-80": 3, "a100-pcie-40": 3}
A100 = "a100-sxm-80"


def tops(model):
    return MODELS[model]["dense"]["int8_tops"]


def deadline(model, n, steps):
    return round(max(MIN_S, BASE_S + 2 * n**3 * steps / (tops(model) * 1e12 * MIN_EFF)), 1)


def recommend(t, model, n=N, net=1.0):
    """(steps, deadline_s, own_s): steps where net + steps*t ~ TARGET * deadline(steps). The deadline is
    max(MIN_S, BASE_S + steps*k): try the floor, then the sloped part; if the formula is so generous this GPU can't
    reach TARGET (fast chips: the formula assumes a quarter of peak), keep the floor's steps and give the explicit
    deadline own / TARGET instead."""
    k = 2 * n**3 / (tops(model) * 1e12 * MIN_EFF)
    s = int((TARGET * MIN_S - net) / t)
    if s < 1 or BASE_S + s * k > MIN_S:
        slope = t - TARGET * k
        s_lin = int((TARGET * BASE_S - net) / slope) if slope > 0 else 0
        s = s_lin if s_lin >= 1 else max(1, s)
    own, d = net + s * t, deadline(model, n, s)
    return s, (d if abs(own / d - TARGET) <= 0.05 else round(own / TARGET, 1)), own


def gpu_seconds_per_step(n, warm=3, steps=20):
    """CUDA-event median of one exam step (generate A and B, INT8 matmul, row fingerprints), plus the real code
    path's wall clock per step (prover.gpu.Gpu.fingerprints + leaf hashes, as in a check). Returns the larger."""
    import cupy as cp
    from prover.gpu import Gpu, _rowfp, self_check
    self_check()
    g = Gpu.__new__(Gpu)
    p = Params(0xCA11B, n, warm + steps)
    key = cp.uint64(p.fp_key)
    times = []
    for s in range(warm + steps):
        a, b = cp.cuda.Event(), cp.cuda.Event()
        a.record()
        _rowfp(cp.from_dlpack(g._product(p, s)), key, axis=1)
        b.record()
        b.synchronize()
        if s >= warm:
            times.append(cp.cuda.get_elapsed_time(a, b) / 1e3)
    t0 = time.perf_counter()
    [leaf_hash(f) for f in g.fingerprints(Params(0xCA11C, n, steps))]
    wall = (time.perf_counter() - t0) / steps
    ev = statistics.median(times)
    print(f"per step: {ev * 1e3:.2f} ms (CUDA events, median of {steps}, spread {min(times) * 1e3:.2f}–"
          f"{max(times) * 1e3:.2f} ms) · {wall * 1e3:.2f} ms wall clock on the real code path")
    return max(ev, wall)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prover.calibrate")
    ap.add_argument("--cpu", action="store_true", help="no GPU: simulated estimate from the datasheet")
    ap.add_argument("--model", choices=sorted(MODELS), help="this GPU's model (default: from its SM count)")
    ap.add_argument("--net", type=float, default=1.0, help="seconds of network + hashing between start and commit")
    a = ap.parse_args(argv)

    model = a.model
    if a.cpu:
        model = model or "h100-sxm"
        t = 2 * N**3 / (tops(model) * 1e12 * SIM_EFF)
        print(f"SIMULATED (no GPU): {MODELS[model]['name']} assumed at {SIM_EFF:.0%} of its {tops(model)} TOPS "
              f"datasheet rating. Not a measurement: run this on the pod.")
    else:
        import torch
        if not torch.cuda.is_available():
            print("No CUDA device here. On a laptop, use --cpu for a simulated estimate.")
            return 1
        sms = torch.cuda.get_device_properties(0).multi_processor_count
        model = model or MODEL_BY_SMS.get(sms)
        if not model or not MODELS[model]["dense"].get("int8_tops"):
            print(f"{torch.cuda.get_device_name(0)} ({sms} SMs): pass --model <id> (one of core/gpu_specs.json).")
            return 1
        print(f"{torch.cuda.get_device_name(0)}: {sms} SMs -> {MODELS[model]['name']}")
        t = gpu_seconds_per_step(N)
    eff = 2 * N**3 / t / 1e12
    steps, d, own = recommend(t, model, N, a.net)
    print(f"\nseconds per step: {t:.4f} s at n={N} ({eff:.0f} TOPS, {100 * eff / tops(model):.0f}% of the rating)")
    print(f"recommended steps: {steps} -> this GPU ~{own:.1f} s (with {a.net:g} s network) of a {d:.1f} s deadline "
          f"({100 * own / d:.0f}%); the formula alone would give {deadline(model, N, steps):.1f} s")
    if model not in (A100, "a100-sxm-40", "a100-pcie-80", "a100-pcie-40"):
        a100 = a.net + steps * t * tops(model) / tops(A100)
        print(f"an A100 at the same steps: ~{a100:.1f} s ({tops(model)}/{tops(A100)} TOPS ratio) -> "
              + ("misses the deadline" if a100 > d else "still inside the deadline; the class check has to catch it"))
    cls = CLASS.get(model)
    keys = ", ".join(f'"{k}": {d}' for k in ([str(cls)] if cls else []) + [model])
    print(f"\nset on the API (Vercel env), then redeploy:\n  CHECK_STEPS={steps}\n  DEADLINES='{{{keys}}}'")
    print("  (DEADLINES fixes the deadline for the class outright, so it only fits CHECK_STEPS. Calibrate on the"
          " GPU model the listings claim; a relabelled pod gets that model's deadline.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
