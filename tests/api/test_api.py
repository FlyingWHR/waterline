import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import app as appmod, chain
from core.challenge import Params, fingerprint_rows, leaf_hash, merkle_root, product

client = TestClient(appmod.app)
H100 = {"sms": 132, "fp8": True, "clock_ghz": 1.98, "bw_tbs": 3.2, "fingerprint": "0x" + "ab" * 32}
A100 = {"sms": 108, "fp8": False, "clock_ghz": 1.41, "bw_tbs": 1.9, "fingerprint": "0x" + "cd" * 32}


@pytest.fixture(autouse=True)
def reset():
    chain.DRY_RUN_CALLS.clear()


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
    assert r["published"] is True and chain.DRY_RUN_CALLS[-1][2] == appmod.DEGRADED  # no listing needed
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


def test_a_starved_host_is_reported_but_still_passes():
    from prover import health, host
    r = run_check(uuid="GPU-HOST", health=health.simulated(132, 10) | {"host": host.simulated(starved=True)})
    assert r["verdict"] == "pass" and [f["kind"] for f in r["delivery"]] == ["cpu", "memory", "disk", "network"]
    assert client.get(f"/api/reports/{r['report_id']}").json()["delivery"] == r["delivery"]
    assert run_check(uuid="GPU-HOST2")["delivery"] == []


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


LISTING = "H100 80GB SXM5 · 1x · $2.49/h (test listing)"


def publish(report_id, listing=LISTING, **extra):
    return post("/api/report/publish", {"report_id": report_id, "listing": listing, **extra})


def test_publish_records_fail():
    rep = run_check(uuid="GPU-A", claimed=1, probes=A100)
    assert rep["published"] is False and not chain.DRY_RUN_CALLS  # a failure waits for the listing
    assert publish(rep["report_id"]).json()["published"] is True
    cloud, gpu, verdict, cls, cores, fp, gv, pv, *_ = chain.DRY_RUN_CALLS[-1]
    assert (verdict, cls, cores) == (2, 3, 108) and gv != pv and gv != bytes(32)
    r = publish(rep["report_id"])
    assert r.status_code == 409 and "already published" in r.json()["error"]
    assert len(chain.DRY_RUN_CALLS) == 1


def test_each_failure_report_is_its_own_voter():
    first, second = (run_check(uuid="GPU-D", claimed=1, probes=A100) for _ in range(2))
    for r in (first, second):
        assert publish(r["report_id"]).json()["published"] is True
    assert len({c[6] for c in chain.DRY_RUN_CALLS}) == 2 and len({c[7] for c in chain.DRY_RUN_CALLS}) == 2
    g = next(x for x in client.get("/api/gpus").json()["gpus"] if x["node"] == first["node"])
    assert g["status"] == "failed"
    p = next(x for x in client.get("/api/providers").json()["providers"] if x["name"] == "cloud-b.waterline.eth")
    assert p["humans"] == p["fails"] >= 2


def test_two_passes_after_failure_recover():
    rep = run_check(uuid="GPU-R", claimed=1, probes=A100)
    publish(rep["report_id"])
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


def test_reporting_needs_the_listing():
    rep = run_check(uuid="GPU-L", claimed=1, probes=A100)
    r = post("/api/report/publish", {"report_id": rep["report_id"]})
    assert r.status_code == 422 and "Paste the listing" in r.json()["error"]
    assert publish(rep["report_id"]).json()["published"] is True
    full = client.get(f"/api/reports/{rep['report_id']}").json()
    assert full["listing"].startswith("H100 80GB SXM5")
    ev = client.get(f"/api/reports/{rep['report_id']}/evidence").json()
    assert "listing" not in ev and "provider_voter" not in ev  # added after the verdict: outside the frozen hash


