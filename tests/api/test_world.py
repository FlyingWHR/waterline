"""World ID 4.0 (IDKit): RP signatures against World's published vectors; proofs only count once the portal and our
own binding (nonce, action, environment, signal) agree. No network: the portal call is faked."""
import pytest

from api import world
from fastapi.testclient import TestClient
from api.app import app

KEY = "0x" + "ab" * 32


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("WORLD_MOCK", "0")
    calls = []

    def portal(url, json, timeout):
        calls.append((url, json))
        body = portal.body
        return type("R", (), {"status_code": portal.status, "headers": {"content-type": "application/json"},
                              "json": lambda self: body})()
    portal.status, portal.body = 200, {"success": True, "environment": "sandbox", "nullifier": "0x" + "0a" * 32}
    monkeypatch.setattr(world.httpx, "post", portal)
    return portal, calls


def test_hash_to_field_vectors():
    assert world.hash_to_field(b"").hex() == "00c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a4"
    assert world.hash_to_field(b"test_signal").hex() == "00c1636e0a961a3045054c4d61374422c31a95846b8442f0927ad2ff1d6112ed"


def test_sign_request_vector():
    r = world.sign_request(KEY, "test-action", 1700000000, 300, rand=bytes(range(32)))
    assert r["nonce"] == "0x008ae1aa597fa146ebd3aa2ceddf360668dea5e526567e92b0321816a4e895bd"
    assert r["sig"] == ("0x05594adb6c1495768a38d523d7d6ee6356b2c31231919198794ed022ade7d08f73753f83bd167067d99c9b969d"
                        "28e9222315837c66af25867b041273a6d5056f1b")
    assert r["expires_at"] == 1700000300


def proof_for(wid, **over):
    s = world.session_public(wid)
    signal_hash = "0x" + world.hash_to_field(s["signal"].encode()).hex()
    return {"protocol_version": "4.0", "nonce": s["rp_context"]["nonce"], "action": s["action"], "environment": "sandbox",
            "responses": [{"identifier": "selfie", "signal_hash": signal_hash, "proof": ["0x1"] * 5,
                           "nullifier": "0x" + "0a" * 32, "issuer_schema_id": 11}]} | over


def test_verified_proof_approves(live):
    _, calls = live
    d = world.device_start("waterline-login-x", "Log in")
    assert d["verification_uri_complete"] == f"https://panel.test/#/world/{d['device_code']}"
    assert world.device_poll(d["device_code"]) == ("pending", None)
    assert world.session_result(d["device_code"], proof_for(d["device_code"])) == "approved"
    status, claims = world.device_poll(d["device_code"])
    assert status == "approved" and claims["sub"] == "0a" * 32
    assert calls[0][0] == "https://developer.world.org/api/v4/verify/rp_test"
    assert world.session_public(d["device_code"]) is None  # finished: nothing more to sign or show


@pytest.mark.parametrize("over", [{"nonce": "0x01"}, {"action": "other"}, {"environment": "production"},
                                  {"responses": []}])
def test_mismatched_proof_fails_without_portal(live, over):
    _, calls = live
    wid = world.device_start("a", "x")["device_code"]
    assert world.session_result(wid, proof_for(wid, **over)) == "failed"
    assert calls == []


def test_wrong_signal_fails(live):
    wid = world.device_start("a", "x")["device_code"]
    p = proof_for(wid)
    p["responses"][0]["signal_hash"] = "0x0"
    assert world.session_result(wid, p) == "failed"


def test_portal_refusal_fails(live):
    portal, _ = live
    portal.status, portal.body = 400, {"success": False, "code": "nullifier_replayed", "detail": "used"}
    wid = world.device_start("a", "x")["device_code"]
    assert world.session_result(wid, proof_for(wid)) == "failed"
    assert world.device_poll(wid) == ("failed", None)


def test_rejection_is_denied_and_first_answer_sticks(live):
    wid = world.device_start("a", "x")["device_code"]
    good = proof_for(wid)
    assert world.session_result(wid, error="user_rejected") == "denied"
    assert world.session_result(wid, good) == "denied"  # a later valid proof can't overturn it
    assert world.device_poll(wid) == ("denied", None)


def test_portal_down_is_not_approval(live, monkeypatch):
    def down(*a, **k):
        raise world.httpx.ConnectError("down")
    monkeypatch.setattr(world.httpx, "post", down)
    wid = world.device_start("a", "x")["device_code"]
    with pytest.raises(world.Unavailable):
        world.session_result(wid, proof_for(wid))
    assert world.device_poll(wid) == ("pending", None)


def test_session_endpoints(live):
    c = TestClient(app)
    wid = world.device_start("a", "x")["device_code"]
    s = c.get(f"/api/world/session/{wid}").json()
    assert s["app_id"] == "app_test" and s["rp_context"]["rp_id"] == "rp_test" and s["environment"] == "sandbox"
    assert "sig" not in s and KEY[2:] not in str(s)
    assert c.post(f"/api/world/session/{wid}/result", json={"error": "user_rejected"}).json() == {"status": "denied"}
    assert c.get(f"/api/world/session/{wid}").status_code == 404
