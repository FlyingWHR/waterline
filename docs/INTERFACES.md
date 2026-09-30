# Waterline interfaces (source of truth for every folder)

All code is written at the event (Classic track). Never copy from `files/` (pre-event reference only).
Python 3.12+, numpy. One chain: Ethereum Sepolia (chainId 11155111).

## Identifiers
- GPU label: `gpu-` + the first 8 hex digits of the NVIDIA UUID (`GPU-6f3c2a1b-…` -> `gpu-6f3c2a1b`); any other
  UUID format falls back to the first 8 hex of sha256(uuid_string) (`api/check.gpu_label`).
- Cloud label: lowercase `[a-z0-9-]+` chosen by the agent, e.g. `cloud-a`.
- GPU name: `<gpu label>.<cloud label>.waterline.eth`. `node` = ENS namehash of that name (bytes32).
- Class codes (uint8): 0 unknown, 1 `H100 SXM`, 2 `H100 PCIe`, 3 `A100`, … (25 classes, `core/gpu_classes.json`).
- Verdict codes (uint8): 1 pass, 2 fail, 3 degraded.

## core/ (shared maths, pure Python + numpy)
- `rng.py`: `mix64(x)`, `matrix(seed,n,tag,step)`, `row(seed,n,tag,step,r)`, `col(seed,n,tag,step,c)`.
  Entry = low byte of `mix64(mix64(seed) ^ (((tag<<20)+step)*n + r)*n + c)` as int8 (matches the CUDA kernel).
- `challenge.py`: `Params(seed,n,steps)` with `.fp_key = mix64(seed ^ 0xABCDEF)`;
  `product(p, step)` (int64 C = A@B exact); `fingerprint_rows(C, fp_key)` (uint64 per row: sum of
  mix64(entry ^ fp_key), wrapping); `row_fingerprint(row, fp_key)`; `leaf_hash(fps)` (blake2b-256 of the uint64
  bytes, hex); `merkle_root(leaf_hexes)` (blake2b-256 pairs, duplicate last on odd); `verify_row_entries(p, step,
  r, row, cols)` (recompute entries from seed).
- `verify_vectors.py`: must pass against `core/vectors.json` (frozen copy of `files/vectors.json` data).

## Waterline API (api/, Vercel Python, state in Redis; in-memory store when `REDIS_URL` unset)
All JSON. Errors: `{ "error": "<plain sentence>" }` with 4xx.

`POST /api/check/start`
  in  `{ "cloud": "cloud-b", "uuid": "GPU-…", "claimed_class": 1 (a code from core/gpu_classes.json), "n"?: 16384, "steps"?: 100 }`
  (`n` defaults to 16384, `steps` to `CHECK_STEPS` (default 100). When `DEADLINES` fixes the class's deadline,
  any other n/steps is refused with 400: a fixed deadline only holds for the work it was calibrated on.)
  out `{ "session_id", "seed" (string int), "n", "steps", "fp_key" (string int), "deadline_s" }`
  The API starts its clock here. `deadline_s`: the `DEADLINES` entry for the claim, else the formula in
  docs/METRICS.md (5.0 s for H100 SXM at the defaults).
`POST /api/check/commit`
  in  `{ "session_id", "root", "probes": { "sms": int, "fp8": bool, "clock_ghz": float, "bw_tbs": float, "fingerprint": "0x…32 bytes" } }`
  out `{ "elapsed_s", "samples": [[step,row], …] }`  (8 samples, drawn from secrets AFTER the root arrives)
`POST /api/check/reveal`
  in  `{ "session_id", "fingerprints": {"<step>": [uint64 as strings…]}, "leaf_hashes": {"<step>": hex}, "rows": {"<step>:<row>": [int…]}, "health"?: {…} }`
  `health` is optional and advisory; `health` and `metrics` together are at most 256 KB of JSON (else 413). Stored
  with the report, stamped `"grade": "reported by the machine"`, never graded: it can't change the verdict.
  out `{ "report_id", "verdict": "pass"|"degraded"|"fail", "measured_class": int, "reasons": [str], "gpu_name", "node", "published": bool, "tx": str|null, "via", "report_hash" }`
  Checks: deadline, leaf hashes, root recomputed from leaf hashes, row fingerprints, 64 spot entries per row
  (columns from secret randomness), model from probes and metrics (`api/classify.py`, docs/METRICS.md).
  Verdict: fail if the work fails a check or the measured model isn't the claimed one (or confusable with it);
  degraded if only the deadline was missed; else pass.
  Reasons are plain sentences with class names, e.g. "Measured as A100 (108 SMs, no FP8), listed as H100 SXM.",
  "Answer locked in after 9.6 s; the deadline was 5.0 s."
  A **pass or degraded is published immediately** (Marks.record). A fail is stored as pending, `published: false`.
