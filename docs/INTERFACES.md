# Waterline interfaces (source of truth for every folder)

All code is written at the event (Classic track). Never copy from `files/` (pre-event reference only).
Python 3.12+, numpy. One chain: Ethereum Sepolia (chainId 11155111).

## Identifiers
- GPU label: `gpu-<first 8 hex of sha256(uuid_string)>` e.g. `gpu-91c0ab12`.
- Cloud label: lowercase `[a-z0-9-]+` chosen by the agent, e.g. `cloud-a`.
- GPU name: `<gpu label>.<cloud label>.waterline.eth`. `node` = ENS namehash of that name (bytes32).
- Class codes (uint8): 0 unknown, 1 `H100 SXM`, 2 `H100 PCIe`, 3 `A100`.
- Verdict codes (uint8): 1 pass, 2 fail.

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
  in  `{ "cloud": "cloud-b", "uuid": "GPU-…", "claimed_class": 1, "n": 16384, "steps": 100 }`
  out `{ "session_id", "seed" (string int), "n", "steps", "fp_key" (string int), "deadline_s" }`
  The API starts its clock here. `deadline_s` comes from a per-class table (env/config), default 5.0 for class 1.
`POST /api/check/commit`
  in  `{ "session_id", "root", "probes": { "sms": int, "fp8": bool, "clock_ghz": float, "bw_tbs": float, "fingerprint": "0x…32 bytes" } }`
  out `{ "elapsed_s", "samples": [[step,row], …] }`  (8 samples, drawn from secrets AFTER the root arrives)
`POST /api/check/reveal`
  in  `{ "session_id", "fingerprints": {"<step>": [uint64 as strings…]}, "leaf_hashes": {"<step>": hex}, "rows": {"<step>:<row>": [int…]}, "health"?: {…} }`
  `health` is optional, advisory, at most 256 KB of JSON (else 413). Stored with the report, stamped
  `"grade": "reported by the machine"`, never graded: it can't change the verdict.
  out `{ "report_id", "verdict": "pass"|"fail", "measured_class": int, "reasons": [str], "gpu_name", "node", "published": bool, "tx": str|null }`
  Checks: deadline, leaf hashes, root recomputed from leaf hashes, row fingerprints, 64 spot entries per row
  (columns from secret randomness), class from probes (sms 132 + fp8 -> 1, sms 114 + fp8 -> 2, sms 108 & !fp8 -> 3).
  Verdict fail if any check fails OR measured class != claimed class.
  Reasons are plain sentences with class names, e.g. "Measured as A100 (108 SMs, no FP8), listed as H100 SXM.",
  "Answer locked in after 9.6 s; the deadline was 5.0 s."
  A **pass is published immediately** (Marks.record). A fail is stored as pending, `published: false`.
`POST /api/world/login/start` -> `{ "device_id", "user_code", "verification_uri_complete", "expires_in" }`
`POST /api/world/login/poll` in `{ "device_id" }` -> `{ "status": "pending"|"approved"|"denied"|"expired", "agent_token"? }`
`POST /api/report/approve/start` in `{ "report_id", "agent_token" }` -> same shape as login/start (fresh device grant)
`POST /api/report/approve/poll` in `{ "device_id" }` ->
  `{ "status": "pending"|"approved"|"denied"|"expired", "published"?: bool, "tx"?: str, "status_text"?: str }`
  On approve: check `auth_time` is fresh (< 120 s), same human as the agent token, compute
  `voter_id = HMAC_SHA256(VOTER_SECRET, sub || node)`; call Marks.record(fail). Denied/expired: nothing published.
World: OIDC device grant against `WORLD_ISSUER` (default `https://sandbox.auth.world.org`), endpoints
`/api/v1/device_authorization`, `/api/v1/token`, JWKS `/.well-known/jwks.json`, scope `openid`, RS256 id_token.
Env: `WORLD_CLIENT_ID`, `WORLD_CLIENT_SECRET`, `VOTER_SECRET`, `AGENT_TOKEN_SECRET`, `REPORTER_KEY`, `SEPOLIA_RPC`,
`MARKS_ADDRESS`, `REDIS_URL`. `WORLD_MOCK=1` enables a local mock IdP (approve/deny via env or a test hook) for tests.

