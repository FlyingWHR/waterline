import pytest

from api import check
from prover import calibrate as cal


def test_formula_matches_the_api():
    for model in ("h100-sxm", "h100-pcie", "a100-sxm-80"):
        for steps in (1, 100, 400):
            assert cal.deadline(model, cal.N, steps) == check.deadline_s(model, cal.N, steps)


@pytest.mark.parametrize("t", [0.005, 0.0074, 0.02, 0.05, 0.2])
def test_recommendation_lands_near_70_percent(t):
    steps, d, own = cal.recommend(t, "h100-sxm")
    assert steps >= 1 and own == pytest.approx(1.0 + steps * t) and 0.6 <= own / d <= 0.75


def test_cpu_path_is_labelled_simulated(capsys):
    assert cal.main(["--cpu"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("SIMULATED (no GPU)") and "CHECK_STEPS=" in out and "DEADLINES='{\"1\": " in out
    assert "an A100 at the same steps" in out