`POST /api/report/publish` in `{ "report_id", "listing", "report_anyway"?: bool }` ->
  `{ "published": bool, "tx"?: str, "via"?: str, "status_text": str }`
  The listing (URL or text, 8+ characters) is required (422 without it) and stored beside the report. Jev reads it: a
  confident reading as another class than the one reported -> 409 unless `report_anyway`. Each report is its own voter:
  `gpuVoter = keccak256("gpu:" || report_id || node)`, `providerVoter = keccak256("provider:" || report_id ||
  providerNode)`; call Marks.record(fail). 409 when already published.
## Marks contract (contracts/, Solidity ^0.8.25, Foundry)
```
constructor(address admin, address reporter, bytes32 parent)   parent = namehash(waterline.eth)
record(bytes32 cloudLabel, bytes32 gpuLabel, uint8 verdict, uint8 cls, uint16 cores, bytes32 fingerprint,
       bytes32 gpuVoter, bytes32 providerVoter, uint32 topsX10, uint16 pctBps, bytes32 reportHash)
  provider = keccak(parent, cloudLabel), node = keccak(provider, gpuLabel): a GPU always rolls up to its provider.
  Needs ROLE_REPORTER on node, or on provider (covers all its GPUs), or root.
  1 pass: passes++, sinceFail++ (2 since the last fail with active > 0 -> active = 0, recoveries++)
  2 fail: both voters != 0; gpuVoter once per node ever; providerVoter counted once per provider; fails++, humans++, active++
  3 degraded: degraded++ (right chip, correct answers, too slow: no voter, no recovery credit, never "failed")
setNote(bytes32 providerNode, string note)   ROLE_NOTE on providerNode; <= 280 bytes; changes no count
Each published check: node = keccak(gpuNode, keccak("<n>")), i.e. namehash("<n>." + GPU name); checkNode(gpuNode, n).
  Keys on a check name: waterline.verdict (pass|fail|degraded), class, cores, tops, pct_of_spec, at, report, gpu.
  The GPU name adds waterline.checks. The API stores check_no / check_name on the report when it publishes.
setClassNames(uint8[] codes, string[] names)  ROLE_CLASSES at the root (admin); names waterline.class; changes no count.
                                             Codes and names: core/gpu_classes.json (append only). GET /api/gpu-classes serves it.
event Reported(bytes32 indexed node, bytes32 indexed provider, bytes32 gpuVoter, bytes32 providerVoter, uint8 verdict,
               uint8 cls, uint16 cores, bytes32 fingerprint, uint32 topsX10, uint16 pctBps, uint64 at, uint32 passes,
               uint32 fails, uint32 active, bytes32 reportHash)
event ProviderTally(bytes32 indexed provider, uint32 gpus, uint32 failedGpus, uint32 humans, uint32 passes, uint32 fails, uint64 at)
event NoteSet(bytes32 indexed provider, string note)
text(bytes32 node, string key) view -> string
  GPU keys: waterline.status, .class, .cores, .fingerprint, .passes, .fails, .humans, .recoveries, .degraded, .tops,
            .pct_of_spec, .report (0x hex of the latest reportHash)
  provider keys (<cloud>.waterline.eth): waterline.status ("1 of 3 GPUs failed · 2 failure reports"), .gpus,
            .failed_gpus, .humans, .passes, .fails, .degraded, .note
resolve(bytes dnsName, bytes data) -> bytes   (ENSIP-10; handles text(bytes32,string) and addr(bytes32) -> zero)
supportsInterface: 0x01ffc9a7, 0x9061b923 (IExtendedResolver), 0x59d1d43c (text), IEnhancedAccessControl
grantRoles(uint256(node), ROLE_REPORTER|ROLE_NOTE, account) / grantRootRoles(...)  admin only (ENSv2 EnhancedAccessControl)
```
GPU status: active >= 2 -> failed; active == 1 -> suspect · 1 of 2 reports; last verdict degraded -> degraded;
fails > 0 -> recovered; passes > 0 -> pass; else unknown.
Class shown on the name is the last measured class.

