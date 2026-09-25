"""Challenge maths shared by the profiler (GPU) and the Waterline API (CPU).

Step i computes C_i = A_i @ B_i from the seed. Each output row becomes one 64-bit fingerprint:
sum of mix64(entry ^ fp_key), wrapping. The fingerprint is nonlinear on purpose: a linear sum lets
a prover patch a revealed row to match its commitment.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .rng import col, matrix, mix64, row

TAG_A, TAG_B = 0, 1


@dataclass(frozen=True)
class Params:
    seed: int
    n: int
    steps: int

    @property
    def fp_key(self) -> int:
        return int(mix64(np.uint64(self.seed ^ 0xABCDEF)))


def product(p: Params, step: int) -> np.ndarray:
    # float64 is exact here: |entry| <= n * 128 * 128, far below 2**53
    a = matrix(p.seed, p.n, TAG_A, step).astype(np.float64)
    b = matrix(p.seed, p.n, TAG_B, step).astype(np.float64)
    return np.rint(a @ b).astype(np.int64)


def _mixed(values: np.ndarray, fp_key: int) -> np.ndarray:
    return mix64(np.asarray(values, dtype=np.int64).view(np.uint64) ^ np.uint64(fp_key))


def fingerprint_rows(c: np.ndarray, fp_key: int) -> np.ndarray:
    with np.errstate(over="ignore"):
        return _mixed(c, fp_key).sum(axis=1, dtype=np.uint64)


def row_fingerprint(values, fp_key: int) -> int:
    with np.errstate(over="ignore"):
        return int(_mixed(values, fp_key).sum(dtype=np.uint64))


def leaf_hash(fingerprints: np.ndarray) -> str:
    return hashlib.blake2b(np.asarray(fingerprints, dtype=np.uint64).tobytes(), digest_size=32).hexdigest()


def merkle_root(leaf_hexes: list[str]) -> str:
    level = [bytes.fromhex(h) for h in leaf_hexes]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.blake2b(level[i] + level[i + 1], digest_size=32).digest()
                 for i in range(0, len(level), 2)]
    return level[0].hex()


def verify_row_entries(p: Params, step: int, r: int, claimed_row, cols) -> bool:
    """Recompute a few entries of one row from the seed: O(n) each, no full matrix."""
    a = row(p.seed, p.n, TAG_A, step, r).astype(np.int64)
    for c in cols:
        if int(claimed_row[int(c)]) != int(a @ col(p.seed, p.n, TAG_B, step, int(c)).astype(np.int64)):
            return False
    return True
