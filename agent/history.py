"""GPU history from MultiBaas (Reported events indexed from Marks) and picking a GPU from listings.

The LLM never decides; the history does: choose() ranks on MultiBaas tallies and price only."""
import json
import os
import urllib.request

from Crypto.Hash import keccak

EVENT = "Reported(bytes32,bytes32,uint8,uint8,uint16,bytes32,uint32,uint16,uint64,uint32,uint32,uint32,bytes32)"
# Reported(node 0, voterId 1, verdict 2, cls 3, cores 4, fingerprint 5, topsX10 6, pctBps 7, at 8, passes 9,
# fails 10, humans 11, reportHash 12). Tallies only grow, so max = latest; per-report fields use last.
FIELDS = [("node", 0, None), ("verdict", 2, "last"), ("cls", 3, "last"), ("cores", 4, "last"),
          ("at", 8, "max"), ("passes", 9, "max"), ("fails", 10, "max"), ("humans", 11, "max")]
QUERY = {"events": [{"eventName": EVENT, "select": [
    {"type": "input", "inputIndex": i, "alias": a, **({"aggregator": g} if g else {})} for a, i, g in FIELDS]}],
    "groupBy": "node", "orderBy": "at", "order": "DESC"}


def _keccak(b):
    return keccak.new(digest_bits=256, data=b).digest()


def namehash(name):
    node = b"\0" * 32
    for label in reversed(name.split(".") if name else []):
        node = _keccak(node + _keccak(label.encode()))
    return "0x" + node.hex()


def status(row):
    """Same rule as Marks.text(waterline.status)."""
    humans, passes = int(row.get("humans") or 0), int(row.get("passes") or 0)
    if humans >= 2:
        return "failed"
    if humans == 1:
        return "suspect · 1 of 2 humans"
    return "pass" if passes > 0 else "unknown"


def fetch(mb_url=None, key=None):
    """{node: row} for every GPU with a report."""
    mb_url = (mb_url or os.environ["MB_URL"]).rstrip("/")
    key = key or os.environ["MB_API_KEY"]
    req = urllib.request.Request(f"{mb_url}/api/v0/queries", data=json.dumps(QUERY).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())["result"]["rows"]
    return {str(r["node"]).lower(): r for r in rows}


def full_name(gpu):
    return gpu if gpu.endswith(".waterline.eth") else gpu + ".waterline.eth"


def choose(listings, hist, max_price=None):
    """Decide from history only: skip any GPU with a failure report or a suspect/failed status, and any over
    max_price; then prefer more passes, then the lower price. Returns (pick or None, [(listing, why skipped)])."""
    ok, skipped = [], []
    for li in listings:
        row = hist.get(namehash(full_name(li["gpu"]))) or {}
        fails, passes, st = int(row.get("fails") or 0), int(row.get("passes") or 0), status(row)
        price = float(li["price"])
        if fails:
            skipped.append((li, f"{fails} failure report{'s' if fails > 1 else ''}"))
        elif st.startswith("suspect") or st == "failed":
            skipped.append((li, f"status {st}"))
        elif max_price is not None and price > max_price:
            skipped.append((li, f"{price:g}/h is over your max price of {max_price:g}/h"))
        else:
            ok.append((-passes, price, li, f"{passes} pass{'es' if passes != 1 else ''}, no failures"))
    ok.sort(key=lambda x: x[:2])
    return (ok[0][2] | {"history": ok[0][3]} if ok else None), skipped
