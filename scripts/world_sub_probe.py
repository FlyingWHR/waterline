"""Does World's sandbox give the same person the same `sub` twice? Runs two device logins with the live client from
.env and prints each id_token's claim names plus short hashes of the identifying values (never the raw values).

  .venv/bin/python scripts/world_sub_probe.py
"""
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.multibaas_link import load_env  # noqa: E402

load_env()
from api import world  # noqa: E402


def once(n):
    d = world.device_start()
    print(f"\n[{n}] open {d['verification_uri_complete']}  (code {d['user_code']})", flush=True)
    until = time.time() + d["expires_in"]
    while time.time() < until:
        time.sleep(max(3, d["interval"]))
        status, claims = world.device_poll(d["device_code"])
        if status in ("pending", "slow_down"):
            continue
        if status != "approved":
            print(f"[{n}] {status}")
            return None
        short = {k: hashlib.sha256(json.dumps(v).encode()).hexdigest()[:12] for k, v in claims.items()}
        print(f"[{n}] claims: {sorted(claims)}")
        print(f"[{n}] hashed: {json.dumps(short)}")
        return short
    print(f"[{n}] expired")


a, b = once(1), once(2)
if a and b:
    same = [k for k in a if a.get(k) == b.get(k)]
    print("\nsub stable across logins:", a["sub"] == b["sub"], "| claims equal in both:", same)
