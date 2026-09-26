import pytest

from api import check
from api.classify import classification, expected, match, models_table
from core.specs import MODELS, partners
from prover.metrics import simulate


@pytest.mark.parametrize("mid", sorted(MODELS))
def test_every_spec_classifies_as_itself_or_a_declared_partner(mid):
    c = match(expected(mid), mid)
    assert c["best_match"] in {mid} | partners(mid)
    assert set(c["ambiguous_with"]) <= partners(mid) | {mid}, "undeclared tie: add the pair or a separating feature"
    assert c["consistent"] and c["fit"] == "good"


def test_hardest_pair_is_one_class_not_a_guess():
    c = match(expected("a100-sxm-40"), "a100-pcie-40")
    assert {c["best_match"], *c["ambiguous_with"]} == {"a100-sxm-40", "a100-pcie-40"} and c["consistent"]


def test_simulated_profiles_classify_from_measured_features_only():
    for mid in ("h100-sxm", "b200", "b300", "l40", "a10g", "rtx-5090", "a100-sxm-80"):
        c = classification(metrics=simulate(mid), claimed=mid)
        assert c["consistent"], (mid, c["best_match"], c["why"])


def test_b300_is_told_from_b200_by_int8():
    c = classification(metrics=simulate("b300"), claimed="b200")
    assert c["best_match"] == "b300" and not c["consistent"] and "INT8" in c["candidates"][1]["why"]


def test_a100_sold_as_h100_fails_naming_both():
    c, reasons = check.class_check("h100-sxm", metrics=simulate("a100-sxm-40"))
    assert c["best_match"] in ("a100-sxm-40", "a100-pcie-40") and len(reasons) == 1
    assert reasons[0].startswith("Measured as A100") and "108 SMs, no FP8" in reasons[0]
    assert reasons[0].endswith("listed as H100 SXM5 80GB.")


def test_40gb_sold_as_80gb_is_caught_by_allocation():
    _, reasons = check.class_check("a100-sxm-80", metrics=simulate("a100-sxm-40"))
    assert reasons and "GiB" in reasons[0]


def test_emulated_fp8_does_not_count():
    m = simulate("a100-sxm-80")
    m["fp8_tflops"] = m["bf16_tflops"] | {"supported": True}  # "runs", at BF16 speed: a shim, not tensor cores
    c = classification(metrics=m)
    assert c["features"]["fp8"] is False and c["best_match"].startswith("a100")


def test_legacy_class_codes_and_claims():
    assert check.classify({"sms": 132, "fp8": True, "bw_tbs": 3.2}, claimed=1) == 1
    assert check.classify({"sms": 132, "fp8": True, "bw_tbs": 4.3}, claimed=5) == 5  # an H200: the claim stands
    assert check.classify({"sms": 132, "fp8": True, "bw_tbs": 3.2}, claimed=5) != 5  # H100 bandwidth sold as H200
    assert check.classify({"sms": 132, "fp8": True, "bw_tbs": 3.2}, claimed=3) in (1, 4, 5, 6)  # never A100
    assert check.classify({"sms": 114, "fp8": True, "bw_tbs": 1.7}) == 2
    assert check.classify({"sms": 108, "fp8": False, "bw_tbs": 1.9}) == 3
    assert check.classify({"sms": 0, "fp8": False}) == 0
    assert check.class_check(3, metrics=simulate("a100-pcie-80"))[1] == []  # class 3 accepts every A100
    assert check.class_check(1, probes={"sms": 0})[1][0].startswith("The GPU could not be measured")


def test_deadline_formula_and_override(monkeypatch):
    assert check.deadline_s(1, 64, 4) == 5.0  # floor
    assert check.deadline_s("h100-sxm", 16384, 100) == 5.0
    assert check.deadline_s(2, 16384, 100) == pytest.approx(3.0 + 2 * 16384**3 * 100 / (1513e12 * 0.25), abs=0.05)
    assert check.deadline_s(3, 16384, 100) == 8.6
    assert check.deadline_s("v100-pcie", 16384, 100) == 60.0
    monkeypatch.setenv("DEADLINES", '{"1": 7.5, "a100-sxm-80": 12}')
    assert check.deadline_s(1, 16384, 100) == 7.5 and check.deadline_s("a100-sxm-80", 64, 1) == 12.0


def test_models_table():
    t = {m["id"]: m for m in models_table()}
    assert len(t) == len(MODELS) and t["h100-sxm"]["int8_tops"] == 1979 and t["a100-sxm-40"]["fp8_tflops"] is None
