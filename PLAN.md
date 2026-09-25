# Waterline: event plan (ETHGlobal Tokyo 2026)

Deadline: **Sun 27 Sep 2026, 09:00 JST**. Solo build. Classic track: all submitted code is written at the event.
`files/` holds pre-event reference work: read it, never copy it into the repo, keep it out of git.
Showcase page (architecture + demo rehearsal, keep in sync): https://claude.ai/artifact/5JN2HXLQyCzHWby7sy8t8P

## Narrative
**A public record of what every rented GPU really is, built by renters, with no provider cooperation.**
Your agent checks the GPU you rented from inside your own rental. Passes carry their own evidence; failures
need a fresh approval from a verified human, and one human can't mark a GPU failed alone. The record lives on
the GPU's ENS name, which the host can't edit.

- Opening: *"When you rent a GPU, you take the host's word for what it is."* No competitor names on stage.
- Builds on: Unprivileged Topology Certificates (arXiv 2606.24934), DrawnApart. Cite them; don't claim the probes are new.
- What is ours: delivered work + hardware class in one check; evidence for passes, verified humans for failures;
  a provider-independent public record that agents act on.
- Pitch-only: lender as mystery shopper (same agent checks loan collateral); Waterline's report as the
  "delivery certificate" of the bigger renter-run telemetry network.

## Partners (3): ENS, World, Curvegrid
| Partner | Its one job | Track |
|---|---|---|
| ENS | `gpu-<id>.<cloud>.waterline.eth` resolves to the GPU's record; Marks is the resolver, so no registration per GPU and the host can't edit it | Best Use of ENSv2 |
| World | Renter logs in once (Human Continuity); a failure needs a fresh approval; one voice per human per GPU | World ID for Agents |
| Curvegrid | MultiBaas indexes Marks; the agent skips GPUs with bad history; a web page shows GPU health | AI Agent, Dashboard |

Not picked: Intercepta (no payment flow to screen). If ENSv2 fails by **Sat 12:00 JST**: swap ENS for Intercepta
and add a screened agent payment (~3-4 h).

## Architecture: five pieces, one chain
```
agent/      renter's laptop, Python CLI
            World login once -> Jev reads the listing into a GPU class -> SSH into the rented pod, run the profiler
            -> show verdict -> on FAIL ask the renter to approve through World -> skip GPUs with bad history (MultiBaas)
prover/     inside the rented pod, Python + CUDA
            puzzle under deadline, core-count staircase, H100-only instruction probe, per-core fingerprint, GPU UUID
api/        Waterline API, Vercel Python Functions + Upstash Redis
            puzzle -> locked-in answer -> parts to check (chosen after) -> answers -> verdict
            World login + fresh approval; the ONLY account allowed to write to Marks
contracts/  Marks, Ethereum Sepolia
            stores reports + running tallies, emits Reported, and IS the ENS resolver for *.waterline.eth
web/        control panel served by the API (same origin): Overview (system status), GPUs (health + live ENS lookup),
            Checks (approve failures with World: code + QR), Check detail (probes, timing, staircase), Settings
core/       shared maths (rng.py, challenge.py, verify_vectors.py), used by prover/ and api/
```
- ENS: register `waterline.eth` on ENSv2 Sepolia, set its resolver to Marks. Marks implements the wildcard
  resolver interface (`IExtendedResolver`, ENSIP-10) and answers text records from its own storage.
- Records per GPU: `waterline.class`, `waterline.cores`, `waterline.fingerprint`, `waterline.passes`,
  `waterline.fails`, `waterline.status` (`pass` / `suspect · 1 of 2 humans` / `failed` at 2 humans).
- MultiBaas: `event Reported(bytes32 indexed gpuId, bytes32 voterId, uint8 verdict, uint8 class, bytes32 fingerprint,
  uint64 at, uint32 passes, uint32 fails, uint32 humans)`. Queries only need `last`/`max` (no count aggregator exists).
