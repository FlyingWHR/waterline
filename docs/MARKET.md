# Market research: what renters and data buyers need (2026-10-01)

Four passes: Reddit renters, X/Twitter, existing solutions, and the paying side (lenders, futures venues, buyers,
insurers). Quotes are verbatim with links; upvotes and likes as of the research date. Inferences are marked.

## The short version

- **The pain is "less", not "fake".** Renters mostly get throttled, flaky or starved machines. Swapped chips were a
  2024 story (io.net) and barely appear in 2025–26 renter talk; NVIDIA attestation will shrink that case further.
  Performance spread within one SKU is measured and large: up to 34.5% compute on H100 PCIe and 38% memory
  bandwidth on H200 SXM across 3,500 rented GPUs and 11 clouds (Silicon Data with William & Mary and Jefferson Lab,
  Apr 2026, [IEEE Spectrum via AOL](https://www.aol.com/articles/gpu-renters-playing-silicon-lottery-180601000.html)).
- **Renters cope instead of paying.** They kill the instance and re-rent, or run home-made test scripts. Nobody on
  Reddit asked to pay for verification. A free tool plus a public per-host record is the adoption path; the money
  is elsewhere.
- **The paying side exists and is crowded.** Silicon Data (renter-run SiliconMark, per-serial certificates, the CME
  index, $35M raised), SemiAnalysis ClusterMAX (paid provider ratings), Aravolta and Silicon Lien (telemetry for
  GPU lenders).
- **The open slot:** a continuous, renter-run, re-checkable, public record per host and provider, which providers
  can't see coming or buy, covering the long tail (marketplaces, spot, short rentals, agents) that ClusterMAX
  excludes and where nobody runs a burn-in.

## 1. Renters (Reddit)

Ranked by frequency and heat:

1. **Flaky or dead hosts, billed anyway.** "The instance wouldn't accept any SSH keys… do not refund used credits…
   There are evidently no quality checks on the listed machines." (r/vastai, Mar 2026,
   [link](https://reddit.com/r/vastai/comments/1rm7uba/)). 8x H200 on a "verified datacenter" stuck "Connecting…
   it charges as if it's running" ([link](https://reddit.com/r/vastai/comments/1rfosym/)). A video-generation SaaS on
   Vast: "gpus dying, host issues" ([link](https://reddit.com/r/deeplearning/comments/1uqtnb5/)).
2. **Less GPU than paid for.** "I've also run into hosts that had less than half of the expected performance"
   ([link](https://reddit.com/r/LocalLLaMA/comments/1na3f1s/)). A 122-upvote claim that Vast slices GPUs was
   pushed back hard and half-retracted in the same thread.
3. **The host starves the GPU.** Same LoRA job, same RTX 4090: 1.95 h on a 5-vCPU host at 40% GPU utilisation,
   1.07 h on a 24-vCPU host ([link](https://reddit.com/r/comfyui/comments/1wd88xm/)).
4. **Listings can't be judged.** "check hidden problems with every instance, and pray the machine is actually worth
   renting" ([link](https://reddit.com/r/vastai/comments/1weilj8/)); price trackers keep appearing, none measures
   delivery: "Unfortunately, they didn't shows the uptime in the list."

The strongest "would use" line: asked about renting from an unknown B300 provider, "1. If it runs (not faked)
2. Runs stable (more than an hour) 3. Encrypted container. Then yes, yes I would." (194 upvotes,
[link](https://reddit.com/r/LocalLLaMA/comments/1pbzw8f/)).

Against the idea: cheap is accepted as unreliable ("the ebay of cloud GPU computing… still a good deal", 30);
"90% of machines work amazing"; many "slow GPU" posts are user error (VRAM leaks, fp32 defaults, CPU-bound loaders);
Vast already shows reliability scores and a verified flag; managed clouds like Lambda are trusted and refund; in 2026
scarcity and price dominate ("people are literally fighting over machines",
[link](https://reddit.com/r/vastai/comments/1tlciel/)).

Adjacent and louder: trust in third-party **model API** providers. "How am I supposed to know which third party
provider can be trusted not to completely lobotomize a model?" (792,
[link](https://reddit.com/r/LocalLLaMA/comments/1nr3n2r/)); a provider caught routing to cheaper models (858,
[link](https://reddit.com/r/LocalLLaMA/comments/1wgwe4n/)).

Who feels it most: small inference and SaaS businesses on marketplace GPUs (production pain); hobbyists and creators
(volume, low dollars); indie labs renting 8x H100/H200 (high cost per incident, mostly on trusted clouds); hosts who
want to prove their machines are good and find marketplace verification slow ("sometimes months",
[link](https://reddit.com/r/LocalLLaMA/comments/1t0mki5/)).

## 2. X/Twitter

- **"It's a lottery."** Yi Tay (Reka): "variance is super high and it's almost a lottery to what hardware one could
  get!" (918k views, [link](https://x.com/YiTayML/status/1765105066263052718)). Carmen Li (Silicon Data): "as
  compute markets mature, that variance becomes basis risk… Markets don't need perfection. They need measurement.
  That's what we're building." ([link](https://x.com/carmenli/status/2024912495412940927)).
- **Lenders.** Bill Hsu: "The missing piece is an independent auditor that can verify GPU inventory, health, and
  utilization for lenders… no one has become the standard." ([link](https://x.com/cebillhsu/status/2077583972113264852)).
  USD.AI lends against verified GPU assets and uses Aravolta telemetry nodes.
- **Ratings.** ClusterMAX 3.0 (23 Sep 2026) tracks 323 providers, reviews 77, interviewed 200+ end users, and ships a
  renter-run CLI (`pip install clustermax`) for software checks
  ([link](https://x.com/JordanNanos/status/2102871532267847699)).
- **Counter-signal from index venues:** "We just want to make a good enough one… it just needs to be good enough to
  facilitate any risk mitigation." ([link](https://x.com/AnneliesGamble/status/2046618812548800647)). Futures settle
  on price; quality enters only as basis risk.
- Individual renter complaints are under-sampled on X (no API token for full search).

## 3. What already exists

| Solution | Verifies | Run by | Public? | Gap |
|---|---|---|---|---|
| SemiAnalysis ClusterMAX | Managed-cluster quality, 10 categories | Analyst, provider knows | Tier per provider | Periodic, announced; excludes bare metal and marketplaces; also consults for covered firms |
| Silicon Data SiliconMark | FP16, HBM, interconnect, per-GPU fingerprint | Renter, under 5 min | Paid dashboard ($998/mo Pro) | No sign of signed results; no public per-provider failure record |
| Silicon Data / Ornn indexes | Price per GPU-hour | Third party | Yes | Price only; an H100-hour is an H100-hour |
| Vast verified, RunPod Secure | Host uptime, datacenter tier | Marketplace | Score in listing | The marketplace grades itself; slow and coarse |
| SF Compute audits | 48 h–7 day burn-in before listing | Marketplace | No | One-time, private |
| DCGM, gpu-burn, nccl-tests, Imbue | Broken or not | Operator or renter | No | Plain text anyone can edit; not "the promised chip at the promised speed" |
| Datadog GPU Monitoring | Your own utilisation | Renter | No | Trusts the driver; measures usage, not delivery |
| NVIDIA CC + NRAS attestation | Genuine GPU, firmware | Renter's CVM | No | Needs CC mode (rare on rentals); identity, not speed |
| io.net, Aethir, Akash, Lium (Bittensor) | Device real, liveness, specs | Network | On-chain | Only inside their own network, to set rewards |
| Gensyn Verde, Hyperbolic PoSP, TOPLOC | Output correctness | Network | Partly | Correctness, not chip or speed |
| SLAs | Uptime | Provider | No | Performance excluded; credits are the only remedy |
| Aravolta, Silicon Lien | Fleet utilisation, health for lenders | On-site node | No | Operator-side; lender-paid |

Funding: Silicon Data $4.7M seed (Mar 2025) and $30.5M Series A (11 Aug 2026, Valor Atreides, CME Ventures, DRW,
VanEck) ([release](https://www.silicondata.com/news-room/silicon-data-raises-30-5-million-series-a)); Ornn $5.7M
seed, then a reported $33M; one vendor claims ClusterMAX tiers move price by +33% (Silver) to +180% (Platinum)
([Hedgehog](https://hedgehog.cloud/blog/the-sell-math-how-clustermax-2.0-ratings-translate-into-revenue),
unverified).

## 4. The paying side

- **GPU-backed debt:** CoreWeave $35bn total debt (30 Jun 2026) including the $8.5bn A3-rated DDTL 4.0; Lambda
  $926M Baa2 term loan B; Nebius $775M; Fluidstack up to $10bn. Lenders underwrite mainly on investment-grade
  offtake contracts, serial-level UCC-1 filings and borrowing-base certificates; telemetry is emerging (Aravolta for
  USD.AI, Silicon Lien). KBRA's Data Center ABS methodology (Jan 2026) stresses cash flows, not GPU performance.
  *Inference:* renter samples don't cover a fleet leased to one offtaker, so lenders are a later customer.
- **Futures:** CME H100 and B200 rental-index futures list 5 Oct 2026, cash-settled on Silicon Data's price index;
  ICE lists Ornn's index. Delivered quality is not in either spec. Compute Exchange runs a standards council on
  quality, quantity and delivery. *Inference:* quality-graded sub-indices are the next product, and venues without
  their own benchmark (Ornn/ICE, Compute Exchange) are the likelier buyers.
- **Enterprise buyers:** SLAs cover availability only; acceptance is a one-time 3–7 day burn-in; ClusterMAX: "a
  stale green result is not evidence of health."
- **Insurance:** residual-value products exist (American Compute, Ornn); nothing insures delivered performance. An
  insurer writing a residual-value floor needs failure and degradation curves by model.
- No named lender, fund or exchange has said it would pay for renter-sourced data. They pay for the neighbouring
  products above.

## 5. What this means for Waterline

1. **Lead with delivered performance and reliability; keep the chip check as one line of it.** The chip-identity
   probes stay (they catch PCIe-as-SXM and class substitution, which attestation doesn't), but the headline is
   "share of rating delivered, held over time".
2. **Measure what renters actually hit.** Time until the instance is usable; host bottlenecks (vCPU count, disk and
   network throughput, PCIe link width and generation, throttle-reason bits); a sustained run of an hour or more,
   since a one-minute exam misses thermal throttling; interruptions per GPU-hour. Separate host faults from
   workload faults, since many "slow GPU" reports are user error.
3. **First users: small inference and SaaS businesses and indie labs on marketplaces** (Vast, RunPod, TensorDock,
   Akash), plus hosts who want to prove their machines. Free CLI and a public per-host history; nobody here pays.
4. **First revenue: the dataset.** Per provider and SKU: p5/p50/p95 of share of rating delivered, failure and
   interruption rates per GPU-hour, sample size and freshness, in Silicon Data's SKU taxonomy and 730 GPU-hour
   units. Sell or license to venues without their own benchmark and to marketplaces; lenders and insurers later,
   once coverage exists.
5. **What stays distinct from Silicon Data:** results anyone can re-check (seeded exam, deadline, evidence hash),
   failures published by name, and a methodology providers can't buy. Silicon Data serves providers and financial
   firms, so it keeps data behind a paywall and doesn't name failures.
6. **The chain is the anchor, not the pitch.** Buyers want an open API and a trustworthy method; ENS and Marks make
   the record tamper-evident, which matters for re-checking, not for the sale.
7. **Anti-gaming is the core risk.** Providers can spot benchmark jobs and serve them better. Random checks inside
   real jobs, and eventually a light background sampler in the renter's own workload, are the answer.
8. **Adjacent opening:** verifying model API providers (is this the model and quantisation I'm paying for). It has
   louder demand than raw GPUs and fits the venture's inference-economics stream.

## Risks

- Silicon Data is one step (signed results, a public board) from this slot, with CME and DRW behind it.
- In a seller's market renters take what they can get; quality leverage returns when supply loosens.
- Short checks miss long-run throttling; sparse renter data can be gamed.
- Demand for quality data from payers is inferred, not stated.
