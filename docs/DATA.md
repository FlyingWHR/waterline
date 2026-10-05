# Waterline data: methodology and dictionary

Delivered GPU compute as renters get it: is it the listed chip, what share of its rating it delivers, how it holds
up under sustained load, and what the machine around it holds back. Collected by a check the renter runs inside the
rented machine; graded by the API; each verdict's hash is recorded on Marks (Ethereum Sepolia) under the GPU's ENS
name.

## Access

| Endpoint | What |
|---|---|
| `GET /api/data/checks` | One row per check, newest first. `?format=csv`, `?since=2026-10-01` (UTC), `?include_simulated=true` |
| `GET /api/data/summary` | Per provider × listed model statistics (below). `?since=` |
| `GET /api/data/dictionary` | The table below as JSON |
| `GET /api/reports/<id>` | The full report behind a row |
| `GET /api/reports/<id>/evidence` | Its canonical bytes; keccak256 equals `report_hash` and the GPU name's `waterline.report` |

## How a check collects

1. **Exam (verified).** A seeded INT8 matmul exam under a deadline on the API's clock; the GPU seals a Merkle root,
   then the API re-computes random rows it picks afterwards. The verified throughput is a lower bound (it includes
   generation, hashing and network).
2. **Chip (measured).** Core count from a timing staircase, FP8 support, memory bandwidth; matched to 37 reference
   models (`core/gpu_specs.json`).
3. **Performance profile (measured).** INT8, BF16, FP8 matmuls, memory copy and read, host↔device copies, kernel
   launch latency: CUDA events, L2 flushed, medians of 10–20 trials (`docs/METRICS.md`).
4. **Burn (measured + reported).** Back-to-back BF16 matmuls, TFLOPS each second, NVML sampled each second (clocks,
   temperature, power, clock-event reasons). `--sustain 30m` runs a long one; `sustained_drop_pct` compares its
   first and last window.
5. **Host (reported + measured).** Usable CPU cores after the container's quota, RAM limit, disk write/read in the
   work directory, download speed.
6. **Findings (API).** Thresholds applied API-side, never by the pod: under 8 usable cores per GPU, less RAM than
   GPU memory, disk under 200 MB/s, downloads under 25 MB/s, a 10 % sustained drop.

## Trust levels

- **verified**: graded by the API against secret, timed work; the machine can't improve it.
- **measured**: timed on the machine by our profiler; a modified profiler could change it.
- **reported**: the machine's own counters (NVML, cgroups); a rigged driver or container could skew it.
- **stated**: what the renter told us (provider, listing, price).

## Statistics (`/api/data/summary`)

Grouped by provider × listed model. Simulated runs (CPU tests) are left out.

- **checks, gpus**: rows, and distinct NVIDIA UUIDs.
- **first, last**: collection window (UTC).
- **pct_rating_\***: p10 / p50 / p90 of the share of the listed model's rating delivered (inclusive quantiles).
- **rates** (`spec_mismatch`, `sustained_throttle`, `cpu_starved`, `disk_slow`, `network_slow`, `pcie_below_max`):
  share of checks where the condition held, with **n = the checks that measured it**. Checks made before an input
  existed don't count against a provider.
- **card_variation_cv_pct**: median, across cards checked at least twice, of the coefficient of variation of BF16
  TFLOPS between checks of the same card.
- **usd_per_bf16_pflops_hour**: the renter's stated price per GPU-hour ÷ delivered BF16 PFLOPS. Two listings at the
  same price that deliver differently cost differently here.

Read a group with its n: a rate over 3 checks is an anecdote, over 300 a measurement.

## Dictionary

