"""Grading a reveal against the committed root, the model verdict and the deadline. Pure functions; no I/O."""
import hashlib
import json
import os
import secrets

import numpy as np

from core.challenge import Params, leaf_hash, merkle_root, row_fingerprint, verify_row_entries
from core.specs import MODELS

from . import classify as cl

SAMPLES = 8
SPOT_COLS = 64
CLASS_NAMES = {1: "H100 SXM", 2: "H100 PCIe", 3: "A100"}
# On-chain class codes (uint8) -> the gpu_specs.json models each one accepts
CLASS_MODELS = {1: ["h100-sxm"], 2: ["h100-pcie"], 3: ["a100-sxm-80", "a100-sxm-40", "a100-pcie-80", "a100-pcie-40"]}
# Dense INT8 tensor-core TOPS per class, from core/gpu_specs.json (1979, 1513, 624: datasheet sparse / 2)
SPEC_TOPS = {c: MODELS[ids[0]]["dense"]["int8_tops"] for c, ids in CLASS_MODELS.items()}
# Deadline = max(MIN, BASE + 2 n^3 steps / (dense INT8 x MIN_EFF)); docs/METRICS.md. Calibrate on real pods.
DEADLINE_BASE_S, DEADLINE_MIN_EFF, DEADLINE_MIN_S, DEADLINE_NO_INT8_S = 3.0, 0.25, 5.0, 60.0


def claimed_models(claimed) -> list[str]:
    """Model ids a claim accepts: a class code (1-3) or a gpu_specs.json model id."""
    return CLASS_MODELS.get(claimed, []) if isinstance(claimed, int) else [claimed] if claimed in MODELS else []


def claimed_name(claimed) -> str:
    return CLASS_NAMES.get(claimed, "an unknown chip") if isinstance(claimed, int) else \
        MODELS[claimed]["name"] if claimed in MODELS else "an unknown chip"


def class_for_model(model_id) -> int:
    """On-chain class code for a model id (0 when it has none)."""
    return next((c for c, ids in CLASS_MODELS.items() if model_id in ids), 0)


def deadline_s(claimed, n: int, steps: int) -> float:
    """Seconds allowed from start to commit for a claim (model id or class code). DEADLINES env (JSON, keys =
    model id or class code) overrides."""
    table = json.loads(os.environ.get("DEADLINES") or "{}")
    if str(claimed) in table:
        return float(table[str(claimed)])
    tops = [MODELS[i]["dense"]["int8_tops"] for i in claimed_models(claimed) if MODELS[i]["dense"].get("int8_tops")]
    if not tops:
        return DEADLINE_NO_INT8_S
    return round(max(DEADLINE_MIN_S, DEADLINE_BASE_S + 2 * n**3 * steps / (min(tops) * 1e12 * DEADLINE_MIN_EFF)), 1)


def class_check(claimed, probes=None, metrics=None):
    """(classification, reasons). Fails when the claimed model is neither the best match nor ambiguous with it."""
    c = cl.classification(probes, metrics, claimed_models(claimed))
    c["claimed"] = claimed
    if c["best_match"] is None:
        return c, [f"The GPU could not be measured, so it can't be confirmed as {claimed_name(claimed)}."]
    if c["consistent"]:
        return c, []
    return c, [f"Measured as {c['name']} ({cl.evidence(c['features'])}), listed as {claimed_name(claimed)}."]


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


def classify(probes: dict, metrics: dict | None = None) -> int:
    """On-chain class code (0-3) of the measured GPU: the best match, else the first ambiguous partner with a code."""
    c = cl.classification(probes, metrics)
    return next((class_for_model(i) for i in [c["best_match"], *c["ambiguous_with"]] if class_for_model(i)), 0)


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

