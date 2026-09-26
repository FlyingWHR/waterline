"""Writes to Marks on Sepolia. Dry-run (log + remember the call, tx None) unless MARKS_ADDRESS, REPORTER_KEY and
SEPOLIA_RPC are all set."""
import logging
import os

import httpx
from eth_abi import encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

log = logging.getLogger("waterline.chain")
SIGNATURE = "record(bytes32,uint8,uint8,uint16,bytes32,bytes32,uint32,uint16)"
SELECTOR = keccak(text=SIGNATURE)[:4]
ARG_TYPES = ["bytes32", "uint8", "uint8", "uint16", "bytes32", "bytes32", "uint32", "uint16"]
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
    return all(os.environ.get(k) for k in ("MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC"))


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


def record(node: bytes, verdict: int, cls: int, cores: int, fingerprint: bytes, voter_id: bytes,
           tops_x10: int = 0, pct_bps: int = 0):
    """Marks.record(...). tops_x10 / pct_bps from encode_perf. Returns the tx hash (0x hex), or None in dry-run.
    Raises ChainError on failure."""
    args = (node, verdict, cls, cores, fingerprint, voter_id, tops_x10, pct_bps)
    addr, key, rpc = (os.environ.get(k) for k in ("MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC"))
    if not (addr and key and rpc):
        DRY_RUN_CALLS.append(args)
        log.info("dry-run Marks.record node=0x%s verdict=%d cls=%d cores=%d fp=0x%s voter=0x%s tops_x10=%d pct_bps=%d",
                 node.hex(), verdict, cls, cores, fingerprint.hex(), voter_id.hex(), tops_x10, pct_bps)
        return None
    data = SELECTOR + encode(ARG_TYPES, list(args))
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