| Field | Unit | Trust | Description |
|---|---|---|---|
| `report_id` | — | verified | Check id; /api/reports/<id> has the full report, /api/reports/<id>/evidence its hashed bytes |
| `checked_at` | UTC | verified | When the API issued the verdict |
| `provider` | — | stated | Provider as the renter named it (core/providers.json keeps names consistent) |
| `provider_known` | bool | verified | The provider name is on our list of 63 known providers |
| `gpu_name` | — | verified | ENS name of the GPU: gpu-<first 8 hex of the NVIDIA UUID>.<provider>.waterline.eth |
| `gpu_uuid` | — | reported | NVIDIA UUID as the host's driver reports it |
| `listed_model` | — | stated | The model the listing promised (claimed class, resolved to a reference model) |
| `measured_model` | — | measured | Closest reference model from cores, FP8, bandwidth and throughput |
| `verdict` | — | verified | pass | degraded (right chip, too slow) | fail (wrong chip or wrong answers) |
| `spec_mismatch` | bool | verified | The measured chip class differs from the listing |
| `sms` | count | measured | Streaming multiprocessors counted by the timing staircase |
| `fp8` | bool | measured | FP8 tensor cores present |
| `int8_tops_verified` | TOPS | verified | Exam INT8 ops / API-clock seconds start to commit (a lower bound) |
| `pct_rating_int8_verified` | % | verified | int8_tops_verified as a share of the listed model's dense INT8 rating |
| `bf16_tflops` | TFLOPS | measured | BF16 8192^2 matmul, median of 20, L2 flushed |
| `pct_rating_bf16` | % | measured | bf16_tflops as a share of the listed model's dense BF16 rating |
| `hbm_copy_tbs` | TB/s | measured | Device memory copy, 4 GiB, (read+write)/time |
| `pct_rating_hbm` | % | measured | hbm_copy_tbs as a share of the listed model's memory bandwidth |
| `h2d_gbs` | GB/s | measured | Pinned 1 GiB host-to-device copy |
| `pct_rating_h2d` | % | measured | h2d_gbs as a share of the listed model's host link, per direction |
| `burn_seconds` | s | measured | Length of the sustained BF16 burn |
| `burn_tflops_mean` | TFLOPS | measured | Mean BF16 TFLOPS over the burn |
| `sustained_drop_pct` | % | measured | Mean TFLOPS of the first vs last window of a burn of 60 s or more |
| `throttle_reasons` | list | reported | NVML clock-event reasons seen during the burn, ';'-separated |
| `max_temp_c` | °C | reported | Highest GPU temperature during the burn |
| `pcie_gen` | — | reported | PCIe generation under load |
| `pcie_width` | lanes | reported | PCIe width under load |
| `pcie_below_max` | bool | reported | PCIe link under load runs below the card's maximum generation or width |
| `mig` | bool | reported | MIG is enabled: a slice of the card |
| `ecc_uncorrected` | count | reported | Uncorrected memory errors since boot |
| `host_cpu_usable` | cores | reported | CPU cores the container may use: affinity capped by the cgroup quota |
| `host_cpu_visible` | cores | reported | CPU cores visible to the container |
| `host_ram_gib` | GiB | reported | RAM the container may use: cgroup limit, else MemTotal |
| `disk_write_mbs` | MB/s | measured | 1 GiB sequential write with fsync, in the renter's work directory |
| `disk_read_mbs` | MB/s | measured | The same file read back after dropping it from the page cache |
| `download_mbs` | MB/s | measured | Repeated 25 MB fetches from speed.cloudflare.com for ~8 s |
| `findings` | list | measured | Delivery findings (cpu, memory, disk, network, sustained), ';'-separated |
| `price_usd_per_gpu_hour` | USD | stated | What the renter pays per GPU-hour |
| `usd_per_bf16_pflops_hour` | USD | measured | price / delivered BF16 PFLOPS: what an hour of delivered compute costs |
| `series` | — | verified | Periodic series id when the renter re-checks one rental (--every) |
| `seq` | — | verified | Place in the series |
| `published` | bool | verified | Recorded on Marks (Ethereum Sepolia) |
| `tx` | — | verified | Marks transaction hash |
| `report_hash` | — | verified | keccak256 of the canonical report; equals the GPU name's waterline.report record |
| `simulated` | bool | verified | A CPU test run, not a GPU (excluded from statistics) |

## Point in time and retention

A row never changes after its verdict: the numbers are frozen in the report and its hash is onchain. Fields that
change later (published, tx, indexed) are outside the hash. Reports are kept for good.

## Limits

- Coverage is whatever renters run: a provider or model with few checks has wide uncertainty.
- A host can recognise a benchmark and serve it better. Periodic checks at jittered times (`--every`) raise the
  cost of that; checks inside real workloads would close it.
- Short checks miss long-run behaviour unless `--sustain` is used; `sustained_drop_pct` is empty for burns under 60 s.
- Host figures describe the container the renter got, which is what their workload gets.
- Price is as the renter states it.

## Change log

- 2026-10-06: dataset endpoints, dictionary and statistics; stated price and price per delivered PFLOPS-hour;
  reports kept for good (they expired after 7 days before).
- 2026-10-04: host report (CPU quota, RAM, disk, download), sustained drop, delivery findings.
