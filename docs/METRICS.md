# Waterline metrics: definitions, methods, trust

The contract for every number in the performance profile (`metrics` in a report, shape in
`docs/INTERFACES.md` > "Performance profile"). Code: `prover/perf.py` (GPU), `prover/metrics.py` (statistics,
staircase, CPU simulation), `api/perf.py` (spec, %-of-spec, flags, verified metric, cohorts),
`api/classify.py` (model match), `api/check.py` (verdict, deadline).

## Conventions

- **Units are SI.** TB/s = 10^12 B/s, GB/s = 10^9 B/s, TOPS = 10^12 int ops/s, TFLOPS = 10^12 flop/s.
  One multiply-add = 2 ops. GiB (2^30 B) only for `mem_alloc_gib`.
- **Specs are dense** (no structured sparsity) from `core/gpu_specs.json`. Where NVIDIA publishes only a sparse
  figure it was halved there (`derived_from_sparse`); `spec_source` says so, and says "unverified" when the field
  is in the model's `unverified` list.
- **Statistic.** `value` is the **median** of `n` trials. `p10`/`p90` are the 10th/90th percentiles
  (`statistics.quantiles(n=10, method="inclusive")`), `cv` is the coefficient of variation in **percent**
  (population stddev / mean × 100). Single measurements (`sm_count`, `mem_alloc_gib`, `stability_cv`,
  `int8_tops_verified`) have `n = 1`, `median = p10 = p90 = value`, `cv = 0`.
- **Timer.** GPU work is timed with CUDA events recorded on the same stream around the timed call only
  (`torch.cuda.Event(enable_timing=True)`, `end.synchronize()`), never with host clocks, except `launch_us`,
  which is a host round trip by definition.
- **Warm-up** before every timed series (first-call JIT, cuBLASLt heuristics, clock ramp-up), then
  `torch.cuda.synchronize()`.
- **L2 flush between trials**: zero a device buffer of `max(256 MiB, 2 × reported L2)` before each trial (outside
  the events). 256 MiB is ≥ 2× every L2 in the table (H100 50 MB, B200 about 126 MB), so a driver that
  under-reports L2 still gets a full flush.
