"""Control panel reads: /api/health, /api/reports, /api/reports/{id}, /api/gpus."""
import httpx

from api import app as appmod, chain
from test_api import A100, H100, client, publish, run_check

STAIR = {str(k): round(5.0 * -(-k // 132), 3) for k in range(64, 161)}


def test_health_dry_run():
    h = client.get("/api/health").json()
    assert h["api"] == "ok" and h["store"] == "memory"
    assert h["chain"] == {"mode": "dry-run", "write_path": "dry-run", "chain_id": 11155111, "marks": None, "reporter": None,
                          "reporter_balance_eth": None}
    assert h["multibaas"] == {"configured": False, "url": None, "webhook": False}
    assert h["ens"]["parent"] == "waterline.eth" and h["ens"]["universal_resolver"].startswith("0x")


def test_health_live_reads_reporter_balance(monkeypatch):
    key = "0x" + "11" * 32
    monkeypatch.setenv("MARKS_ADDRESS", "0x" + "22" * 20)
    monkeypatch.setenv("REPORTER_KEY", key)
    monkeypatch.setenv("SEPOLIA_RPC", "http://rpc.invalid")
    calls = []
    monkeypatch.setattr(chain, "_rpc", lambda url, m, p, timeout=20: calls.append((m, p)) or hex(25 * 10**16))
    c = client.get("/api/health").json()["chain"]
    assert c["mode"] == "live" and c["reporter_balance_eth"] == 0.25
    assert calls == [("eth_getBalance", [chain.Account.from_key(key).address, "latest"])]


def test_static_page_and_api_routes_coexist():
    assert client.get("/").headers["content-type"].startswith("text/html")
    assert client.get("/api/health").headers["content-type"] == "application/json"


def test_reports_index_and_detail_with_staircase():
    ok = run_check(uuid="GPU-P1", probes=H100 | {"staircase": STAIR})
    bad = run_check(uuid="GPU-P2", probes=A100)
    rows = client.get("/api/reports?limit=500").json()
    ids = [r["report_id"] for r in rows]
    assert ids.index(bad["report_id"]) < ids.index(ok["report_id"])  # newest first
    b = rows[ids.index(bad["report_id"])]
    assert b["verdict"] == "fail" and b["published"] is False and b["claimed_class"] == 1 and b["cloud"] == "cloud-b"
    assert "Nothing is published" in b["status_text"]

    d = client.get(f"/api/reports/{ok['report_id']}").json()
    assert d["staircase"] == STAIR and d["probes"]["sms"] == 132 and "staircase" not in d["probes"]
    assert len(d["samples"]) == 8 and all(len(x) == 2 for x in d["samples"])  # secret columns are not exposed
    assert (d["n"], d["steps"], d["deadline_s"]) == (64, 4, 5.0) and d["elapsed_s"] >= 0
    assert client.get(f"/api/reports/{bad['report_id']}").json()["staircase"] is None
    assert client.get("/api/reports/nope").status_code == 404


def test_gpus_local_tallies_follow_marks_rules():
    ok = run_check(uuid="GPU-L1")
    bad = run_check(uuid="GPU-L2", probes=A100)
    by_node = {g["node"]: g for g in client.get("/api/gpus").json()["gpus"]}
    assert by_node[ok["node"]]["status"] == "pass" and by_node[ok["node"]]["gpu_name"] == ok["gpu_name"]
    assert bad["node"] not in by_node  # a pending failure is not on the record
    publish(bad["report_id"])
    r = client.get("/api/gpus").json()
    g = {g["node"]: g for g in r["gpus"]}[bad["node"]]
    assert r["source"] == "local" and (g["fails"], g["humans"], g["cls"], g["cores"]) == (1, 1, 3, 108)
    assert g["status"] == "suspect · 1 of 2 reports"
    rep = client.get(f"/api/reports/{bad['report_id']}").json()
    assert rep["published"] is True and rep["status_text"] == "Recorded on Marks."


def test_gpus_from_multibaas(monkeypatch):
    ok = run_check(uuid="GPU-M1")
    monkeypatch.setenv("MB_URL", "https://mb.invalid/")
    monkeypatch.setenv("MB_API_KEY", "admin-key")
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        rows = [{"node": ok["node"], "provider": "0x" + "ee" * 32, "cls": "1", "cores": "132", "at": "1790000000",
                 "passes": "3", "fails": "2", "active": "2", "pct_bps": "7125", "tops_x10": "14105"}]
        return httpx.Response(200, json={"result": {"rows": rows}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(appmod.httpx, "post", fake_post)
    r = client.get("/api/gpus").json()
    assert seen["url"] == "https://mb.invalid/api/v0/queries" and seen["auth"] == "Bearer admin-key"
    assert seen["body"]["groupBy"] == "node"
    assert r == {"source": "multibaas", "error": None, "gpus": [{
        "node": ok["node"], "provider_node": "0x" + "ee" * 32, "last_verdict": 0, "pct_of_spec": 71.25, "tops": 1410.5, "gpu_name": ok["gpu_name"], "listed_class": 1, "cls": 1,
        "cores": 132,
        "passes": 3, "fails": 2, "active": 2, "humans": 2, "last_at": 1790000000, "status": "failed"}]}


def test_gpus_falls_back_to_local_when_multibaas_is_down(monkeypatch):
    monkeypatch.setenv("MB_URL", "https://mb.invalid")
    monkeypatch.setenv("MB_API_KEY", "k")

    def down(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(appmod.httpx, "post", down)
    r = client.get("/api/gpus").json()
    assert r["source"] == "local" and "MultiBaas" in r["error"]
