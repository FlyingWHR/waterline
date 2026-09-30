"""Go-live preflight. Read-only: never sends a transaction, never prints a secret.

  .venv/bin/python scripts/check_live.py

Reads .env (the shell's env wins). Prints one ✓/✗ line per check, each ✗ with a plain fix. Exit 1 if any ✗.
"""
import json
import os
import sys
from pathlib import Path

import httpx
from eth_abi import decode, encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.multibaas_link import ROOT, load_env, mb  # noqa: E402

SEPOLIA = 11155111
# deploy + commit + register before Marks exists; after, the deployer only pays for role grants. Reporter: a few dozen record() calls
MIN_DEPLOYER_ETH, MIN_DEPLOYER_AFTER_ETH = 0.05, 0.005
MIN_REPORTER_ETH = 0.01
ROLE_REPORTER = 1


def rpc(method, params):
    url = os.environ.get("SEPOLIA_RPC") or os.environ.get("PUBLIC_SEPOLIA_RPC") or "https://ethereum-sepolia-rpc.publicnode.com"
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=15)
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "RPC error"))
    return body["result"]


def call(to, sig, types, args):
    return bytes.fromhex(rpc("eth_call", [{"to": to, "data": "0x" + (keccak(text=sig)[:4] + encode(types, args)).hex()},
                                          "latest"])[2:])


def namehash(name):
    node = b"\0" * 32
    for label in reversed(name.split(".")):
        node = keccak(node + keccak(text=label))
    return node


def dns_encode(name):
    return b"".join(bytes([len(x)]) + x.encode() for x in name.split(".")) + b"\0"


def parent_name():
    dep = ROOT / "contracts/deployments/sepolia.json"
    label = json.loads(dep.read_text()).get("label") if dep.is_file() else None
    return (label or os.environ.get("NAME_LABEL") or "waterline") + ".eth"


def addr_of(key_var, addr_var=None):
    try:
        return Account.from_key(os.environ[key_var]).address
    except (KeyError, ValueError):
        return os.environ.get(addr_var) if addr_var else None