def test_listing_that_contradicts_the_claim_needs_report_anyway():
    rep = run_check(uuid="GPU-J", claimed=1, probes=A100)  # reported "as H100"
    r = publish(rep["report_id"], "1x NVIDIA A100 80GB PCIe · $1.19/h")
    assert r.status_code == 409 and "reads as A100" in r.json()["error"] and "report anyway" in r.json()["error"]
    assert not chain.DRY_RUN_CALLS
    assert publish(rep["report_id"], "1x NVIDIA A100 80GB PCIe · $1.19/h", report_anyway=True).json()["published"] is True
    full = client.get(f"/api/reports/{rep['report_id']}").json()
    assert full["listing_reads_as"] == {"class": 3, "confidence": 0.95, "source": "rules", "contradicts": True}


def test_ens_report_hash_finds_the_check():
    r = run_check(uuid="GPU-hash")
    found = client.get(f"/api/reports/by-hash/{r['report_hash'].upper().replace('0X', '0x')}")
    assert found.status_code == 200 and found.json()["report_id"] == r["report_id"]
    assert client.get("/api/reports/by-hash/0x" + "00" * 32).status_code == 404


def test_known_providers_are_advisory():
    from core import providers
    assert providers.listed("lambda") and not providers.listed("cloud-b")
    assert providers.suggest("run-pod") == "runpod" and providers.suggest("Vast-AI") == "vastai"
    assert providers.suggest("cloud-b") is None
    assert any(p["slug"] == "coreweave" for p in client.get("/api/providers/known").json())
    run_check(uuid="GPU-unlisted")  # an unlisted name is still recorded, just marked
    rows = client.get("/api/providers").json()["providers"]
    assert any(p["name"] == "cloud-b.waterline.eth" and p["listed"] is False for p in rows)


def test_multibaas_bytes32_as_byte_list():
    from agent.history import hex32
    raw = "[253, 55, 131, 8, 229, 95, 207, 107, 90, 95, 70, 59, 32, 42, 22, 234, 237, 137, 25, 253, 70, 243, 172, 53, 229, 104, 8, 223, 81, 116, 38, 231]"
    want = "0xfd378308e55fcf6b5a5f463b202a16eaed8919fd46f3ac35e56808df517426e7"
    assert appmod._hex0x(raw) == hex32(raw) == want and appmod._hex0x(want[2:]) == want


def test_gpu_label_is_the_nvidia_uuid_prefix():
    assert appmod.gpu_label("GPU-6f3c2a1b-9d0e-4c1a-8b2f-0123456789ab") == "gpu-6f3c2a1b"
    assert appmod.gpu_label("GPU-1").startswith("gpu-") and len(appmod.gpu_label("GPU-1")) == 12


def test_suspicious_failures_are_flagged_not_blocked():
    run_check(uuid="GPU-FLAG", claimed=3, probes=A100)  # the card passes as a listed A100
    rep = run_check(uuid="GPU-FLAG", claimed=1, probes=A100)  # then someone lists the same card as an H100
    assert publish(rep["report_id"], "apprcedsd").json()["published"] is True  # never blocked: flagged
    kinds = {f["kind"] for f in client.get(f"/api/reports/{rep['report_id']}").json()["flags"]}
    assert kinds == {"listing_no_gpu", "card_listed_twice"}
    row = next(r for r in client.get("/api/reports").json() if r["report_id"] == rep["report_id"])
    assert len(row["flags"]) == 2


def test_each_published_check_is_named_under_its_gpu(monkeypatch):
    monkeypatch.setenv("MARKS_ADDRESS", "0x" + "ab" * 20)
    a, b = run_check(uuid="GPU-NAMES"), run_check(uuid="GPU-NAMES")
    ra, rb = (client.get(f"/api/reports/{r['report_id']}").json() for r in (a, b))
    assert ra["check_name"] == "1." + a["gpu_name"] and rb["check_name"] == "2." + b["gpu_name"]
    assert client.get(f"/api/reports/{a['report_id']}/evidence").status_code == 200  # the name isn't part of the evidence
    q = appmod._only_marks(appmod.MB_QUERY)["events"][0]["filter"]["children"][0]
    assert q == {"operator": "Equal", "value": "0x" + "ab" * 20, "fieldType": "contract_address"}