## Profiler (prover/, runs in the rented pod)
`python -m prover.run --api <url> --cloud cloud-b --claimed 1 [--cpu] [--n N --steps S]`
Reads the GPU UUID (nvidia-smi / torch), calls start, computes all steps (GPU: CuPy + torch._int_mm; `--cpu`: core/),
sends commit with probes, then reveal with the requested rows; prints the API's JSON result.
`--burn-seconds S` (default 10, 0 skips the burn). After commit it builds the health report (prover/health.py)
and sends it with the reveal; CPU mode sends a report marked `"source": "simulated"`. `--sustain 30m` sets a long
burn; `--disk-dir` and `--no-net` shape the host report (`health.host`: `{gpus, cpu: {visible, quota, usable, model},
memory_gib, disk: {path, write_mbs, read_mbs, free_gib}, download_mbs, notes?}`). The reveal answer and the stored
report carry `delivery: [{kind, text}]` from `api/perf.delivery` (docs/METRICS.md).
Health report: `{ grade, source: "nvml"|"torch"|"simulated"|"error", device: {name, uuid, driver, cuda, vbios,
memory_gib, mig, pcie: {gen, width, max_gen, max_width}, nvlink: {up, down}|null, ecc: {enabled, pending (mode after next reboot)}},
memory: {ecc_errors: {volatile|aggregate: {corrected, uncorrected}}, retired_pages: {single_bit, double_bit, pending},
remapped_rows: {corrected, uncorrected, pending, failure}}, burn: {seconds, n, dtype, tflops: {mean, std, min, max},
per_second: [TFLOPS], reasons_seen: [str], max_temp_c, max_power_w, power_limit_w, min_sm_mhz, max_sm_mhz, mem_mhz,
util_gpu, pcie_gen, pcie_width}, dcgm: {available, passed?, tests?: [{name, result}], note?}, notes?: [str] }`.
Any NVML value the driver doesn't offer is null.
Probes: SM-count staircase (spin kernel, one block per SM via large dynamic shared memory), FP8 capability
(torch._scaled_mm on float8_e4m3fn), measured clock, copy bandwidth, per-SM timing fingerprint
(sha256 of quantised per-SM cycle ratios, 32 bytes). CPU mode reports fixed test probes.

## Agent (agent/, renter's laptop)
`python -m agent check --pod ssh://… --cloud cloud-b --listing "H100 80GB SXM"`
· `python -m agent history` · `python -m agent choose --listings listings.json`
Jev (optional, `TYPESAFE_API_KEY`): listing text -> class Choice; below 0.9 confidence ask the human. Without a key: rule-based parse.
Check flow: SSH to the pod, run the profiler there, show verdict; on fail stop paying, then publish the failure with the listing (`POST /api/report/publish`), show outcome.
History: MultiBaas `POST {MB_URL}/api/v0/queries` (groupBy node, last/max aggregators) with `MB_API_KEY`; skip suspect/failed GPUs.

## Control panel (web/, served by the API)
The API serves `web/` at `/` (same origin, no CORS, no keys in the browser). Name lookup reads ENS text records in
the browser (viem via CDN, Universal Resolver on Sepolia); GPU and provider tables come from the endpoints below.
Read endpoints (JSON):
- `GET /api/health` -> `{ api: "ok", store: "redis"|"memory", chain: {mode: "live"|"dry-run", chain_id, marks, reporter, reporter_balance_eth}, multibaas: {configured: bool, url, webhook: bool (MB_WEBHOOK_SECRET set)}, ens: {parent: "waterline.eth", universal_resolver, rpc} }`
  (`universal_resolver` from `ENS_UNIVERSAL_RESOLVER`, else `contracts/ens.sepolia.json` when present; `rpc` = `PUBLIC_SEPOLIA_RPC`, default publicnode; `SEPOLIA_RPC` is never shown because it may carry a key.)