- **Buffers defeat caches**: bandwidth buffers are 4 GiB (≫ any L2; 80× H100's), shrunk only when free memory
  forces it (the actual size is in the metric's `bytes`).
- **Context**: each metric carries `context: {start, end}`, NVML snapshots taken right before and after it:
  `sm_mhz, mem_mhz, temp_c, power_w, power_limit_w, reasons` (throttle reasons). Null when NVML is absent.
- **Budget**: the measured suite runs after the commit (the deadline clock has stopped) and stops starting new
  benchmarks once `--perf-seconds` (default 60) is spent; skipped metrics have `value: null, skipped: true`.
  Typical total on an H100 is about 10–20 s plus the 10 s health burn.
- **Flags** (API, `api/perf.py`): `unstable` if `cv > 5 %` (for `stability_cv`: value > 3 %); else `low` /
  `high` if `pct_of_spec` is outside `expected_pct`; else null. A flag never changes the verdict.

## Trust grades

| grade | meaning | who can inflate it |
|---|---|---|
| **verified** | computed from work the API re-graded (Merkle root + spot checks), timed on the API's own clock | nobody: a faster answer needs faster hardware (or a precomputed answer, which the fresh seed rules out) |
| **measured** | timed by our code in the renter's pod with CUDA events | only a malicious driver/runtime that lies about event times or fakes results; a host can always make it *lower* |
| **reported** | read from the driver / NVML (names, clocks, ECC, power) | anyone who controls the driver or an NVML shim |

The profiler runs in the renter's pod, launched by the renter. The threat is the host (driver, hypervisor,
LD_PRELOAD shims, time-slicing), not the renter. `api/perf.annotate` re-stamps `trust` from its own table and
drops any `int8_tops_verified` sent by the pod.

## Summary table

| key | unit | trust | method | spec compared to | expected % of spec (heuristic) |
|---|---|---|---|---|---|
| `int8_tops_verified` | TOPS | verified | 2·n³·steps / API seconds start→commit | dense INT8 | none (lower bound, depends on n, steps, network) |
| `int8_tops` | TOPS | measured | `torch._int_mm` 8192², warm-up 5, median of 20 | dense INT8 | 60–85 (90–105 GeForce/workstation) |
| `bf16_tflops` | TFLOPS | measured | `torch.matmul` BF16 8192², same | dense BF16 (FP16 on T4/V100) | 60–85 |
| `fp8_tflops` | TFLOPS | measured | `torch._scaled_mm` e4m3→bf16 8192², same; null + `supported:false` on error | dense FP8 | 55–85 |
| `hbm_copy_tbs` | TB/s | measured | device→device copy of 4 GiB, (read+write bytes)/time, warm-up 3, median of 20 | `bw_tbs` | 80–92 |
| `hbm_read_tbs` | TB/s | measured | int32 sum over 4 GiB, bytes read/time, same | `bw_tbs` | 85–95 |
| `h2d_gbs` / `d2h_gbs` | GB/s | measured | pinned 1 GiB `copy_(non_blocking)`, warm-up 2, median of 10 | PCIe x16 per direction (Gen3 15.75, Gen4 31.5, Gen5 63, Gen6 121); NVLink-C2C 450 | PCIe 75–90; C2C 30–90 |
| `launch_us` | µs | measured | empty CuPy kernel launch + stream sync, host clock, warm-up 50, median of 1000 | none | none (typical 4–10 µs) |
| `mem_alloc_gib` | GiB | measured | allocate 1 GiB chunks then 64 MiB until OOM, write a per-chunk pattern, read back by sum | `mem_gb` (vendor GB ≈ GiB) | 90–101 |
| `sm_count` | SMs | measured | spin-kernel staircase 32..256 blocks, 1 block/SM | `sms` | exactly 100 |
| `stability_cv` | % | measured | CV of per-second BF16 TFLOPS over the health burn (default 10 s) | none | ≤ 3 % |

Expected ranges are **heuristics from common practice, not calibrated on our pods**: large dense GEMMs through
cuBLAS(Lt) typically reach 60–85 % of datasheet peak on datacenter parts (datasheet peaks assume max boost clock,
sustained clocks under a power cap are lower); device copies reach 80–92 % of the memory's theoretical bandwidth
(nvbandwidth / STREAM-style copy on HBM); pinned PCIe copies reach about 25–27 GB/s on Gen4 x16 and 50–55 GB/s
on Gen5 x16 (TLP/DLLP overhead eats 10–20 %). GeForce and workstation cards often boost above the clock the spec
was computed at, and their tensor specs are derived from a single sparse "AI TOPS" figure, so their upper bound
is wider. **Calibrate every range on real pods** and edit `EXPECTED` in `api/perf.py`.

## Per metric

### `int8_tops_verified` (verified)
- **Definition.** `2 · n³ · steps / elapsed_s / 1e12`, where `elapsed_s` is the API's clock from `/check/start` to
  `/check/commit` and the work is the seeded INT8 matmul challenge that the API then spot-checks.
- **Why a lower bound.** `elapsed_s` includes matrix generation, fingerprinting, hashing, Python, two HTTPS
  round trips and cold starts. At n = 16384, steps = 100 the GEMM share is about 1 s on an H100, so expect
  10–40 % of spec; no expected range is set.
- **Can't fake:** the host can't make it higher without doing the work faster (the answer is checked, the seed is
  fresh, parts to check are drawn after the root is locked). **Can:** make it lower (slow network, throttling).
- **Chain:** `topsX10 = round(value × 10)`, `pctBps = round(pct_of_spec × 100)` (see `api/chain.encode_perf`).

### `int8_tops`, `bf16_tflops`, `fp8_tflops` (measured)
- **Method.** One GEMM per trial, n = 8192 (A 8192×8192, B column-major as cuBLASLt wants for INT8/FP8, output
  INT32 / BF16). 2·8192³ = 1.1 × 10^12 ops per trial, about 0.6–1.5 ms on Hopper: well above event resolution
  (~0.5 µs). Warm-up 5, then 20 trials, L2 flushed before each, CUDA events around the GEMM only.
- **FP8.** `torch._scaled_mm(a_e4m3, b_e4m3, scale 1.0, out bf16)`. If it errors (Ampere and older, no FP8 tensor
  cores) the metric is `value: null, supported: false`. The classifier treats FP8 as supported **only** if its
  throughput is ≥ 1.3× the measured BF16 rate: real FP8 tensor cores run 2× BF16, a software emulation on an
  A100 would run at or below BF16 speed, so "the kernel ran" alone is not trusted.
- **Can't fake:** a faster-than-real GEMM (a lying event timer could, but the verified metric and the
  classifier's ratio features cross-check it). **Can:** make it slower (power cap, clocks, a co-tenant).
- **Confounders:** power limit and boost behaviour (GEMMs are power-bound on H100/B200), thermal state, MIG /
  time-slicing, first-run JIT (handled by warm-up), cuBLASLt algorithm choice per driver/torch version.

### `hbm_copy_tbs`, `hbm_read_tbs` (measured)
- **Method.** Two 4 GiB device buffers. Copy: `dst.copy_(src)`, bytes = 2 × size (read + write). Read:
  `src.view(int32).sum()`, bytes = size (the int64 result is negligible). Warm-up 3, 20 trials, L2 flushed.
- **Why 4 GiB.** H100's L2 is 50 MB, B200's about 126 MB: 4 GiB is 30–80× larger, so at most ~2 % of a trial can
  hit in L2 even without the flush.
- **Spec.** `bw_tbs` (theoretical). Copy reaches less than read because of read/write turnaround.
- **Can't fake:** higher bandwidth than the memory has. **Can:** lower (co-tenant, ECC on GDDR parts costs a few
  percent, memory clock locked low).

