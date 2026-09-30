"""Link Marks in MultiBaas: upload the ABI, alias the address, link with a startingBlock, create the webhook.

  .venv/bin/python scripts/multibaas_link.py [--dry-run]
  .venv/bin/python scripts/multibaas_link.py --skip-webhook   # before the Vercel URL exists; re-run later for it

Env (or .env): MB_URL, MB_API_KEY (admin), MARKS_ADDRESS, API_URL (for the webhook; unset skips it),
MB_MARKS_ALIAS / MB_MARKS_LABEL (default marks). Bodies follow https://data.multibaas.com/api/v0/openapi.yaml.
Safe to re-run: an existing ABI version, alias or link counts as done once the alias is confirmed to point at
MARKS_ADDRESS, and an existing webhook to the same URL is kept.
"""
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

import httpx
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
ABI = ROOT / "contracts/out/Marks.sol/Marks.json"
BROADCAST = ROOT / "contracts/broadcast/Deploy.s.sol/11155111"


def load_env(path=ROOT / ".env"):
    """KEY=VALUE lines (bash-style quotes and trailing # comments) into os.environ; the shell's env wins."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, eq, val = line.strip().removeprefix("export ").partition("=")
        if not eq or key.startswith("#") or not key.isidentifier():
            continue
        words = shlex.split(val, comments=True)
        os.environ.setdefault(key, words[0] if words else "")


def mb(method, path, body=None):
    """-> (HTTP status, parsed JSON or None). Never raises for HTTP errors; network errors raise httpx.HTTPError."""
    r = httpx.request(method, os.environ["MB_URL"].rstrip("/") + "/api/v0" + path, json=body, timeout=20,
                      headers={"Authorization": f"Bearer {os.environ['MB_API_KEY']}"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def deploy_block(marks: str) -> str:
    """Block of the Marks CREATE from forge's broadcast files, as MultiBaas wants it (a string); else "-100"."""
    for f in sorted(BROADCAST.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            run = json.loads(f.read_text())
            tx = next(t["hash"] for t in run["transactions"]
                      if t.get("transactionType") == "CREATE" and str(t.get("contractAddress")).lower() == marks.lower())
            rc = next(r for r in run["receipts"] if r["transactionHash"] == tx)
            return str(int(rc["blockNumber"], 16) if isinstance(rc["blockNumber"], str) else rc["blockNumber"])
        except (StopIteration, KeyError, ValueError, TypeError):
            continue
    return "-100"


def plan(marks, api_url, alias, label):
    """The requests in order; the webhook only when api_url is set."""
    art = json.loads(ABI.read_text())
    raw_abi = json.dumps(art["abi"], separators=(",", ":"))
    version = "1-" + keccak(text=raw_abi).hex()[:8]  # a new ABI gets a new version; the same ABI re-uses its own
    return [
        # bin is required in practice: MultiBaas answers 400 (bytecode NOT NULL) without it
        ("POST", f"/contracts/{label}", {"label": label, "contractName": "Marks", "version": version, "rawAbi": raw_abi,
                                         "bin": art["bytecode"]["object"]}),
        ("POST", "/chains/ethereum/addresses", {"alias": alias, "address": marks}),
        ("POST", f"/chains/ethereum/addresses/{alias}/contracts",
         {"label": label, "version": version, "startingBlock": deploy_block(marks)}),
    ] + ([("POST", "/webhooks", {"url": api_url.rstrip("/") + "/api/webhooks/multibaas", "label": "waterline",
                                 "subscriptions": ["event.emitted"]})] if api_url else [])


def linked(alias, marks, label=None):
    """Does the alias already point at marks (and, with label, carry that contract)?"""
    code, out = mb("GET", f"/chains/ethereum/addresses/{alias}")
    r = (out or {}).get("result") or {}
    return code == 200 and str(r.get("address")).lower() == marks.lower() and (
        label is None or any(c.get("label") == label for c in r.get("contracts") or []))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="multibaas_link")
    ap.add_argument("--dry-run", action="store_true", help="print the requests, send nothing")
    ap.add_argument("--skip-webhook", action="store_true", help="link only (e.g. before the API has a URL)")
    a = ap.parse_args(argv)
    load_env()
    env = os.environ.get
    missing = [k for k in ("MB_URL", "MB_API_KEY", "MARKS_ADDRESS") if not env(k)]
    if missing:
        print(f"Set {', '.join(missing)} in .env first.")
        return 1
    if not ABI.is_file():
        print("No contracts/out/Marks.sol/Marks.json: run `forge build` in contracts/ first.")
        return 1
    alias, label, marks = env("MB_MARKS_ALIAS") or "marks", env("MB_MARKS_LABEL") or "marks", env("MARKS_ADDRESS")
    reqs = plan(marks, None if a.skip_webhook else env("API_URL"), alias, label)
    if a.dry_run:
        for method, path, body in reqs:
            shown = body | ({"rawAbi": f"<Marks ABI, {len(body['rawAbi'])} chars>",
                             "bin": f"<Marks bytecode, {len(body['bin'])} chars>"} if "rawAbi" in body else {})
            print(method, env("MB_URL").rstrip("/") + "/api/v0" + path, json.dumps(shown))
        return 0

    hook_url = reqs[-1][2]["url"] if reqs[-1][1] == "/webhooks" else None
    try:
        for method, path, body in reqs:
            if path == "/webhooks":
                code, out = mb("GET", "/webhooks?limit=100")
                same = [w for w in ((out or {}).get("result") or []) if w.get("url") == hook_url]
                if same:
                    print(f"✓ webhook {same[0].get('id')} already sends to {hook_url}; its secret is not shown again "
                          "(delete it in MultiBaas and re-run to get a new one)")
                    continue
            code, out = mb(method, path, body)
            msg = (out or {}).get("message") or ""
            if code < 300:
                print(f"✓ {method} {path}")
            elif path.startswith("/chains/") and linked(alias, marks, label if path.endswith("/contracts") else None):
                print(f"✓ {method} {path}: already there")  # alias / link exist and point at this Marks
            elif path.startswith("/contracts/") and (code == 409 or "exist" in msg.lower()):
                print(f"✓ {method} {path}: this ABI version is already uploaded")
            else:
                print(f"✗ {method} {path}: HTTP {code} {msg}")
                return 1
            if path == "/webhooks" and code < 300:
                print("\nWebhook secret (shown once): " + out["result"]["secret"])
                print("Put it in MB_WEBHOOK_SECRET in .env and on Vercel (vercel env add MB_WEBHOOK_SECRET production).")
    except httpx.HTTPError as e:
        print(f"✗ MultiBaas unreachable at {env('MB_URL')}: {type(e).__name__}")
        return 1
    print(f"\nMarks linked as {alias} from block {reqs[2][2]['startingBlock']}.")
    if not hook_url:
        print("Webhook skipped: once the API is deployed, set API_URL and re-run this script (without --skip-webhook).")
    print("Next: .venv/bin/python scripts/check_live.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
