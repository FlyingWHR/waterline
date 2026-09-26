"""Waterline profiler. Runs inside the rented pod.

  python -m prover.run --api URL --cloud cloud-b --claimed 1          # GPU (CuPy + torch)
  python -m prover.run --api URL --cloud cloud-b --claimed 1 --cpu    # CPU, core/ maths, fixed probes

Probes run first (they are not part of the timed work). Then: start -> compute every step -> commit
(root + probes) -> reveal the rows the API picked. Prints the API's final JSON on stdout; progress goes to
stderr. Writes result.json (probes, staircase timings in ms, API result) for the agent and the web chart.
The staircase (blocks -> ms) also goes to the API as probes.staircase, for the web chart.
After commit (the deadline clock has stopped) it collects a health report (prover/health.py: NVML, a sustained
burn, DCGM; CPU mode: simulated) and the performance profile (prover/perf.py, docs/METRICS.md; CPU mode:
simulated from core/gpu_specs.json) and sends both with the reveal.
"""
import argparse
import hashlib
import secrets
import json
import sys
import time
import urllib.error
import urllib.request

from core.challenge import Params, fingerprint_rows, leaf_hash, merkle_root, product
from core.specs import MODELS
from prover import health
from prover.metrics import sim_staircase, simulate


class ApiError(Exception):
    pass


def post(api, path, body, timeout=120):
    req = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = None
        raise ApiError(f"{path}: {msg or e}") from None


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# CPU mode: which model an SM count plays (108 plays the A100 40GB, like health.simulated)
MODEL_BY_SMS = {132: "h100-sxm", 114: "h100-pcie", 108: "a100-sxm-40"}


class Cpu:
    """core/ maths on the CPU. Probes and metrics are simulated from the model's specs; the staircase is synthetic
    (seconds, like the GPU's)."""

    def __init__(self, sms=132, uuid=None, model=None):
        self.model = model or MODEL_BY_SMS.get(sms) or next((k for k, m in MODELS.items() if m["sms"] == sms), None)
        self.sms = MODELS[model]["sms"] if model else sms
        self.uuid = uuid or "GPU-00000000-0000-4000-8000-00000000c0de"
        self.sim_seed = uuid or secrets.token_hex(4)

    def probes(self):
        m = MODELS.get(self.model, {})
        return {"sms": self.sms, "fp8": bool(m.get("fp8")), "clock_ghz": 1.98,
                "bw_tbs": round(0.86 * m.get("bw_tbs", 3.35), 3),
                "fingerprint": "0x" + hashlib.sha256(f"cpu-{self.sms}".encode()).hexdigest()}, sim_staircase(self.sms)

    def health(self, burn_seconds):
        return health.simulated(self.sms, burn_seconds)

    def metrics(self, budget_s, per_second=None):
        # each simulated run is a different card: vary the per-GPU offset like real silicon does
        return simulate(self.model, per_second, seed=self.sim_seed) if self.model else {}

    def fingerprints(self, p):
        return [fingerprint_rows(product(p, s), p.fp_key) for s in range(p.steps)]

    def rows(self, p, samples):
        out = {}
        for s in sorted({s for s, _ in samples}):
            c = product(p, s)
            out.update({f"{s}:{r}": c[r].tolist() for s2, r in samples if s2 == s})
        return out


