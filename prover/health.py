"""Health report: standard GPU profiling for the renter. Advisory only: it NEVER changes the verdict.

Collected on the pod after the timed check: NVML identity, config and memory-error counters, a sustained BF16
matmul burn (TFLOPS each second, gpu-fryer style) with NVML sampled during it, and `dcgmi diag -r 1` when DCGM
is installed. Every NVML call goes through _q, so a function missing from the driver or the bindings gives
null, never a crash. Names checked against nvidia-ml-py 13.615 (`pip install nvidia-ml-py`, import pynvml).
"""
import math
import random
import re
import shutil
import subprocess
import time

GRADE = "reported by the machine"
# nvmlClocksEventReason* bits (the old nvmlClocksThrottleReason* names carry the same values)
REASONS = [(0x1, "idle"), (0x2, "applications clocks"), (0x4, "power cap"), (0x8, "HW slowdown"),
           (0x10, "sync boost"), (0x20, "SW thermal"), (0x40, "HW thermal"), (0x80, "HW power brake"),
           (0x100, "display clock")]
CLOCK_SM, CLOCK_MEM, TEMP_GPU = 1, 2, 0     # NVML_CLOCK_SM, NVML_CLOCK_MEM, NVML_TEMPERATURE_GPU
CORRECTED, UNCORRECTED = 0, 1               # NVML_MEMORY_ERROR_TYPE_*
VOLATILE, AGGREGATE = 0, 1                  # NVML_VOLATILE_ECC, NVML_AGGREGATE_ECC
NVLINK_MAX = 18                             # H100 SXM has 18 links; NVML_NVLINK_MAX_LINKS is larger in new bindings


def decode(mask):
    return [name for bit, name in REASONS if mask & bit] if mask is not None else None


def stats(xs):
    """Welford's running mean and population stddev, plus min and max."""
    n = mean = m2 = 0
    for x in xs:
        n += 1
        d = x - mean
        mean += d / n
        m2 += d * (x - mean)
    if not n:
        return None
    return {"mean": round(mean, 2), "std": round(math.sqrt(m2 / n), 2), "min": round(min(xs), 2),
            "max": round(max(xs), 2)}


def _q(nv, name, *a):
    fn = getattr(nv, name, None)
    if fn is None:
        return None
    try:
        v = fn(*a)
    except Exception:  # NVMLError_NotSupported, NoPermission, FunctionNotFound (old driver), ...
        return None
    return v.decode() if isinstance(v, bytes) else v


def device(nv, h):
    q = lambda name, *a: _q(nv, name, h, *a)  # noqa: E731
    mem, ecc, mig = q("nvmlDeviceGetMemoryInfo"), q("nvmlDeviceGetEccMode"), q("nvmlDeviceGetMigMode")
    cuda = _q(nv, "nvmlSystemGetCudaDriverVersion")  # e.g. 12040
    links = [x for x in (q("nvmlDeviceGetNvLinkState", i) for i in range(NVLINK_MAX)) if x is not None]
    max_gen = q("nvmlDeviceGetGpuMaxPcieLinkGeneration") or q("nvmlDeviceGetMaxPcieLinkGeneration")
    return {
        "name": q("nvmlDeviceGetName"), "uuid": q("nvmlDeviceGetUUID"),
        "driver": _q(nv, "nvmlSystemGetDriverVersion"),
        "cuda": f"{cuda // 1000}.{cuda % 1000 // 10}" if isinstance(cuda, int) else None,
        "vbios": q("nvmlDeviceGetVbiosVersion"),
        "memory_gib": round(mem.total / 2**30, 1) if mem is not None else None,
        "mig": None if mig is None else ("enabled" if mig[0] else "disabled"),
        # current PCIe link is read again during the burn: an idle GPU drops its link to save power
        "pcie": {"gen": q("nvmlDeviceGetCurrPcieLinkGeneration"), "width": q("nvmlDeviceGetCurrPcieLinkWidth"),
                 "max_gen": max_gen, "max_width": q("nvmlDeviceGetMaxPcieLinkWidth")},
        "nvlink": {"up": sum(1 for x in links if x), "down": sum(1 for x in links if not x)} if links else None,
        # pending = the ECC mode after the next reboot (NVML), so enabled != pending means a change is queued
        "ecc": None if ecc is None else {"enabled": bool(ecc[0]), "pending": bool(ecc[1])},
    }