### `h2d_gbs`, `d2h_gbs` (measured)
- **Method.** 1 GiB pinned host buffer (`pin_memory=True`) and a 1 GiB device buffer; `dev.copy_(host,
  non_blocking=True)` and back; warm-up 2, 10 trials; events on the copy stream.
- **Spec.** Link generation from the model's `interconnect` (PCIe x16 per direction after 128b/130b encoding;
  NVLink-C2C 900 GB/s bidirectional = 450 per direction on GH200/GB200/GB300).
- **Use.** Separates Grace parts (C2C, hundreds of GB/s) from PCIe hosts, and catches a card in a Gen3 slot or
  an x8 link. Hypervisors with IOMMU/bounce buffers also show up here.
- **Can't fake:** higher than the link. **Can:** lower (slot, riser, NUMA placement, IOMMU).

### `launch_us` (measured)
- **Method.** An empty CuPy `RawKernel` launched 1×1 and the stream synchronised; host `perf_counter` round
  trip; warm-up 50, 1000 samples, median.
- **Use.** Virtualisation / time-slicing / remote-GPU shims inflate it (tens to hundreds of µs). No spec.

### `mem_alloc_gib` (measured)
- **Method.** Free the torch and CuPy pools. Allocate 1 GiB chunks until allocation fails, then 64 MiB chunks.
  Fill chunk i (as int32) with `(i mod 127 + 1) × 0x01010101`. Release the last small chunk (headroom for the
  reduction) and read every other chunk back by `sum()`; count only chunks whose sum matches. Value = verified
  bytes / 2^30. Extras: `chunks`, `bad_chunks`.
