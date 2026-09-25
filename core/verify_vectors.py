"""Frozen vectors: any rewrite of rng.py / challenge.py must pass this."""
import json
from pathlib import Path

import numpy as np

from core.challenge import Params, fingerprint_rows, leaf_hash, merkle_root, product
from core.rng import col, matrix, row

V = json.loads((Path(__file__).parent / "vectors.json").read_text())
g = V["generator"]
seed, n = g["seed"], g["n"]

for e in g["entries"]:
    t, s, r, c = e["tag"], e["step"], e["row"], e["col"]
    M = matrix(seed, n, t, s)
    assert M[r, c] == row(seed, n, t, s, r)[c] == col(seed, n, t, s, c)[r] == e["value"], e

p = Params(seed, n, len(V["steps"]))
assert p.fp_key == int(V["fp_key"])

leaves = []
for sv in V["steps"]:
    C = product(p, sv["step"])
    fp = fingerprint_rows(C, p.fp_key)
    assert int(fp[0]) == int(sv["row0_fingerprint"]), sv["step"]
    assert int(fp[7]) == int(sv["row7_fingerprint"]), sv["step"]
    assert C[7, :8].tolist() == sv["row7_first8_values"], sv["step"]
    assert leaf_hash(fp) == sv["leaf_hash"], sv["step"]
    leaves.append(leaf_hash(fp))

assert merkle_root(leaves) == V["root"]
print("vectors: PASS")
