# Waterline

**When you rent a GPU, Waterline checks it's really the chip you paid for, from inside your own rental, and puts the result on a public record the host can't edit.**

ETHGlobal Tokyo 2026 · Ethereum Sepolia · ENS · World ID · Curvegrid MultiBaas

- Demo video: [link]
- Live app: [link]
- Showcase (architecture + demo walkthrough): [link]

## The problem

GPU rentals run on the host's word. The "same" H100 rents for $1.49–$6.98 an hour across 15+ clouds
([IntuitionLabs](https://intuitionlabs.ai/articles/h100-rental-prices-cloud-comparison)); SemiAnalysis tracks 209 GPU
clouds and gave a medal to 37 of the 84 it rated ([ClusterMAX 2.0](https://newsletter.semianalysis.com/p/clustermax-20-the-industry-standard));
one decentralised network found ~400,000 spoofed GPU workers ([io.net](https://x.com/ionet/status/1780877493672595941)).
Ratings are periodic reviews. Nothing checks *your* rental, right now.

## How it works

1. **Check (a timed exam).** The renter's agent runs our profiler inside its own rental. From a secret seed, the GPU
   builds 16,384 × 16,384 INT8 matrices and multiplies them on its tensor cores (8.8 trillion operations a step) against
   a deadline only the listed chip can meet. It seals every result row in a Merkle root; only then does our API pick
   8 rows at random and recompute 64 entries of each. Timing probes count the GPU's cores (132 on an H100 SXM, 108 on an
   A100) and test for Hopper-only FP8. Heat can slow a chip down; it can't remove cores.
2. **Record (ENS).** A pass carries its own proof and is published straight away to the `Marks` contract on Sepolia.
   `Marks` is the resolver for `waterline.eth`, so every GPU has a name the moment it's checked
   (`gpu-<id>.<cloud>.waterline.eth`, ENSv2 wildcard resolution): anyone can look it up, and the host can't change it.
3. **Approve (World ID).** A failure can't prove itself and it hurts someone, so it's published only after a fresh
   approval from a verified human (World ID, Human Continuity). One person gets one voice per GPU; a GPU is marked
   failed only when two different people report it.
4. **Use (Curvegrid MultiBaas).** MultiBaas indexes every report; the agent skips GPUs with a bad record, and the
   control panel shows each GPU's health.

Renters also get a **health report**: sustained throughput, throttling, memory errors, link speeds, and NVIDIA's DCGM
diagnostic where available. It's reported by the machine, so it informs but never decides the verdict.

The provider never has to sign up or cooperate.

## Repo

| Folder | What |
|---|---|
| `core/` | Challenge maths: seeded INT8 generator, row fingerprints, Merkle root; frozen test vectors |
| `contracts/` | `Marks`: report store + ENS wildcard resolver (Foundry); ENSv2 pinned at `sepolia-deployment-2026-09-15` |
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

- **Event indexing** of `Marks.Reported` on Ethereum Sepolia.
- **Event queries** grouped by GPU (latest class, tallies, last report) power both the agent's GPU choice and the
  control panel's health table. `Marks` emits running totals so queries only need `last`/`max`.
- **Webhook** on new reports (alerting renters of that GPU).

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
