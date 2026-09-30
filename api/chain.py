"""Writes to Marks on Sepolia. Three paths (write_path()):
- "multibaas": MB_URL + MB_API_KEY + REPORTER_KEY. MultiBaas composes the call (fills nonce and gas), we check and sign
  it locally with REPORTER_KEY, MultiBaas submits it. Contract: MB_MARKS_ALIAS (default marks) / MB_MARKS_LABEL (marks).
- "rpc": MARKS_ADDRESS + REPORTER_KEY + SEPOLIA_RPC, straight JSON-RPC.
- "dry-run": otherwise (log + remember the call, tx None)."""
import logging
import os

import httpx
from eth_abi import encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

log = logging.getLogger("waterline.chain")
# Marks derives node = keccak(keccak(parent, cloudLabel), gpuLabel), so a GPU always rolls up to its provider
SIGNATURE = "record(bytes32,bytes32,uint8,uint8,uint16,bytes32,bytes32,bytes32,uint32,uint16,bytes32)"
SELECTOR = keccak(text=SIGNATURE)[:4]
ARG_TYPES = ["bytes32", "bytes32", "uint8", "uint8", "uint16", "bytes32", "bytes32", "bytes32", "uint32", "uint16",
             "bytes32"]
ZERO = b"\0" * 32
DRY_RUN_CALLS = []  # dry-run record() calls, newest last (tests read this)


def namehash(name: str) -> bytes:
    """ENSIP-1 namehash. Labels are expected already normalised (ours are lowercase [a-z0-9-])."""
    node = b"\0" * 32
    if name:
        for label in reversed(name.split(".")):
            node = keccak(node + keccak(text=label))
    return node


def encode_perf(tops, pct):
    """(topsX10, pctBps) for Marks.record from verified TOPS and percent of spec; None -> 0, clamped to the uint range."""
    clamp = lambda x, scale, hi: 0 if x is None else max(0, min(hi, round(x * scale)))  # noqa: E731
    return clamp(tops, 10, 2**32 - 1), clamp(pct, 100, 2**16 - 1)


class ChainError(Exception):
    pass


def _rpc(url, method, params, timeout=20):
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise ChainError(f"{method}: {body['error'].get('message', body['error'])}")
    return body["result"]


def live() -> bool:
    return write_path() != "dry-run"


def write_path() -> str:
    env = os.environ.get
    if env("MB_URL") and env("MB_API_KEY") and env("REPORTER_KEY"):
        return "multibaas"
    return "rpc" if all(env(k) for k in ("MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC")) else "dry-run"


def _mb(method, path, body=None):
    """MultiBaas REST call -> `result`. Any trouble -> ChainError with a plain message."""
    try:
        r = httpx.request(method, os.environ["MB_URL"].rstrip("/") + "/api/v0" + path, json=body, timeout=20,
                          headers={"Authorization": f"Bearer {os.environ['MB_API_KEY']}"})
        out = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ChainError(f"MultiBaas unreachable: {type(e).__name__}") from None
    if r.status_code >= 400 or not isinstance(out, dict) or "result" not in out:
        msg = out.get("message") if isinstance(out, dict) else None
        raise ChainError(f"MultiBaas refused the call (HTTP {r.status_code}): {msg or 'no message'}")
    return out["result"]


def _int(x):
    return int(x, 0) if isinstance(x, str) else int(x)


def _record_mb(args, data, key):
    try:
        acct = Account.from_key(key)
    except ValueError:
        raise ChainError("REPORTER_KEY is not a valid private key.") from None
    alias, label = os.environ.get("MB_MARKS_ALIAS") or "marks", os.environ.get("MB_MARKS_LABEL") or "marks"
    mb_args = ["0x" + a.hex() if isinstance(a, bytes) else str(a) for a in args]
    tx = _mb("POST", f"/chains/ethereum/addresses/{alias}/contracts/{label}/methods/record",
             {"args": mb_args, "from": acct.address, "formatInts": "as_strings"}).get("tx") or {}
    chain_id = _mb("GET", "/chains/ethereum/status").get("chainID")
    try:
        # MultiBaas composed it, but the reporter key signs it: sign only the exact call we asked for.
        want = os.environ.get("MARKS_ADDRESS")
        if bytes.fromhex(str(tx["data"]).removeprefix("0x")) != data or _int(tx.get("value") or 0) != 0 or (
                want and to_checksum_address(tx["to"]) != to_checksum_address(want)):
            raise ChainError("MultiBaas composed a different transaction than Marks.record; not signing it.")
        fields = {"chainId": _int(chain_id), "nonce": _int(tx["nonce"]), "to": to_checksum_address(tx["to"]),
                  "data": data, "value": 0, "gas": _int(tx["gas"])}
        if tx.get("gasFeeCap") is not None:  # EIP-1559: MultiBaas names them gasFeeCap / gasTipCap
            fields |= {"type": 2, "maxFeePerGas": _int(tx["gasFeeCap"]), "maxPriorityFeePerGas": _int(tx["gasTipCap"])}
        else:
            fields["gasPrice"] = _int(tx["gasPrice"])
        signed = acct.sign_transaction(fields)
    except (KeyError, TypeError, ValueError) as e:
        raise ChainError(f"MultiBaas returned a transaction we could not sign ({type(e).__name__}: {e}).") from None
    _mb("POST", "/chains/ethereum/transactions/submit", {"signedTx": "0x" + bytes(signed.raw_transaction).hex()})
    return "0x" + bytes(signed.hash).hex()


