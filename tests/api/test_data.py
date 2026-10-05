import csv
import io

from api import data
from prover import health, host
from test_api import client, run_check


def rep(i, uuid="GPU-a", bf16=600.0, pct=60.0, starved=False, price=2.5, source="nvml", drop_burn=600):
    hr = health.simulated(132, drop_burn) | {"host": host.simulated(starved), "source": source}
    return {"report_id": f"r{i}", "created_at": 1790000000 + i, "cloud": "runpod", "gpu_name": "gpu-x.runpod.waterline.eth",
            "uuid": uuid, "claimed_model": "h100-sxm", "claimed_class": 1, "measured_class": 1, "verdict": "pass",
            "classification": {"best_match": "h100-sxm"}, "probes": {"sms": 132, "fp8": True},
            "metrics": {"bf16_tflops": {"value": bf16, "pct_of_spec": pct}}, "health": hr,
            "delivery": [{"kind": "cpu"}, {"kind": "disk"}] if starved else [], "price_usd_per_gpu_hour": price,
            "published": True, "report_hash": "0x" + "ab" * 32}


def test_row_flattens_a_report_and_joins_price_to_delivery():
    r = data.row(rep(1))
    assert list(r) == data.COLUMNS and r["provider_known"] is True and r["spec_mismatch"] is False
    assert r["usd_per_bf16_pflops_hour"] == 4.17  # $2.50 / 0.6 PFLOPS
    assert r["host_cpu_usable"] == 64 and r["sustained_drop_pct"] is not None and r["simulated"] is False


def test_summary_per_provider_and_model():
    rows = [data.row(x) for x in (rep(1, bf16=600), rep(2, bf16=660, pct=66), rep(3, "GPU-b", starved=True, pct=50),
                                  rep(4, source="simulated"))]
    [g] = data.summary(rows)
    assert (g["provider"], g["listed_model"], g["checks"], g["gpus"]) == ("runpod", "h100-sxm", 3, 2)
    assert g["pct_rating_bf16"]["p50"] == 60.0 and g["cpu_starved"] == {"rate": 0.333, "n": 3}
    assert g["card_variation_cv_pct"] == {"median": 4.76, "cards": 1}  # GPU-a ran twice: 600 and 660
    assert g["first"] < g["last"] and g["verdicts"]["pass"] == 3


def test_csv_and_dictionary_cover_every_column():
    text = data.to_csv([data.row(rep(1))])
    assert next(csv.reader(io.StringIO(text))) == data.COLUMNS
    d = client.get("/api/data/dictionary").json()
    assert [f["name"] for f in d] == data.COLUMNS and all(f["trust"] in ("verified", "measured", "reported", "stated") for f in d)


def test_data_endpoints_serve_rows_and_leave_test_runs_out():
    real = run_check(uuid="GPU-DATA", health=health.simulated(132, 10) | {"source": "nvml"})
    run_check(uuid="GPU-SIM", health=health.simulated(132, 10))
    ids = [r["report_id"] for r in client.get("/api/data/checks").json()["rows"]]
    assert real["report_id"] in ids and all(r["simulated"] is False for r in client.get("/api/data/checks").json()["rows"])
    assert any(r["simulated"] for r in client.get("/api/data/checks?include_simulated=true").json()["rows"])
    assert client.get("/api/data/checks?format=csv").text.startswith("report_id,checked_at,")
    assert client.get("/api/data/checks?since=2999-01-01").json()["rows"] == []
    assert any(g["provider"] == "cloud-b" for g in client.get("/api/data/summary").json()["groups"])


def test_docs_list_every_column():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[2] / "docs" / "DATA.md").read_text()
    assert all(f"`{c}`" in doc for c in data.COLUMNS)
