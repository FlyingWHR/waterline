"""Grading a reveal against the committed root. Pure functions over core/; no I/O."""
import hashlib
import secrets

import numpy as np

from core.challenge import Params, leaf_hash, merkle_root, row_fingerprint, verify_row_entries

SAMPLES = 8
SPOT_COLS = 64
CLASS_NAMES = {1: "H100 SXM", 2: "H100 PCIe", 3: "A100"}
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
            reasons.append("The leaf hashes do not rebuild the committed root.")
    except ValueError:
        reasons.append("Leaf hashes are missing or not hex.")

    good_fps = {}  # step -> fingerprints that hash to the committed leaf
    for s in sorted({s for s, _, _ in samples}):
        try:
            fps = _u64s(fingerprints.get(str(s), []))
        except (ValueError, TypeError):
            reasons.append(f"Step {s}: fingerprints are not uint64 values.")
            continue
        if len(fps) != p.n or leaf_hash(fps) != leaves[s]:
            reasons.append(f"Step {s}: fingerprints do not match the committed leaf hash.")
        else:
            good_fps[s] = fps

    for s, r, cols in samples:
        vals = rows.get(f"{s}:{r}")
        if not isinstance(vals, list) or len(vals) != p.n or any(not -(1 << 63) <= int(v) < 1 << 63 for v in vals):
            reasons.append(f"Row {s}:{r} is missing or has the wrong length.")
            continue
        if s not in good_fps or row_fingerprint(vals, p.fp_key) != int(good_fps[s][r]):
            reasons.append(f"Row {s}:{r} does not match its fingerprint.")
        if not verify_row_entries(p, s, r, vals, cols):
            reasons.append(f"Row {s}:{r} has wrong entries at the spot-checked columns.")
    return reasons