def reporter_address():
    try:
        return Account.from_key(os.environ["REPORTER_KEY"]).address
    except (KeyError, ValueError):
        return os.environ.get("REPORTER_ADDRESS") or None


def balance_eth(address):
    """Sepolia balance in ETH, or None if unknown (no RPC, RPC down)."""
    rpc = os.environ.get("SEPOLIA_RPC")
    if not (rpc and address):
        return None
    try:
        return int(_rpc(rpc, "eth_getBalance", [address, "latest"], timeout=5), 16) / 1e18
    except (httpx.HTTPError, ChainError, ValueError):
        return None


def record(cloud: str, gpu_label: str, verdict: int, cls: int, cores: int, fingerprint: bytes, gpu_voter: bytes = ZERO,
           provider_voter: bytes = ZERO, tops_x10: int = 0, pct_bps: int = 0, report_hash: bytes = ZERO):
    """Marks.record(...) for gpu_label.cloud.waterline.eth. A failure needs both voter ids (one report -> a
    per-GPU and a per-provider id); a pass carries none. tops_x10 / pct_bps from encode_perf; report_hash = keccak256
    of the canonical report. Returns the tx hash (0x hex), or None in dry-run.
    The path taken is write_path(). Raises ChainError on failure."""
    args = (keccak(text=cloud), keccak(text=gpu_label), verdict, cls, cores, fingerprint, gpu_voter, provider_voter,
            tops_x10, pct_bps, report_hash)
    addr, key, rpc = (os.environ.get(k) for k in ("MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC"))
    path = write_path()
    if path == "dry-run":
        DRY_RUN_CALLS.append(args)
        log.info("dry-run Marks.record %s.%s verdict=%d cls=%d cores=%d fp=0x%s voters=0x%s/0x%s tops_x10=%d "
                 "pct_bps=%d report=0x%s", gpu_label, cloud, verdict, cls, cores, fingerprint.hex(), gpu_voter.hex(),
                 provider_voter.hex(), tops_x10, pct_bps, report_hash.hex())
        return None
    data = SELECTOR + encode(ARG_TYPES, list(args))
    if path == "multibaas":
        return _record_mb(args, data, key)
    acct = Account.from_key(key)
    try:
        tx = {"from": acct.address, "to": to_checksum_address(addr), "data": "0x" + data.hex(), "value": 0}
        # estimateGas also surfaces reverts (e.g. a voter already used on this node) before we spend gas
        gas = int(_rpc(rpc, "eth_estimateGas", [tx]), 16)
        base = int(_rpc(rpc, "eth_getBlockByNumber", ["latest", False])["baseFeePerGas"], 16)
        tip = int(_rpc(rpc, "eth_maxPriorityFeePerGas", []), 16)
        # ponytail: nonce from "pending"; two publishes in the same instant can collide. Queue them if that happens.
        nonce = int(_rpc(rpc, "eth_getTransactionCount", [acct.address, "pending"]), 16)
        chain_id = int(_rpc(rpc, "eth_chainId", []), 16)
    except httpx.HTTPError as e:
        raise ChainError(f"RPC unreachable: {type(e).__name__}") from None
    signed = acct.sign_transaction({
        "type": 2, "chainId": chain_id, "nonce": nonce, "to": tx["to"], "data": data, "value": 0,
        "gas": gas * 12 // 10, "maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip,
    })
    try:
        return _rpc(rpc, "eth_sendRawTransaction", ["0x" + bytes(signed.raw_transaction).hex()])
    except httpx.HTTPError as e:
        raise ChainError(f"RPC unreachable: {type(e).__name__}") from None
