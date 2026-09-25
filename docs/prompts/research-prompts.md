# Research prompts (verbatim)

Research agents only read sources and reported back; their findings were checked and folded into `PLAN.md`.

## 1. ENSv2 on Sepolia
> Research ENSv2 as of September 2026 for a hackathon build (ETHGlobal Tokyo 2026, ENS prize "Best Use of ENSv2" requires ENSv2 on Sepolia with hierarchical registry, wildcard resolution, Enhanced Access Control, Permissioned Resolvers — features must be central).
>
> Our design: each GPU gets a subname like h100-07.acme.waterline.eth. The provider (acme) owns its subname, but ONLY a verifier contract/address may write specific records (e.g. text records "waterline.lastPass", "waterline.grade", "waterline.markHash"), and a scorer address may only write a "waterline.score" record. The provider must NOT be able to overwrite those records. Optionally wildcard resolution for job-<id>.waterline.eth -> per-job escrow address.
>
> Find, from official ENS docs/GitHub (docs.ens.domains, github.com/ensdomains, ENSv2 repos, ETHGlobal Tokyo 2026 ENS prize page https://ethglobal.com/events/tokyo2026/prizes):
> 1. Is ENSv2 deployed on Sepolia? Contract addresses (registry, root, .eth registry, resolvers), and repo/branch names.
> 2. How Enhanced Access Control works: roles, per-record or per-field permissions, how to grant a third address write access to specific records on a name it doesn't own. Exact function names/signatures.
> 3. How Permissioned Resolvers work and whether they support restricting writes per record key.
> 4. How to create a hierarchical subregistry (waterline.eth -> acme -> h100-07) on Sepolia; cost; how to get a .eth name on Sepolia.
> 5. Wildcard resolution (ENSIP-10) in v2: how to deploy a resolver answering for job-*.waterline.eth.
> 6. Tooling: viem/ethers support, SDKs, example repos, Foundry/Hardhat usage.
> 7. Maturity risks / known gotchas.
>
> Report concisely with concrete function signatures, addresses and URLs. Clearly mark anything you could not verify. Do not write code files.

## 2. World ID for Agents
> Research World (Worldcoin) developer tools as of September 2026 for a hackathon build at ETHGlobal Tokyo 2026. Prize page: https://ethglobal.com/events/tokyo2026/prizes — tracks "Best Use of IDKit" and "Best Use of World ID for Agents" (requirements include official integration on dev environment, denied/expired path demonstration, secure backend or onchain validation, integration feedback).
>
> Our use case: (a) a GPU provider must be a verified unique human, and a provider caught cheating must not be able to register a fresh provider identity (same human rejected) — i.e. nullifier-based one-human-one-provider; (b) an AI agent that rents GPUs and pays should be bound to a verified human via World ID for Agents.
>
> Find from official docs (docs.world.org, developer.worldcoin.org, github.com/worldcoin):
> 1. What exactly "World ID for Agents" is: how an agent gets bound to a human, what the agent presents, how a backend/contract verifies it. SDK names, API endpoints, exact function names.
> 2. IDKit current version: how to request a proof (Proof of Human / Orb, Passport/NFC, Selfie Check), action IDs, signal, nullifier hash semantics — specifically how nullifiers make one-human-per-action enforceable so a banned human can't re-register.
> 3. Onchain verification: WorldIDRouter address on Base Sepolia and/or Ethereum Sepolia, verifyProof signature, groupId; or cloud verification API endpoint.
> 4. Dev/testing without an Orb: simulator/staging app, how to test on a laptop.
> 5. How to demonstrate a denied/expired path.
> 6. Gotchas and time to integrate.
>
> Report concisely with concrete APIs, addresses, and URLs. Clearly mark anything unverified. Do not write code files.

## 3. Curvegrid MultiBaas (first pass)
> Research Curvegrid MultiBaas as of September 2026 for a hackathon build at ETHGlobal Tokyo 2026 (Curvegrid prizes: Best RWA Tokenization, Best Digital Asset Dashboard, Best AI Agent Project; see https://ethglobal.com/events/tokyo2026/prizes and any Curvegrid ETHGlobal Tokyo 2026 developer package blog post on curvegrid.com). Docs: https://docs.curvegrid.com/multibaas/
>
> Our architecture: Solidity contracts on Base Sepolia (escrow per job, a registry of verifier results "marks", a GPU-collateral loan health contract). A Python verifier service must post results onchain; an AI agent (TypeScript or Python) rents GPUs and pays; a dashboard shows each GPU's pass/fail history and loan health from contract events; a webhook on a MarkPosted event should trigger settlement.
>
> Find: 1. deployment + API keys; 2. deploying/linking contracts (Hardhat/Foundry plugins); 3. REST API for contract calls (signing); 4. event indexing and Event Queries; 5. webhooks (payload, signature header); 6. SDKs; 7. CORS / browser use; 8. sample apps and judging emphasis; 9. gotchas. Report concisely with endpoint paths, package names, and URLs. Clearly mark anything unverified. Do not write code files.

## 4. Curvegrid MultiBaas (deep pass)
> Deep research on Curvegrid (MultiBaas) for our ETHGlobal Tokyo 2026 hackathon project … [our design: renter-sourced, provider-independent GPU verification; a `Marks` contract on Ethereum Sepolia records per-GPU reports and is the only writer of the GPU's ENSv2 records; the agent picks GPUs using the GPU history; a dashboard shows GPU health.]
>
> Dig into what we don't know yet: 1. Ethereum Sepolia support; 2. Event Queries in depth — can we compute per-GPU aggregates, what can't it do, an example EventQuery JSON; 3. the MultiBaas MCP server and other agent tooling; 4. what Curvegrid judges value and past ETHGlobal Curvegrid prize winners; 5. the Tokyo workshop; 6. 2026 features (EIP-7702, transaction manager, multi-chain, factories); 7. indexing third-party contracts (ENSv2); 8. practical pitfalls. Report concisely with URLs and exact API shapes. Mark anything unverified. Do not write code files.

## 5. GPU reference dataset
> Build a verified GPU reference dataset for Waterline (a tool that checks whether a rented GPU is the model it's listed as, by measuring SM count with a timing staircase, FP8 capability, memory size/bandwidth and INT8 tensor throughput). … Write only `core/gpu_specs.json` and `docs/GPU_REFERENCE.md`.
>
> Part A: which GPUs matter for rentals (target: cover 95%+ of what is rented today), with evidence and a stated estimation method. Part B: per-model specs from primary sources (vendor datasheets), dense (non-sparse) INT8/FP8/BF16/FP32, SM counts, memory, bandwidth, FP8 support, TDP, form factor, interconnect, each with a source URL. Part C: confusable pairs and the cheapest measurable feature separating them; which features a host could fake. Report the coverage list, unverifiable figures, and the confusable pairs.
