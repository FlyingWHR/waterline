"""World ID 4.0 through IDKit: RP-signed requests, proofs checked by World's Developer Portal, our own agent token,
voter ids.

A World session is one proof request. The API signs it (WORLD_SIGNING_KEY, the signer registered for WORLD_RP_ID),
the panel page runs IDKit (QR -> World ID app) and posts the result back, and the API forwards the proof to
POST developer.world.org/api/v4/verify/{rp_id}. Only a proof the portal accepts, for our nonce, action and signal,
counts as approval. The client can only ever lower the outcome (a posted rejection means denied).
The human's id for a session is the proof's nullifier (per action: World refuses a replay of it).
WORLD_MOCK=1 swaps World for a local stand-in whose decision comes from MOCK["decision"] (test hook) or
WORLD_MOCK_DECISION (approve|deny|expire|pending, default approve).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from .store import store

AGENT_TOKEN_TTL = 7 * 24 * 3600
MOCK = {"decision": None, "sub": None}  # test hook
SESSION_TTL = 600
VERIFY_URL = "https://developer.world.org/api/v4/verify/{rp_id}"
REJECTED = ("user_rejected", "verification_rejected")


def _env(k, default=None):
    v = os.environ.get(k, default)
    if v is None:
        raise RuntimeError(f"{k} is not set")
    return v


def mock_on():
    return os.environ.get("WORLD_MOCK") == "1"


def environment():
    return os.environ.get("WORLD_ENV", "sandbox")  # sandbox | staging | production


def configured():
    return all(os.environ.get(k) for k in ("WORLD_APP_ID", "WORLD_RP_ID", "WORLD_SIGNING_KEY"))


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---- agent token: base64url(json{sub,exp}) "." base64url(HMAC-SHA256) -------------------------------------
def make_agent_token(sub: str) -> str:
    body = _b64(json.dumps({"sub": sub, "exp": int(time.time()) + AGENT_TOKEN_TTL}).encode())
    sig = hmac.new(_env("AGENT_TOKEN_SECRET").encode(), body.encode(), hashlib.sha256).digest()
    return body + "." + _b64(sig)


def agent_sub(token: str):
    """The sub bound in a valid, unexpired agent token, else None."""
    try:
        body, sig = token.split(".")
        want = hmac.new(_env("AGENT_TOKEN_SECRET").encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(sig)):
            return None
        claims = json.loads(_unb64(body))
        return claims["sub"] if claims["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def voter_id(sub: str, node: bytes) -> bytes:
    """HMAC_SHA256(VOTER_SECRET, utf8(sub) || node[32 bytes])."""
    return hmac.new(_env("VOTER_SECRET").encode(), sub.encode() + node, hashlib.sha256).digest()


# ---- IDKit sessions --------------------------------------------------------------------------------------
class Unavailable(Exception):
    """World returned 5xx / was unreachable. Never treated as approval."""


def hash_to_field(b: bytes) -> bytes:
    return (int.from_bytes(keccak(b), "big") >> 8).to_bytes(32, "big")


def sign_request(key_hex: str, action: str, created_at: int, ttl: int = 300, rand: bytes = None) -> dict:
    """World's RP signature: EIP-191 over 0x01 || nonce || created_at || expires_at || hash_to_field(action)."""
    nonce = hash_to_field(rand if rand is not None else secrets.token_bytes(32))
    expires_at = created_at + ttl
    msg = b"\x01" + nonce + created_at.to_bytes(8, "big") + expires_at.to_bytes(8, "big") + hash_to_field(action.encode())
    sig = Account.sign_message(encode_defunct(primitive=msg), key_hex).signature
    return {"sig": "0x" + bytes(sig).hex(), "nonce": "0x" + nonce.hex(), "created_at": created_at, "expires_at": expires_at}


