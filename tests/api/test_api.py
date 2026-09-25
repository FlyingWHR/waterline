import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import app as appmod, chain, world
from core.challenge import Params, fingerprint_rows, leaf_hash, merkle_root, product

client = TestClient(appmod.app)
H100 = {"sms": 132, "fp8": True, "clock_ghz": 1.98, "bw_tbs": 3.2, "fingerprint": "0x" + "ab" * 32}
A100 = {"sms": 108, "fp8": False, "clock_ghz": 1.41, "bw_tbs": 1.9, "fingerprint": "0x" + "cd" * 32}


@pytest.fixture(autouse=True)
def reset():
    chain.DRY_RUN_CALLS.clear()
    world.MOCK.update(decision=None, sub=None)


def post(path, body=None):
    return client.post(path, json=body or {})


def run_check(uuid="GPU-1", claimed=1, probes=H100, n=64, steps=4, lazy=False, patch_row=False, slow=0.0,
              monkeypatch=None):
    st = post("/api/check/start", {"cloud": "cloud-b", "uuid": uuid, "claimed_class": claimed, "n": n, "steps": steps})
    assert st.status_code == 200, st.text
    s = st.json()
    p = Params(int(s["seed"]), n, steps)
    assert str(p.fp_key) == s["fp_key"]
    rng = np.random.default_rng(1)
    C = [product(p, i) for i in range(steps)]
    fps = [rng.integers(0, 2**63, n, dtype=np.uint64) if lazy else fingerprint_rows(c, p.fp_key) for c in C]
    leaves = [leaf_hash(f) for f in fps]
    if slow and monkeypatch:
        t = appmod.now()
        monkeypatch.setattr(appmod, "now", lambda: t + slow)
    cm = post("/api/check/commit", {"session_id": s["session_id"], "root": merkle_root(leaves), "probes": probes})
    assert cm.status_code == 200, cm.text
    samples = cm.json()["samples"]
    assert len(samples) == 8
    rows = {f"{st_}:{r}": C[st_][r].tolist() for st_, r in samples}
    if patch_row:  # change one entry of one revealed row, keep everything else honest
        k = next(iter(rows))
        rows[k][0] += 1
    rv = post("/api/check/reveal", {
        "session_id": s["session_id"],
        "fingerprints": {str(st_): [str(int(x)) for x in fps[st_]] for st_ in {st_ for st_, _ in samples}},
        "leaf_hashes": {str(i): h for i, h in enumerate(leaves)},
        "rows": rows})
    assert rv.status_code == 200, rv.text
    return rv.json()


def test_honest_cpu_run_passes_and_publishes():
    r = run_check()
    assert r["verdict"] == "pass" and r["reasons"] == [] and r["measured_class"] == 1
    assert r["published"] is True and r["tx"] is None  # dry-run
    assert r["gpu_name"] == f"{appmod.gpu_label('GPU-1')}.cloud-b.waterline.eth"
    node, verdict, cls, cores, fp, voter = chain.DRY_RUN_CALLS[-1]
    assert "0x" + node.hex() == r["node"] and (verdict, cls, cores) == (1, 1, 132)
    assert voter == chain.keccak(text=r["report_id"])


def test_lazy_prover_fails():
    r = run_check(lazy=True)
    assert r["verdict"] == "fail" and not r["published"] and not chain.DRY_RUN_CALLS
    assert any("fingerprint" in x for x in r["reasons"])


def test_single_patched_row_fails():
    r = run_check(patch_row=True)
    assert r["verdict"] == "fail" and len(r["reasons"]) >= 1


def test_missed_deadline_fails(monkeypatch):
    r = run_check(slow=6.0, monkeypatch=monkeypatch)
    assert r["verdict"] == "fail" and "deadline" in r["reasons"][0]


def test_class_mismatch_fails():
    r = run_check(claimed=1, probes=A100)  # an A100 sold as H100 SXM
    assert r["verdict"] == "fail" and r["measured_class"] == 3 and len(r["reasons"]) == 1