- `GET /api/providers` -> `{ source, error, providers: [{ provider_node, name, gpus, failed_gpus, humans, passes, fails, degraded, status }] }` (MultiBaas `ProviderTally`, else this API's own replay).
- `GET /api/reports?limit=50` -> `[{ report_id, created_at, gpu_name, node, cloud, claimed_class, measured_class, verdict, published, tx, status_text }]` newest first (API keeps an index of recent report ids in the store).
- `GET /api/reports/{id}` -> full report: the above + `probes`, `staircase` (map of blocks -> ms, if sent), `elapsed_s`, `deadline_s`, `samples`, `reasons`, `n`, `steps`, `health` (or null), and throughput against the listed class:
  `ops_total = 2 * n^3 * steps` (INT8 ops asked for), `effective_tops = ops_total / elapsed_s / 1e12`,
  `spec_tops` = NVIDIA dense INT8 rating of the CLAIMED class (H100 SXM 1979, H100 PCIe 1513, A100 624),
  `pct_of_spec = 100 * effective_tops / spec_tops` (4 significant digits). `elapsed_s` includes generation,
  hashing and network time, so this is a lower bound.
- `GET /api/gpus` -> `{ source: "multibaas"|"local", error: str|null, gpus: [{ node, gpu_name?, cls, cores, passes, fails, humans, status, last_at }] }`. Source: MultiBaas event query when `MB_URL`+`MB_API_KEY` are set (server-side, admin key never leaves the API); otherwise, or if MultiBaas fails (`error` set), built from the API's own published reports. `gpu_name` is filled from the API's reports when known.
Commit `probes` may include optional `staircase: {"64": ms, ...}` which the API stores with the report. The profiler always sends it (CPU mode: synthetic, step at the simulated SM count).
Report `status_text`: "Published." / "Publishing failed." / "Waiting for the listing you rented. Nothing is published yet." / "Recorded on Marks.".

## Performance profile (Ookla-style): shared data model
Every metric is an object: `{ "value": float|null, "unit": str, "method": str, "trust": "verified"|"measured"|"reported",
"n": int, "median": float, "p10": float, "p90": float, "cv": float, "spec": float|null, "pct_of_spec": float|null,
"spec_source": str|null, "expected_pct": [lo, hi]|null, "flag": null|"low"|"high"|"unstable" }`.
Trust grades: **verified** = computed from work the API re-graded and timed on its own clock (can't be inflated);
**measured** = timed by our code in the pod with CUDA events (a malicious driver could lie);
**reported** = read from the driver/NVML (fakeable).

Report field `metrics` (profiler sends the measured ones in the reveal; the API adds the verified ones):
| key | unit | trust | method (see docs/METRICS.md) |
|---|---|---|---|
| `int8_tops_verified` | TOPS | verified | ops_total / API-clock seconds from start to commit (lower bound; includes generation + network) |
| `int8_tops` | TOPS | measured | torch._int_mm n=8192, CUDA events, warm-up 5, median of 20 |
| `bf16_tflops` | TFLOPS | measured | torch.matmul BF16 n=8192, same timing |
| `fp8_tflops` | TFLOPS | measured | torch._scaled_mm e4m3, same timing; null + `supported:false` when it errors |
| `hbm_copy_tbs` / `hbm_read_tbs` | TB/s | measured | device copy and read-reduction over ≥ 4 GiB (≫ L2), L2 flushed between trials |
| `h2d_gbs` / `d2h_gbs` | GB/s | measured | pinned host memory, 1 GiB transfers |
| `launch_us` | µs | measured | empty-kernel round trip, median of 1000 |
| `mem_alloc_gib` | GiB | measured | largest allocation actually written and read back (catches 40 GB sold as 80 GB) |
| `sm_count` | SMs | measured | staircase, sweep 32..256 blocks, independent of the driver's reported count |
| `stability_cv` | % | measured | coefficient of variation of per-second BF16 throughput over the burn |
Plus `classification`: `{ "best_match": model_id, "candidates": [{"id", "distance", "why"}], "claimed": model_id,
"consistent": bool }` using `core/gpu_specs.json` (features: sm_count, fp8 supported, mem_alloc_gib, hbm bandwidth,
int8/fp8 throughput ratios; confusable pairs reported as ambiguous rather than guessed).

Comparison endpoints:
- `GET /api/compare/{report_id}` -> `{ vs_spec: {metric: pct}, vs_models: [{id, name, metric values from spec}],
  cohort: {model_id, n, percentiles: {metric: pct_rank}} | {n, note: "not enough checks yet"} }` (cohort = same
  best_match model; percentiles only when n >= 5).
- `GET /api/leaderboard?model=` -> per (model, cloud): n, median `int8_tops_verified`, median pct_of_spec, pass rate.
Chain: `Marks.record` gains `uint32 topsX10` (verified INT8 TOPS × 10) and `uint16 pctBps` (pct of spec × 100);
text records `waterline.tops`, `waterline.pct_of_spec`; `Reported` event carries both.

## MultiBaas write path, listener and agent actions (Sat 26 Sep)
- Write paths (`/api/health` → `chain.write_path`): `multibaas` when `MB_URL` + `MB_API_KEY` + `REPORTER_KEY` are set
  (compose `methods/record` via MultiBaas, verify calldata/to/value, sign locally, submit via
  `/chains/ethereum/transactions/submit`), else `rpc` (direct JSON-RPC), else `dry-run`.
- Reports gain `via` (write path used), `indexed` (bool) and `indexed_at` (unix seconds|null); reveal and report/publish
  outputs gain `via`.
- `POST /api/webhooks/multibaas`: HMAC-SHA256(`MB_WEBHOOK_SECRET`, raw body + `X-MultiBaas-Timestamp`) in
  `X-MultiBaas-Signature` (hex); stale > 5 min or bad signature → 401. Marks each matching `Reported` event's report
  indexed → `{ok, indexed}`.
- Env: `MB_MARKS_ALIAS`, `MB_MARKS_LABEL` (default `marks`), `MB_WEBHOOK_SECRET`, `WATERLINE_STOP_CMD`.
- Agent: `check --pod-id --stop-cmd` (on FAIL, runs the stop command before reporting; never on PASS).
  `choose`: history only: skip any GPU with ≥ 1 failure report or suspect/failed status, or over `--max-price`;
  then most passes, then cheapest. Jev only reads listing text.
- The `Reported` layout is hard-coded in `api/app.py` and `agent/history.py`; `tests/api/test_event_layout.py`
  checks both against `contracts/src/Marks.sol`.

## Evidence: reportHash (Sat 26 Sep)
- **Canonical report** = the stored report minus the fields that change after the verdict (`published`, `tx`,
  `via`, `indexed`, `indexed_at`, `status_text`, `report_hash`), as JSON with sorted keys, no whitespace
  (`separators=(",",":")`), UTF-8 (`api/app.py: canonical`). `report_hash = keccak256(canonical bytes)`, computed
  once at reveal (the verdict) and stored on the report. It is sent as `reportHash` in `Marks.record` on both the
  pass path and the published-fail path; Marks keeps the latest in `waterline.report` and emits it in `Reported`
  (index 14; the MultiBaas queries don't select it).
- `GET /api/reports/{id}/evidence` -> the exact canonical bytes (`application/json`) + header
  `X-Waterline-Report-Hash`. `/api/reports`, `/api/reports/{id}` and the reveal output carry `report_hash`.
- `GET /api/reports/by-hash/{report_hash}` -> the check summary whose `report_hash` matches (404 if none): ENS to the
  check, from a GPU name's `waterline.report` or any `Reported` event. The panel opens it at `#/r/<hash>`.
- **Anyone can verify**, without trusting the API:
  ```
  curl -s $API/api/reports/$ID/evidence -o report.json
  cast keccak "$(cat report.json)"              # or: python -c 'import sys,eth_utils;print(eth_utils.keccak(open("report.json","rb").read()).hex())'
  cast call $MARKS_ADDRESS "text(bytes32,string)(string)" $(cast namehash $GPU_NAME) waterline.report --rpc-url $RPC
  # or through ENS: viem getEnsText({ name: GPU_NAME, key: "waterline.report" }) (universal resolver)
  ```
  `keccak256(body) == waterline.report` on the GPU's ENS name means the evidence is the one Marks recorded.
  The GPU's text record holds its *latest* report; check an older one against its own `Reported` event
  (`reportHash`, on Etherscan or from MultiBaas) or its check name's `waterline.report`. The panel's Verify button
  does the ENS read in the browser.
