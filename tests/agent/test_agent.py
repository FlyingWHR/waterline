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
    assert "reads as H100 SXM" in out and "PASS" in out and "0xpass" in out
    assert json.loads((env / "result.json").read_text())["probes"]["sms"] == 132
    assert not any(p.startswith("/api/report") for p, _ in api.calls)


@pytest.mark.parametrize("world, expect", [("denied", "Denied: nothing published."),
                                           ("approved", "suspect · 1 of 2 humans")])
def test_check_local_fail_then_world(make_api, env, capsys, world, expect):
    api = make_api(world=world)
    assert run_check(api, env, "--listing", "H100 80GB SXM", "--sim-sms", "108") == 1
    out = capsys.readouterr().out
    assert "FAIL: listed as H100 SXM, measures as A100." in out
    assert "WXYZ-1234" in out and expect in out
    paths = [p for p, _ in api.calls]
    assert paths.index("/api/world/login/poll") < paths.index("/api/report/approve/start")  # logged in first
    approve = next(b for p, b in api.calls if p == "/api/report/approve/start")
    assert approve == {"report_id": "r1", "agent_token": "agent-tok"}


@pytest.mark.parametrize("text, code", [("H100 80GB HBM3 SXM5", 1), ("H100 80GB SXM", 1),
                                        ("H100 PCIe 80GB", 2), ("A100 80GB SXM4", 3)])
def test_rules_parse_demo_listings(text, code):
    assert listing.parse_rules(text) == (code, 0.95)


def test_plain_h100_is_unsure():
    assert listing.parse_rules("H100 80GB")[1] < 0.9 and listing.parse_rules("RTX 4090") == (0, 0.0)


def test_jev_failure_falls_back(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    monkeypatch.setattr(listing, "parse_jev", lambda t, k: (_ for _ in ()).throw(OSError("offline")))
    assert listing.parse("H100 PCIe 80GB") == (2, 0.95, "rules")


def test_namehash():
    assert history.namehash("") == "0x" + "00" * 32
    assert history.namehash("eth") == "0x93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"


def test_choose_skips_bad_history_and_price():
    names = [f"gpu-{i}.cloud-{c}.waterline.eth" for i, c in [(1, "a"), (2, "a"), (3, "b"), (4, "b"), (5, "b")]]
    mb = {"status": 200, "message": "success", "result": {"rows": [
        {"node": history.namehash(names[0]), "passes": "0", "fails": "2", "humans": "2", "cls": 3},
        {"node": history.namehash(names[1]), "passes": "3", "fails": "1", "humans": "1", "cls": 3},
        {"node": history.namehash(names[2]), "passes": "4", "fails": "0", "humans": "0", "cls": 1},
    ]}}
    hist = {r["node"]: r for r in mb["result"]["rows"]}
    listings = [{"gpu": names[0], "price": 1.0}, {"gpu": names[1], "price": 1.5},
                {"gpu": names[2], "price": 2.5}, {"gpu": names[3], "price": 9.0}, {"gpu": names[4], "price": 3.0}]
    pick, skipped = history.choose(listings, hist, max_price=5)
    assert pick["gpu"] == names[2] and pick["history"] == "pass"
    assert [w for _, w in skipped] == ["history: failed", "history: suspect · 1 of 2 humans",
                                       "over the max price (9.0)"]
    pick, _ = history.choose(listings, hist, max_price=2)
    assert pick is None


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
    assert {f["alias"]: f.get("aggregator") for f in ev["select"]}["humans"] == "max"
    assert rows == {"0xab": {"node": "0xAB", "humans": "0"}}
