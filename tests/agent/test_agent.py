import json

import pytest

from agent import history, listing
from agent.cli import main


def run_check(api, env, *extra):
    return main(["--api", api.url, "check", "--local", "--cloud", "cloud-b", "--n", "16", "--steps", "2",
                 "--out", str(env / "result.json"), *extra])


def test_check_local_pass(make_api, env, capsys):
    api = make_api()
    assert run_check(api, env, "--listing", "H100 80GB HBM3 SXM5") == 0
    out = capsys.readouterr().out
    assert "reads as H100 SXM" in out and "keep this rental" in out  # the verdict itself is the profiler's receipt
    assert json.loads((env / "result.json").read_text())["probes"]["sms"] == 132
    assert not any(p.startswith("/api/report") for p, _ in api.calls)


@pytest.mark.parametrize("world, expect", [("denied", "denied: nothing published"),
                                           ("approved", "suspect · 1 of 2 humans")])
def test_check_local_fail_then_world(make_api, env, capsys, world, expect):
    api = make_api(world=world)
    assert run_check(api, env, "--listing", "H100 80GB SXM", "--sim-sms", "108") == 1
    out = capsys.readouterr().out
    assert "listed as H100 SXM, measures as A100: stopping the rental" in out
    assert "your listing reads as H100 SXM: the claim stands" in out  # Jev (or rules) agrees with the claim
    assert "WXYZ-1234" in out and expect in out
    paths = [p for p, _ in api.calls]
    assert paths.index("/api/world/login/poll") < paths.index("/api/report/approve/start")  # logged in first
    approve = next(b for p, b in api.calls if p == "/api/report/approve/start")
    assert approve == {"report_id": "r1", "agent_token": "agent-tok", "listing": "H100 80GB SXM"}


@pytest.mark.parametrize("text, code", [("H100 80GB HBM3 SXM5", 1), ("H100 80GB SXM", 1),
                                        ("H100 PCIe 80GB", 2), ("A100 80GB SXM4", 3)])
def test_rules_parse_demo_listings(text, code):
    assert listing.parse_rules(text) == (code, 0.95)


def test_plain_h100_is_unsure():
    assert listing.parse_rules("H100 80GB")[1] < 0.9 and listing.parse_rules("RTX 4090") == (17, 0.9) and listing.parse_rules("V100 32GB") == (0, 0.0)


