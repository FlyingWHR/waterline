"""World ID: OIDC device grant (RFC 8628), id_token checks, our own agent token, voter ids.

The World id_token never leaves this module; callers only see the verified claims.
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
import jwt

AGENT_TOKEN_TTL = 7 * 24 * 3600
MOCK = {"decision": None, "sub": None}  # test hook
_jwks = {}


def _env(k, default=None):
    v = os.environ.get(k, default)
    if v is None:
        raise RuntimeError(f"{k} is not set")
    return v


def mock_on():
    return os.environ.get("WORLD_MOCK") == "1"


def issuer():
    return os.environ.get("WORLD_ISSUER", "https://sandbox.auth.world.org").rstrip("/")


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


# ---- device grant ----------------------------------------------------------------------------------------
def _auth():
    return (_env("WORLD_CLIENT_ID"), _env("WORLD_CLIENT_SECRET"))


def device_start() -> dict:
    """-> {device_code, user_code, verification_uri_complete, expires_in, interval}. Raises Unavailable."""
    if mock_on():
        code = secrets.token_hex(4).upper()
        return {"device_code": "mock-" + secrets.token_hex(8), "user_code": code, "expires_in": 600, "interval": 0,
                "verification_uri_complete": f"https://mock.world.invalid/device?user_code={code}"}
    try:
        r = httpx.post(f"{issuer()}/api/v1/device_authorization", auth=_auth(),
                       data={"client_id": _auth()[0], "scope": "openid"}, timeout=15)
    except httpx.HTTPError:
        raise Unavailable() from None
    if r.status_code != 200:
        raise Unavailable()
    d = r.json()
    return {"device_code": d["device_code"], "user_code": d["user_code"], "expires_in": int(d["expires_in"]),
            "interval": int(d.get("interval", 5)),
            "verification_uri_complete": d.get("verification_uri_complete") or d["verification_uri"]}


class Unavailable(Exception):
    """World returned 5xx / was unreachable. Never treated as approval."""


def device_poll(device_code: str):
    """-> (status, claims). status: pending | slow_down | approved | denied | expired. Raises Unavailable."""
    if mock_on():
        decision = MOCK["decision"] or os.environ.get("WORLD_MOCK_DECISION", "approve")
        if decision == "approve":
            return "approved", {"sub": MOCK["sub"] or os.environ.get("WORLD_MOCK_SUB", "mock-human-1"),
                                "auth_time": int(time.time())}
        return {"deny": "denied", "expire": "expired"}.get(decision, "pending"), None
    try:
        r = httpx.post(f"{issuer()}/api/v1/token", auth=_auth(), timeout=15, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": device_code})
    except httpx.HTTPError:
        raise Unavailable() from None
    if r.status_code >= 500:
        raise Unavailable()
    d = r.json()
    if r.status_code == 200:
        return "approved", verify_id_token(d["id_token"])
    err = d.get("error")
    if err in ("authorization_pending", "slow_down"):
        return ("pending" if err == "authorization_pending" else "slow_down"), None
    if err == "access_denied":
        return "denied", None
    return "expired", None  # expired_token, or a spent/invalid device code


def verify_id_token(token: str) -> dict:
    """RS256 signature from the issuer's JWKS; iss, aud=client_id, exp, auth_time, sub required."""
    url = f"{issuer()}/.well-known/jwks.json"
    if url not in _jwks:
        _jwks[url] = jwt.PyJWKClient(url, cache_keys=True)
    key = _jwks[url].get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256"], audience=_env("WORLD_CLIENT_ID"), issuer=issuer(),
                      options={"require": ["exp", "iat", "sub", "auth_time"]}, leeway=10)
