"""GPU backend: CuPy kernels + torch._int_mm. Imported only on the pod (python -m prover.run without --cpu).

Generator and fingerprint must match core/rng.py and core/challenge.py bit for bit; self_check() tests that.
"""
import hashlib
import statistics
import subprocess
import time

import cupy as cp
import numpy as np
import torch

from core.challenge import TAG_A, TAG_B
from core.rng import col, mix64, row
from prover.metrics import sm_from_staircase, sm_metric, stability_metric

MIX = r"""
__device__ __forceinline__ unsigned long long mix(unsigned long long x) {
  x += 0x9E3779B97F4A7C15ULL;
  x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
  x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
  return x ^ (x >> 31);
}
"""
# i = r*n + c, so base*n*n + i == ((base*n + r)*n + c), the counter in core/rng.py
_gen = cp.ElementwiseKernel(
    "uint64 key, uint64 base, uint64 n", "int8 out",
    "out = (signed char)(mix(key ^ (base * n * n + (unsigned long long)i)) & 0xFFULL);",
    "wl_gen", preamble=MIX)
# per row: sum of mix(int64(entry) ^ fp_key), wrapping mod 2^64
_rowfp = cp.ReductionKernel(
    "int32 c, uint64 key", "uint64 y",
    "mix((unsigned long long)(long long)c ^ key)", "a + b", "y = a", "0",
    "wl_rowfp", preamble=MIX)

PROBES = r"""
extern "C" __global__ void spin(long long cycles, long long* out) {
  // launched with SMEM bytes of dynamic shared memory, so at most one block fits per SM
  long long start = clock64();
  while (clock64() - start < cycles) { }
  if (threadIdx.x == 0) out[blockIdx.x] = clock64() - start;
}

extern "C" __global__ void chase(const unsigned int* next, int hops, unsigned int* smid, long long* cyc,
                                 unsigned int* sink) {
  if (threadIdx.x != 0) return;
  unsigned int id; asm volatile("mov.u32 %0, %%smid;" : "=r"(id));
  unsigned int j = blockIdx.x;
  long long start = clock64();
  for (int h = 0; h < hops; h++) j = __ldcg(next + j);   // L2 pointer chase: latency depends on SM placement
  cyc[blockIdx.x] = clock64() - start;
  smid[blockIdx.x] = id;
  sink[blockIdx.x] = j;  // keeps the chase from being optimised away
}
"""
QUANT = 50         # per-SM ratio buckets of 2%: calibration knob if the fingerprint flickers between runs


def _smem():
    """Max opt-in dynamic shared memory per block: always more than half an SM's (227/228 KB Hopper and B200,
    163/164 A100, 99/100 Ada and GeForce Blackwell), so at most one spinning block fits per SM. Only sizes the
    trick; the SM count itself is measured."""
    try:
        return int(cp.cuda.Device().attributes["MaxSharedMemoryPerBlockOptin"])
    except Exception:
        return 99 * 1024


SMEM = _smem()


def _kernel(name):
    k = cp.RawKernel(PROBES, name)
    k.max_dynamic_shared_size_bytes = SMEM
    return k


def _sync():
    cp.cuda.Device().synchronize()


def gpu_uuid():
    """The UUID of the GPU this check runs on. From CUDA first: it honours CUDA_VISIBLE_DEVICES (the renter's choice
    of GPU on a multi-GPU pod), which `nvidia-smi -i 0` would not."""
    try:
        return "GPU-" + str(torch.cuda.get_device_properties(0).uuid)
    except Exception:
        pass
    out = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader", "-i", "0"],
                         capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    return out


def sm_staircase(lo=32, hi=256, cycles=4_000_000, reps=3):
    """Launch k spinning blocks for k = lo..hi (never sized from the driver's SM count). Seconds per k from CUDA
    events, min of reps. Time doubles once k exceeds the SM count; see metrics.sm_from_staircase."""
    k, out = _kernel("spin"), cp.zeros(hi, dtype=cp.int64)
    k((lo,), (32,), (cp.int64(cycles * 50), out), shared_mem=SMEM)  # module load + ~0.1 s to ramp clocks up
    _sync()
    times = {}
    for n in range(lo, hi + 1):
        best = None
        for _ in range(reps):
            s, e = cp.cuda.Event(), cp.cuda.Event()
            s.record()
            k((n,), (32,), (cp.int64(cycles), out), shared_mem=SMEM)
            e.record()
            e.synchronize()
            t = cp.cuda.get_elapsed_time(s, e) / 1e3
            best = t if best is None else min(best, t)
        times[n] = best
    return sm_from_staircase(times), times


def clock_ghz(cycles=200_000_000):
    k, out = _kernel("spin"), cp.zeros(1, dtype=cp.int64)
    k((1,), (32,), (cp.int64(1000), out), shared_mem=SMEM)
    _sync()
    t = time.perf_counter()
    k((1,), (32,), (cp.int64(cycles), out), shared_mem=SMEM)
    _sync()
    return float(out[0]) / (time.perf_counter() - t) / 1e9


def copy_bw_tbs(gib=2, reps=10):
    a = cp.ones(gib << 30, dtype=cp.uint8)
    b = cp.empty_like(a)
    cp.copyto(b, a)
    _sync()
    t = time.perf_counter()
    for _ in range(reps):
        cp.copyto(b, a)
    _sync()
    bw = 2 * a.nbytes * reps / (time.perf_counter() - t) / 1e12  # read + write
    del a, b
    cp.get_default_memory_pool().free_all_blocks()
    return bw