def test_jev_failure_falls_back(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    monkeypatch.setattr(listing, "parse_jev", lambda t, k: (_ for _ in ()).throw(OSError("offline")))
    assert listing.parse("H100 PCIe 80GB") == (2, 0.95, "rules")


def test_namehash():
    assert history.namehash("") == "0x" + "00" * 32
    assert history.namehash("eth") == "0x93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"


def test_choose_decides_from_history_only(capsys, monkeypatch, tmp_path):
    names = [f"gpu-{i}.cloud-{c}.waterline.eth" for i, c in [(1, "a"), (2, "a"), (3, "b"), (4, "b"), (5, "b"), (6, "b")]]
    rows = [{"node": history.namehash(names[0]), "passes": "0", "fails": "2", "active": "2", "cls": 3},
            {"node": history.namehash(names[1]), "passes": "3", "fails": "1", "active": "1", "cls": 3},
            {"node": history.namehash(names[2]), "passes": "4", "fails": "0", "active": "0", "cls": 1},
            {"node": history.namehash(names[5]), "passes": "1", "fails": "0", "active": "0", "cls": 1}]
    hist = {r["node"]: r for r in rows}
    listings = [{"gpu": names[0], "price": 1.0}, {"gpu": names[1].removesuffix(".waterline.eth"), "price": 1.5},
                {"gpu": names[2], "price": 2.5}, {"gpu": names[3], "price": 9.0}, {"gpu": names[4], "price": 0.5},
                {"gpu": names[5], "price": 2.0}]
    pick, skipped = history.choose(listings, hist, max_price=5)
    assert pick["gpu"] == names[2] and pick["history"] == "4 passes, no failures"  # more passes beats cheaper
    assert [w for _, w in skipped] == ["status failed (2 failure reports)", "status suspect · 1 of 2 humans (1 failure report)",
                                       "9/h is over your max price of 5/h"]
    pick, _ = history.choose(listings, hist, max_price=2)
    assert pick["gpu"] == names[5]
    assert history.choose(listings, hist, max_price=0.1)[0] is None

    f = tmp_path / "listings.json"
    monkeypatch.setattr(history, "fetch", lambda **k: hist)
    monkeypatch.setattr(history, "fetch_providers", lambda **k: {})
    monkeypatch.setattr(listing, "parse_jev", lambda t, k: (_ for _ in ()).throw(AssertionError("no LLM")))
    f.write_text(json.dumps([dict(li, listing="H100 80GB SXM") for li in listings]))
    assert main(["choose", "--listings", str(f), "--max-price", "5"]) == 0
    out = capsys.readouterr().out
    assert "skipping gpu-2.cloud-a: status suspect" in out and "Rent gpu-3.cloud-b at 2.5/h" in out


def test_choose_prefers_the_provider_with_fewer_failed_gpus_and_allows_recovered():
    a, b = "gpu-7.cloud-a.waterline.eth", "gpu-8.cloud-b.waterline.eth"
    hist = {history.namehash(a): {"passes": "5", "fails": "0", "active": "0"},
            history.namehash(b): {"passes": "2", "fails": "1", "active": "0"}}  # recovered
    provs = {history.namehash("cloud-a.waterline.eth"): {"gpus": "4", "failed_gpus": "2"},
             history.namehash("cloud-b.waterline.eth"): {"gpus": "3", "failed_gpus": "0"}}
    listings = [{"gpu": a, "price": 1.0}, {"gpu": b, "price": 2.0}]
    pick, skipped = history.choose(listings, hist, providers=provs)
    assert pick["gpu"] == b and not skipped  # a renamed-chip-proof provider record outranks a GPU's own passes
    assert pick["history"] == "2 passes, recovered after 1 failure report"
    pick, _ = history.choose(listings, hist)
    assert pick["gpu"] == a


def test_stop_cmd_runs_on_fail_before_approval(make_api, env, capsys):
    api = make_api(world="denied")
    flag = env / "stopped"
    assert run_check(api, env, "--listing", "H100 80GB SXM", "--sim-sms", "108", "--pod-id", "pod 42",
                     "--stop-cmd", f"echo {{pod_id}} > {flag}") == 1
    out = capsys.readouterr().out
    assert flag.read_text().strip() == "pod 42"
    assert out.index("Stopped paying: rental pod 42 ended.") < out.index("WXYZ-1234")


def test_no_stop_cmd_on_fail_says_so(make_api, env, capsys, monkeypatch):
    monkeypatch.delenv("WATERLINE_STOP_CMD", raising=False)
    run_check(make_api(world="denied"), env, "--listing", "H100 80GB SXM", "--sim-sms", "108")
    assert "FAIL: end this rental now (no stop command configured)." in capsys.readouterr().out


def test_stop_cmd_never_runs_on_pass(make_api, env, capsys, monkeypatch):
    flag = env / "stopped"
    monkeypatch.setenv("WATERLINE_STOP_CMD", f"touch {flag}")
    assert run_check(make_api(), env, "--listing", "H100 80GB SXM", "--pod-id", "p1") == 0
    assert not flag.exists() and "Stopped paying" not in capsys.readouterr().out


def test_fetch_sends_multibaas_query(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps({"status": 200, "result": {"rows": [{"node": "0xAB", "humans": "0"}]}}).encode()

    def fake_urlopen(req, timeout):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.headers["Authorization"], json.loads(req.data)
        return Resp()
    monkeypatch.setattr(history.urllib.request, "urlopen", fake_urlopen)
    rows = history.fetch("https://mb.example/", "k")
    assert seen["url"] == "https://mb.example/api/v0/queries" and seen["auth"] == "Bearer k"
    ev = seen["body"]["events"][0]
    assert seen["body"]["groupBy"] == "node" and ev["eventName"].startswith("Reported(")
    assert {f["alias"]: f.get("aggregator") for f in ev["select"]}["active"] == "last"
    assert rows == {"0xab": {"node": "0xAB", "humans": "0"}}


def test_choose_skips_a_degraded_gpu_and_says_why():
    g = "gpu-9.cloud-c.waterline.eth"
    hist = {history.namehash(g): {"passes": "3", "fails": "0", "active": "0", "verdict": "3"}}
    pick, skipped = history.choose([{"gpu": g, "price": 1.0}], hist)
    assert pick is None and skipped[0][1].startswith("degraded on its last check")
    assert history.status(hist[history.namehash(g)]) == "degraded"


def test_world_wait_survives_a_dropped_connection(monkeypatch):
    from agent import cli
    answers = iter([{"device_id": "d", "user_code": "C", "verification_uri_complete": "https://w", "expires_in": 60},
                    OSError("SSL: UNEXPECTED_EOF"), {"status": "approved", "agent_token": "t"}])

    def fake_post(api, path, body):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(cli, "post", fake_post)
    monkeypatch.setattr(cli, "poll_s", lambda: 0)
    assert cli.world_flow("http://api", "/s", {}, "/p", "Log in")["status"] == "approved"


def test_fail_with_web_flag_leaves_approval_to_the_web(make_api, env, capsys):
    api = make_api(world="approved")
    assert run_check(api, env, "--listing", "H100 80GB SXM", "--sim-sms", "108", "--web") == 1
    out = capsys.readouterr().out
    assert "approve it on the web:" in out and "/#/check/r1" in out
    assert not any(p.startswith("/api/world/") for p, _ in api.calls)  # no World login here


def test_history_without_multibaas_keys_reads_through_the_api(monkeypatch):
    monkeypatch.delenv("MB_URL", raising=False)
    monkeypatch.delenv("MB_API_KEY", raising=False)
    seen = []
    def get(api, path):
        seen.append(api + path)
        return {"gpus": [{"node": "0xab", "passes": 1, "fails": 0, "active": 0, "last_verdict": 3}]} if path == "/api/gpus" \
            else {"providers": [{"provider_node": "0xcd", "gpus": 2, "failed_gpus": 1}]}
    monkeypatch.setattr(history, "_api_get", get)
    rows = history.fetch(api="https://w.example")
    assert history.status(rows["0xab"]) == "degraded"
    assert history.fetch_providers(api="https://w.example")["0xcd"]["failed_gpus"] == 1
    assert seen == ["https://w.example/api/gpus", "https://w.example/api/providers"]
