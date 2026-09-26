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
              monkeypatch=None, health=None, raw=False):
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
        "rows": rows, **({"health": health} if health is not None else {})})
    if raw:
        return rv
    assert rv.status_code == 200, rv.text
    return rv.json()


def test_honest_cpu_run_passes_and_publishes():
    r = run_check()
    assert r["verdict"] == "pass" and r["reasons"] == [] and r["measured_class"] == 1
    assert r["published"] is True and r["tx"] is None  # dry-run
    assert r["gpu_name"] == f"{appmod.gpu_label('GPU-1')}.cloud-b.waterline.eth"
    cloud, gpu, verdict, cls, cores, fp, gv, pv, *_ = chain.DRY_RUN_CALLS[-1]
    # Marks derives node = keccak(keccak(namehash(waterline.eth), cloud), gpu): the same as the name's namehash
    pnode = chain.keccak(chain.namehash("waterline.eth") + cloud)
    assert "0x" + chain.keccak(pnode + gpu).hex() == r["node"] and (verdict, cls, cores) == (1, 1, 132)
    assert gv == pv == chain.ZERO  # a pass carries no voter


def test_lazy_prover_fails():
    r = run_check(lazy=True)
    assert r["verdict"] == "fail" and not r["published"] and not chain.DRY_RUN_CALLS
    assert any("fingerprint" in x for x in r["reasons"])


def test_single_patched_row_fails():
    r = run_check(patch_row=True)
    assert r["verdict"] == "fail" and len(r["reasons"]) >= 1


def test_right_chip_too_slow_is_degraded_not_fail(monkeypatch):
    r = run_check(slow=6.0, monkeypatch=monkeypatch)  # heat can slow a chip; it can't change its core count
    assert r["verdict"] == "degraded" and r["reasons"][0].startswith("Answer locked in after 6.")
    assert r["reasons"][0].endswith("; the deadline was 5.0 s.")
    assert r["published"] is True and chain.DRY_RUN_CALLS[-1][2] == appmod.DEGRADED  # no human needed
    assert chain.DRY_RUN_CALLS[-1][6] == chain.ZERO
    g = next(x for x in client.get("/api/gpus").json()["gpus"] if x["node"] == r["node"])
    assert g["status"] == "degraded" and g["active"] == 0


def test_wrong_chip_that_is_also_slow_is_fail(monkeypatch):
    r = run_check(uuid="GPU-SLOW-A100", probes=A100, slow=6.0, monkeypatch=monkeypatch)
    assert r["verdict"] == "fail" and not r["published"]


def test_class_mismatch_fails():
    r = run_check(claimed=1, probes=A100)  # an A100 sold as H100 SXM
    assert r["verdict"] == "fail" and r["measured_class"] == 3
    assert r["reasons"] == ["Measured as A100 SXM4 80GB (108 SMs, no FP8, 1.90 TB/s), listed as H100 SXM."]


def test_throughput_against_listed_class():
    r = client.get(f"/api/reports/{run_check()['report_id']}").json()
    assert r["ops_total"] == 2 * 64**3 * 4 and r["spec_tops"] == 1979
    assert r["effective_tops"] == pytest.approx(r["ops_total"] / r["elapsed_s"] / 1e12, rel=1e-3)
    assert r["pct_of_spec"] == pytest.approx(100 * r["effective_tops"] / 1979, rel=1e-3)
    a = client.get(f"/api/reports/{run_check(uuid='GPU-T', claimed=3, probes=A100)['report_id']}").json()
    assert a["spec_tops"] == 624 and a["verdict"] == "pass"


def test_health_is_stored_but_never_decides():
    hr = {"source": "simulated", "grade": "trust me", "burn": {"tflops": {"mean": 1.0}}, "memory": {"x": 99}}
    r = run_check(uuid="GPU-H", health=hr)
    assert r["verdict"] == "pass"  # a scary health report does not change the verdict
    d = client.get(f"/api/reports/{r['report_id']}").json()
    assert d["health"] == hr | {"grade": "reported by the machine"}  # the API stamps the grade
    assert client.get(f"/api/reports/{run_check(uuid='GPU-H0')['report_id']}").json()["health"] is None


def test_oversized_health_rejected():
    r = run_check(uuid="GPU-HB", health={"blob": "x" * 300_000}, raw=True)
    assert r.status_code == 413 and "KB" in r.json()["error"]


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
    cloud, gpu, verdict, cls, cores, fp, gv, pv, *_ = chain.DRY_RUN_CALLS[-1]
    assert (verdict, cls, cores) == (2, 3, 108)
    node, pnode = bytes.fromhex(rep["node"][2:]), chain.namehash("cloud-b.waterline.eth")
    assert gv == world.voter_id("human-1", node) and pv == world.voter_id("human-1", pnode)


def test_one_human_counts_once_per_provider():
    tok = login()
    for uuid in ("GPU-P1", "GPU-P2"):  # a heavy renter reports two bad pods from one cloud
        rep = run_check(uuid=uuid, claimed=1, probes=A100)
        assert approve(rep["report_id"], tok, "approve").json()["published"] is True
    pv = {c[7] for c in chain.DRY_RUN_CALLS}
    assert len(pv) == 1 and len({c[6] for c in chain.DRY_RUN_CALLS}) == 2  # one provider voice, two GPU voices
    p = next(x for x in client.get("/api/providers").json()["providers"] if x["name"] == "cloud-b.waterline.eth")
    assert p["humans"] == 1 and p["fails"] >= 2


def test_two_passes_after_failure_recover():
    rep = run_check(uuid="GPU-R", claimed=1, probes=A100)
    approve(rep["report_id"], login(), "approve")
    run_check(uuid="GPU-R")
    run_check(uuid="GPU-R")
    g = next(x for x in client.get("/api/gpus").json()["gpus"] if x["node"] == rep["node"])
    assert g["status"] == "recovered" and g["humans"] == 1


def test_fingerprint_change_is_observed_not_judged():
    first = run_check(uuid="GPU-F")
    second = run_check(uuid="GPU-F", probes=A100)
    reps = {r["report_id"]: r for r in client.get("/api/reports").json()}
    full = client.get(f"/api/reports/{second['report_id']}").json()
    assert full["fingerprint_changed"] is True and first["report_id"] in reps


def test_deny_and_expired_publish_nothing():
    rep = run_check(uuid="GPU-B", claimed=1, probes=A100)
    tok = login()
    assert approve(rep["report_id"], tok, "deny").json() == {"status": "denied", "published": False}
    assert approve(rep["report_id"], tok, "expire").json() == {"status": "expired", "published": False}
    assert not chain.DRY_RUN_CALLS


def test_other_human_approval_refused():
    rep = run_check(uuid="GPU-C", claimed=1, probes=A100)
    r = approve(rep["report_id"], login("human-1"), "approve", sub="human-2").json()
    assert r["published"] is False and "not the one logged in" in r["status_text"]
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
    r = approve(second["report_id"], tok, "approve")  # refused before World is even asked
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
