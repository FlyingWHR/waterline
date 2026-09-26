import random

import pytest

from prover.metrics import METHODS, metric, sim_staircase, simulate, sm_from_staircase, summary
from prover.run import Cpu, profile


def test_summary_statistics():
    s = summary(range(1, 11))
    assert s["n"] == 10 and s["median"] == 5.5 and s["p10"] == pytest.approx(1.9) and s["p90"] == pytest.approx(9.1)
    assert s["cv"] == pytest.approx(100 * 2.8723 / 5.5, rel=1e-3)
    assert summary([3.0]) == {"n": 1, "median": 3.0, "p10": 3.0, "p90": 3.0, "cv": 0.0}
    assert summary([])["median"] is None


@pytest.mark.parametrize("sms", [40, 58, 108, 114, 132, 148, 170, 188, 253])
def test_staircase_finds_the_step_through_noise_spikes_and_drift(sms):
    rnd = random.Random(sms)
    t = {k: v * (1 + 0.1 * (k - 32) / 224) * rnd.uniform(0.97, 1.03) for k, v in sim_staircase(sms).items()}
    for k in rnd.sample(sorted(t), 6):  # isolated outliers: a co-tenant, a clock blip
        t[k] *= 2.5
    assert sm_from_staircase(t) == sms


def test_staircase_without_a_step_is_none():
    assert sm_from_staircase({k: 0.002 for k in range(32, 257)}) is None
    assert sm_from_staircase(sim_staircase(254)) is None  # a step needs 3 points after it: 253 is the most
    assert sm_from_staircase(sim_staircase(300)) is None  # beyond the sweep (AMD CUs)


def test_simulated_profile_is_complete_and_marked():
    m = simulate("h100-sxm", per_second=[700, 702, 698, 701])
    assert set(m) == set(METHODS) and all(v["simulated"] and v["method"].startswith("Simulated") for v in m.values())
    assert m["sm_count"]["value"] == 132 and 0.6 < m["int8_tops"]["value"] / 1979 < 0.85
    assert m["int8_tops"]["n"] == 20 and m["launch_us"]["n"] == 1000 and m["int8_tops"]["cv"] < 2
    a = simulate("a100-sxm-40")
    assert a["fp8_tflops"]["value"] is None and a["fp8_tflops"]["supported"] is False and "stability_cv" not in a
    assert simulate("v100-pcie")["int8_tops"]["value"] is None


def test_metric_shape_matches_interfaces():
    keys = {"value", "unit", "method", "trust", "n", "median", "p10", "p90", "cv", "spec", "pct_of_spec",
            "spec_source", "expected_pct", "flag"}
    assert keys <= set(metric("int8_tops", [1, 2, 3]))


def test_reveal_carries_metrics_after_commit(api):
    rv, local = profile(api.url, "cloud-b", 3, Cpu(model="a100-sxm-80"), 16, 2)
    assert rv["verdict"] == "pass"
    commit = [b for p, b in api.calls if p == "/api/check/commit"][0]
    reveal = [b for p, b in api.calls if p == "/api/check/reveal"][0]
    assert "metrics" not in commit and reveal["metrics"] == local["metrics"]
    assert reveal["metrics"]["mem_alloc_gib"]["value"] > 75 and reveal["metrics"]["stability_cv"]["value"] > 0
    _, local0 = profile(api.url, "cloud-b", 1, Cpu(), 16, 2, perf_seconds=0)
    assert local0["metrics"] == {} and "metrics" not in [b for p, b in api.calls if p == "/api/check/reveal"][-1]
