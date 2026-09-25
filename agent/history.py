"""GPU history from MultiBaas (Reported events indexed from Marks) and picking a GPU from listings."""
import json
import os
import urllib.request

from Crypto.Hash import keccak

EVENT = "Reported(bytes32,bytes32,uint8,uint8,uint16,bytes32,uint64,uint32,uint32,uint32)"
# Reported(node 0, voterId 1, verdict 2, cls 3, cores 4, fingerprint 5, at 6, passes 7, fails 8, humans 9)
# Tallies only grow, so max = latest; per-report fields use last.
FIELDS = [("node", 0, None), ("verdict", 2, "last"), ("cls", 3, "last"), ("cores", 4, "last"),
          ("at", 6, "max"), ("passes", 7, "max"), ("fails", 8, "max"), ("humans", 9, "max")]
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


def choose(listings, hist, max_price=None):
    """Cheapest listing whose GPU is not suspect/failed. Returns (pick or None, [(listing, why skipped)])."""
    ok, skipped = [], []
    for li in listings:
        row = hist.get(namehash(li["gpu"]))
        st = status(row) if row else "no record"
        if max_price is not None and float(li["price"]) > max_price:
            skipped.append((li, f"over the max price ({li['price']})"))
        elif st.startswith("suspect") or st == "failed":
            skipped.append((li, f"history: {st}"))
        else:
            ok.append((float(li["price"]), li, st))
    ok.sort(key=lambda x: x[0])
    return (ok[0][1] | {"history": ok[0][2]} if ok else None), skipped