def memory(nv, h):
    q = lambda name, *a: _q(nv, name, h, *a)  # noqa: E731
    errs = {scope: {kind: q("nvmlDeviceGetTotalEccErrors", k, s) for kind, k in
                    (("corrected", CORRECTED), ("uncorrected", UNCORRECTED))}
            for scope, s in (("volatile", VOLATILE), ("aggregate", AGGREGATE))}
    retired = [q("nvmlDeviceGetRetiredPages", c) for c in (0, 1)]  # cause: multiple single-bit, double-bit
    pending = q("nvmlDeviceGetRetiredPagesPendingStatus")
    remap = q("nvmlDeviceGetRemappedRows")  # Ampere and newer: (corrected, uncorrected, pending, failure)
    return {"ecc_errors": errs,
            "retired_pages": None if retired == [None, None] else {
                "single_bit": len(retired[0] or []), "double_bit": len(retired[1] or []),
                "pending": None if pending is None else bool(pending)},
            "remapped_rows": None if remap is None else {
                "corrected": remap[0], "uncorrected": remap[1], "pending": bool(remap[2]), "failure": bool(remap[3])}}


def live(nv, h):
    q = lambda name, *a: _q(nv, name, h, *a)  # noqa: E731
    mask = q("nvmlDeviceGetCurrentClocksEventReasons")
    if mask is None:
        mask = q("nvmlDeviceGetCurrentClocksThrottleReasons")  # older bindings / drivers
    power, limit, util = q("nvmlDeviceGetPowerUsage"), q("nvmlDeviceGetEnforcedPowerLimit"), q("nvmlDeviceGetUtilizationRates")
    return {"sm_mhz": q("nvmlDeviceGetClockInfo", CLOCK_SM), "max_sm_mhz": q("nvmlDeviceGetMaxClockInfo", CLOCK_SM),
            "mem_mhz": q("nvmlDeviceGetClockInfo", CLOCK_MEM), "max_mem_mhz": q("nvmlDeviceGetMaxClockInfo", CLOCK_MEM),
            "power_w": None if power is None else round(power / 1000, 1),
            "power_limit_w": None if limit is None else round(limit / 1000, 1),
            "temp_c": q("nvmlDeviceGetTemperature", TEMP_GPU), "util_gpu": None if util is None else util.gpu,
            "reasons": decode(mask), "pcie_gen": q("nvmlDeviceGetCurrPcieLinkGeneration"),
            "pcie_width": q("nvmlDeviceGetCurrPcieLinkWidth")}


def summarize(seconds, n, per_second, samples):
    """Burn summary from per-second TFLOPS and the NVML samples taken each second."""
    col = lambda k: [s[k] for s in samples if s.get(k) is not None]  # noqa: E731
    seen = {r for s in samples for r in (s.get("reasons") or [])}
    last = samples[-1] if samples else {}
    return {"seconds": seconds, "n": n, "dtype": "bf16", "tflops": stats(per_second),
            "per_second": [round(x, 1) for x in per_second],
            "reasons_seen": [name for _, name in REASONS if name in seen],
            "max_temp_c": max(col("temp_c"), default=None), "max_power_w": max(col("power_w"), default=None),
            "min_sm_mhz": min(col("sm_mhz"), default=None),
            **{k: last.get(k) for k in ("power_limit_w", "max_sm_mhz", "mem_mhz", "max_mem_mhz", "util_gpu")},
            "pcie_gen": max(col("pcie_gen"), default=None), "pcie_width": max(col("pcie_width"), default=None)}


def burn(seconds, nv=None, h=None, n=8192):
    """BF16 matmul back to back; TFLOPS measured over each ~1 s window, NVML sampled at the end of each."""
    import torch
    a, b = (torch.randn(n, n, device="cuda", dtype=torch.bfloat16) for _ in range(2))
    c = torch.empty_like(a)
    torch.matmul(a, b, out=c)
    torch.cuda.synchronize()
    per_second, samples, end = [], [], time.perf_counter() + seconds
    while time.perf_counter() < end:
        t, k = time.perf_counter(), 0
        while time.perf_counter() - t < 1.0:
            for _ in range(8):
                torch.matmul(a, b, out=c)
            k += 8
            torch.cuda.synchronize()
        per_second.append(2 * n**3 * k / (time.perf_counter() - t) / 1e12)
        if nv is not None:
            samples.append(live(nv, h))  # GPU is still hot from the window just finished
    del a, b, c
    torch.cuda.empty_cache()
    return summarize(seconds, n, per_second, samples)


