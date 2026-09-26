"""Performance profile: /api/models, metrics + classification on reports, /api/compare, /api/leaderboard."""
import pytest

import test_api
from api import app as appmod, chain
from api.store import MemoryStore
from core.specs import MODELS, PAIRS
from test_api import A100, client


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(appmod, "store", MemoryStore())  # cohort and leaderboard counts start from zero


def check(monkeypatch, cloud="cloud-a", metrics=None, **kw):
    """test_api.run_check with a chosen cloud and optional profiler metrics in the reveal."""
    def post(path, body=None):
        body = dict(body or {})
        if path.endswith("/start"):
            body["cloud"] = cloud
        if path.endswith("/reveal") and metrics:
            body["metrics"] = metrics
        return client.post(path, json=body)
    monkeypatch.setattr(test_api, "post", post)
    return test_api.run_check(**kw)


def m(v, unit, trust="measured", **extra):
    return {"value": v, "unit": unit, "method": "test", "trust": trust, "n": 20, "median": v, "p10": v * 0.98,
            "p90": v * 1.01, "cv": 0.01} | extra


def test_models_table():
    r = client.get("/api/models").json()
    assert len(r["models"]) == len(MODELS) and len(r["confusable_pairs"]) == len(PAIRS)
    h = next(x for x in r["models"] if x["id"] == "h100-sxm")
    assert (h["sms"], h["int8_tops"], h["bw_tbs"], h["fp8"]) == (132, 1979, 3.35, True) and h["sources"]
    assert next(x for x in r["models"] if x["id"] == "h200-sxm")["unverified"] == ["sms"]


def test_report_carries_metrics_and_the_profiler_cannot_claim_verified(monkeypatch):
    sent = {"int8_tops_verified": m(9999, "TOPS", "verified"), "int8_tops": m(1500, "TOPS", "verified"),
            "hbm_copy_tbs": m(1.94, "TB/s"), "mem_alloc_gib": m(79.2, "GiB"), "sm_count": m(132, "SMs")}
    r = check(monkeypatch, metrics=sent)
    rep = client.get(f"/api/reports/{r['report_id']}").json()
    mt, cl = rep["metrics"], rep["classification"]
    assert mt["int8_tops_verified"]["value"] == pytest.approx(rep["effective_tops"], rel=1e-3)
    assert mt["int8_tops_verified"]["trust"] == "verified"
    v = mt["int8_tops_verified"]  # the pass went to Marks with the verified numbers
    assert chain.DRY_RUN_CALLS[-1][8:10] == chain.encode_perf(v["value"], v["pct_of_spec"])
    assert rep["claimed_model"] == "h100-sxm"
    assert mt["int8_tops"]["trust"] == "measured"  # a pod can't promote its own number
    assert mt["hbm_copy_tbs"]["pct_of_spec"] == pytest.approx(57.91) and mt["hbm_copy_tbs"]["flag"] == "low"
    assert mt["hbm_copy_tbs"]["expected_pct"] == [80, 92] and mt["sm_count"]["flag"] is None
    assert cl["claimed"] == "h100-sxm" and cl["best_match"] == "h100-sxm" and cl["consistent"] is True
    assert "H200" not in cl["candidates"][0]["why"] and any("differs" in c["why"] for c in cl["candidates"])


def test_failed_work_has_no_verified_number_and_a100_is_not_an_h100(monkeypatch):
    lazy = client.get(f"/api/reports/{check(monkeypatch, lazy=True)['report_id']}").json()
    assert "int8_tops_verified" not in lazy["metrics"] and lazy["work_ok"] is False
    a = client.get(f"/api/reports/{check(monkeypatch, probes=A100)['report_id']}").json()
    assert a["work_ok"] is True and a["verdict"] == "fail"  # honest work, wrong chip
    assert a["classification"]["best_match"].startswith("a100") and a["classification"]["consistent"] is False


def test_compare_needs_five_of_the_same_model(monkeypatch):
    ids = [check(monkeypatch, uuid=f"GPU-C{i}")["report_id"] for i in range(2)]
    c = client.get(f"/api/compare/{ids[0]}").json()
    best = c["classification"]["best_match"]  # bare probes can't split H100 SXM from NVL; either is fine here
    assert best in ("h100-sxm", "h100-nvl") and "h100-sxm" in [best, *c["classification"]["ambiguous_with"]]
    assert c["cohort"] == {"model_id": best, "n": 1, "min_n": 5, "note": "not enough checks yet"}  # n: other checks
    assert len(c["vs_models"]) == len(MODELS) and "int8_tops_verified" in c["vs_spec"]
    check(monkeypatch, probes=A100)  # other model: not in the cohort
    ids += [check(monkeypatch, uuid=f"GPU-C{i}")["report_id"] for i in range(2, 6)]
    co = client.get(f"/api/compare/{ids[-1]}").json()["cohort"]
    assert co["n"] == 5 and len(co["values"]["int8_tops_verified"]) == 5
    assert 10 <= co["percentiles"]["int8_tops_verified"] <= 90  # ties count half; never 0 or 100 for a member
    assert client.get("/api/compare/nope").status_code == 404


def test_leaderboard_ranks_only_clouds_with_three_checks(monkeypatch):
    for i in range(3):
        check(monkeypatch, cloud="cloud-a", uuid=f"GPU-A{i}")
    check(monkeypatch, cloud="cloud-a", uuid="GPU-A9", lazy=True)
    check(monkeypatch, cloud="cloud-b", uuid="GPU-B0")
    lb = client.get("/api/leaderboard?model=h100-sxm").json()
    a, b = lb["rows"]
    assert (a["cloud"], a["n"], a["pass_rate"], a["rank"], a["few"]) == ("cloud-a", 4, 0.75, 1, False)
    assert a["median_tops"] > 0 and a["median_pct_of_spec"] > 0
    assert (b["cloud"], b["few"], b["rank"]) == ("cloud-b", True, None)
    assert lb["models"] == [{"id": "h100-sxm", "name": "H100 SXM5 80GB", "n": 5}]
    for i in range(3):  # another model's clouds don't take ranks from this one
        check(monkeypatch, cloud="cloud-z", uuid=f"GPU-Z{i}", claimed=2, probes=test_api.H100 | {"sms": 114})
    ranks = {(x["model"], x["cloud"]): x["rank"] for x in client.get("/api/leaderboard").json()["rows"]}
    assert ranks[("h100-sxm", "cloud-a")] == 1 and ranks[("h100-pcie", "cloud-z")] == 1
    assert client.get("/api/leaderboard?model=nope").status_code == 404
