"""GPU history from MultiBaas (Reported events indexed from Marks) and picking a GPU from listings.

The LLM never decides; the history does: choose() ranks on MultiBaas tallies and price only."""
import json
import os
import urllib.request

from Crypto.Hash import keccak

EVENT = ("Reported(bytes32,bytes32,bytes32,bytes32,uint8,uint8,uint16,bytes32,uint32,uint16,uint64,uint32,uint32,"
         "uint32,bytes32)")
# Reported(node 0, provider 1, gpuVoter 2, providerVoter 3, verdict 4, cls 5, cores 6, fingerprint 7, topsX10 8,
# pctBps 9, at 10, passes 11, fails 12, active 13, reportHash 14). passes/fails only grow (max = latest); active can
# drop on recovery and per-report fields use last.
FIELDS = [("node", 0, None), ("provider", 1, "last"), ("verdict", 4, "last"), ("cls", 5, "last"), ("cores", 6, "last"),
          ("at", 10, "max"), ("passes", 11, "max"), ("fails", 12, "max"), ("active", 13, "last")]
PROVIDER_EVENT = "ProviderTally(bytes32,uint32,uint32,uint32,uint32,uint32,uint64)"
PROVIDER_FIELDS = [("provider", 0, None), ("gpus", 1, "max"), ("failed_gpus", 2, "last"), ("humans", 3, "max"),
                   ("at", 6, "max")]


def _query(event, fields, group):
    return {"events": [{"eventName": event, "select": [
        {"type": "input", "inputIndex": i, "alias": a, **({"aggregator": g} if g else {})} for a, i, g in fields]}],
        "groupBy": group, "orderBy": "at", "order": "DESC"}


QUERY = _query(EVENT, FIELDS, "node")
PROVIDER_QUERY = _query(PROVIDER_EVENT, PROVIDER_FIELDS, "provider")


def _keccak(b):
    return keccak.new(digest_bits=256, data=b).digest()


def namehash(name):
    node = b"\0" * 32
    for label in reversed(name.split(".") if name else []):
        node = _keccak(node + _keccak(label.encode()))
    return "0x" + node.hex()


def status(row):
    """Same rule as Marks.status: active humans decide; then a degraded latest check (verdict 3); failures with none
    active means recovered."""
    active, fails, passes = (int(row.get(k) or 0) for k in ("active", "fails", "passes"))
    if active >= 2:
        return "failed"
    if active == 1:
        return "suspect · 1 of 2 humans"
    if int(row.get("verdict") or 0) == 3:
        return "degraded"
    if fails:
        return "recovered"
    return "pass" if passes > 0 else "unknown"


def _post(query, mb_url, key, group):
    mb_url = (mb_url or os.environ["MB_URL"]).rstrip("/")
    key = key or os.environ["MB_API_KEY"]
    addr = (os.environ.get("MARKS_ADDRESS") or "").lower()
    if addr:  # only the live Marks: event queries otherwise match older deployments too
        query = query | {"events": [e | {"filter": {"rule": "and", "children": [
            {"operator": "Equal", "value": addr, "fieldType": "contract_address"}]}} for e in query["events"]]}
    req = urllib.request.Request(f"{mb_url}/api/v0/queries", data=json.dumps(query).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())["result"]["rows"]
    return {hex32(r[group]): r for r in rows}


def hex32(v):
    """bytes32 from MultiBaas as 0x hex: it answers hex, or a list of byte values ("[253, 55, ...]")."""
    if isinstance(v, str) and v.startswith("["):
        v = json.loads(v)
    if isinstance(v, list):
        return "0x" + bytes(int(b) for b in v).hex()
    v = str(v).lower()
    return v if v.startswith("0x") else "0x" + v


def fetch(mb_url=None, key=None):
    """{node: row} for every GPU with a report."""
    return _post(QUERY, mb_url, key, "node")


def fetch_providers(mb_url=None, key=None):
    """{provider node: row} for every provider with a report (each human counted once per provider)."""
    return _post(PROVIDER_QUERY, mb_url, key, "provider")


def full_name(gpu):
    return gpu if gpu.endswith(".waterline.eth") else gpu + ".waterline.eth"


def choose(listings, hist, max_price=None, providers=None):
    """Decide from history only: skip a GPU that is suspect or failed right now, and any over max_price; then prefer
    the provider with the smaller share of failed GPUs (renaming a chip doesn't clean its provider), more passes, then
    the lower price. A recovered GPU is allowed but says so. Returns (pick or None, [(listing, why skipped)])."""
    ok, skipped = [], []
    for li in listings:
        name = full_name(li["gpu"])
        row = hist.get(namehash(name)) or {}
        fails, passes, st = int(row.get("fails") or 0), int(row.get("passes") or 0), status(row)
        prov = (providers or {}).get(namehash(name.split(".", 1)[1])) or {}
        gpus, failed = int(prov.get("gpus") or 0), int(prov.get("failed_gpus") or 0)
        price = float(li["price"])
        if st.startswith("suspect") or st == "failed":
            skipped.append((li, f"status {st} ({fails} failure report{'s' if fails != 1 else ''})"))
        elif st == "degraded":
            skipped.append((li, "degraded on its last check: the right chip, too slow for its price"))
        elif max_price is not None and price > max_price:
            skipped.append((li, f"{price:g}/h is over your max price of {max_price:g}/h"))
        else:
            why = f"{passes} pass{'es' if passes != 1 else ''}, " + (
                f"recovered after {fails} failure report{'s' if fails != 1 else ''}" if fails else "no failures")
            if failed:
                why += f"; its provider has {failed} of {gpus} GPUs failed"
            ok.append((failed / gpus if gpus else 0, -passes, price, li, why))
    ok.sort(key=lambda x: x[:3])
    return (ok[0][3] | {"history": ok[0][4]} if ok else None), skipped