DIAG_LINE = re.compile(r"^\|\s*([A-Za-z][^|]*?)\s*\|\s*(Pass|Fail|Warn|Skip)\w*", re.I)


def parse_diag(text):
    tests = [{"name": m[1], "result": m[2].lower()} for m in map(DIAG_LINE.match, text.splitlines()) if m]
    return tests


def dcgm(timeout=240):
    if not shutil.which("dcgmi"):
        return {"available": False, "note": "not available"}
    try:
        out = subprocess.run(["dcgmi", "diag", "-r", "1"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"available": True, "passed": None, "tests": [], "note": f"timed out after {timeout} s"}
    except OSError as e:
        return {"available": False, "note": str(e)[:200]}
    tests = parse_diag(out.stdout)
    note = None if tests else (out.stderr or out.stdout).strip()[-300:] or f"exit code {out.returncode}"
    return {"available": True, "passed": bool(tests) and all(t["result"] != "fail" for t in tests),
            "tests": tests, "note": note}


def collect(burn_seconds=10):
    """Real report on the pod. Anything that breaks becomes a note; the check has already been answered."""
    rep, notes, nv = {"grade": GRADE, "source": "nvml"}, [], None
    try:
        import pynvml as nv
        nv.nvmlInit()
        h = nv.nvmlDeviceGetHandleByIndex(0)
        rep |= {"device": device(nv, h), "memory": memory(nv, h)}
    except Exception as e:
        nv = h = None
        rep["source"] = "torch"
        notes.append(f"NVML unavailable ({type(e).__name__}); install nvidia-ml-py")
    try:
        rep["burn"] = burn(burn_seconds, nv, h) if burn_seconds > 0 else None
    except Exception as e:
        notes.append(f"burn failed: {type(e).__name__}: {str(e)[:200]}")
    if nv is not None and rep.get("burn"):
        rep["device"]["pcie"] |= {k: rep["burn"][f"pcie_{k}"] or rep["device"]["pcie"][k] for k in ("gen", "width")}
    rep["dcgm"] = dcgm()
    if nv is not None:
        _q(nv, "nvmlShutdown")
    return rep | ({"notes": notes} if notes else {})


def simulated(sms=132, burn_seconds=10, seed=7):
    """CPU mode: a plausible report, clearly marked simulated. H100 SXM for 132 SMs, else an A100 40GB."""
    rnd = random.Random(seed + sms)
    h100 = sms != 108
    base, mem = (720.0, 80.0) if h100 else (265.0, 40.0)
    per = [base * (1 - 0.004 * i) + rnd.uniform(-6, 6) for i in range(max(1, burn_seconds))]
    samples = [{"temp_c": 58 + 2 * i, "power_w": (690 if h100 else 395) + rnd.uniform(-8, 4),
                "sm_mhz": (1980 if h100 else 1410) - 15 * (i > 6), "reasons": ["power cap"] if not h100 and i > 5 else [],
                "power_limit_w": 700.0 if h100 else 400.0, "max_sm_mhz": 1980 if h100 else 1410,
                "mem_mhz": 2619 if h100 else 1215, "max_mem_mhz": 2619 if h100 else 1215, "util_gpu": 100,
                "pcie_gen": 5 if h100 else 4, "pcie_width": 16} for i in range(len(per))]
    return {
        "grade": GRADE, "source": "simulated",
        "device": {"name": "NVIDIA H100 80GB HBM3" if h100 else "NVIDIA A100-SXM4-40GB",
                   "uuid": "GPU-00000000-0000-4000-8000-00000000c0de", "driver": "550.90.07", "cuda": "12.4",
                   "vbios": "96.00.99.00.01" if h100 else "92.00.45.00.03", "memory_gib": mem, "mig": "disabled",
                   "pcie": {"gen": 5 if h100 else 4, "width": 16, "max_gen": 5 if h100 else 4, "max_width": 16},
                   "nvlink": {"up": 18 if h100 else 12, "down": 0}, "ecc": {"enabled": True, "pending": True}},
        "memory": {"ecc_errors": {"volatile": {"corrected": 0, "uncorrected": 0},
                                  "aggregate": {"corrected": 0 if h100 else 3, "uncorrected": 0}},
                   "retired_pages": {"single_bit": 0, "double_bit": 0, "pending": False},
                   "remapped_rows": {"corrected": 0 if h100 else 1, "uncorrected": 0, "pending": False,
                                     "failure": False}},
        "burn": summarize(burn_seconds, 8192, per, samples),
        "dcgm": {"available": False, "note": "not available"},
    }
