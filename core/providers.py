"""Known GPU providers (core/providers.json): standard <cloud> labels, so one provider's reputation isn't split across
spellings. Advisory only: an unlisted name is still recorded as typed, and nothing here proves which provider a pod is on."""
import difflib
import json
import pkgutil

KNOWN = json.loads(pkgutil.get_data("core", "providers.json"))  # works from disk and from the in-memory bundle
SLUGS = {p["slug"] for p in KNOWN}


def listed(slug: str) -> bool:
    return slug in SLUGS


def suggest(slug: str) -> str | None:
    """The known label a typo most likely meant: runpod for run-pod or RunPod1."""
    squash = {s.replace("-", ""): s for s in SLUGS}
    key = slug.lower().replace("-", "").replace("_", "")
    if key in squash:
        return squash[key]
    near = difflib.get_close_matches(key, list(squash), n=1, cutoff=0.75)
    return squash[near[0]] if near else None