def test_commit_only_once():
    s = post("/api/check/start", {"cloud": "c", "uuid": "u", "claimed_class": 1, "n": 64, "steps": 2}).json()
    body = {"session_id": s["session_id"], "root": "00" * 32, "probes": H100}
    assert post("/api/check/commit", body).status_code == 200
    r = post("/api/check/commit", body)
    assert r.status_code == 409 and "error" in r.json()


def test_namehash_matches_ensip1():
    assert chain.namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"


def login(sub="human-1"):
    world.MOCK.update(decision="approve", sub=sub)
    d = post("/api/world/login/start").json()
    r = post("/api/world/login/poll", {"device_id": d["device_id"]}).json()
    assert r["status"] == "approved"
    return r["agent_token"]


def approve(report_id, token, decision, sub="human-1"):
    world.MOCK.update(decision=decision, sub=sub)
    d = post("/api/report/approve/start", {"report_id": report_id, "agent_token": token})
    if d.status_code != 200:
        return d
    return post("/api/report/approve/poll", {"device_id": d.json()["device_id"]})


def test_approve_publishes_fail():
    rep = run_check(uuid="GPU-A", claimed=1, probes=A100)
    r = approve(rep["report_id"], login(), "approve").json()
    assert r["status"] == "approved" and r["published"] is True
    node, verdict, cls, cores, fp, voter = chain.DRY_RUN_CALLS[-1]
    assert (verdict, cls, cores) == (2, 3, 108)
    assert voter == world.voter_id("human-1", node)


def test_deny_and_expired_publish_nothing():
    rep = run_check(uuid="GPU-B", claimed=1, probes=A100)
    tok = login()
    assert approve(rep["report_id"], tok, "deny").json() == {"status": "denied", "published": False}
    assert approve(rep["report_id"], tok, "expire").json() == {"status": "expired", "published": False}
    assert not chain.DRY_RUN_CALLS


def test_other_human_approval_refused():
    rep = run_check(uuid="GPU-C", claimed=1, probes=A100)
    r = approve(rep["report_id"], login("human-1"), "approve", sub="human-2").json()
    assert r["published"] is False and "not the logged-in human" in r["status_text"]
    assert not chain.DRY_RUN_CALLS


def test_stale_auth_time_refused(monkeypatch):
    rep = run_check(uuid="GPU-G", claimed=1, probes=A100)
    tok = login()
    old = appmod.now() - 300
    monkeypatch.setattr(world, "device_poll", lambda _: ("approved", {"sub": "human-1", "auth_time": old}))
    r = approve(rep["report_id"], tok, "approve").json()
    assert r["published"] is False and "not fresh" in r["status_text"] and not chain.DRY_RUN_CALLS


def test_double_vote_same_human_same_gpu_rejected():
    tok = login()
    first = run_check(uuid="GPU-D", claimed=1, probes=A100)
    assert approve(first["report_id"], tok, "approve").json()["published"] is True
    second = run_check(uuid="GPU-D", claimed=1, probes=A100)  # same GPU, new failed check, same human
    r = approve(second["report_id"], tok, "approve")
    assert r.status_code == 409 and "already reported" in r.json()["error"]
    assert len(chain.DRY_RUN_CALLS) == 1


def test_bad_agent_token_rejected():
    rep = run_check(uuid="GPU-E", claimed=1, probes=A100)
    tok = login()
    r = approve(rep["report_id"], tok[:-2] + ("AA" if not tok.endswith("AA") else "BB"), "approve")
    assert r.status_code == 401


def test_world_unavailable_is_not_approval(monkeypatch):
    rep = run_check(uuid="GPU-F", claimed=1, probes=A100)
    tok = login()
    d = post("/api/report/approve/start", {"report_id": rep["report_id"], "agent_token": tok}).json()

    def down(_):
        raise world.Unavailable()
    monkeypatch.setattr(world, "device_poll", down)
    r = post("/api/report/approve/poll", {"device_id": d["device_id"]})
    assert r.status_code == 503 and not chain.DRY_RUN_CALLS