def checks():
    """Yields (ok, what, fix). Checks that depend on a failed one are left out."""
    env = os.environ.get
    try:
        cid = int(rpc("eth_chainId", []), 16)
        rpc_ok = cid == SEPOLIA
        yield rpc_ok, f"RPC reachable, chain id {cid}", "Point SEPOLIA_RPC at an Ethereum Sepolia endpoint (chain id 11155111)."
    except Exception as e:  # noqa: BLE001 (any failure here is "unreachable")
        rpc_ok = False
        yield False, f"RPC unreachable ({type(e).__name__})", "Check SEPOLIA_RPC (e.g. https://ethereum-sepolia-rpc.publicnode.com)."

    for who, key, addr, low in (("Deployer", "DEPLOYER_KEY", None, MIN_DEPLOYER_AFTER_ETH if env("MARKS_ADDRESS") else MIN_DEPLOYER_ETH),
                                ("Reporter", "REPORTER_KEY", "REPORTER_ADDRESS", MIN_REPORTER_ETH)):
        a = addr_of(key, addr)
        if not a:
            yield False, f"{who} account not set", f"Set {key} in .env."
        elif rpc_ok:
            bal = int(rpc("eth_getBalance", [a, "latest"]), 16) / 1e18
            yield bal >= low, f"{who} {a} has {bal:.4f} ETH", f"Send at least {low} Sepolia ETH to {a} (a Sepolia faucet)."
    rep_env, rep_key = env("REPORTER_ADDRESS"), addr_of("REPORTER_KEY")
    if rep_env and rep_key and rep_env.lower() != rep_key.lower():
        yield False, "REPORTER_ADDRESS is not the address of REPORTER_KEY", f"Set REPORTER_ADDRESS={rep_key}."
    rep_key = rep_key or rep_env

    marks = env("MARKS_ADDRESS")
    if not marks:
        yield False, "MARKS_ADDRESS not set", "Run scripts/deploy_marks.sh, then put MARKS_ADDRESS in .env."
    elif rpc_ok:
        marks = to_checksum_address(marks)
        code = rpc("eth_getCode", [marks, "latest"])
        yield len(code) > 2, f"Marks has code at {marks}", "Nothing deployed there: run scripts/deploy_marks.sh."
        if len(code) > 2 and rep_key:
            has = decode(["bool"], call(marks, "hasRootRoles(uint256,address)", ["uint256", "address"], [ROLE_REPORTER, rep_key]))[0]
            yield has, f"Reporter {rep_key} holds ROLE_REPORTER on Marks",\
                "Deploy with REPORTER_ADDRESS = the address of REPORTER_KEY, or grantRootRoles(1, reporter) as admin."
        name = parent_name()
        ur = env("ENS_UNIVERSAL_RESOLVER") or json.loads((ROOT / "contracts/ens.sepolia.json").read_text())["UniversalResolver"]
        try:  # same call as contracts/test/EnsFork.t.sol: resolve(name, text(node, "description")) -> (bytes, resolver)
            data = keccak(text="text(bytes32,string)")[:4] + encode(["bytes32", "string"], [namehash(name), "description"])
            _, resolver = decode(["bytes", "address"], call(ur, "resolve(bytes,bytes)", ["bytes", "bytes"], [dns_encode(name), data]))
            yield resolver.lower() == marks.lower(), f"{name} resolves through {to_checksum_address(resolver)}",\
                f"Its resolver is not Marks ({marks}): run scripts/deploy_marks.sh --register-only."
        except Exception as e:  # noqa: BLE001 (a revert here means no resolver)
            yield False, f"{name} does not resolve ({str(e)[:60]})", "Register it: scripts/deploy_marks.sh --register-only."

    if not (env("MB_URL") and env("MB_API_KEY")):
        yield False, "MultiBaas not configured", "Set MB_URL and MB_API_KEY (admin key) in .env."
    else:
        try:
            code, out = mb("GET", "/chains/ethereum/status")
            mb_cid = ((out or {}).get("result") or {}).get("chainID")
            yield code == 200 and mb_cid == SEPOLIA, f"MultiBaas reachable (HTTP {code}, chain {mb_cid})",\
                "Check MB_URL / MB_API_KEY, and that the deployment is on Ethereum Sepolia."
            alias = env("MB_MARKS_ALIAS") or "marks"
            code, out = mb("GET", f"/chains/ethereum/addresses/{alias}")
            r = (out or {}).get("result") or {}
            linked = code == 200 and str(r.get("address")).lower() == str(marks).lower() and \
                any(c.get("label") == (env("MB_MARKS_LABEL") or "marks") for c in r.get("contracts") or [])
            yield linked, f"Marks linked in MultiBaas as '{alias}'", "Run .venv/bin/python scripts/multibaas_link.py."
            hook = (env("API_URL") or "").rstrip("/") + "/api/webhooks/multibaas"
            code, out = mb("GET", "/webhooks?limit=100")
            hooks = [w for w in (out or {}).get("result") or [] if w.get("url") == hook]
            yield bool(env("API_URL")) and bool(hooks), f"Webhook to {hook} " + (f"(failed calls: {hooks[0].get('failedCalls')})" if hooks else "missing"),\
                "Set API_URL, then run scripts/multibaas_link.py (it creates the webhook)."
            yield bool(env("MB_WEBHOOK_SECRET")), "MB_WEBHOOK_SECRET set", "Paste the secret multibaas_link.py printed into .env and Vercel."
        except httpx.HTTPError as e:
            yield False, f"MultiBaas unreachable ({type(e).__name__})", "Check MB_URL."

    api = env("API_URL")
    if not api:
        yield False, "API_URL not set", "Set API_URL to the Vercel production URL."
    else:
        try:
            h = httpx.get(api.rstrip("/") + "/api/health", timeout=15).json()
            wp = h["chain"].get("write_path")
            yield True, f"API {api} answers /api/health (store {h['store']})", ""
            yield wp in ("multibaas", "rpc"), f"API write path: {wp}",\
                "On Vercel set REPORTER_KEY + MB_URL + MB_API_KEY (or MARKS_ADDRESS + SEPOLIA_RPC), then redeploy."
            yield h["store"] == "redis", f"API store: {h['store']}", "Add Redis on Vercel (REDIS_URL), then redeploy."
            yield bool(h["chain"].get("marks")) and str(h["chain"]["marks"]).lower() == str(marks).lower(),\
                f"API uses Marks {h['chain'].get('marks')}", "Set MARKS_ADDRESS on Vercel to the deployed Marks, then redeploy."
            import io
            import zipfile
            names = set(zipfile.ZipFile(io.BytesIO(httpx.get(api + "/api/bundle", timeout=30).content)).namelist())
            need = {"core/challenge.py", "core/listing.py", "core/gpu_specs.json", "core/providers.json", "prover/run.py", "prover/gpu.py"}
            yield need <= names, f"One-liner bundle carries the profiler ({len(names)} files)", \
                f"Missing from /api/bundle: {sorted(need - names)}. Check .vercelignore and vercel.json excludeFiles."
        except (httpx.HTTPError, ValueError, KeyError) as e:
            yield False, f"API unreachable at {api} ({type(e).__name__})", "Deploy the API (vercel --prod) and check API_URL."


def main():
    load_env()
    bad = 0
    try:
        for ok, what, fix in checks():
            bad += not ok
            print(f"{'✓' if ok else '✗'} {what}" + ("" if ok else f"\n    fix: {fix}"), flush=True)
    except Exception as e:  # noqa: BLE001 (a flaky endpoint mid-run: say so, don't dump a trace)
        bad += 1
        print(f"✗ the preflight stopped: {type(e).__name__}: {str(e)[:120]}\n    fix: re-run; if it repeats, check that endpoint.")
    print(f"\n{'All good: live.' if not bad else f'{bad} to fix.'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