- World: device grant at `sandbox.auth.world.org`; `voterId = HMAC(secret, sub || gpuId)` = one voice per GPU;
  fresh approval checked via `auth_time`; deny (`access_denied`) publishes nothing.
- Jev: one call in core (listing text -> GPU class, < 0.9 confidence -> ask the renter). Never decides a verdict.

## Build plan (from Sat 01:30 JST)
| When (JST) | Build | Done when | Needs from you |
|---|---|---|---|
| Sat 01:30-04:30 | **Marks + ENS**: contract, Foundry tests on a Sepolia fork, register waterline.eth, deploy, link in MultiBaas | `gpu-test.cloud-a.waterline.eth` resolves on Sepolia from Marks | Sepolia RPC, funded deployer key, MultiBaas deployment |
| Sat 04:30-07:30 | **core/ + API**: puzzle flow, verdict, writes to Marks; runs on Vercel + Redis | CPU prover passes, lazy prover fails, report lands on chain | Vercel project + Upstash Redis |
| Sat 07:30-11:30 | **Sleep** | | |
| Sat 11:30-15:00 | **Profiler on GPUs**: H100 + A100 (A100 listed as H100 by us), real staircase, set the deadline | H100 passes, A100 fails, real numbers into the demo | Rent the two pods at 11:30 |
| Sat 15:00-18:00 | **World**: login once + fresh approval before a failure, deny path | Deny publishes nothing; approve records `suspect · 1 of 2` | World sandbox client + TestFlight app |
| Sat 18:00-20:00 | **Agent CLI** end to end, Jev reads listings, skip bad GPUs via MultiBaas | One command runs the whole demo flow | Jev access (direct or AI Gateway) |
| Sat 20:00-22:00 | **Web page**: name lookup, GPU table, staircase | Live link works | MultiBaas DApp User key + CORS |
| Sat 22:00-Sun 03:00 | **Extras, in order**: MultiBaas webhook alert; claimed provider branch with per-record write roles (ENS); Jev picks the GPU; fingerprint-change flag | Each one is optional | |
| Sun 03:00-08:30 | **Freeze**: rehearse on the showcase page, record video, README (one-line summary, how MultiBaas is used, team, setup, MultiBaas feedback), submit by 08:30 | Submitted | |

If a block overruns, cut from the Extras row first, never from the GPU run or World.

## Demo rules (script lives on the showcase page's Demo tab)
- Solo: no teammates. The failed GPU ends as `suspect · 1 of 2 humans`.
- Neutral names only (Cloud A / Cloud B). Say on stage that the A100 was relabelled by us to play a dishonest host.
- World: Deny first (required path), then Approve.
- Pods rented before the demo; every number on screen comes from the live run.
- No overclaims: "the host can't edit it", "backed by evidence", "the host can't see who reported".

## Trust rules
- Pass/fail comes from the check only. Telemetry, Jev and self-reported data never decide a verdict.
- Parts to check are chosen with the API's secret randomness **after** the answer is locked in.
- The profiler runs only in the renter's pod, launched by the renter's agent.
- Only the API can write to Marks. A failure needs a fresh approval; one voice per human per GPU.
- `vectors.json` is frozen; `verify_vectors.py` must pass.

## Gotchas (from research)
- ENSv2: addresses from tag `sepolia-deployment-2026-09-15`, kept in one config file (no hard-coded values is a
  prize rule). waterline.eth: MockUSDC mint -> approve -> commit -> wait 60 s -> register (min 28 days).
  Wildcard resolvers must support ERC-165 `0x9061b923`. Use viem >= 2.35; the ensjs npm package is stale.
- World: fix the Vercel production domain **before** registering the client (the `sub` is tied to it). Exact
  HTTPS callbacks; id_token 5 min, device code 20 min; check `auth_time`, not `iat`; sandbox identities are fake.
- MultiBaas: link Marks right after deploy with a `startingBlock` (free plan looks back 100 blocks); sign
  locally; DApp User key only in the browser; add every frontend origin to CORS.
