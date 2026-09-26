"""Read-only view of core/gpu_specs.json (35 models, confusable pairs). Shared by prover/ and api/."""
import json
import pkgutil
import re

_DATA = json.loads(pkgutil.get_data("core", "gpu_specs.json"))  # works from disk and from the in-memory bundle
MODELS = {m["id"]: m for m in _DATA["models"]}
PAIRS = [(p["a"], p["b"]) for p in _DATA["confusable_pairs"]]
PCIE_X16_GBS = {3: 15.75, 4: 31.5, 5: 63.0, 6: 121.0}  # per direction, after line encoding
C2C_GBS = 450.0  # NVLink-C2C: 900 GB/s bidirectional


def partners(model_id: str) -> set[str]:
    """Declared confusable partners of a model."""
    return {b if a == model_id else a for a, b in PAIRS if model_id in (a, b)}


def host_link(model_id: str):
    """(GB/s per direction, label) of the host link, or (None, None) when the table doesn't say."""
    ic = MODELS[model_id].get("interconnect") or ""
    if "C2C" in ic:
        return C2C_GBS, "NVLink-C2C (900 GB/s bidirectional)"
    m = re.search(r"PCIe Gen(\d)", ic)
    if m and int(m[1]) in PCIE_X16_GBS:
        return PCIE_X16_GBS[int(m[1])], f"PCIe Gen{m[1]} x16"
    return None, None
