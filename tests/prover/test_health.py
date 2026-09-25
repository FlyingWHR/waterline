import statistics
import subprocess

from prover import health


def test_welford_matches_statistics():
    xs = [700.1, 712.4, 698.0, 650.2, 705.5]
    s = health.stats(xs)
    assert s["mean"] == round(statistics.fmean(xs), 2) and s["std"] == round(statistics.pstdev(xs), 2)
    assert (s["min"], s["max"]) == (650.2, 712.4) and health.stats([]) is None


def test_reasons_decode():
    assert health.decode(0x4 | 0x8 | 0x40) == ["power cap", "HW slowdown", "HW thermal"]
    assert health.decode(0) == [] and health.decode(None) is None


class FakeNvml:
    """Only some functions exist, one raises: every field must come back as a value or None, never raise."""

    class Mem:
        total = 80 * 2**30

    def nvmlDeviceGetName(self, h):
        return b"NVIDIA H100 80GB HBM3"

    def nvmlDeviceGetMemoryInfo(self, h):
        return self.Mem()

    def nvmlDeviceGetEccMode(self, h):
        return [1, 0]

    def nvmlSystemGetCudaDriverVersion(self):
        return 12040

    def nvmlDeviceGetCurrPcieLinkGeneration(self, h):
        return 4

    def nvmlDeviceGetMaxPcieLinkGeneration(self, h):
        return 5

    def nvmlDeviceGetNvLinkState(self, h, i):
        if i >= 4:
            raise RuntimeError("NVML_ERROR_INVALID_ARGUMENT")
        return int(i != 3)

    def nvmlDeviceGetCurrentClocksThrottleReasons(self, h):  # old name only: the new one is missing
        return 0x20

    def nvmlDeviceGetTotalEccErrors(self, h, kind, scope):
        raise RuntimeError("NVML_ERROR_NOT_SUPPORTED")


def test_missing_or_failing_nvml_calls_give_null():
    nv = FakeNvml()
    d = health.device(nv, None)
    assert d["name"] == "NVIDIA H100 80GB HBM3" and d["cuda"] == "12.4" and d["memory_gib"] == 80.0
    assert d["pcie"] == {"gen": 4, "width": None, "max_gen": 5, "max_width": None}
    assert d["nvlink"] == {"up": 3, "down": 1} and d["ecc"] == {"enabled": True, "pending": False}  # disable queued
    assert d["vbios"] is None and d["mig"] is None
    m = health.memory(nv, None)
    assert m["ecc_errors"]["volatile"] == {"corrected": None, "uncorrected": None}
    assert m["retired_pages"] is None and m["remapped_rows"] is None
    live = health.live(nv, None)
    assert live["reasons"] == ["SW thermal"] and live["power_w"] is None


def test_summary_and_simulated_report():
    s = health.summarize(3, 8192, [700, 690, 650], [{"temp_c": 60, "reasons": ["power cap"], "pcie_gen": 5},
                                                   {"temp_c": 71, "reasons": ["idle", "power cap"]}])
    assert s["max_temp_c"] == 71 and s["reasons_seen"] == ["idle", "power cap"] and s["pcie_gen"] == 5
    a = health.simulated(108, 10)
    assert a["source"] == "simulated" and "A100" in a["device"]["name"] and "power cap" in a["burn"]["reasons_seen"]


def test_dcgm_absent_and_parsed(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda _: None)
    assert health.dcgm() == {"available": False, "note": "not available"}
    out = """+---------------------------+------------------------------------------------+
| Diagnostic                | Result                                         |
+===========================+================================================+
|-----  Deployment  --------+------------------------------------------------|
| Denylist                  | Pass                                           |
| NVML Library              | Pass                                           |
| Persistence Mode          | Fail                                           |
| Environment Variables     | Skip                                           |
+---------------------------+------------------------------------------------+"""
    monkeypatch.setattr(health.shutil, "which", lambda _: "/usr/bin/dcgmi")
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    r = health.dcgm()
    assert r["available"] and r["passed"] is False and r["note"] is None
    assert [t["result"] for t in r["tests"]] == ["pass", "pass", "fail", "skip"] and r["tests"][0]["name"] == "Denylist"
