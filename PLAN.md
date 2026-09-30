# Waterline: event plan (ETHGlobal Tokyo 2026)

Deadline: **Sun 27 Sep 2026, 09:00 JST**. Solo build. Classic track: all submitted code is written at the event.
`files/` holds pre-event reference work: read it, never copy it into the repo, keep it out of git.
Showcase (architecture + demo rehearsal): https://claude.ai/artifact/5JN2HXLQyCzHWby7sy8t8P ·
Source doc: "Waterline — Project Soul" (Sep 26).

## Why
**Ground truth for rented GPUs.** A renter's agent proves the GPU it pays for is the GPU it got, and the verdict
goes on a record the provider can't edit.
- GPU cloud: over $25B in 2025, heading to ~$400B by 2031, 58% a year ([Synergy](https://www.srgresearch.com/articles/neocloud-market-forecast-to-approach-400b-by-2031-driven-by-surging-ai-infrastructure-demand)).
- Banks lend against GPUs: CoreWeave's $8.5B facility was the first investment-grade GPU-backed loan
  ([CoreWeave](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Closes-Landmark-8-5-Billion-Financing-Facility-Achieving-First-Investment-Grade-Rated-GPU-backed-Financing/default.aspx)). Use as stakes only: its rating rested on customer contracts.
- The "same" H100 costs $1.49–$6.98/hr; one network found ~400,000 spoofed GPUs.
- Clouds test their own fleets and reviewers audit them now and then; **the renter can't check their own rental**.
  (Say "a claim the renter can't check", not "nobody checks": the latter is refutable.)

## Three pillars
| Pillar | Question | Carried by | Built as |
|---|---|---|---|
| **Proof** | Is this GPU what was sold? | Profiler | Secret INT8 exam under a deadline on the API's clock; seal (Merkle root), then 8 random rows re-graded; core-count staircase + FP8; performance profile vs 37 models; health report advisory only |
| **Place** | Where does truth live? | ENSv2 | Marks is the resolver of `waterline.eth`: every `gpu-….waterline.eth` resolves with no registration (wildcard). Writing is governed by ENSv2's **Enhanced Access Control**: a REPORTER role, scoped per GPU name, held today only by our API |
| **Use** | How does truth become action? | Curvegrid MultiBaas | Indexer (history the agent chooses from), listener (webhook confirms each report), and write path (MultiBaas builds each write with nonce and gas; we check and sign; it submits). The history decides, not the LLM. On a FAIL the agent stops paying for that rental |

Deliberately not used: soulbound names, aliasing (ENS); Intercepta (no payment to screen). Jev only reads listing
text; it never picks a GPU.

Failures carry the listing: a failed check publishes with the listing the renter rented; Jev reads it, and a reading
as another GPU stops the report unless the renter reports anyway. Each report is its own voter; two mark a GPU failed.

## Principles and limits
- Evidence decides; the machine's claims only inform. Passes carry evidence; failures carry the listing.
- No provider cooperation; the host never writes the score.
- **Limits, said first:** the switch attack (mitigated by random in-job checks; hardware attestation closes it);
  no exact serial proof yet; failure reports carry no identity, so one renter can file both that mark a GPU failed;
  the demo's "fake H100" is an A100 we listed ourselves.

## Pitch
- **One line:** your agent proves the H100 you're paying for is really an H100, and the verdict goes on a public
  record the provider can't edit.
- **Versus OpenBook:** OpenBook guarantees the data is fresh; Waterline guarantees the machine is real.
- **Per sponsor:** ENS: every GPU has a public name only our API can write. Curvegrid: serverless backend, so MultiBaas is our indexer, our listener, and builds and submits every write. (Don't say "nonce manager": its concurrent nonce management needs MultiBaas-hosted wallets.)
- **Closer:** proof, place, use.
- **Next stage (last 15 s or Q&A only):** every renter's container becomes a verifier; the ENSv2 REPORTER role goes
  from our one API to many independent verifiers (already one `grantRoles` call per GPU or for all). That network is the delivered-quality layer for GPU lending and compute futures
  (Watermark). No tokens or data sales in the hackathon pitch.

## Architecture: five pieces, one chain (Ethereum Sepolia)
```
agent/      renter's laptop: Jev reads the listing -> SSH: run the profiler in the pod
            -> FAIL: stop paying (stop command), publish the failure with the listing -> choose next GPU from history
prover/     in the rented pod: exam, staircase, FP8, performance profile, health report (CuPy + torch, NVML)
api/        Vercel Python + Upstash Redis: exam, verdict, classification, failure publishing, writes via MultiBaas,
            MultiBaas webhook listener, compare / leaderboard endpoints, serves web/
contracts/  Marks: report store + ENSIP-10 wildcard resolver for *.waterline.eth + ENSv2 Enhanced Access Control
web/        control panel: Overview, GPUs, Checks (Publish a failure with its listing), Check detail (performance, vs models,
            vs same model, health), Providers (roll-up + by model), Models, Settings
core/       challenge maths, frozen vectors, gpu_specs.json (37 models)
```
Details: `docs/INTERFACES.md` (spec), `docs/METRICS.md` (measurement method), `docs/GPU_REFERENCE.md` (specs + sources).

## Demo (3:30) — one proof moment carries it
Pod B shows 108 cores and a missed deadline, then FAIL, then the agent stops paying. Pod A and Pod B side by side.
| Time | Beat | Key line |
|---|---|---|
| 0:00 | Problem | "Compute is becoming a financial asset. Its delivery is still self-reported." |
| 0:20 | Setup | 4 pods, 2 clouds. "B2 is an A100 I relabelled myself." |
| 0:30 | Overview | "Proof of delivered compute. Permissionless: any renter checks." |
| 0:45 | Check | The one-liner on A1: PASS, its ENS name, published via MultiBaas. |
| 1:10 | The exam | "The cores decide the chip; the clock decides the speed." |
| 1:35 | Fail · Publish | Agent on B2: fail, stops paying, listing pasted, Jev agrees, published. |
| 2:15 | Record · Compare | The ENS name tree: B2 suspect, one failure report on cloud-b, % of rating per cloud; Verify the hash. |
| 3:00 | Use | MultiBaas indexed; `agent choose` skips B2. |
| 3:20 | Close | "Proof of Delivered Compute." |
Full script: the showcase's Demo tab. Video rules: 2–4 min (auto-reject outside), ≥720p, own voice, no speed-up,
no phone recording.

## Status (Sat 14:30 JST)
Built and tested locally: all five pieces, 122 Python tests + 14 contract tests + the ENS test on a Sepolia fork.
Not yet live: nothing deployed, no real GPU run, MultiBaas only simulated.

## Remaining schedule (to Sun 09:00 JST)
| When (JST) | Do | Needs from you |
|---|---|---|
| Sat now → 16:00 | Deploy Marks + register waterline.eth; link in MultiBaas; webhook | **Fund deployer + reporter; MultiBaas deployment + keys** |
| Sat 16:00–18:00 | Deploy API + web to Vercel with Redis; a live failure published | **Vercel project + domain** |
| Sat 18:00–22:00 | GPU pods: `python -m prover.gpu` self-check, calibrate steps/deadline, real H100 vs A100 runs, real numbers into the demo | **Rent H100 SXM + A100 pods** |
| Sat 22:00–Sun 02:00 | Fix what the live runs expose; README links; MultiBaas feedback in your words | |
| Sun 02:00–04:00 | Sleep (minimum) | |
| Sun 04:00–08:00 | Rehearse, record the video (3:30), final README, push to GitHub | **GitHub repo** |
| Sun 08:00–08:45 | Submit (15 min buffer before 09:00) | |
If the pods slip past 20:00, record the check beat with whatever real run exists and say so; never fake numbers.

## Go-live sequence
Done: Marks `0xb69D2F0690b3d8F96Ff041eA524657391FB521c1` deployed, `waterline.eth` registered (resolver = Marks),
reporter holds the REPORTER role, Marks linked in MultiBaas (indexing from block 11784289).
1. Vercel project + fixed domain; `vercel --prod` with env: REDIS_URL, REPORTER_KEY,
   MARKS_ADDRESS, SEPOLIA_RPC, PUBLIC_SEPOLIA_RPC, ENS_UNIVERSAL_RESOLVER, MB_URL, MB_API_KEY, MB_MARKS_ALIAS,
   MB_MARKS_LABEL, CHECK_STEPS (ALLOW_CLIENT_SIZES unset).
2. `API_URL` in .env → `.venv/bin/python scripts/multibaas_link.py` (creates the webhook, prints its secret once) →
   `vercel env add MB_WEBHOOK_SECRET production` → `vercel --prod`.
3. `.venv/bin/python scripts/check_live.py` until all ✓.
4. H100 pod: `python3 -m prover.calibrate` → set CHECK_STEPS + DEADLINES on Vercel → `vercel --prod`.
5. Real checks: Pod A (H100), Pod B (A100 listed as H100, `--stop-cmd` set) → Verify in the panel.
The API decides the exam size in production (a prover can't ask for a tiny exam); `ALLOW_CLIENT_SIZES=1` is for local only.

## Trust rules
- Pass/fail comes from the check only. Telemetry, Jev and self-reported data never decide a verdict.
- Parts to check are chosen with the API's secret randomness after the answer is locked in.
- The profiler runs only in the renter's pod, launched by the renter's agent.
- Only holders of the ENSv2 REPORTER role write to Marks (today: our API). A failure needs the listing the renter
  rented; each report is its own voter; two failure reports mark a GPU failed.
- Two layers. Class comes only from heat-proof probes (cores, FP8): wrong chip or wrong answers = FAIL (needs
  the listing). Right chip, right answers, too slow = DEGRADED (published at once with its numbers, never counts toward
  failed; throttle flags explain it, never decide it). Pass = in time.
- Asymmetry: passes need real silicon, failures need the listing. Each failure report gives a per-GPU and a
  per-provider voter id; Marks rolls every report up to `<cloud>.waterline.eth`. Two passes after a GPU's last failure = `recovered`. Providers may write
  `waterline.note` (NOTE role) on their own name, never a score.
- A failure requires the listing (URL or text); the check page shows it with Jev's reading and any automatic flags.
- `core/vectors.json` is frozen; `python -m core.verify_vectors` must pass.

## Demo rules
- Solo: no teammates. The failed GPU ends as `suspect · 1 of 2 reports`.
- Neutral names (Cloud A / Cloud B); say on stage the A100 was relabelled by us.
- Pods rented before the demo; every number on screen comes from the live run.
- No overclaims: "the renter can't check", "backed by evidence", "the host can't see who reported".

## Gotchas (from research)
- ENSv2: addresses from tag `sepolia-deployment-2026-09-15`, kept in one config file (no hard-coded values is a
  prize rule). waterline.eth: MockUSDC mint -> approve -> commit -> wait 60 s -> register (min 28 days).
  Wildcard resolvers must support ERC-165 `0x9061b923`. Marks inherits ENSv2's EnhancedAccessControl (remapped from contracts-v2); the role grant is `grantRoles(uint256(node), ROLE_REPORTER, verifier)` or `grantRootRoles` for all GPUs. Use viem >= 2.35; the ensjs npm package is stale.
- MultiBaas: link Marks right after deploy with a `startingBlock` (free plan looks back 100 blocks); sign
  locally; DApp User key only in the browser; add every frontend origin to CORS.