## Marks contract (contracts/, Solidity ^0.8.25, Foundry)
```
record(bytes32 node, uint8 verdict, uint8 cls, uint16 cores, bytes32 fingerprint, bytes32 voterId, uint32 topsX10, uint16 pctBps)  onlyReporter
  pass: passes++ ; fail: require voterId != 0 and not used for this node; fails++, humans++
event Reported(bytes32 indexed node, bytes32 voterId, uint8 verdict, uint8 cls, uint16 cores, bytes32 fingerprint,
               uint32 topsX10, uint16 pctBps, uint64 at, uint32 passes, uint32 fails, uint32 humans)
text(bytes32 node, string key) view -> string
  keys: waterline.class ("H100 SXM"…), waterline.cores, waterline.fingerprint (0x hex), waterline.passes,
        waterline.fails, waterline.humans, waterline.status ("unknown"|"pass"|"suspect · 1 of 2 humans"|"failed")
resolve(bytes dnsName, bytes data) -> bytes   (ENSIP-10; handles text(bytes32,string) and addr(bytes32) -> zero)
supportsInterface: 0x01ffc9a7, 0x9061b923 (IExtendedResolver), 0x59d1d43c (text)
grantRoles(uint256(node), ROLE_REPORTER, account) / grantRootRoles(ROLE_REPORTER, account)  admin only (ENSv2 EnhancedAccessControl); unauthorized record() reverts EACUnauthorizedAccountRoles
```
Status rule: humans >= 2 -> failed; humans == 1 -> suspect; passes > 0 -> pass; else unknown.
Class shown on the name is the last measured class.

## Profiler (prover/, runs in the rented pod)
`python -m prover.run --api <url> --cloud cloud-b --claimed 1 [--cpu] [--n N --steps S]`
Reads the GPU UUID (nvidia-smi / torch), calls start, computes all steps (GPU: CuPy + torch._int_mm; `--cpu`: core/),
sends commit with probes, then reveal with the requested rows; prints the API's JSON result.
`--burn-seconds S` (default 10, 0 skips the burn). After commit it builds the health report (prover/health.py)
and sends it with the reveal; CPU mode sends a report marked `"source": "simulated"`.
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
`python -m agent login` · `python -m agent check --pod ssh://… --cloud cloud-b --listing "H100 80GB SXM"`
· `python -m agent history` · `python -m agent choose --listings listings.json`
Jev (optional, `TYPESAFE_API_KEY`): listing text -> class Choice; below 0.9 confidence ask the human. Without a key: rule-based parse.
Check flow: SSH to the pod, run the profiler there, show verdict; on fail start approval (prints the World code/link), poll, show outcome.
History: MultiBaas `POST {MB_URL}/api/v0/queries` (groupBy node, last/max aggregators) with `MB_API_KEY`; skip suspect/failed GPUs.

## Web page (web/index.html, static, deployed with the API on Vercel)
Look up a GPU name (viem via CDN, ENS universal resolver on Sepolia, text records); GPU health table from a MultiBaas
DApp User key (read-only); staircase chart from a pasted/linked profiler result. Config in `web/config.js`.

## Control panel additions (web app served by the API)
The web app lives in `web/` and is served by the API at `/` (same origin, no CORS, no keys in the browser).
New read endpoints (JSON):
- `GET /api/health` -> `{ api: "ok", store: "redis"|"memory", chain: {mode: "live"|"dry-run", chain_id, marks, reporter, reporter_balance_eth}, world: {mode: "live"|"mock", issuer}, multibaas: {configured: bool, url}, ens: {parent: "waterline.eth", universal_resolver, rpc} }`
  (`universal_resolver` from `ENS_UNIVERSAL_RESOLVER`, else `contracts/ens.sepolia.json` when present; `rpc` = `PUBLIC_SEPOLIA_RPC`, default publicnode; `SEPOLIA_RPC` is never shown because it may carry a key.)