def device_start(action: str, what: str) -> dict:
    """Open a proof request for `action`. -> {device_code, user_code, verification_uri_complete, expires_in, interval}."""
    wid = secrets.token_urlsafe(12)
    link = f"{os.environ.get('API_URL', '').rstrip('/')}/#/world/{wid}"
    if mock_on():
        return {"device_code": "mock-" + wid, "user_code": wid[:6], "expires_in": 600, "interval": 0,
                "verification_uri_complete": link}
    if not configured():
        raise Unavailable()
    rp = sign_request(_env("WORLD_SIGNING_KEY"), action, int(time.time()), SESSION_TTL)
    store.put(f"wsess:{wid}", {"action": action, "what": what, "signal": wid, "rp": rp}, SESSION_TTL + 60)
    return {"device_code": wid, "user_code": wid[:6], "expires_in": SESSION_TTL, "interval": 0,
            "verification_uri_complete": link}


def session_public(wid: str):
    """What the panel page needs to run IDKit for a session (nothing secret), or None."""
    s = store.get(f"wsess:{wid}")
    if not s or "result" in s:
        return None
    rp = s["rp"]
    return {"app_id": _env("WORLD_APP_ID"), "action": s["action"], "what": s["what"], "signal": s["signal"],
            "environment": environment(), "preset": os.environ.get("WORLD_PRESET", "selfieCheck"),
            "rp_context": {"rp_id": _env("WORLD_RP_ID"), "nonce": rp["nonce"], "created_at": rp["created_at"],
                           "expires_at": rp["expires_at"], "signature": rp["sig"]}}


def _nullifier(n) -> str:
    return f"{int(n, 16) if isinstance(n, str) else int(n):064x}"


def session_result(wid: str, result: dict = None, error: str = None) -> str:
    """The panel posts IDKit's outcome. Only the first answer counts. -> the session's status after it."""
    key = f"wsess:{wid}"
    s = store.get(key)
    if not s:
        return "expired"
    if "result" in s:
        return s["result"]["status"]
    if result is None:
        out = {"status": "denied" if error in REJECTED else "failed", "error": str(error or "no result")[:64]}
    else:
        out = _verify(s, result)
    s["result"] = out
    store.put(key, s, SESSION_TTL + 60)
    return out["status"]


def _verify(s: dict, result: dict) -> dict:
    """Portal check plus our own binding: same nonce, action, environment, and the signal we asked for."""
    items = result.get("responses") or []
    if (result.get("nonce") != s["rp"]["nonce"] or result.get("action") != s["action"]
            or result.get("environment", "production") != environment() or len(items) != 1
            or int(items[0].get("signal_hash", "0x0"), 16) != int.from_bytes(hash_to_field(s["signal"].encode()), "big")):
        return {"status": "failed", "error": "proof_mismatch"}
    try:
        r = httpx.post(VERIFY_URL.format(rp_id=_env("WORLD_RP_ID")), json=result, timeout=20)
    except httpx.HTTPError:
        raise Unavailable() from None
    if r.status_code >= 500:
        raise Unavailable()
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or body.get("success") is not True or body.get("environment", environment()) != environment():
        return {"status": "failed", "error": str(body.get("code") or r.status_code)[:64]}
    return {"status": "approved", "sub": _nullifier(body.get("nullifier") or items[0]["nullifier"]),
            "auth_time": int(time.time())}


def device_poll(device_code: str):
    """-> (status, claims). status: pending | approved | denied | failed | expired. Claims: {sub, auth_time}."""
    if mock_on():
        decision = MOCK["decision"] or os.environ.get("WORLD_MOCK_DECISION", "approve")
        if decision == "approve":
            return "approved", {"sub": MOCK["sub"] or os.environ.get("WORLD_MOCK_SUB", "mock-human-1"),
                                "auth_time": int(time.time())}
        return {"deny": "denied", "expire": "expired"}.get(decision, "pending"), None
    s = store.get(f"wsess:{device_code}")
    if not s:
        return "expired", None
    if "result" not in s:
        return "pending", None
    r = s["result"]
    return r["status"], ({"sub": r["sub"], "auth_time": r["auth_time"]} if r["status"] == "approved" else None)
