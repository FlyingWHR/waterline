"""Writes through MultiBaas (compose -> sign locally -> submit) and the MultiBaas webhook listener."""
import hashlib
import hmac
import json
import time

import httpx
import pytest
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from fastapi.testclient import TestClient
from hexbytes import HexBytes

from api import app as appmod, chain
from test_api import run_check

client = TestClient(appmod.app)
KEY = "0x" + "11" * 32
REPORTER = Account.from_key(KEY).address
MARKS = "0x" + "4d" * 20
ARGS = (b"\1" * 32, 1, 1, 132, b"\2" * 32, b"\3" * 32, 14105, 7125, b"\4" * 32)


@pytest.fixture
def mb(monkeypatch):
    """Fake MultiBaas at https://mb.test. `seen` records the calls; set `fail` to a path to make it answer 500."""
    monkeypatch.setenv("MB_URL", "https://mb.test/")
    monkeypatch.setenv("MB_API_KEY", "admin-key")
    monkeypatch.setenv("REPORTER_KEY", KEY)
    monkeypatch.setenv("MARKS_ADDRESS", MARKS)
    fake = {"seen": [], "fail": None, "data": None}

    def request(method, url, json=None, headers=None, timeout=None):
        path = url.removeprefix("https://mb.test/api/v0")
        fake["seen"].append((method, path, json, headers))
        if fake["fail"] and fake["fail"] in path:
            return httpx.Response(500, json={"status": 500, "message": "execution reverted: EACUnauthorizedAccountRoles"})
        if path.endswith("/methods/record"):
            args = [bytes.fromhex(x[2:]) if x.startswith("0x") else int(x) for x in json["args"]]
            data = fake["data"] or "0x" + (chain.SELECTOR + chain.encode(chain.ARG_TYPES, args)).hex()
            return httpx.Response(200, json={"status": 200, "message": "success", "result": {
                "kind": "TransactionToSignResponse", "submitted": False,
                "tx": {"type": 2, "nonce": 7, "gas": 90000, "gasFeeCap": "3000000000", "gasTipCap": "1000000",
                       "from": REPORTER, "to": MARKS, "value": "0", "data": data}}})
        if path == "/chains/ethereum/status":
            return httpx.Response(200, json={"status": 200, "result": {"chainID": 11155111, "blockNumber": 1}})
        if path == "/chains/ethereum/transactions/submit":
            return httpx.Response(200, json={"status": 200, "result": {"tx": {"hash": "0xfromMB"}}})
        return httpx.Response(404, json={"status": 404, "message": "not found"})
    monkeypatch.setattr(chain.httpx, "request", request)
    return fake


def test_record_via_multibaas_signs_and_submits(mb):
    assert chain.write_path() == "multibaas"
    tx = chain.record(*ARGS)
    (_, compose, body, hdr), (_, status, *_), (_, submit, sub, _) = mb["seen"]
    assert compose == "/chains/ethereum/addresses/marks/contracts/marks/methods/record"
    assert status == "/chains/ethereum/status" and submit == "/chains/ethereum/transactions/submit"
    assert hdr["Authorization"] == "Bearer admin-key"
    assert body == {"args": ["0x" + "01" * 32, "1", "1", "132", "0x" + "02" * 32, "0x" + "03" * 32, "14105", "7125",
                             "0x" + "04" * 32],
                    "from": REPORTER, "formatInts": "as_strings"}
    raw = bytes.fromhex(sub["signedTx"][2:])
    assert raw[0] == 2  # EIP-1559
    t = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
    assert (t["chainId"], t["nonce"], t["gas"], t["maxFeePerGas"], t["maxPriorityFeePerGas"]) == \
        (11155111, 7, 90000, 3000000000, 1000000)
    assert Account.recover_transaction(raw) == REPORTER
    assert tx == "0x" + bytes(Account.from_key(KEY).sign_transaction({
        "type": 2, "chainId": 11155111, "nonce": 7, "to": chain.to_checksum_address(MARKS), "value": 0, "gas": 90000,
        "maxFeePerGas": 3000000000, "maxPriorityFeePerGas": 1000000,
        "data": chain.SELECTOR + chain.encode(chain.ARG_TYPES, list(ARGS))}).hash).hex()


