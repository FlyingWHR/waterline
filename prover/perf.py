"""Measured performance profile on the pod (docs/METRICS.md). torch + CuPy; imported only in GPU mode.

Runs after the commit, so it never eats the deadline. Every timed series: warm-up, synchronize, then per trial an
L2 flush (outside the events) and CUDA events around the timed call only. Stops starting new benchmarks once
the budget is spent.
"""
import time

import cupy as cp
import torch

from prover import health
from prover.metrics import metric, missing

GIB = 1 << 30
N = 8192               # GEMM size: 1.1e12 ops per trial, ~1 ms on Hopper, far above event resolution
BW_BYTES = 4 * GIB     # >> any L2 (H100 50 MB, B200 ~126 MB)
HOST_BYTES = GIB


class Nvml:
    """Context snapshots (clock, temperature, power, throttle reasons); None when NVML is missing."""

    def __init__(self):
        try:
            import pynvml as nv
            nv.nvmlInit()
            self.nv, self.h = nv, health.nvml_handle(nv)
        except Exception:
            self.nv = None

    def snap(self):
        if self.nv is None:
            return None
        s = health.live(self.nv, self.h)
        return {k: s.get(k) for k in ("sm_mhz", "mem_mhz", "temp_c", "power_w", "power_limit_w", "reasons")}


def _timed(fn, warmup, trials, flush=None):
    """Milliseconds per trial from CUDA events."""
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    ms = []
    for _ in range(trials):
        if flush is not None:
            flush.zero_()
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record()
        fn()
        e.record()
        e.synchronize()
        ms.append(s.elapsed_time(e))
    return ms


def _gemm(key, flush):
    ops = 2 * N**3
    if key == "int8_tops":
        a = torch.randint(-128, 128, (N, N), dtype=torch.int8, device="cuda")
        b = torch.randint(-128, 128, (N, N), dtype=torch.int8, device="cuda").t().contiguous().t()  # column-major
        fn = lambda: torch._int_mm(a, b)  # noqa: E731
    elif key == "bf16_tflops":
        a, b = (torch.randn(N, N, device="cuda", dtype=torch.bfloat16) for _ in range(2))
        c = torch.empty_like(a)
        fn = lambda: torch.matmul(a, b, out=c)  # noqa: E731
    else:
        a = torch.randn(N, N, device="cuda").to(torch.float8_e4m3fn)
        b = torch.randn(N, N, device="cuda").to(torch.float8_e4m3fn).t()  # column-major
        one = torch.tensor(1.0, device="cuda")
        fn = lambda: torch._scaled_mm(a, b, scale_a=one, scale_b=one, out_dtype=torch.bfloat16)  # noqa: E731
        try:
            fn()
            torch.cuda.synchronize()
        except Exception as e:  # no FP8 tensor cores (Ampere and older): not a failure of the run
            return missing(key, supported=False, note=f"{type(e).__name__}: {str(e)[:120]}")
    ms = _timed(fn, 5, 20, flush)
    return metric(key, [ops / (t / 1e3) / 1e12 for t in ms], **({"supported": True} if key == "fp8_tflops" else {}))


def _hbm(key, flush):
    free = torch.cuda.mem_get_info()[0]
    size = min(BW_BYTES, (free - GIB) // 2) // (64 << 20) * (64 << 20)
    src = torch.ones(size, dtype=torch.uint8, device="cuda")
    if key == "hbm_copy_tbs":
        dst = torch.empty_like(src)
        fn, moved = (lambda: dst.copy_(src)), 2 * size
    else:
        v = src.view(torch.int32)
        fn, moved = (lambda: v.sum()), size
    ms = _timed(fn, 3, 20, flush)
    return metric(key, [moved / (t / 1e3) / 1e12 for t in ms], bytes=size)


def _host(key, flush):
    host = torch.empty(HOST_BYTES, dtype=torch.uint8, pin_memory=True)
    dev = torch.empty(HOST_BYTES, dtype=torch.uint8, device="cuda")
    fn = (lambda: dev.copy_(host, non_blocking=True)) if key == "h2d_gbs" else (lambda: host.copy_(dev, non_blocking=True))
    ms = _timed(fn, 2, 10)
    return metric(key, [HOST_BYTES / (t / 1e3) / 1e9 for t in ms], bytes=HOST_BYTES)


def _launch(key, flush):
    k = cp.RawKernel('extern "C" __global__ void nop() {}', "nop")
    sync = cp.cuda.get_current_stream().synchronize
    us = []
    for i in range(1050):
        t = time.perf_counter()
        k((1,), (1,), ())
        sync()
        if i >= 50:
            us.append((time.perf_counter() - t) * 1e6)
    return metric(key, us)


def mem_alloc():
    """Largest amount of memory actually allocated, written and read back (GiB)."""
    torch.cuda.empty_cache()
    cp.get_default_memory_pool().free_all_blocks()
    chunks = []
    try:
        for size in (GIB, 64 << 20):
            while True:
                try:
                    c = torch.empty(size, dtype=torch.uint8, device="cuda")
                except torch.cuda.OutOfMemoryError:
                    break
                v = (len(chunks) % 127 + 1) * 0x01010101  # < 2^31, differs between neighbours: catches aliasing
                c.view(torch.int32).fill_(v)
                chunks.append((c, v))
        torch.cuda.synchronize()
        if chunks:
            chunks.pop()  # headroom for the reduction's scratch; not counted
        torch.cuda.empty_cache()
        ok = [c.numel() for c, v in chunks if int(c.view(torch.int32).sum()) == v * (c.numel() // 4)]
        return metric("mem_alloc_gib", [round(sum(ok) / GIB, 2)], chunks=len(chunks), bad_chunks=len(chunks) - len(ok))
    finally:
        chunks.clear()
        torch.cuda.empty_cache()


BENCHES = [("int8_tops", _gemm), ("bf16_tflops", _gemm), ("fp8_tflops", _gemm), ("hbm_copy_tbs", _hbm),
           ("hbm_read_tbs", _hbm), ("h2d_gbs", _host), ("d2h_gbs", _host), ("launch_us", _launch)]


def run(budget_s=60):
    """All measured metrics except sm_count and stability_cv (those come from the staircase and the burn)."""
    nvml, t0, out = Nvml(), time.perf_counter(), {}
    flush = torch.empty(max(256 << 20, 2 * torch.cuda.get_device_properties(0).L2_cache_size),
                        dtype=torch.uint8, device="cuda")

    def one(key, fn, *args):
        if time.perf_counter() - t0 > budget_s:
            return missing(key, skipped=True, note=f"time budget of {budget_s} s spent")
        start = nvml.snap()
        try:
            m = fn(*args)
        except Exception as e:  # one broken benchmark must not lose the others
            m = missing(key, note=f"{type(e).__name__}: {str(e)[:160]}")
        torch.cuda.empty_cache()
        return m | {"context": {"start": start, "end": nvml.snap()}}

    for key, fn in BENCHES:
        out[key] = one(key, fn, key, flush)
    flush = None  # give its memory back before measuring how much can be allocated
    out["mem_alloc_gib"] = one("mem_alloc_gib", mem_alloc)
    return out
