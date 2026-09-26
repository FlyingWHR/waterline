<img src="web/logo.svg" alt="" width="72" height="72">

# Waterline

**Proof of Delivered Compute.** Check that the GPU you rent delivers what you pay for: the right chip, at its rated speed.

*Heat slows a chip but can't remove cores, so the chip check is heat-proof and slowness reads as degraded. Failures
need real people. Reputation rolls up to the provider, so renaming a chip hides nothing.*

ETHGlobal Tokyo 2026 · Ethereum Sepolia · ENS · World ID · Curvegrid MultiBaas

## One-sentence summary

Waterline is permissionless proof of delivered compute: any renter verifies, from inside the rental, that a cloud GPU
is the listed chip at its rated speed, and the verdict lives onchain on the GPU's ENS name.

- Demo video: [link]
- Live app: [link]
- Showcase (architecture + demo walkthrough): [link]

## Check a GPU in one line

In the rented pod's terminal: the provider, then the GPU the listing promises (`h100`, `h200`, `b200`, `l40s`, `a100`,
`rtx-4090`, … 25 NVIDIA classes in `core/gpu_classes.json`):

```bash
curl -fsSL waterline-eth.vercel.app/run | python3 - <cloud> <gpu>
```

The profiler is fetched from our API and runs from memory; nothing is written to the pod. It needs numpy and torch
(every PyTorch image has them) and installs cupy once if the image lacks it. A pass or a degraded result is published
at once; a failure prints a link where you approve it with World. The renter agent (`python -m agent check --pod …`)
does the same over SSH, and also stops paying on a failure and picks the next GPU from history.

## Why

