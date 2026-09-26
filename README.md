# Waterline

**Your agent proves the H100 you're paying for is really an H100, and the verdict goes on a public record the provider can't edit.**

ETHGlobal Tokyo 2026 · Ethereum Sepolia · ENS · World ID · Curvegrid MultiBaas

- Demo video: [link]
- Live app: [link]
- Showcase (architecture + demo walkthrough): [link]

## Why

GPU cloud passed $25B in 2025 and is heading to ~$400B by 2031
([Synergy](https://www.srgresearch.com/articles/neocloud-market-forecast-to-approach-400b-by-2031-driven-by-surging-ai-infrastructure-demand)).
Banks now lend against GPUs ([CoreWeave's $8.5B facility](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Closes-Landmark-8-5-Billion-Financing-Facility-Achieving-First-Investment-Grade-Rated-GPU-backed-Financing/default.aspx)).
Yet the "same" H100 rents for $1.49–$6.98 an hour ([IntuitionLabs](https://intuitionlabs.ai/articles/h100-rental-prices-cloud-comparison)),
and one network found ~400,000 spoofed GPU workers ([io.net](https://x.com/ionet/status/1780877493672595941)).
Clouds test their own fleets and reviewers audit them now and then
([SemiAnalysis ClusterMAX](https://newsletter.semianalysis.com/p/clustermax-20-the-industry-standard)), but the
renter can't check their own rental, right now.

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
- **Performance, like a speed test.** A verified minimum INT8 throughput from the re-graded work, plus measured
  tensor, memory and host-link throughput, usable memory and stability, each against the listed model's rating,
  against 37 reference GPUs, and against other checks of the same model (`docs/METRICS.md`).
- The machine's own health report (NVML, DCGM, burn test) is advisory; it never decides the verdict.

### Place: ENSv2
- **Wildcard resolution:** `Marks` is the resolver of `waterline.eth`, so every `gpu-<id>.<cloud>.waterline.eth`
  resolves with no registration. Every GPU has a public name for free.
- **Enhanced Access Control:** writing a GPU's record takes the REPORTER role from ENSv2's access-control
  library, scoped per GPU name. Today only our API holds it; providers and renters can never write a score.

### People: World ID for Agents
- **Stable, private, pairwise ID** (World's Human Continuity provider): one identifier per human in our app, no name
  or email. However many agents someone runs, they get one voice per GPU.
- **Step-up:** renting and passing need no World check. Publishing a failure steps up to a fresh human approval,
  checked in our backend. Deny publishes nothing. A GPU is marked failed only when two different people agree.

### Use: Curvegrid MultiBaas
- **The history decides, not the LLM.** A MultiBaas event query (grouped by GPU) makes the agent skip suspect GPUs;
  Jev only reads listing text. On a FAIL, the agent stops paying for that rental.
- **The agent never holds a key.** Writes go through our API and MultiBaas, which composes each transaction
  (nonce and gas); we sign; MultiBaas submits, indexes the event and calls our webhook when it's recorded.

## Principles and limits

- Evidence decides; the machine's claims only inform. Passes carry evidence; failures need verified humans.
- No provider cooperation; the host never writes the score.
- **Limits:** a host could answer checks on a real H100 while running the job on an A100 (mitigated by random
  in-job checks; hardware attestation would close it); no exact serial-number proof yet; real people could be
  bribed; World is checked in our backend; the demo's "fake H100" is an A100 we listed ourselves.

## What's next

Today one renter verifies one GPU. Next, every renter's container is a verifier: the REPORTER role goes from our
one API to many independent verifiers (one role grant per GPU, or for all), with World keeping each a distinct
human.

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

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m core.verify_vectors                 # frozen vectors
.venv/bin/python -m pytest -q                           # API, profiler, agent
(cd contracts && forge test --no-match-contract EnsFork) # contract
(cd contracts && forge test --match-contract EnsFork --fork-url $SEPOLIA_RPC)  # real ENSv2 on a Sepolia fork

# local end to end (simulated World, CPU profiler)
WORLD_MOCK=1 AGENT_TOKEN_SECRET=dev VOTER_SECRET=dev .venv/bin/uvicorn api.app:app --port 8787
.venv/bin/python -m prover.run --api http://127.0.0.1:8787 --cloud cloud-a --claimed 1 --cpu
.venv/bin/python -m prover.run --api http://127.0.0.1:8787 --cloud cloud-b --claimed 1 --cpu --sms 108
open http://127.0.0.1:8787
```
On a real GPU pod: `prover/POD_SETUP.md`. Deploying the contract: `contracts/README.md`. Environment: `.env.example`.

## How MultiBaas is used

- **Nonce manager:** the API composes every `Marks.record` call through MultiBaas (nonce and gas filled in), signs it
  locally, and submits it through MultiBaas.
- **Indexer:** event indexing of `Marks.Reported`; event queries grouped by GPU power the agent's choice and the
  control panel's health table and leaderboard. `Marks` emits running totals so queries only need `last`/`max`.
- **Listener:** a webhook on `Reported` confirms each report was indexed; the control panel shows it.

## MultiBaas feedback

[Written by the builder after using it. Notes from the build:]
- The free plan's 100-block look-back means you must link a contract right after deploying it, or history is lost.
- Event queries have no count or count-distinct aggregator, so we emit running totals from the contract instead.
- The MCP server proof of concept can't select `triggered_at` or `contract_address_alias`, which limits agent use.
- The Python SDK lags the current API paths; we called REST directly.

## World ID feedback

[Written by the builder after the live integration.]

## How we built it

Architecture, design decisions and product direction by the builder: the provider-independent design, the
evidence-vs-human rule for passes and failures, per-GPU ENS names with only the contract able to write the verdict,
World ID Human Continuity for one-person-one-voice, the health report, and the five-piece system. Implementation was
assisted by Claude Code, working from our specs (`PLAN.md`, `docs/INTERFACES.md`); the prompts are in `docs/prompts/`.
All code was written during the event.

## Team

[name] · [X / GitHub handle] · solo builder

## Builds on

Unprivileged Topology Certificates (arXiv 2606.24934) and DrawnApart for GPU fingerprinting; standard GPU tooling
(NVML, DCGM, gpu-fryer-style burns) for the health report. What's new is combining them into a renter-run check
whose passes carry evidence and whose failures need verified humans, recorded where the host can't edit it.