- `GET /api/reports?limit=50` -> `[{ report_id, created_at, gpu_name, node, cloud, claimed_class, measured_class, verdict, published, tx, status_text }]` newest first (API keeps an index of recent report ids in the store).
- `GET /api/reports/{id}` -> full report: the above + `probes`, `staircase` (map of blocks -> ms, if sent), `elapsed_s`, `deadline_s`, `samples`, `reasons`, `n`, `steps`, `health` (or null), and throughput against the listed class:
  `ops_total = 2 * n^3 * steps` (INT8 ops asked for), `effective_tops = ops_total / elapsed_s / 1e12`,
  `spec_tops` = NVIDIA dense INT8 rating of the CLAIMED class (H100 SXM 1979, H100 PCIe 1513, A100 624),
  `pct_of_spec = 100 * effective_tops / spec_tops` (4 significant digits). elapsed_s includes generation,
  hashing and network time, so this is a lower bound on the chip's real throughput.
- `GET /api/gpus` -> `{ source: "multibaas"|"local", error: str|null, gpus: [{ node, gpu_name?, cls, cores, passes, fails, humans, status, last_at }] }`. Source: MultiBaas event query when `MB_URL`+`MB_API_KEY` are set (server-side, admin key never leaves the API); otherwise, or if MultiBaas fails (`error` set), built from the API's own published reports. `gpu_name` is filled from the API's reports when known.
Commit `probes` may include optional `staircase: {"64": ms, ...}` which the API stores with the report. The profiler always sends it (CPU mode: synthetic, step at the simulated SM count).
Report `status_text`: "Published." / "Publishing failed." / "Waiting for a human approval. Nothing is published yet." / "Recorded on Marks.".
The approval endpoints stay as they are; the web app uses the agent token from the World login done in the browser (stored in localStorage only as a convenience; the agent CLI keeps its own).

## Performance profile (Ookla-style) — shared data model
Every metric is an object: `{ "value": float|null, "unit": str, "method": str, "trust": "verified"|"measured"|"reported",
"n": int, "median": float, "p10": float, "p90": float, "cv": float, "spec": float|null, "pct_of_spec": float|null,
"spec_source": str|null, "expected_pct": [lo, hi]|null, "flag": null|"low"|"high"|"unstable" }`.
Trust grades: **verified** = computed from work the API re-graded and timed on its own clock (can't be inflated);
**measured** = timed by our code in the pod with CUDA events (a malicious driver could, in principle, lie);
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
- Reports gain `via` (write path used), `indexed` (bool) and `indexed_at` (unix seconds|null); reveal and approve/poll
  outputs gain `via`.
- `POST /api/webhooks/multibaas`: HMAC-SHA256(`MB_WEBHOOK_SECRET`, raw body + `X-MultiBaas-Timestamp`) in
  `X-MultiBaas-Signature` (hex); stale > 5 min or bad signature → 401. Marks matching `Reported` events indexed → `{ok, indexed}`.
- Env: `MB_MARKS_ALIAS`, `MB_MARKS_LABEL` (default `marks`), `MB_WEBHOOK_SECRET`, `WATERLINE_STOP_CMD`.
- Agent: `check --pod-id --stop-cmd` (on FAIL, runs the stop command before asking for World approval; never on PASS).
  `choose`: history only: skip any GPU with ≥ 1 failure report or suspect/failed status, or over `--max-price`;
  then most passes, then cheapest. Jev only reads listing text.
- The `Reported` layout is hard-coded in `api/app.py` and `agent/history.py`; `tests/api/test_event_layout.py`
  checks both against `contracts/src/Marks.sol`.
