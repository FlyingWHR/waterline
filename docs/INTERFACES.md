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
  in  `{ "session_id", "fingerprints": {"<step>": [uint64 as strings…]}, "leaf_hashes": {"<step>": hex}, "rows": {"<step>:<row>": [int…]} }`
  out `{ "report_id", "verdict": "pass"|"fail", "measured_class": int, "reasons": [str], "gpu_name", "node", "published": bool, "tx": str|null }`
  Checks: deadline, leaf hashes, root recomputed from leaf hashes, row fingerprints, 64 spot entries per row
  (columns from secret randomness), class from probes (sms 132 + fp8 -> 1, sms 114 + fp8 -> 2, sms 108 & !fp8 -> 3).
  Verdict fail if any check fails OR measured class != claimed class.
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
record(bytes32 node, uint8 verdict, uint8 cls, uint16 cores, bytes32 fingerprint, bytes32 voterId)  onlyReporter
  pass: passes++ ; fail: require voterId != 0 and not used for this node; fails++, humans++
event Reported(bytes32 indexed node, bytes32 voterId, uint8 verdict, uint8 cls, uint16 cores, bytes32 fingerprint,
               uint64 at, uint32 passes, uint32 fails, uint32 humans)
text(bytes32 node, string key) view -> string
  keys: waterline.class ("H100 SXM"…), waterline.cores, waterline.fingerprint (0x hex), waterline.passes,
        waterline.fails, waterline.humans, waterline.status ("unknown"|"pass"|"suspect · 1 of 2 humans"|"failed")
resolve(bytes dnsName, bytes data) -> bytes   (ENSIP-10; handles text(bytes32,string) and addr(bytes32) -> zero)
supportsInterface: 0x01ffc9a7, 0x9061b923 (IExtendedResolver), 0x59d1d43c (text)
setReporter(address) onlyAdmin
```
Status rule: humans >= 2 -> failed; humans == 1 -> suspect; passes > 0 -> pass; else unknown.
Class shown on the name is the last measured class.

## Profiler (prover/, runs in the rented pod)
`python -m prover.run --api <url> --cloud cloud-b --claimed 1 [--cpu] [--n N --steps S]`
Reads the GPU UUID (nvidia-smi / torch), calls start, computes all steps (GPU: CuPy + torch._int_mm; `--cpu`: core/),
sends commit with probes, then reveal with the requested rows; prints the API's JSON result.
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
- `GET /api/reports/{id}` -> full report: the above + `probes`, `staircase` (map of blocks -> ms, if sent), `elapsed_s`, `deadline_s`, `samples`, `reasons`, `n`, `steps`.
- `GET /api/gpus` -> `{ source: "multibaas"|"local", error: str|null, gpus: [{ node, gpu_name?, cls, cores, passes, fails, humans, status, last_at }] }`. Source: MultiBaas event query when `MB_URL`+`MB_API_KEY` are set (server-side, admin key never leaves the API); otherwise, or if MultiBaas fails (`error` set), built from the API's own published reports. `gpu_name` is filled from the API's reports when known.
Commit `probes` may include optional `staircase: {"64": ms, ...}` which the API stores with the report. The profiler always sends it (CPU mode: synthetic, step at the simulated SM count).
Report `status_text`: "Published." / "Publishing failed." / "Waiting for a human approval. Nothing is published yet." / "Recorded on Marks.".
The approval endpoints stay as they are; the web app uses the agent token from the World login done in the browser (stored in localStorage only as a convenience; the agent CLI keeps its own).
