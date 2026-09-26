"""reportHash: keccak256 of the canonical report, frozen at the verdict, sent to Marks, served as evidence."""
import json

from eth_utils import keccak

from api import app as appmod, chain
from test_api import A100, approve, client, login, post, run_check


def evidence(rid):
    r = client.get(f"/api/reports/{rid}/evidence")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    return r


def test_pass_sends_the_hash_and_evidence_bytes_match():
    r = run_check(uuid="GPU-EV1")
    rep = client.get(f"/api/reports/{r['report_id']}").json()
    h = rep["report_hash"]
    assert r["report_hash"] == h and chain.DRY_RUN_CALLS[-1][8] == bytes.fromhex(h[2:])
    ev = evidence(r["report_id"])
    assert "0x" + keccak(ev.content).hex() == h == ev.headers["X-Waterline-Report-Hash"]
    body = json.loads(ev.content)
    assert ev.content == json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert not {"published", "tx", "via", "indexed", "status_text", "report_hash"} & body.keys()


def test_hash_is_stable_across_indexing_and_approval():
    r = run_check(uuid="GPU-EV2", claimed=1, probes=A100)
    before = client.get(f"/api/reports/{r['report_id']}").json()
    assert approve(r["report_id"], login("human-ev"), "approve", sub="human-ev").json()["published"] is True
    rep = appmod.store.get(f"report:{r['report_id']}")
    appmod.store.put(f"report:{r['report_id']}", rep | {"indexed": True, "indexed_at": 1, "tx": "0xabc"}, 60)
    after = client.get(f"/api/reports/{r['report_id']}").json()
    assert after["published"] and after["report_hash"] == before["report_hash"]
    assert chain.DRY_RUN_CALLS[-1][8] == bytes.fromhex(before["report_hash"][2:])  # the approved fail carries it
    assert "0x" + keccak(evidence(r["report_id"]).content).hex() == before["report_hash"]


def test_edited_evidence_no_longer_matches():
    r = run_check(uuid="GPU-EV3")
    rep = appmod.store.get(f"report:{r['report_id']}")
    appmod.store.put(f"report:{r['report_id']}", rep | {"reasons": ["nothing to see"]}, 60)
    ev = evidence(r["report_id"])
    assert "0x" + keccak(ev.content).hex() != ev.headers["X-Waterline-Report-Hash"] == rep["report_hash"]


def test_start_defaults_and_fixed_deadline_pins_the_work(monkeypatch):
    s = post("/api/check/start", {"cloud": "cloud-b", "uuid": "GPU-x", "claimed_class": 1}).json()
    assert (s["n"], s["steps"]) == (16384, 100)  # what prover.run on a GPU gets when it sends neither
    monkeypatch.setenv("DEADLINES", '{"1": 9.5}')
    monkeypatch.setenv("CHECK_STEPS", "300")
    monkeypatch.setenv("ALLOW_CLIENT_SIZES", "0")  # production: the API decides the exam size
    small = post("/api/check/start", {"cloud": "cloud-b", "uuid": "GPU-x", "claimed_class": 1, "n": 64, "steps": 1})
    assert small.status_code == 200 and (small.json()["n"], small.json()["steps"]) == (16384, 300)
    s = post("/api/check/start", {"cloud": "cloud-b", "uuid": "GPU-x", "claimed_class": 1}).json()
    assert (s["steps"], s["deadline_s"]) == (300, 9.5)

