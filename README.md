<img src="web/logo.svg" alt="" width="72" height="72">

# Waterline

**Your agent proves the H100 you're paying for is really an H100 delivering its speed, and the verdict goes on a public record the provider can't edit.**

*Heat can slow a chip but can't remove cores, so chip class is heat-proof. Throughput is reported honestly as
degraded. And if someone lies about the cores, World makes sure they can only lie once, under a name that remembers.*

ETHGlobal Tokyo 2026 · Ethereum Sepolia · ENS · World ID · Curvegrid MultiBaas

## One-sentence summary

Waterline lets a renter's agent prove, from inside the rental, that a cloud GPU is the chip on the listing and
delivers its speed, and publishes the verdict on the GPU's ENS name, where the provider can't edit it.

- Demo video: [link]
- Live app: [link]
- Showcase (architecture + demo walkthrough): [link]

## Check a GPU in one line

In the rented pod's terminal (provider, then what the listing promises: `h100`, `h100-pcie` or `a100`):

```bash
curl -fsSL waterline-eth.vercel.app/run | python3 - cloud-b h100
```

The profiler is fetched from our API and runs from memory; nothing is written to the pod. It needs numpy and torch
(every PyTorch image has them) and installs cupy once if the image lacks it. A pass or a degraded result is published
at once; a failure prints a link where you approve it with World. The renter agent (`python -m agent check --pod …`)
does the same over SSH, and also stops paying on a failure and picks the next GPU from history.

## Why