def profile(api, cloud, claimed, backend, n=None, steps=None, burn_seconds=10, perf_seconds=60):
    """Full check against the API. Returns (api_result, local_result)."""
    log("probing hardware ...")
    probes, stair = backend.probes()
    stair_ms = {str(k): round(v * 1000, 3) for k, v in sorted(stair.items())}
    log(f"probes: {probes}")

    body = {"cloud": cloud, "uuid": backend.uuid, "claimed_class": claimed}
    if n:
        body["n"] = n
    if steps:
        body["steps"] = steps
    st = post(api, "/api/check/start", body)
    p = Params(int(st["seed"]), int(st["n"]), int(st["steps"]))
    if int(st["fp_key"]) != p.fp_key:
        raise ApiError("the API's fp_key does not match the seed")
    log(f"session {st['session_id']}: n={p.n} steps={p.steps} deadline {st['deadline_s']}s")

    t0 = time.perf_counter()
    fps = backend.fingerprints(p)
    leaves = [leaf_hash(f) for f in fps]
    root = merkle_root(leaves)
    compute_s = time.perf_counter() - t0
    log(f"computed {p.steps} steps in {compute_s:.2f}s")

    cm = post(api, "/api/check/commit", {"session_id": st["session_id"], "root": root,
                                         "probes": probes | {"staircase": stair_ms}})
    samples = [(int(x[0]), int(x[1])) for x in cm["samples"]]
    log(f"committed; API saw {cm['elapsed_s']}s; revealing {len(samples)} rows")

    rows = backend.rows(p, samples)
    log(f"health report: {burn_seconds}s burn ...")
    try:
        hr = backend.health(burn_seconds)
    except Exception as e:  # advisory: never let it break the check
        hr = {"grade": health.GRADE, "source": "error", "notes": [f"{type(e).__name__}: {str(e)[:200]}"]}
    metrics = {}
    if perf_seconds > 0:
        log(f"performance profile (budget {perf_seconds}s) ...")
        try:
            metrics = backend.metrics(perf_seconds, ((hr or {}).get("burn") or {}).get("per_second"))
        except Exception as e:  # advisory too
            log(f"performance profile failed: {type(e).__name__}: {str(e)[:200]}")
    rv = post(api, "/api/check/reveal", {
        "session_id": st["session_id"],
        "fingerprints": {str(s): [str(int(v)) for v in fps[s]] for s in sorted({s for s, _ in samples})},
        "leaf_hashes": {str(i): h for i, h in enumerate(leaves)},
        "rows": rows,
        "health": hr,
        **({"metrics": metrics} if metrics else {}),
    })
    local = {"uuid": backend.uuid, "cloud": cloud, "claimed_class": claimed, "n": p.n, "steps": p.steps,
             "compute_s": round(compute_s, 4), "elapsed_s": cm["elapsed_s"], "probes": probes,
             "staircase": stair_ms, "health": hr, "metrics": metrics, "result": rv}
    return rv, local


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prover.run")
    ap.add_argument("--api", required=True)
    ap.add_argument("--cloud", required=True)
    ap.add_argument("--claimed", type=int, required=True)
    ap.add_argument("--cpu", action="store_true", help="no GPU: core/ maths and fixed test probes")
    ap.add_argument("--sms", type=int, default=132, help="CPU mode: SM count to report (108 plays an A100)")
    ap.add_argument("--model", choices=sorted(MODELS), help="CPU mode: model to simulate (overrides --sms)")
    ap.add_argument("--uuid", help="CPU mode: fake GPU UUID")
    ap.add_argument("--n", type=int)
    ap.add_argument("--steps", type=int)
    ap.add_argument("--burn-seconds", type=int, default=10, help="health report: sustained burn length (0 skips it)")
    ap.add_argument("--perf-seconds", type=int, default=60,
                    help="performance profile time budget, run after commit (0 skips it)")
    ap.add_argument("--out", help="also write the probes and timings to this file (nothing is written without it)")
    a = ap.parse_args(argv)

    if a.cpu:
        backend, n, steps = Cpu(a.sms, a.uuid, a.model), a.n or 64, a.steps or 4
    else:
        from prover.gpu import Gpu  # lazy: CuPy/torch only exist on the pod
        backend, n, steps = Gpu(), a.n, a.steps
    try:
        rv, local = profile(a.api, a.cloud, a.claimed, backend, n, steps, a.burn_seconds, a.perf_seconds)
    except ApiError as e:
        log(f"error: {e}")
        return 2
    if a.out:
        with open(a.out, "w") as f:
            json.dump(local, f, indent=1)
    link = f"{a.api.rstrip('/')}/#/check/{rv['report_id']}"
    log({"pass": "PASS: the listed chip, done in time.",
         "degraded": "DEGRADED: the listed chip with correct answers, but too slow for the deadline.",
         }.get(rv["verdict"], "FAIL: " + "; ".join(rv.get("reasons") or ["see the report"])))
    log(f"report: {link}" + ("\n  nothing is published yet: open the link and Approve with World to publish this failure"
                             if rv["verdict"] == "fail" else ""))
    print(json.dumps(rv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