def has_fp8():
    """FP8 tensor-core matmul exists on Hopper, not on Ampere (A100): it errors there, heat can't fake it."""
    try:
        a = torch.randn(64, 64, device="cuda").to(torch.float8_e4m3fn)
        b = torch.randn(64, 64, device="cuda").to(torch.float8_e4m3fn).t()  # column-major
        one = torch.tensor(1.0, device="cuda")
        torch._scaled_mm(a, b, scale_a=one, scale_b=one, out_dtype=torch.bfloat16)
        torch.cuda.synchronize()
        return True
    except Exception:
        return False


def sm_fingerprint(sms, hops=20000, reps=5, words=1 << 20):
    """Per-SM L2 latency, as ratios to the median, quantised and hashed (sha256, 32 bytes)."""
    rng = np.random.default_rng(7)
    perm = rng.permutation(words).astype(np.uint32)
    nxt = np.empty(words, dtype=np.uint32)
    nxt[perm] = np.roll(perm, -1)  # one big cycle
    nxt = cp.asarray(nxt)
    k = _kernel("chase")
    best = {}
    for _ in range(reps):
        smid, cyc, sink = (cp.zeros(sms, dtype=t) for t in (cp.uint32, cp.int64, cp.uint32))
        k((sms,), (32,), (nxt, np.int32(hops), smid, cyc, sink), shared_mem=SMEM)
        _sync()
        for s, c in zip(cp.asnumpy(smid).tolist(), cp.asnumpy(cyc).tolist()):
            best[s] = min(best.get(s, c), c)
    med = statistics.median(best.values())
    q = [round(best[s] / med * QUANT) for s in sorted(best)]
    return "0x" + hashlib.sha256(",".join(map(str, q)).encode()).hexdigest(), q


class Gpu:
    def __init__(self):
        self.uuid = gpu_uuid()
        self.name = torch.cuda.get_device_name(0)

    def probes(self):
        from core.challenge import Params
        self.fingerprints(Params(1, 256, 1))  # compile the generator and fingerprint kernels before the clock starts
        sms, stair = sm_staircase()
        self.stair = stair
        fp, _ = sm_fingerprint(sms or torch.cuda.get_device_properties(0).multi_processor_count)
        return {"sms": sms or 0, "fp8": has_fp8(), "clock_ghz": round(clock_ghz(), 3),
                "bw_tbs": round(copy_bw_tbs(), 3), "fingerprint": fp}, stair

    def health(self, burn_seconds):
        from prover.health import collect
        return collect(burn_seconds)

    def metrics(self, budget_s, per_second=None):
        """Measured profile (prover/perf.py) plus sm_count from the staircase and stability_cv from the burn."""
        from prover import perf
        cp.get_default_memory_pool().free_all_blocks()
        out = perf.run(budget_s)
        out["sm_count"] = sm_metric(self.stair)
        if per_second:
            out["stability_cv"] = stability_metric(per_second)
        return out

    @staticmethod
    def _mat(p, tag, step):
        out = cp.empty((p.n, p.n), dtype=cp.int8)
        _gen(cp.uint64(int(mix64(np.uint64(p.seed)))), cp.uint64((tag << 20) + step), cp.uint64(p.n), out)
        return torch.from_dlpack(out)

    def _product(self, p, step):
        a = self._mat(p, TAG_A, step)
        b = self._mat(p, TAG_B, step).t().contiguous().t()  # column-major B for cuBLASLt int8
        return torch._int_mm(a, b)  # INT8 x INT8 -> INT32, exact (|entry| <= n * 128^2 < 2^31 for n <= 2^17)

    def fingerprints(self, p):
        key = cp.uint64(p.fp_key)
        fps = [_rowfp(cp.from_dlpack(self._product(p, s)), key, axis=1) for s in range(p.steps)]
        _sync()
        return [cp.asnumpy(f) for f in fps]

    def rows(self, p, samples):
        """C matrices are not kept (100 x 1 GiB at n=16384); recompute the sampled steps after commit."""
        out = {}
        for s in sorted({s for s, _ in samples}):
            c = self._product(p, s)
            for s2, r in samples:
                if s2 == s:
                    out[f"{s}:{r}"] = c[r].cpu().tolist()
        return out


def self_check(seed=0xC0FFEE, n=1024):
    """Generator + fingerprint on the GPU must equal core/ on the CPU. Run on the pod before the demo."""
    from core.challenge import Params, fingerprint_rows, product
    p = Params(seed, n, 2)
    g = Gpu._mat(p, TAG_B, 1).cpu().numpy()
    for i in (0, 7, n - 1):
        assert np.array_equal(g[i], row(seed, n, TAG_B, 1, i)) and np.array_equal(g[:, i], col(seed, n, TAG_B, 1, i))
    gpu = Gpu.__new__(Gpu)
    assert np.array_equal(gpu.fingerprints(p)[1], fingerprint_rows(product(p, 1), p.fp_key))
    print("gpu self-check: PASS")


if __name__ == "__main__":
    self_check()
    sms, stair = sm_staircase()
    print(f"SMs {sms}  fp8 {has_fp8()}  clock {clock_ghz():.2f} GHz  bw {copy_bw_tbs():.2f} TB/s")
    print("fingerprint", sm_fingerprint(sms)[0])