- **Catches** 40 GB sold as 80 GB, and aliasing (two "allocations" backed by the same pages: the later write
  changes the earlier chunk's sum). Driver-reported total memory is never used.
- **Confounders.** ECC on GDDR parts reserves ~6 %; CUDA context and other tenants take memory; MIG slices
  expose a fraction. Vendor "80 GB" ≈ 80 GiB on HBM parts, hence 90–101 %.

### `sm_count` (measured)
- **Method.** A spin kernel (`clock64` loop, ~4 M cycles) launched with k blocks for k = 32..256, each block
  asking for the maximum opt-in dynamic shared memory, so at most one block fits per SM (more than half of an
  SM's shared memory on every part: 227/228 KB Hopper/B200, 163/164 A100, 99/100 Ada and GeForce Blackwell).
  CUDA events; per k the minimum of 3 launches. Time is flat while k ≤ SMs and doubles at k = SMs + 1.
- **Jump detection.** Median filter (window 5) over k, baseline = median of the first 8 filtered points, jump =
  first k where 3 consecutive filtered points exceed 1.5 × baseline; SMs = jump − 1. The sweep range is never
  sized from the driver's reported SM count (fakeable); 256 covers RTX PRO 6000 (188), RTX 5090 (170),
  B200 (148). Detectable range 40..253 (8 baseline points, 3 after the step). Out of range: slices with
  < 40 SMs (small MIG slices) and AMD (304 CUs).
- **Can't fake** upward (a time-sliced or MIG slice shows its real, smaller count, which is correct).

### `stability_cv` (measured)
- **Method.** The health burn (`prover/health.py`): back-to-back BF16 8192² GEMMs, TFLOPS over each ~1 s window,
  Welford mean/stddev. Value = stddev / mean × 100 over the per-second series.
- **Use.** A GPU that starts fast and sags (thermal or power throttling, a co-tenant arriving) shows a high CV
  even when its median looks fine. Flag `unstable` above 3 %.
- **Sustained drop.** On a burn of a minute or more (`--sustain 30m`), `burn.sustained` compares the mean TFLOPS of
  the first and last window (a tenth of the burn, 10 to 60 s). A short check misses heat soak; this shows it.

## Delivery findings (`api/perf.delivery`)
What the machine around the GPU holds back, from `health.host` (`prover/host.py`) and the burn. Thresholds are
applied API-side; findings are advisory and never change the verdict.

| Finding | From | Threshold |
|---|---|---|
| `cpu` | usable cores: affinity mask capped by the cgroup CPU quota | under 8 per GPU |
| `memory` | cgroup memory limit, else MemTotal | less RAM than the GPUs' memory |
| `disk` | 1 GiB of random data, written with fsync, read back after dropping it from the page cache, in `--disk-dir` | write or read under 200 MB/s |
| `network` | repeated 25 MB fetches from speed.cloudflare.com for ~8 s (`--no-net` skips it) | under 25 MB/s |
| `sustained` | `burn.sustained.drop_pct`, with the throttle reasons seen | 10 % or more |

GPU-side conditions (PCIe below its maximum, MIG, ECC, throttle reasons) are flagged by the panel from the same
health report.

## Classification (`api/classify.py`)

Features, measured only: `sm_count` (staircase), FP8 (throughput rule above; falls back to the commit probe
"it ran"), `mem_alloc_gib`, `hbm_copy_tbs` (falls back to the commit probe's copy bandwidth), `int8_tops`, the
clock-independent `fp8_tflops / int8_tops` ratio, and `h2d_gbs` (separates NVLink-C2C Grace parts from PCIe
hosts; without it H100 NVL and GH200 tie). Each model's expected values: its `sms`, `fp8`,
`0.97 × mem_gb` GiB, `0.86 × bw_tbs`, `0.72 × int8`, its spec FP8/INT8 ratio and `0.83 ×` its host link. Per feature a z-score:
`(sms − spec)/2 SMs`; FP8 mismatch = 5; else `ln(measured/expected) / ln(1 + tol)` with tol 6 % memory, 12 %
bandwidth, 30 % INT8, 25 % ratio, 150 % host link (wide: a Gen5 card in a Gen4 slot stays close, C2C vs PCIe is ~7×).
Distance = √Σz². Missing features are skipped (never guessed), except the SM count: without it there is no
match (`best_match: null`, the verdict fails as "could not be measured").
`ambiguous_with` = every other model within 1.0 of the best distance: declared confusable pairs such as
A100 SXM 40 / PCIe 40 or H200 SXM / NVL come out as one class, and with few features the set grows instead of
a guess being made. `fit: "poor"` when the best distance is above 3. The verdict fails only if the claimed
model is neither the best match nor in `ambiguous_with`.

## Deadline (`api/check.deadline_s`)

`deadline_s = max(5.0, 3.0 + 2·n³·steps / (INT8_dense × 10^12 × 0.25))`, rounded to 0.1 s, using the claimed
model's dense INT8 rating (for the legacy class 3 "A100", the slowest A100 variant). 3.0 s covers generation,
hashing, JIT and two round trips; 0.25 allows the GEMMs to run at a quarter of peak. At n = 16384, steps = 100:
H100 SXM 5.0 s, H100 PCIe 5.3 s, A100 8.6 s. `DEADLINES` env (JSON, keys = model id or class code) overrides.
Models without an INT8 rating get 60 s. Calibrate `BASE`/`MIN_EFF` on the real pods.

## Known confounders (record, don't correct)

Clocks and boost (datasheet peaks assume max boost), enforced power limit, thermal state (hot GPUs clamp near
86–87 °C on H100), MIG and time-slicing, ECC on/off, driver / CUDA / torch versions (cuBLASLt kernels), other
tenants on the same GPU, first-run JIT. The per-metric `context` and the health report carry what NVML shows.
