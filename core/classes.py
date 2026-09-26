"""GPU classes (core/gpu_classes.json): the onchain class code, the slug a renter types, the display name, and the
gpu_specs.json models each class accepts. One table for the API, the profiler, the agent and the contract's names."""
import json
import pkgutil

TABLE = json.loads(pkgutil.get_data("core", "gpu_classes.json"))["classes"]  # from disk or the in-memory bundle
NAMES = {c["code"]: c["name"] for c in TABLE}
MODELS = {c["code"]: c["models"] for c in TABLE}
BY_SLUG = {c["slug"]: c["code"] for c in TABLE} | {"h100-sxm": 1}
MAX = max(NAMES)
