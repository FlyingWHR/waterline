"""Waterline profiler. Runs inside the rented pod.

  python -m prover.run --api URL --cloud cloud-b --claimed 1          # GPU (CuPy + torch)
  python -m prover.run --api URL --cloud cloud-b --claimed 1 --cpu    # CPU, core/ maths, fixed probes

Probes run first (they are not part of the timed work). Then: start -> compute every step -> commit
(root + probes) -> reveal the rows the API picked. Prints the API's final JSON on stdout; progress goes to
stderr. Writes result.json (probes, staircase timings in ms, API result) for the agent and the web chart.
The staircase (blocks -> ms) also goes to the API as probes.staircase, for the web chart.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

from core.challenge import Params, fingerprint_rows, leaf_hash, merkle_root, product


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


# SM counts per class, used only to fake probes in CPU mode.
FP8_BY_SMS = {132: True, 114: True, 108: False}


class Cpu:
    """core/ maths on the CPU. Probes are fixed test values; the staircase is synthetic (seconds, like the GPU's)."""

    def __init__(self, sms=132, uuid=None):
        self.sms = sms
        self.uuid = uuid or "GPU-00000000-0000-4000-8000-00000000c0de"

    def probes(self):
        # time ~ ceil(k / sms) waves of ~5 ms, plus a little deterministic jitter so the chart looks measured
        stair = {k: 0.005 * -(-k // self.sms) + 0.00002 * ((k * 37) % 7) for k in range(64, 161)}
        return {"sms": self.sms, "fp8": FP8_BY_SMS.get(self.sms, False), "clock_ghz": 1.98, "bw_tbs": 3.35,
                "fingerprint": "0x" + hashlib.sha256(f"cpu-{self.sms}".encode()).hexdigest()}, stair

    def fingerprints(self, p):
        return [fingerprint_rows(product(p, s), p.fp_key) for s in range(p.steps)]

    def rows(self, p, samples):
        out = {}
        for s in sorted({s for s, _ in samples}):
            c = product(p, s)
            out.update({f"{s}:{r}": c[r].tolist() for s2, r in samples if s2 == s})
        return out


def profile(api, cloud, claimed, backend, n=None, steps=None):
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

    rv = post(api, "/api/check/reveal", {
        "session_id": st["session_id"],
        "fingerprints": {str(s): [str(int(v)) for v in fps[s]] for s in sorted({s for s, _ in samples})},
        "leaf_hashes": {str(i): h for i, h in enumerate(leaves)},
        "rows": backend.rows(p, samples),
    })
    local = {"uuid": backend.uuid, "cloud": cloud, "claimed_class": claimed, "n": p.n, "steps": p.steps,
             "compute_s": round(compute_s, 4), "elapsed_s": cm["elapsed_s"], "probes": probes,
             "staircase": stair_ms, "result": rv}
    return rv, local


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prover.run")
    ap.add_argument("--api", required=True)
    ap.add_argument("--cloud", required=True)
    ap.add_argument("--claimed", type=int, required=True)
    ap.add_argument("--cpu", action="store_true", help="no GPU: core/ maths and fixed test probes")
    ap.add_argument("--sms", type=int, default=132, help="CPU mode: SM count to report (108 plays an A100)")
    ap.add_argument("--uuid", help="CPU mode: fake GPU UUID")
    ap.add_argument("--n", type=int)
    ap.add_argument("--steps", type=int)
    ap.add_argument("--out", default="result.json")
    a = ap.parse_args(argv)

    if a.cpu:
        backend, n, steps = Cpu(a.sms, a.uuid), a.n or 64, a.steps or 4
    else:
        from prover.gpu import Gpu  # lazy: CuPy/torch only exist on the pod
        backend, n, steps = Gpu(), a.n, a.steps
    try:
        rv, local = profile(a.api, a.cloud, a.claimed, backend, n, steps)
    except ApiError as e:
        log(f"error: {e}")
        return 2
    with open(a.out, "w") as f:
        json.dump(local, f, indent=1)
    print(json.dumps(rv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