GPU cloud passed $25B in 2025 and is heading to ~$400B by 2031
([Synergy](https://www.srgresearch.com/articles/neocloud-market-forecast-to-approach-400b-by-2031-driven-by-surging-ai-infrastructure-demand)).
Banks now lend against GPUs ([CoreWeave's $8.5B facility](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Closes-Landmark-8-5-Billion-Financing-Facility-Achieving-First-Investment-Grade-Rated-GPU-backed-Financing/default.aspx)).
Yet the "same" H100 rents for $1.49–$6.98 an hour ([IntuitionLabs](https://intuitionlabs.ai/articles/h100-rental-prices-cloud-comparison)),
and one network found ~400,000 spoofed GPU workers ([io.net](https://x.com/ionet/status/1780877493672595941)).
Clouds test their own fleets and reviewers audit them now and then
([SemiAnalysis ClusterMAX](https://newsletter.semianalysis.com/p/clustermax-20-the-industry-standard)), but the
renter can't check their own rental, right now.

What renters actually complain about is mostly **degraded delivery**, not counterfeit chips: an H100 host that drops
from 1,755 MHz to ~345 MHz under load at full price ([matt.sh](https://matt.sh/cloud-gpu-thermal-throttling),
[Spheron](https://www.spheron.network/blog/sustained-load-gpu-throttling-we-measured-the-hidden-clock-t/)), specs
that don't match the listing, broken NVSwitch, a third of the RAM paid for
([Vast.ai](https://www.trustpilot.com/review/vast.ai), [RunPod](https://dk.trustpilot.com/review/runpod.io) reviews).
Marketplaces verify hosts because hosts misreport ([Vast.ai](https://docs.vast.ai/host/understanding-verification)),
so crude swaps are rare on the big ones; an A100 sold as an H100 is the extreme case, and the one our demo plays
(we relabelled it ourselves). The complaints live on Trustpilot, where nobody's next rental reads them.

## Four pillars

| Pillar | Question | Carried by |
|---|---|---|
| **Proof** | Is this GPU what was sold? | The profiler |
| **Place** | Where does truth live? | ENSv2 |
| **People** | Who may say it's false? | World ID for Agents |
| **Use** | How does truth become action? | Curvegrid MultiBaas |

### Proof: the profiler
Work only the claimed chip can finish in time, run from inside the renter's own rental.
- **A secret INT8 exam.** From a fresh seed, the GPU builds 16,384 × 16,384 INT8 matrices and multiplies them on its
  tensor cores (8.8 trillion operations a step) against a deadline on the verifier's clock. INT8 because it's the
  only tensor-core format whose answer is exactly reproducible, so we can grade it bit for bit.
- **Seal, then spot-check.** The GPU commits a Merkle root of every result row first; then the API picks 8 rows at
  random and recomputes 64 entries of each on a CPU.
- **Count the cores.** A timing staircase counts SMs (132 = H100 SXM, 108 = A100) and FP8 is tested by throughput.
  Heat slows a chip; it can't remove cores.
- **Two layers, three verdicts.** Chip class comes only from heat-proof probes (cores, FP8): another chip, or wrong
  answers, is **fail**. The deadline measures delivery: the listed chip with correct answers but too slow is
  **degraded** (heat, a power cap, sharing), published with its numbers and never counted toward failed. The
  machine's throttle flags can explain a degraded result; they never decide one.
- **Performance, like a speed test.** A verified minimum INT8 throughput from the re-graded work, plus measured
  tensor, memory and host-link throughput, usable memory and stability, each against the listed model's rating,
  against 37 reference GPUs, and against other checks of the same model (`docs/METRICS.md`).
- The machine's own health report (NVML, DCGM, burn test) is advisory; it never decides the verdict.

### Place: ENSv2
- **Wildcard resolution:** `Marks` is the resolver of `waterline.eth`, so every `gpu-<id>.<cloud>.waterline.eth`
  resolves with no registration. Every GPU has a public name for free.
- **The name tree is the roll-up.** Marks derives each GPU's node from its provider's, so every report also lands on
  `<cloud>.waterline.eth`, which keeps its own score: GPUs checked, failed right now, degraded, and how many people
  reported any of them (each counted once). Rename the chip and the provider remembers. Two passes after a GPU's
  last failure bring it back as `recovered`; the history stays.
- **Enhanced Access Control:** writing a record takes the REPORTER role from ENSv2's access-control library,
  scoped per name (a GPU, or a provider for all its GPUs). Today only our API holds it; providers and renters can
  never write a score. A provider can be granted the NOTE role on its own name, "letting an account edit only
  certain text records on a name": `waterline.note` (a rebuttal or contact), a voice, never the verdict.
- **Evidence anchored:** each record carries the hash of the full report (`waterline.report`). Download the report
  from `/api/reports/{id}/evidence`, hash it, and compare: if one byte changed, it won't match.

### People: World ID for Agents
- **World ID for Agents (Human Continuity IdP, dev environment).** The renter logs their agent in once with a
  device code; World returns a private, stable, pairwise `sub` for our service, checked in our backend (RS256,
  issuer, audience, `auth_time`). From it the API derives two voter ids for a failure: one per GPU and one per
  provider, so one person is one voice per GPU and counts once per provider however many GPUs they report.
- **Step-up:** renting, passing and degraded results need no World check. Publishing a failure steps up to a fresh
  device-code approval from the same person (a fresh `auth_time`, same `sub`). Deny or expiry publishes nothing. A GPU is marked failed only when two different people agree.

### Use: Curvegrid MultiBaas
- **The history decides, not the LLM.** MultiBaas event queries (grouped by GPU, and by provider) make the agent
  skip suspect, failed and degraded GPUs and prefer providers with fewer failed GPUs; Jev only reads listing text.
  On a FAIL, the agent stops paying for that rental.
- **The agent never holds a key.** Writes go through our API and MultiBaas: MultiBaas builds each transaction
  (nonce and gas), we check and sign it, MultiBaas submits it, indexes the event and calls our webhook when it lands.

## Principles and limits

- Evidence decides; the machine's claims only inform. Passes carry evidence; failures need verified humans.
- Two layers. **Measurement** (can one exam mislead?) is defended by exam design: secret seed, sealed answers,
  API-chosen rows, heat-proof class. **Aggregation** (can one misleading exam become a verdict?) is defended by World,
  two humans, per-provider dedup, and passes that outrank failures. World doesn't make a measurement ungameable; it
  makes gaming unamplifiable and attributable.
- No provider cooperation; the host never writes the score.
- **Limits:** a host could answer checks on a real H100 while running the job on an A100 (mitigated by random
  in-job checks; hardware attestation would close it); a modified profiler could report 108 cores on a real H100,
  which no exam run inside the renter's container can catch (two humans, the provider roll-up and later passes
  bound it; attestation closes it); a renter picks when to check today; the site is not modelled; no exact
  serial-number proof yet; real people could be bribed; World is checked in our backend; the demo's "fake H100" is
  an A100 we listed ourselves.

## What's next

Today one renter verifies one GPU, at a moment the renter picks. Next: checks fire at API-chosen moments inside the
job, several per rental, so a report is a distribution, not a point. Then every renter's container is a verifier:
the REPORTER role goes from our one API to many independent verifiers (one role grant per GPU, per provider, or
for all), with World keeping each a distinct human. The name tree grows a site level
(`gpu-….tyo1.cloud-b.waterline.eth`, "as listed"), where throttling clusters.

## Repo

| Folder | What |
|---|---|
| `core/` | Challenge maths: seeded INT8 generator, row fingerprints, Merkle root; frozen test vectors |
| `contracts/` | `Marks`: report store, ENS wildcard resolver, ENSv2 Enhanced Access Control (Foundry); ENSv2 pinned at `sepolia-deployment-2026-09-15` |
| `api/` | Waterline API (FastAPI on Vercel + Redis): the check, World login and approval, chain writes |
| `prover/` | Profiler that runs in the rented pod (CuPy + torch), health report |
| `agent/` | Renter CLI: check a pod over SSH, approve failures, choose GPUs from history |
| `web/` | Control panel served by the API |
| `docs/` | `INTERFACES.md` (the spec), `prompts/` (build prompts), GPU reference data |

## Setup and testing

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m core.verify_vectors                 # frozen vectors
.venv/bin/python -m pytest -q                           # API, profiler, agent
(cd contracts && forge test --no-match-contract EnsFork) # contract
(cd contracts && forge test --match-contract EnsFork --fork-url $SEPOLIA_RPC)  # real ENSv2 on a Sepolia fork

# local end to end (simulated World, CPU profiler)
WORLD_MOCK=1 ALLOW_CLIENT_SIZES=1 AGENT_TOKEN_SECRET=dev VOTER_SECRET=dev .venv/bin/uvicorn api.app:app --port 8787
.venv/bin/python -m prover.run --api http://127.0.0.1:8787 --cloud cloud-a --claimed 1 --cpu
.venv/bin/python -m prover.run --api http://127.0.0.1:8787 --cloud cloud-b --claimed 1 --cpu --sms 108
open http://127.0.0.1:8787
```
On a real GPU pod: `prover/POD_SETUP.md`. Deploying the contract: `contracts/README.md`. Environment: `.env.example`.

## How we used MultiBaas

- **Writes:** the API composes every `Marks.record` call through MultiBaas (nonce and gas filled in), checks the
  calldata, signs it locally, and submits it through MultiBaas. (MultiBaas's concurrent nonce management needs its
  hosted wallets; our reporter signs locally, so we publish one report at a time.)
- **Indexer:** event indexing of `Marks.Reported`; event queries grouped by GPU power the agent's choice and the
  control panel's health table and leaderboard. `Marks` emits running totals so queries only need `last`/`max`.
- **Listener:** a webhook on `Reported` confirms each report was indexed; the control panel shows it.
- **Provider roll-up:** a second event query on `ProviderTally`, grouped by provider, ranks clouds for the agent.

## Our experience with MultiBaas

[DRAFT from build notes: rewrite in your own words before submitting.]
- **Wins:** compose-then-sign kept the reporter key on our side while MultiBaas handled nonce and gas; the webhook
  gave us "indexed" confirmation for free; event queries grouped by GPU and by provider replaced a backend database.
- **Challenges:** the free plan's 100-block look-back means you must link a contract right after deploying it, or
  history is lost. Re-deploying a contract version hit a 409 on the existing address alias; we linked it under a new
  alias (`marks2`). Event queries have no count or count-distinct aggregator, so the contract emits running totals.
- **Feedback:** the MCP server proof of concept can't select `triggered_at` or `contract_address_alias`, which limits
  agent use; the Python SDK lags the current API paths, so we called REST directly.

## World ID integration debrief

[DRAFT from build notes: rewrite in your own words, and fill in the time, before submitting.]

### Time to first success
[N] hours from first attempt to the first live device-code login through `sandbox.auth.world.org` from our
production API. Most of it went to finding the right portal; the code itself worked the first time it had a
valid client.

### Friction encountered
We first registered an app at developer.world.org. Its id returned `invalid_client` at the World ID for Agents
IdP with no hint that it belonged to a different product, so we rebuilt the approval on IDKit before the prize page
pointed us to `sandbox.auth.world.org/portal`. The portal calls OIDC clients "apps", which made the right button
hard to find. The sandbox app's TestFlight enrollment stayed pending; the note that proofs are mocked lived only on
the prize page.

### Missing capability or documentation
docs.world.org doesn't link to the Agents IdP or its portal, and the device-grant guide is only reachable through
the IdP's MCP resources. It isn't documented whether the World ID Simulator works with the sandbox IdP.

### The one improvement with the greatest impact
Make `invalid_client` say which environment and portal a client id belongs to (or link both portals from each other).
That one message would have saved us the IDKit detour.

## How we built it

Architecture, design decisions and product direction by the builder: the provider-independent design, the
evidence-vs-human rule for passes and failures, per-GPU ENS names with only the contract able to write the verdict,
World ID Human Continuity for one-person-one-voice, the health report, and the five-piece system. Implementation was
assisted by Claude Code, working from our specs (`PLAN.md`, `docs/INTERFACES.md`); the prompts are in `docs/prompts/`.
All code was written during the event.

## Team

[name] · [X handle] · [GitHub handle] · solo builder

## Builds on

Unprivileged Topology Certificates (arXiv 2606.24934) and DrawnApart for GPU fingerprinting; standard GPU tooling
(NVML, DCGM, gpu-fryer-style burns) for the health report. What's new is combining them into a renter-run check
whose passes carry evidence and whose failures need verified humans, recorded where the host can't edit it.
