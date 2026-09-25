# Builder prompts

AI coding agents built individual components against `docs/INTERFACES.md`. Each prompt below is what the lead
session sent; every result was reviewed, tested (pytest / forge), and checked in a browser before it was committed.
Long prompts are abridged only where they repeat `docs/INTERFACES.md`; the full text is in the session transcript.

## 1. Waterline API
> You are building the **Waterline API** for a solo hackathon project … Read `docs/INTERFACES.md` (the contract you must implement exactly), `PLAN.md`, `CLAUDE.md`. Work ONLY in `api/` (plus vercel config and `tests/api/`). Never copy from `files/` (pre-event code; Classic track rule). No git commits, no deploys, no account creation, no secrets in code.
> Build: 1. Vercel Python deployment shape (check the current Vercel docs first); 2. Redis state with in-memory fallback; 3. the endpoints in INTERFACES.md — samples and spot columns drawn with `secrets` AFTER the commitment, verification via core.challenge, class from probes, a pass published immediately via `chain.py` (dry-run offline), World OIDC device grant with id_token validation, a fresh `auth_time` for approvals, `voter_id = HMAC(VOTER_SECRET, sub || node)`, denied/expired publish nothing, a 5xx from World is never approval, `WORLD_MOCK=1` for tests; 4. tests for honest/lazy/patched/late/class-mismatch/approve/deny/expired/double-vote; 5. `api/README.md`.

## 2. Profiler, agent and web page
> You are building three client pieces of **Waterline** … `prover/` (runs in the rented pod: CuPy generator bit-identical to core.rng, `torch._int_mm` INT8→INT32, per-row fingerprints, probes — SM staircase, FP8, clock, bandwidth, per-SM timing fingerprint; `--cpu` mode fully tested; `POD_SETUP.md`), `agent/` (login / check over SSH / history / choose; Jev for listing text with a rule-based fallback; MultiBaas event query; `--local` mode), `web/index.html` (ENS lookup via viem, MultiBaas health table, staircase chart). Tests against a fake API. You may READ `files/gpu_check.py` for ideas; re-implement fresh.

## 3. Control panel
> Build the **Waterline control panel web app** and the API read endpoints it needs … `GET /api/health`, `GET /api/reports`, `GET /api/reports/{id}`, `GET /api/gpus` (MultiBaas server-side, or local aggregate); optional `probes.staircase`; web views Overview / GPUs (health + live ENS lookup) / Checks ("Approve with World": login, code + QR, poll, clear outcomes) / Check detail (staircase drawn to scale) / Settings; follow `docs/blueprint-reference.css`; verify in a browser with mock World.

## 4. Health report and flow view
> Improve the Waterline app … the user wants the GPU test to be both more technical and simpler (大道至简). 1. `prover/health.py`: NVML identity/config, ECC and memory repairs, clocks/power/temperature and throttle reasons sampled during a BF16 burn (mean ± stddev), `dcgmi diag -r 1` if present — advisory only, never changes the verdict; 2. profiler sends `health` in the reveal; API stores it and computes `ops_total`, `effective_tops`, `pct_of_spec` against NVIDIA dense INT8 ratings; plain-word verdict reasons; 3. panel: one-line principle, technical strip, health report section, responsive "How a check works" flow, rebuilt terrain; 4. verify with screenshots.
> Follow-up: fade the background grid behind text (mask it to the page edges), solid panels, cap text at ~70ch.