def test_multibaas_errors_are_plain_and_do_not_publish(mb, monkeypatch):
    mb["fail"] = "/methods/record"
    with pytest.raises(chain.ChainError, match="EACUnauthorizedAccountRoles"):
        chain.record(*ARGS)
    mb["fail"], mb["data"] = None, "0xdeadbeef"  # MultiBaas composed something else: never sign it
    with pytest.raises(chain.ChainError, match="different transaction"):
        chain.record(*ARGS)
    assert not any(p.endswith("/submit") for _, p, *_ in mb["seen"])
    mb["data"], mb["fail"] = None, "/submit"
    r = run_check()
    assert r["verdict"] == "pass" and r["published"] is False and r["tx"] is None


def test_publish_result_and_health_report_the_write_path(mb):
    r = run_check()
    assert r["published"] is True and r["via"] == "multibaas" and r["tx"].startswith("0x")
    assert client.get("/api/health").json()["chain"]["write_path"] == "multibaas"


def test_dry_run_path_is_reported():
    assert run_check()["via"] == "dry-run"
    assert client.get("/api/health").json()["chain"]["write_path"] == "dry-run"


# ---- webhook ------------------------------------------------------------------------------------------------
SECRET = "whsec-test"


def hook(events, ts=None, secret=SECRET):
    raw = json.dumps(events).encode()
    ts = str(int(time.time()) if ts is None else ts)
    sig = hmac.new(secret.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/multibaas", content=raw,
                       headers={"X-MultiBaas-Signature": sig, "X-MultiBaas-Timestamp": ts,
                                "Content-Type": "application/json"})


def reported(node, tx, label="marks", kind_key="event", name="Reported"):
    return {"id": "e1", kind_key: "event.emitted", "data": {
        "triggeredAt": "2026-09-26T12:00:00+09:00",
        "event": {"name": name, "signature": "Reported(...)", "inputs": [{"name": "node", "value": node,
                                                                           "hashed": False, "type": "bytes32"}],
                  "contract": {"address": "0x" + "99" * 20, "addressLabel": label, "name": "Marks", "label": "marks"}},
        "transaction": {"txHash": tx, "blockNumber": 10}}}


@pytest.fixture
def published(monkeypatch):
    monkeypatch.setenv("MB_WEBHOOK_SECRET", SECRET)
    r = run_check(uuid="GPU-hook")
    rep = appmod.store.get(f"report:{r['report_id']}") | {"tx": "0xABC123"}  # dry-run has no tx; give it one
    appmod.store.put(f"report:{r['report_id']}", rep, 60)
    return rep


@pytest.mark.parametrize("kind_key", ["event", "eventType"])
def test_webhook_marks_report_indexed(published, kind_key):
    r = hook([{"event": "transaction.included", "data": {}}, reported(published["node"], "0xabc123", kind_key=kind_key)])
    assert r.status_code == 200 and r.json()["indexed"] == 1
    row = next(x for x in client.get("/api/reports").json() if x["report_id"] == published["report_id"])
    assert row["indexed"] is True and row["indexed_at"]
    assert client.get(f"/api/reports/{published['report_id']}").json()["indexed"] is True


def test_webhook_rejects_bad_or_stale_signatures(published):
    ev = [reported(published["node"], "0xabc123")]
    assert hook(ev, secret="wrong").status_code == 401
    assert hook(ev, ts=int(time.time()) - 301).status_code == 401
    assert client.post("/api/webhooks/multibaas", json=ev).status_code == 401
    assert client.get(f"/api/reports/{published['report_id']}").json()["indexed"] is False


def test_webhook_ignores_other_events(published):
    node = published["node"]
    r = hook([reported(node, "0xabc123", name="ReporterChanged"), reported(node, "0xabc123", label="other"),
              reported(node, "0xfff"), {"weird": 1}, "not an object"])
    assert r.status_code == 200 and r.json()["indexed"] == 0
    assert client.get(f"/api/reports/{published['report_id']}").json()["indexed"] is False
