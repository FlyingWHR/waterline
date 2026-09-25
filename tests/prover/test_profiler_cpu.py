import json

from prover.run import Cpu, main, profile


def test_cpu_round_trip_passes(api, tmp_path, capsys):
    out = tmp_path / "result.json"
    assert main(["--api", api.url, "--cloud", "cloud-b", "--claimed", "1", "--cpu", "--n", "32", "--steps", "3",
                 "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["verdict"] == "pass", printed["reasons"]
    local = json.loads(out.read_text())
    assert local["probes"]["sms"] == 132 and local["result"] == printed
    assert local["staircase"]["132"] < local["staircase"]["133"]  # chart has its step at the SM count
    # reveal carries every leaf hash, but fingerprints only for sampled steps
    stair = [b for p, b in api.calls if p == "/api/check/commit"][0]["probes"]["staircase"]  # ms, sent to the API
    assert stair == local["staircase"] and stair["133"] > 1.5 * stair["132"]
    reveal = [b for p, b in api.calls if p == "/api/check/reveal"][0]
    assert len(reveal["leaf_hashes"]) == 3 and len(reveal["rows"]) >= 1


def test_a100_listed_as_h100_fails(api):
    rv, _ = profile(api.url, "cloud-b", 1, Cpu(sms=108), 16, 2)
    assert rv["verdict"] == "fail" and rv["measured_class"] == 3
    assert rv["reasons"] == ["The measured class does not match the listing."]  # the work itself checked out


def test_tampered_row_is_caught(api):
    class Lazy(Cpu):
        def rows(self, p, samples):
            return {k: [0] * p.n for k in super().rows(p, samples)}
    rv, _ = profile(api.url, "cloud-b", 1, Lazy(), 16, 2)
    assert rv["verdict"] == "fail" and any("row fp" in r for r in rv["reasons"])
