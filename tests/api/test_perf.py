import json
from pathlib import Path

import pytest

from api import chain, perf
from prover.metrics import metric, simulate


def test_annotate_attaches_spec_pct_range_and_flags():
    m = perf.annotate(simulate("h100-sxm"), "h100-sxm")
    i8 = m["int8_tops"]
    assert i8["spec"] == 1979 and i8["pct_of_spec"] == pytest.approx(100 * i8["value"] / 1979, rel=1e-3)
    assert i8["expected_pct"] == [60, 85] and i8["flag"] is None and "halved" in i8["spec_source"]
    assert m["h2d_gbs"]["spec"] == 63.0 and "Gen5" in m["h2d_gbs"]["spec_source"]
    assert m["sm_count"]["pct_of_spec"] == 100 and m["launch_us"]["spec"] is None
    # an A100 measured against the H100 it was sold as: throughput reads low
    a = perf.annotate(simulate("a100-sxm-40"), "h100-sxm")
    assert a["int8_tops"]["flag"] == "low" and a["mem_alloc_gib"]["flag"] == "low"
    assert a["fp8_tflops"]["value"] is None and a["fp8_tflops"]["supported"] is False


def test_pod_cannot_forge_verified_or_trust():
    sent = {"int8_tops_verified": metric("int8_tops", [9999]), "junk": {"value": 1},
            "int8_tops": metric("int8_tops", [1400]) | {"trust": "verified"}}
    m = perf.annotate(sent, "h100-sxm")
    assert set(m) == {"int8_tops"} and m["int8_tops"]["trust"] == "measured"


def test_unstable_and_consumer_ranges():
    noisy = metric("bf16_tflops", [700, 500, 720, 400, 710])
    assert perf.annotate({"bf16_tflops": noisy}, "h100-sxm")["bf16_tflops"]["flag"] == "unstable"
    assert perf.annotate({"stability_cv": metric("stability_cv", [4.2])}, "h100-sxm")["stability_cv"]["flag"] == "unstable"
    assert perf.expected_for("int8_tops", "rtx-4090") == [60, 105]
    assert perf.expected_for("h2d_gbs", "gh200") == perf.C2C_EXPECTED
    assert perf.spec_for("bf16_tflops", "t4")[0] == 65 and "fp16" in perf.spec_for("bf16_tflops", "t4")[1]


def test_verified_int8():
    v = perf.verified_int8(16384, 100, 4.0, "h100-sxm")
    assert v["trust"] == "verified" and v["value"] == pytest.approx(2 * 16384**3 * 100 / 4.0 / 1e12, rel=1e-4)
    assert v["pct_of_spec"] == pytest.approx(100 * v["value"] / 1979, rel=1e-3) and v["expected_pct"] is None
    assert perf.verified_int8(64, 4, 0, "h100-sxm")["value"] is None


def test_percentile_rank_needs_five():
    assert perf.percentile_rank(5, [1, 2, 3, 4]) is None
    assert perf.percentile_rank(5, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 45.0


def _rep(i, cloud, model, tops, verdict="pass", launch=5.0):
    return {"report_id": f"r{i}", "cloud": cloud, "verdict": verdict, "classification": {"best_match": model},
            "metrics": {"int8_tops_verified": perf.verified_int8(16384, 100, 8.8e14 / tops / 1e12, model),
                        "launch_us": {"value": launch}}}


def test_cohort_leaderboard_and_compare():
    past = [_rep(i, "cloud-a", "h100-sxm", 300 + 10 * i, launch=4 + i) for i in range(6)]
    past += [_rep(9, "cloud-b", "h100-sxm", 200, "fail"), _rep(10, "cloud-b", "a100-sxm-40", 100)]
    me = _rep(99, "cloud-a", "h100-sxm", 335, launch=4.5)
    c = perf.cohort(me, past)
    assert c["n"] == 7 and c["percentiles"]["int8_tops_verified"] > 50
    assert c["percentiles"]["launch_us"] > 50  # lower is better: fast launch ranks high
    assert perf.cohort(_rep(98, "x", "a100-sxm-40", 90), past) == {"n": 1, "note": "not enough checks yet"}
    lb = perf.leaderboard(past)
    assert [(r["model"], r["cloud"], r["n"]) for r in lb] == [("h100-sxm", "cloud-a", 6), ("h100-sxm", "cloud-b", 1),
                                                              ("a100-sxm-40", "cloud-b", 1)]
    assert lb[0]["median_tops_verified"] == pytest.approx(325, rel=1e-3) and lb[1]["pass_rate"] == 0
    assert [r["model"] for r in perf.leaderboard(past, "a100-sxm-40")] == ["a100-sxm-40"]
    cmp = perf.compare(me | {"claimed_model": "h100-sxm"}, past)
    assert cmp["vs_models"][0]["id"] == "h100-sxm" and cmp["vs_models"][0]["int8_tops"] == 1979
    assert cmp["vs_spec"]["int8_tops_verified"] == me["metrics"]["int8_tops_verified"]["pct_of_spec"]


def test_chain_selector_and_perf_encoding():
    assert chain.SELECTOR.hex() == chain.keccak(
        text="record(bytes32,bytes32,uint8,uint8,uint16,bytes32,bytes32,bytes32,uint32,uint16,bytes32)")[:4].hex()
    abi = Path(__file__).parents[2] / "contracts/out/Marks.sol/Marks.json"
    if abi.exists():  # built by `forge build` / `forge test`
        ids = json.loads(abi.read_text())["methodIdentifiers"]
        assert ids[chain.SIGNATURE] == chain.SELECTOR.hex()
    assert chain.encode_perf(1410.54, 71.254) == (14105, 7125)
    assert chain.encode_perf(None, -3) == (0, 0) and chain.encode_perf(1e12, 1e9) == (2**32 - 1, 2**16 - 1)
    chain.DRY_RUN_CALLS.clear()
    chain.record("cloud-b", "gpu-1", 1, 1, 132, b"\2" * 32, tops_x10=14105, pct_bps=7125, report_hash=b"\4" * 32)
    assert chain.DRY_RUN_CALLS[-1][:2] == (chain.keccak(text="cloud-b"), chain.keccak(text="gpu-1"))
    assert chain.DRY_RUN_CALLS[-1][8:] == (14105, 7125, b"\4" * 32)