- **Compute is becoming a financial asset. Its delivery is still self-reported.** GPU cloud is heading from $25B (2025) to ~$400B
  by 2031 ([Synergy](https://www.srgresearch.com/articles/neocloud-market-forecast-to-approach-400b-by-2031-driven-by-surging-ai-infrastructure-demand)),
  banks lend against GPUs ([CoreWeave, $8.5B](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Closes-Landmark-8-5-Billion-Financing-Facility-Achieving-First-Investment-Grade-Rated-GPU-backed-Financing/default.aspx)),
  and H100 rentals trade as futures on CME from October 5, pending review ([CME](https://investor.cmegroup.com/news-releases/news-release-details/cme-group-and-silicon-data-launch-compute-futures-october-5)).
  Buyers rely on the provider's own monitoring, periodic audits and benchmarks that are easy to game. There is no
  independent, shared record.
- **Renters mostly get less, not fake.** Throttled H100s at full price (1,755 → ~345 MHz under load:
  [matt.sh](https://matt.sh/cloud-gpu-thermal-throttling), [Spheron](https://www.spheron.network/blog/sustained-load-gpu-throttling-we-measured-the-hidden-clock-t/)),
  specs that don't match, broken NVSwitch ([Vast.ai](https://www.trustpilot.com/review/vast.ai),
  [RunPod](https://dk.trustpilot.com/review/runpod.io) reviews). Swapped chips are the extreme case: ~400,000 spoofed
  workers on one network ([io.net](https://x.com/ionet/status/1780877493672595941)). Our demo's A100-as-H100 is our own relabel.
- **Complaints don't reach the next renter.** Clouds test themselves and reviewers audit now and then
  ([SemiAnalysis](https://newsletter.semianalysis.com/p/clustermax-20-the-industry-standard)); the renter can't check
  their own rental, and the record lives on Trustpilot.

## Four pillars

| Pillar | Question | Carried by |
|---|---|---|
| **Proof** | Is this GPU what was sold? | The profiler |
| **Place** | Where is the result kept? | ENSv2 |
| **People** | Who can report a failure? | World ID for Agents |
| **Use** | How do agents act on it? | Curvegrid MultiBaas |

### Proof: the profiler
Sealed work, a deadline and a core count, run from inside the renter's rental.
- **Secret INT8 exam.** A fresh seed; 16,384² INT8 matrices multiplied on the tensor cores (8.8 trillion ops a step)
  against a deadline on the API's clock. INT8 because its answer is exact, so we grade it bit for bit.
- **Seal, then spot-check.** The GPU commits a Merkle root of every row; then the API picks 8 random rows and
  recomputes them on a CPU.
- **Count the cores.** A timing staircase counts SMs (132 H100 SXM, 114 PCIe, 108 A100); FP8 is timed. Heat slows a
  chip; it can't remove cores.
- **Three verdicts.** Wrong chip or wrong answers: **fail**. Right chip, too slow: **degraded**, published with its
  numbers, never counted toward failed. In time: **pass**.
- **Performance.** Verified INT8 throughput from the re-graded work, plus measured tensor, memory and link numbers,
  against the rating, 37 reference GPUs and other checks of the same model (`docs/METRICS.md`). The machine's own
  health report is advisory.

### Place: ENSv2
- **Wildcard resolution.** `Marks` resolves every `gpu-<id>.<cloud>.waterline.eth` and `<cloud>.waterline.eth` with
  no registrations.
- **The tree is the roll-up.** A GPU's node derives from its provider's, so every report also scores the provider
  (GPUs checked, failed now, degraded, people who reported, each once). Renaming a chip hides nothing. Two passes
  after a failure mark a GPU `recovered`.
- **Enhanced Access Control.** Only the REPORTER role writes scores (today our API; grantable per GPU or provider).
  A provider's NOTE role is "letting an account edit only certain text records on a name": `waterline.note`, never a
  score.
- **Evidence anchored.** `waterline.report` is the hash of the full report; download it, hash it, compare.

### People: World ID for Agents
- **One person, one voice.** The agent logs in once by device code; World's pairwise `sub`, validated in our backend
  (RS256, issuer, audience, fresh `auth_time`), gives one voice per GPU and one per provider.
- **Failures step up.** Passes and degraded results need no World check. A failure needs a fresh approval from the
  same person; deny or expiry publishes nothing; two different people mark a GPU failed.
- **Accusations cost something.** The reporter pastes the listing they rented and accepts that the report is tied to
  their World ID. **Jev reads that listing**: if it contradicts the claim (an A100 listing reported "as H100"), the
  report stops unless they insist. The dashboard shows the listing, Jev's reading and the pseudonymous reporter.

### Use: Curvegrid MultiBaas
- **History decides, not an LLM.** Event queries by GPU and by provider make the agent skip suspect, failed and
  degraded GPUs and prefer providers with fewer failures. Jev only reads listings. On a fail, the agent stops paying.
- **Writes without handing over a key.** MultiBaas builds each transaction, we check and sign it, MultiBaas submits
  it, indexes the event and calls our webhook.

## Principles and limits

- **Evidence decides.** Passes carry evidence; failures need verified humans; the machine's own numbers only inform.
- **Two layers.** Exam design guards the measurement (secret seed, sealed answers, API-chosen rows, heat-proof chip
  check). World, two humans and per-provider dedup guard the verdict: gaming can't be amplified, and it's attributable.
- **No provider cooperation.** The host never writes a score.
- **Limits.** A host could answer checks on a real H100 and run your job on an A100 (random in-job checks raise the
  cost; attestation closes it). A modified profiler could report 108 cores on a real H100 (two humans, the roll-up
  and later passes bound it). The renter picks when to check. No serial-number proof. People can be bribed. World is
  checked in our backend.

## What's next

- Checks at API-chosen moments inside the job, several per rental: a distribution, not a point.
- Every renter's container a verifier: the REPORTER role granted per GPU, per provider or for all, World keeping
  each a distinct human.
- A site level in the name tree (`gpu-….tyo1.cloud-b.waterline.eth`, as listed), where throttling clusters.
- The listing bound into the evidence, so "listed as" stops being the renter's word.

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
  control panel's GPU list, name tree and Providers page. `Marks` emits running totals so queries only need `last`/`max`.
- **Listener:** a webhook on `Reported` confirms each report was indexed; the control panel shows it.
- **Provider roll-up:** a second event query on `ProviderTally`, grouped by provider, ranks clouds for the agent.

## Our experience with MultiBaas

[DRAFT from build notes: rewrite in your own words before submitting.]
- **Wins:** compose-then-sign kept the reporter key on our side while MultiBaas handled nonce and gas; the webhook
  gave us "indexed" confirmation for free; event queries grouped by GPU and by provider replaced a backend database.
- **Challenges:** the free plan's 100-block look-back means you must link a contract right after deploying it, or
  history is lost. Re-deploying a contract version hit a 409 on the existing address alias; we linked it under a new
  alias (`marks3`). Event queries have no count or count-distinct aggregator, so the contract emits running totals.
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
whose passes carry evidence and whose failures need verified humans, recorded onchain where no host can write.
