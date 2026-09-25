"""Grading a reveal against the committed root. Pure functions over core/; no I/O."""
import hashlib
import secrets

import numpy as np

from core.challenge import Params, leaf_hash, merkle_root, row_fingerprint, verify_row_entries

SAMPLES = 8
SPOT_COLS = 64
CLASS_NAMES = {1: "H100 SXM", 2: "H100 PCIe", 3: "A100"}
# Dense INT8 tensor-core TOPS per class (NVIDIA H100 and A100 datasheets list INT8 "with sparsity";
# dense is half: H100 SXM 3,958 -> 1,979; H100 PCIe 3,026 -> 1,513; A100 1,248 -> 624).
SPEC_TOPS = {1: 1979, 2: 1513, 3: 624}


def class_name(cls: int) -> str:
    return CLASS_NAMES.get(cls, "an unknown chip")


def throughput(n: int, steps: int, elapsed_s: float, claimed: int) -> dict:
    """The work the check asked for (2 n^3 INT8 ops per step) over the time the API measured."""
    ops = 2 * n**3 * steps
    eff = ops / elapsed_s / 1e12 if elapsed_s > 0 else None
    spec = SPEC_TOPS.get(claimed)
    sig = lambda x: float(f"{x:.4g}")  # noqa: E731  (CPU runs are tiny numbers; keep 4 significant digits)
    return {"ops_total": ops, "effective_tops": None if eff is None else sig(eff), "spec_tops": spec,
            "pct_of_spec": None if eff is None or not spec else sig(100 * eff / spec)}
_rng = secrets.SystemRandom()


def gpu_label(uuid: str) -> str:
    return "gpu-" + hashlib.sha256(uuid.encode()).hexdigest()[:8]


def classify(probes: dict) -> int:
    sms, fp8 = probes.get("sms"), probes.get("fp8")
    if sms == 132 and fp8 is True:
        return 1
    if sms == 114 and fp8 is True:
        return 2
    if sms == 108 and fp8 is False:
        return 3
    return 0


def draw_samples(n: int, steps: int):
    """8 distinct (step, row) plus 64 secret spot columns per sample. Called only after the root is in."""
    k = min(SAMPLES, n * steps)
    picks = set()
    while len(picks) < k:
        picks.add((secrets.randbelow(steps), secrets.randbelow(n)))
    return [[s, r, _rng.sample(range(n), min(SPOT_COLS, n))] for s, r in sorted(picks)]


def _u64s(xs):
    vals = [int(x) for x in xs]  # ValueError on junk
    if any(v < 0 or v >= 1 << 64 for v in vals):
        raise ValueError
    return np.array(vals, dtype=np.uint64)


def grade(p: Params, root: str, samples, fingerprints: dict, leaf_hashes: dict, rows: dict) -> list[str]:
    """Return the list of failed checks (empty = the work checks out)."""
    reasons = []
    leaves = [leaf_hashes.get(str(s), "") for s in range(p.steps)]
    try:
        if merkle_root(leaves) != root:
            reasons.append("The step hashes do not rebuild the locked-in answer.")
    except ValueError:
        reasons.append("Step hashes are missing or not hex.")

    good_fps = {}  # step -> fingerprints that hash to the committed leaf
    for s in sorted({s for s, _, _ in samples}):
        try:
            fps = _u64s(fingerprints.get(str(s), []))
        except (ValueError, TypeError):
            reasons.append(f"Step {s}: the row fingerprints are not 64-bit numbers.")
            continue
        if len(fps) != p.n or leaf_hash(fps) != leaves[s]:
            reasons.append(f"Step {s}: the row fingerprints do not match the locked-in answer.")
        else:
            good_fps[s] = fps

    for s, r, cols in samples:
        vals = rows.get(f"{s}:{r}")
        if not isinstance(vals, list) or len(vals) != p.n or any(not -(1 << 63) <= int(v) < 1 << 63 for v in vals):
            reasons.append(f"Row {r} of step {s} is missing or has the wrong length.")
            continue
        if s not in good_fps or row_fingerprint(vals, p.fp_key) != int(good_fps[s][r]):
            reasons.append(f"Row {r} of step {s} does not match its fingerprint.")
        if not verify_row_entries(p, s, r, vals, cols):
            reasons.append(f"Row {r} of step {s} has wrong entries where the API re-checked it.")
    return reasons

