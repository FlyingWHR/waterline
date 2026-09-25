"""Listing text -> GPU class code. Jev (TypeSafe) when TYPESAFE_API_KEY is set, else simple rules.

The class only sets what the renter *claims*; it never decides a verdict.
"""
import json
import os
import re
import urllib.request

CLASSES = {1: "H100 SXM", 2: "H100 PCIe", 3: "A100"}
CRITERIA = {
    "H100 SXM": "NVIDIA H100 in the SXM form factor (SXM5, HGX, HBM3, 132 SMs, 700 W)",
    "H100 PCIe": "NVIDIA H100 PCIe card (HBM2e, 114 SMs, 350 W)",
    "A100": "NVIDIA A100, any memory size or form factor (Ampere)",
    "unknown": "Any other GPU, or not enough information",
}


def parse_rules(text):
    """(class code, confidence). Confident only when the form factor is explicit."""
    t = text.upper()
    if "A100" in t:
        return 3, 0.95
    if "H100" in t:
        if re.search(r"PCI-?E", t):
            return 2, 0.95
        if "SXM" in t or "HGX" in t:
            return 1, 0.95
        return 1, 0.5  # plain "H100": probably SXM, ask the renter
    return 0, 0.0


def parse_jev(text, key):
    """TypeSafe System One Choice. Shape checked against docs.typesafe.ai/api (Sep 2026)."""
    body = {"state": f"GPU rental listing: {text}", "model": "jev-1.13.0",
            "questions": {"gpu_class": {"type": "choice",
                                        "instructions": "Which GPU class does this rental listing offer?",
                                        "criteria": CRITERIA}}}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        ans = json.loads(r.read())["answers"]["gpu_class"]
    code = {v: k for k, v in CLASSES.items()}.get(ans["choice"], 0)
    return code, float(ans["confidence"])


def parse(text):
    """(class code, confidence, source)."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        try:
            return (*parse_jev(text, key), "Jev")
        except Exception as e:  # network or shape trouble: fall back, don't block the check
            print(f"(Jev unavailable: {e}; using simple rules)")
    return (*parse_rules(text), "rules")
