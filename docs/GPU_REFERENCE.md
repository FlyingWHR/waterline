# GPU reference for Waterline

Generated 2026-09-26. Machine-readable version: `core/gpu_specs.json` (now 37 model entries and 32 confusable pairs;
the JSON has since added separate V100 16 GB entries, which this page folds into the V100 rows).

## 1. Which GPUs are rented, and how much of the market this covers

### Method

No public source we found breaks rental volume (GPU-hours or revenue) out by GPU model. SemiAnalysis ClusterMAX 3.0, Silicon Data, Ornn, gputracker and AIMultiple publish prices, provider ratings or listing counts, not a split by model. So the ranking uses three proxies:

1. **Provider breadth**: how many clouds list the model, from getdeploying.com (index updated 2026-09-26; per-GPU pages dated 2026-09-25). It tracks 108 models across 85 providers and 4,913 configurations.
2. **Idle marketplace inventory**: rentable offers on Vast.ai, pulled from its public API on 2026-09-26. These are GPUs that are **not** rented, so they measure supply, not demand. Without a key the API caps each query at 64 offers.
3. **Hyperscaler SKUs and indices**: AWS, GCP and Azure instance families; which GPUs Silicon Data and Ornn chose to index; SemiAnalysis ClusterMAX coverage; Epoch AI shipment estimates (about 4M Hopper and 3M Blackwell GPUs shipped by Oct 2025).

### Coverage estimate

- **By provider listings.** The 26 GPU families here account for 549 provider listings on getdeploying. The named models left out (P100, RTX 3080, RTX PRO 5000, RTX 4000 Ada, Gaudi 2, A16, MI250, P4) account for 27. That leaves about 73 other tracked models; assuming 1 to 2 providers each gives 73 to 146 more. **Coverage by listing count: about 76–85%.** This metric overweights the tail, because a single small host with one old card counts the same as AWS.
- **By installed rental capacity (GPU count).** Weighted by capacity, the set covers an inferred **95% or more** (from shipment volumes and fleet composition). Most rented capacity is Hopper, Blackwell and A100 in hyperscaler and neocloud fleets; the GPUs left out are legacy cards (P100, P4, K80, M60), small consumer cards, or parts with no rental presence.

### Ranked list

Variants of one family share a rank. Provider counts come from getdeploying; Vast.ai counts are idle GPUs on 2026-09-26.

| Rank | Family | Evidence |
|---|---|---|
| 1 | H100 (SXM, PCIe, NVL) | 57–58 providers, the most of any GPU. Vast: SXM 170, NVL 70, PCIe 54 GPUs. AWS p5, GCP a3, Azure ND H100 v5. Indexed by Silicon Data (SDH100RT) and Ornn |
| 2 | RTX PRO 6000 Blackwell (WS, Server) | 54 providers. Vast: Server 142, WS 104 GPUs. RunPod, including MIG slices |
| 3 | H200 (SXM, NVL) | 51 providers. Vast: SXM 148, NVL 139. AWS p5e/p5en, GCP a3-ultra, Azure ND H200 v5 |
| 4 | A100 (SXM/PCIe, 40/80 GB) | 48 providers. Vast: PCIe 173, SXM4 166. AWS p4d/p4de, GCP a2, Azure NC A100 v4. Silicon Data SDA100RT |
| 5 | B200 | 39 providers. Vast 157. AWS p6-b200, GCP a4. Silicon Data SDB200RT, Ornn |
| 6 | L40S | 38 providers. Vast 198. AWS g6e |
| 7 | B300 | 33 providers. Vast 125. AWS p6-b300 |
| 8 | RTX 5090 | 23 providers. Vast 91. Indexed by Ornn |
| 9 | RTX 4090 | 21 providers. Vast 195 |
| 10 | L4 | 21 providers. Vast 60. AWS g6, GCP g2 |
| 11 | RTX A6000 | 20 providers. Vast 159 |
| 12 | V100 (SXM2, PCIe) | 20 providers. Vast 203, the largest idle pool (cheap, legacy). AWS p3 |
| 13 | RTX 6000 Ada | 14 providers. Vast 135 |
| 14 | L40 | 13 providers. Vast 10 |
| 15 | RTX A4000 | 13 providers. Vast 177 |
| 16 | MI300X | 11 providers. Azure ND MI300X v5. Absent from Vast and RunPod; AIMultiple finds only 7% of listings with confirmed stock |
| 17 | T4 | 11 providers. Vast 34. AWS g4dn, GCP, Azure: a high-volume hyperscaler SKU |
| 18 | A10 / A10G | A10 on 9 providers (Azure NV A10 v5). A10G on 2, effectively AWS g5 only, but high volume there |
| 19 | GB200 | 9 providers, mostly contracts. Azure ND GB200 v6, GCP a4x, AWS p6e-gb200 |
| 20 | RTX 3090 | 9 providers (undercounted, since P2P hosts are mostly excluded). Vast 108 |
| 21 | A40 | 8 providers. Vast 29 |
| 22 | GB300 | 7 providers, mostly on request |
| 23 | GH200 | 5 providers |
| 24 | MI325X | 5 providers |
| 25 | MI355X | 5 providers. Rated in ClusterMAX 3.0 |
| 26 | RTX A5000 | 2 providers on getdeploying (undercounted). Vast 145 |

### Dropped, and why

- **H20**: zero listings; it is a China-only export SKU.
- **Gaudi 3**: zero listings. **Gaudi 2**: 4 providers; Intel has little cloud traction.
- **P100, P4, K80, M60, MI250, A16**: legacy parts with 1–6 providers each.
- **RTX 3080/5070/5080, RTX 4000 Ada, RTX PRO 5000**: 4–5 providers each, or a small consumer tail on Vast. Add them if the profiler meets them in practice.
- **Vera Rubin**: rated in ClusterMAX 3.0 but not generally rentable yet.
- **TPU / Trainium**: not GPUs, and out of scope.

## 2. Spec table

The throughput columns are **dense** (no sparsity). INT8 is in TOPS; FP8, BF16, FP32 and FP4 are in TFLOPS. Memory is the vendor's figure; usable memory is a few GB less. For AMD, the SMs column holds compute units (CUs) and CC holds the ROCm gfx target. A `*` means the entry has at least one unverified field (see section 4).

GeForce cards (4090, 5090, 3090): the FP8 and BF16 figures use FP32 accumulate, which is what cuBLAS uses. The FP16-accumulate FP8 rate is twice that (4090: 660.6; 5090: 838). INT8 is not halved.

| # | id | Name | Arch / die | CC | SMs (full) | Mem | BW TB/s | INT8 | FP8 | BF16 | FP32 | FP4 | TDP W | Form / link |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `h100-sxm` | H100 SXM5 80GB | Hopper / GH100 | 9.0 | 132 (144) | 80 GB HBM3 | 3.35 | 1979 | 1979 | 989.5 | 67 | - | 700 | SXM5; NVLink 4, 900 GB/s; PCIe Gen5 x16 |
| 1 | `h100-pcie` | H100 PCIe 80GB | Hopper / GH100 | 9.0 | 114 (144) | 80 GB HBM2e | 2 | 1513 | 1513 | 756.5 | 51 | - | 350 | PCIe; PCIe Gen5 x16; optional NVLink bridge 600 GB/s |
| 1 | `h100-nvl`* | H100 NVL 94GB | Hopper / GH100 | 9.0 | 132 (144) | 94 GB HBM3 | 3.9 | 1670.5 | 1670.5 | 835.5 | 60 | - | 400 | PCIe; PCIe Gen5 x16; NVLink bridge 600 GB/s (pairs) |
| 2 | `rtx-pro-6000-ws`* | RTX PRO 6000 Blackwell Workstation 96GB | Blackwell / GB202 | 12.0 | 188 (192) | 96 GB GDDR7 ECC | 1.792 | 1000 | 1000 | 500 | 125 | 2000 | 600 | PCIe (workstation); PCIe Gen5 x16 |
| 2 | `rtx-pro-6000-server`* | RTX PRO 6000 Blackwell Server Edition 96GB | Blackwell / GB202 | 12.0 | 188 (192) | 96 GB GDDR7 ECC | 1.597 | 1000 | 1000 | 500 | 120 | 2000 | 600 | PCIe (passive, server); PCIe Gen5 x16 |
| 3 | `h200-sxm`* | H200 SXM 141GB | Hopper / GH100 | 9.0 | 132 (144) | 141 GB HBM3e | 4.8 | 1979 | 1979 | 989.5 | 67 | - | 700 | SXM5; NVLink 4, 900 GB/s; PCIe Gen5 x16 |
| 3 | `h200-nvl`* | H200 NVL 141GB | Hopper / GH100 | 9.0 | 132 (144) | 141 GB HBM3e | 4.8 | 1670.5 | 1670.5 | 835.5 | 60 | - | 600 | PCIe; 2- or 4-way NVLink bridge 900 GB/s per GPU; PCIe Gen5 x16 |
| 4 | `a100-sxm-80`* | A100 SXM4 80GB | Ampere / GA100 | 8.0 | 108 (128) | 80 GB HBM2e | 2.039 | 624 | - | 312 | 19.5 | - | 400 | SXM4; NVLink 3, 600 GB/s; PCIe Gen4 |
| 4 | `a100-sxm-40`* | A100 SXM4 40GB | Ampere / GA100 | 8.0 | 108 (128) | 40 GB HBM2 | 1.555 | 624 | - | 312 | 19.5 | - | 400 | SXM4; NVLink 3, 600 GB/s; PCIe Gen4 |
| 4 | `a100-pcie-80`* | A100 PCIe 80GB | Ampere / GA100 | 8.0 | 108 (128) | 80 GB HBM2e | 1.935 | 624 | - | 312 | 19.5 | - | 300 | PCIe; PCIe Gen4 x16; NVLink bridge 600 GB/s (pairs) |
| 4 | `a100-pcie-40`* | A100 PCIe 40GB | Ampere / GA100 | 8.0 | 108 (128) | 40 GB HBM2 | 1.555 | 624 | - | 312 | 19.5 | - | 250 | PCIe; PCIe Gen4 x16; NVLink bridge 600 GB/s (pairs) |
| 5 | `b200`* | B200 (HGX, 180GB) | Blackwell / GB100 x2 | 10.0 | 148 (160) | 180 GB HBM3e | 8 | 4500 | 4500 | 2250 | 75 | 9000 | 1000 | SXM; NVLink 5, 1.8 TB/s; PCIe Gen5 |
| 6 | `l40s` | L40S 48GB | Ada Lovelace / AD102 | 8.9 | 142 (144) | 48 GB GDDR6 ECC | 0.864 | 733 | 733 | 362.05 | 91.6 | - | 350 | PCIe; PCIe Gen4 x16 (no NVLink) |
| 7 | `b300`* | B300 (HGX, Blackwell Ultra) | Blackwell Ultra / 2 dies (name unpublished) | 10.3 | 148 (160) | 270 GB HBM3e | 8 | 188 | 4500 | 2250 | 75 | 13500 | 1100 | SXM; NVLink 5, 1.8 TB/s; PCIe Gen6 |
| 8 | `rtx-5090` | GeForce RTX 5090 32GB | Blackwell / GB202 | 12.0 | 170 (192) | 32 GB GDDR7 | 1.792 | 838 | 419 | 209.5 | 104.8 | 1676 | 575 | PCIe (consumer); PCIe Gen5 x16 |
| 9 | `rtx-4090` | GeForce RTX 4090 24GB | Ada Lovelace / AD102 | 8.9 | 128 (144) | 24 GB GDDR6X | 1.008 | 660.6 | 330.3 | 165.2 | 82.6 | - | 450 | PCIe (consumer); PCIe Gen4 x16 |
| 10 | `l4`* | L4 24GB | Ada Lovelace / AD104 | 8.9 | 58 (60) | 24 GB GDDR6 ECC | 0.3 | 242 | 242 | 121 | 30.3 | - | 72 | PCIe (1-slot LP); PCIe Gen4 x16 |
| 11 | `rtx-a6000`* | RTX A6000 48GB | Ampere / GA102 | 8.6 | 84 (84) | 48 GB GDDR6 ECC | 0.768 | 309.7 | - | 154.8 | 38.7 | - | 300 | PCIe (workstation); PCIe Gen4 x16; NVLink bridge 112.5 GB/s |
| 12 | `v100-sxm2`* | V100 SXM2 16/32GB | Volta / GV100 | 7.0 | 80 (84) | 32 GB HBM2 | 0.9 | - | - | 125 (FP16) | 15.7 | - | 300 | SXM2; NVLink 2, 300 GB/s |
| 12 | `v100-pcie`* | V100 PCIe 16/32GB | Volta / GV100 | 7.0 | 80 (84) | 32 GB HBM2 | 0.9 | - | - | 112 (FP16) | 14 | - | 250 | PCIe; PCIe Gen3 x16 |
| 13 | `rtx-6000-ada`* | RTX 6000 Ada Generation 48GB | Ada Lovelace / AD102 | 8.9 | 142 (144) | 48 GB GDDR6 ECC | 0.96 | 728.5 | 728.5 | 364.2 | 91.1 | - | 300 | PCIe (workstation); PCIe Gen4 x16 (no NVLink) |
| 14 | `l40` | L40 48GB | Ada Lovelace / AD102 | 8.9 | 142 (144) | 48 GB GDDR6 ECC | 0.864 | 362 | 362 | 181 | 90.5 | - | 300 | PCIe; PCIe Gen4 x16 (no NVLink) |
| 15 | `rtx-a4000`* | RTX A4000 16GB | Ampere / GA104 | 8.6 | 48 (48) | 16 GB GDDR6 ECC | 0.448 | 153.4 | - | 76.7 | 19.2 | - | 140 | PCIe (1-slot workstation); PCIe Gen4 x16 |
| 16 | `mi300x`* | AMD Instinct MI300X 192GB | CDNA 3 / MI300 (8 XCD) | gfx942 | 304 (320) | 192 GB HBM3 | 5.3 | 2614.9 | 2614.9 | 1307.4 | 163.4 | - | 750 | OAM; Infinity Fabric: 8 links x 128 GB/s; PCIe Gen5 x16 |
| 17 | `t4`* | T4 16GB | Turing / TU104 | 7.5 | 40 (48) | 16 GB GDDR6 | 0.32 | 130 | - | 65 (FP16) | 8.1 | - | 70 | PCIe (1-slot LP); PCIe Gen3 x16 |
| 18 | `a10` | A10 24GB | Ampere / GA102 | 8.6 | 72 (84) | 24 GB GDDR6 | 0.6 | 250 | - | 125 | 31.2 | - | 150 | PCIe (1-slot); PCIe Gen4 x16 |
| 18 | `a10g`* | A10G 24GB (AWS) | Ampere / GA102 | 8.6 | 80 (84) | 24 GB GDDR6 | 0.6 | 140 | - | 70 | 35 | - | 300 | PCIe; PCIe Gen4 x16 |
| 19 | `gb200`* | GB200 (Grace Blackwell, per GPU) | Blackwell / GB100 x2 | 10.0 | 148 (160) | 186 GB HBM3e | 8 | 5000 | 5000 | 2500 | 80 | 10000 | 1200 | Superchip (NVL72 rack); NVLink 5, 1.8 TB/s; NVLink-C2C 900 GB/s to Grace |
| 20 | `rtx-3090`* | GeForce RTX 3090 24GB | Ampere / GA102 | 8.6 | 82 (84) | 24 GB GDDR6X | 0.936 | 284.7 | - | 71.2 | 35.6 | - | 350 | PCIe (consumer); PCIe Gen4 x16 |
| 21 | `a40` | A40 48GB | Ampere / GA102 | 8.6 | 84 (84) | 48 GB GDDR6 ECC | 0.696 | 299.3 | - | 149.7 | 37.4 | - | 300 | PCIe (passive); PCIe Gen4 x16; NVLink bridge 112.5 GB/s |
| 22 | `gb300`* | GB300 (Grace Blackwell Ultra, per GPU) | Blackwell Ultra / 2 dies (name unpublished) | 10.3 | 148 (160) | 279 GB HBM3e | 8 | 167 | 5000 | 2500 | 83.3 | 15000 | 1400 | Superchip (NVL72 rack); NVLink 5, 1.8 TB/s; NVLink-C2C 900 GB/s to Grace |
| 23 | `gh200`* | GH200 Grace Hopper (96GB HBM3) | Hopper / GH100 | 9.0 | 132 (144) | 96 GB HBM3 | 4 | 1979 | 1979 | 989.5 | 67 | - | 1000 | Superchip module; NVLink-C2C 900 GB/s to Grace CPU; NVLink 4 |
| 24 | `mi325x`* | AMD Instinct MI325X 256GB | CDNA 3 / MI300 (8 XCD) | gfx942 | 304 (320) | 256 GB HBM3e | 6 | 2614.9 | 2614.9 | 1307.4 | 163.4 | - | 1000 | OAM; Infinity Fabric: 8 links x 128 GB/s; PCIe Gen5 x16 |
| 25 | `mi355x` | AMD Instinct MI355X 288GB | CDNA 4 / MI350 (8 XCD) | gfx950 | 256 (288) | 288 GB HBM3e | 8 | 5000 | 5000 | 2500 | 157.3 | 10100 | 1400 | OAM; Infinity Fabric: 7 links x 153 GB/s; PCIe Gen5 x16 |
| 26 | `rtx-a5000`* | RTX A5000 24GB | Ampere / GA102 | 8.6 | 64 (84) | 24 GB GDDR6 ECC | 0.768 | 222.2 | - | 111.1 | 27.8 | - | 230 | PCIe (workstation); PCIe Gen4 x16; NVLink bridge |

### Tricky ones, checked

- **H100 SM counts:** SXM 132, PCIe 114 and NVL 132. NVIDIA's Hopper blog gives 132 and 114. For NVL, the product brief (PB-11773) gives a 1,785 MHz boost clock, and the official 60 TFLOPS FP32 then requires 132 SMs (132 × 256 × 1.785 GHz); 114 SMs would give only 52. Lenovo's product guide lists 14,592 cores for NVL, but that is a copy of the PCIe row and contradicts NVIDIA's FP32 figure.
- **H200:** same 132 SMs and compute as H100 SXM; 141 GB HBM3e at 4.8 TB/s. H200 NVL has the same memory and bandwidth as H200 SXM and the NVL compute figures.
- **B200:** reported as **one CUDA device with 148 SMs** (two dies of 80 SMs, 74 enabled each; NVIDIA MPS blog). MLOPart can split it into two 70-SM devices.
- **B300:** NVIDIA's MPS blog implies 148 SMs on DGX B300; some third-party sources claim 160. Its **INT8 tensor throughput is far lower** than B200's (HGX page: 3 POPS vs 72 POPS for 8 GPUs, sparse).
- **L40S, L40 and RTX 6000 Ada:** all AD102 with **142 of 144 SMs** enabled (18,176 cores) and 48 GB. They differ in official tensor rate (L40S about 2× L40) and bandwidth (864 vs 960 GB/s).
- **A10 vs A10G:** 72 vs 80 SMs. The A10G is an AWS-only part whose tensor rate is much lower (INT8 140 vs 250 TOPS).
- **A100 40 vs 80 GB:** 1,555 GB/s for both 40 GB parts; 1,935 GB/s for 80 GB PCIe and 2,039 GB/s for 80 GB SXM.
- **RTX 4090 INT8 dense:** 660.6 TOPS (1,321.2 is the sparse figure). **RTX 5090:** 838 dense / 1,676 sparse. **RTX 3090:** 284.7 / 569.4.
- **MI300X:** 304 CUs (8 XCDs × 38 active of 40). MI325X has the same die and CU count. MI355X has 256 CUs (8 × 32 active of 36).

## 3. Confusable pairs: what the profiler can tell apart

`separate_by` keys: `sms` (timing staircase), `mem_gb` (allocate and touch), `bw_tbs` (device copy bandwidth), `fp8` (an FP8 GEMM runs at FP8 speed), `int8_ratio` (sustained tensor throughput vs the reference), and `h2d_bw` (host-to-device copy; NVLink-C2C on Grace parts vs PCIe). Pairs marked HARD have no single cheap, decisive feature.

| A | B | Separate by | Note |
|---|---|---|---|
| `h100-sxm` | `h200-sxm` | mem_gb, bw_tbs | Same 132 SMs and compute. 80 vs 141 GB: allocate >80 GB. 3.35 vs 4.8 TB/s (1.43x). |
| `h100-sxm` | `h100-nvl` | mem_gb, bw_tbs, int8_ratio | Both 132 SMs. 80 vs 94 GB, 3.35 vs 3.9 TB/s; NVL tensor peak is 0.84x (lower clocks, 400 W). |
| `h100-sxm` | `gh200` | mem_gb, bw_tbs, h2d_bw | Both 132 SMs. 80 vs 96 GB; host-to-device copy over NVLink-C2C (~900 GB/s peak) vs PCIe Gen5 (~64 GB/s) is a large, cheap gap. |
| `h200-sxm` | `h200-nvl` | int8_ratio | HARD: same SMs, same 141 GB, same 4.8 TB/s. Only sustained tensor throughput (NVL 0.84x) and power (600 vs 700 W) differ. Needs a stable-clock INT8/BF16 run and a wide tolerance. |
| `h200-sxm` | `gh200` | mem_gb, h2d_bw | 144 GB GH200 variant vs 141 GB H200 is too close to call by memory; use host-to-device bandwidth (C2C) and CPU arch. |
| `h100-nvl` | `h200-nvl` | mem_gb, bw_tbs | Same SMs and compute; 94 vs 141 GB, 3.9 vs 4.8 TB/s. |
| `h100-pcie` | `h100-nvl` | sms, mem_gb, bw_tbs | 114 vs 132 SMs (staircase), 80 vs 94 GB, 2.0 vs 3.9 TB/s. Easy. |
| `h100-pcie` | `a100-pcie-80` | fp8, sms, bw_tbs | FP8 yes/no is the cheapest decisive test. SM staircase 114 vs 108 is only 5.5% apart; bandwidth 2.0 vs 1.935 is too close. |
| `h100-sxm` | `a100-sxm-80` | sms, fp8, bw_tbs, int8_ratio | Waterline's demo pair. 132 vs 108 SMs; FP8 yes/no; 3.35 vs 2.04 TB/s; INT8 about 3.2x. |
| `a100-sxm-80` | `a100-pcie-80` | bw_tbs | HARD: same 108 SMs, 80 GB, compute. 2.039 vs 1.935 TB/s (5%) and 400 vs 300 W under sustained load. NVLink P2P bandwidth separates them only on multi-GPU rentals. |
| `a100-sxm-40` | `a100-pcie-40` | (none cheap) | HARDEST: same SMs, memory, identical 1.555 TB/s. Only the power limit (400 vs 250 W) shows, as throttling under long sustained load; or NVLink P2P on multi-GPU. Treat as one class. |
| `a100-sxm-40` | `a100-sxm-80` | mem_gb, bw_tbs | 40 vs 80 GB; 1.555 vs 2.039 TB/s. |
| `a100-pcie-40` | `a100-pcie-80` | mem_gb, bw_tbs | 40 vs 80 GB; 1.555 vs 1.935 TB/s. |
| `l40s` | `l40` | int8_ratio | Same 142 SMs, 48 GB, 864 GB/s. Official FP8/INT8 peak differs 2x (733 vs 362). |
| `l40s` | `rtx-6000-ada` | bw_tbs | HARD: same 142 SMs, 48 GB, near-equal FP8/INT8 (733 vs 728.5). Only bandwidth differs, 0.864 vs 0.960 TB/s (11%), plus power (350 vs 300 W). |
| `l40` | `rtx-6000-ada` | bw_tbs, int8_ratio | 142 SMs both; 0.864 vs 0.960 TB/s and INT8 about 2x. |
| `rtx-4090` | `rtx-6000-ada` | sms, mem_gb | 128 vs 142 SMs; 24 vs 48 GB. |
| `rtx-a6000` | `a40` | bw_tbs | HARD: same GA102 84 SMs, 48 GB. 0.768 vs 0.696 TB/s (10%); tensor peak within 4%. |
| `rtx-a6000` | `rtx-3090` | mem_gb, sms | 84 vs 82 SMs is too close for the staircase; 48 vs 24 GB is decisive. |
| `a10` | `a10g` | sms, int8_ratio | 72 vs 80 SMs; A10G tensor peak much lower (INT8 140 vs 250 TOPS) despite more SMs. |
| `b200` | `b300` | mem_gb, int8_ratio | Both 148 SMs (per NVIDIA) and 8 TB/s. 180 vs ~270 GB; INT8 dense 4,500 vs ~188 TOPS (about 24x). The INT8 probe is decisive. |
| `b200` | `gb200` | h2d_bw, int8_ratio | 148 SMs, 8 TB/s both; 180 vs 186 GB is too small to trust. NVLink-C2C host link and aarch64 host separate them; tensor peak 1.11x. |
| `b300` | `gb300` | h2d_bw | Same logic as B200/GB200: C2C host link and aarch64 host; memory ~270 vs ~279 GB is too close. |
| `rtx-pro-6000-ws` | `rtx-pro-6000-server` | bw_tbs | Same 188 SMs, 96 GB. 1.792 vs 1.597 TB/s (11%). The 300 W Max-Q variant matches WS bandwidth and differs only in sustained throughput. |
| `rtx-5090` | `rtx-pro-6000-ws` | sms, mem_gb | 170 vs 188 SMs; 32 vs 96 GB; same 1.792 TB/s. |
| `mi300x` | `mi325x` | mem_gb, bw_tbs | Same 304 CUs and compute; 192 vs 256 GB; 5.3 vs 6.0 TB/s. |
| `v100-sxm2` | `v100-pcie` | int8_ratio | HARD: same 80 SMs, 900 GB/s. FP16 tensor 125 vs 112 (1.12x); power 300 vs 250 W. 16 vs 32 GB variants split by allocation. (No INT8 tensor: use FP16.) |
| `l4` | `t4` | sms, fp8 | 58 vs 40 SMs; FP8 yes/no. |

### Rules of thumb for the profiler

- **Cheapest decisive probes, in order:** FP8 yes/no (splits Hopper and later from Ampere); SM staircase (reliable when counts differ by about 10% or more: 132/114/108, 142/128, 188/170, 80/72); allocatable memory (reliable when sizes differ by 10 GB or more); bandwidth (reliable at about 10–15% or more); INT8 ratio (decisive for B200 vs B300 at about 24×, and for L40 vs L40S at 2×).
- **Too close to call on one probe:** 84 vs 82 SMs (A6000 vs 3090), 114 vs 108 SMs (H100 PCIe vs A100), 180 vs 186 GB (B200 vs GB200), 141 vs 144 GB (H200 vs GH200 144 GB). Combine probes.
- **Treat as one class (no cheap probe separates them):** A100 SXM 40 vs A100 PCIe 40, and H200 SXM vs H200 NVL at single-GPU level. They differ mainly in power limit, which shows only as sustained-clock throughput.

### What a host can fake vs what the profiler measures

| Signal | Source | Can the host fake it? |
|---|---|---|
| GPU name, PCI device ID, UUID, serial (nvidia-smi / NVML) | driver self-report | **Yes.** A modified driver, a hypervisor-presented vGPU, or an LD_PRELOAD shim on NVML/CUDA can change them |
| Compute capability, `multiProcessorCount`, `totalGlobalMem`, clocks, power limit (`cudaGetDeviceProperties`) | driver self-report | **Yes**, the same way. The staircase must **not** size its sweep from the reported SM count; sweep past the largest expected count |
| Memory reported by the driver | self-report | Yes. Measure by allocating **and writing/reading** every page. Watch for unified-memory oversubscription (host RAM behind "device" memory), which shows as a bandwidth collapse on the touched pages |
| SM count | **measured** (timing staircase: runtime steps at the SM count) | Hard to fake. A time-sliced or MIG/MLOPart slice shows its real (smaller) SM count, which is correct for the renter |
| FP8 support | **measured** (FP8 GEMM runs, at the expected rate) | A shim could emulate FP8 in software on an A100, so check the **throughput**, not only that the kernel runs |
| INT8 / BF16 tensor throughput | **measured** | Hard to fake upward. It can be lower than spec because of power or thermal throttling, so grade relative to the class with wide tolerance |
| Device memory bandwidth | **measured** | Hard to fake upward |
| Host-to-device bandwidth, CPU arch (aarch64 = Grace) | measured / observed | Hard to fake. It is a cheap separator for GH200/GB200/GB300 |
| Multi-GPU NVLink P2P bandwidth | measured | Only available on multi-GPU rentals. It separates SXM from PCIe |

## 4. Figures not verified from a primary source

Each model's `unverified` array in the JSON lists these fields:

- `h100-nvl`: sms
- `h200-sxm`: sms
- `h200-nvl`: sms
- `gh200`: sms, bw_tbs, dense, tdp_w
- `b200`: tdp_w
- `b300`: sms, mem_gb, dense.int8_tops, tdp_w
- `gb200`: sms, tdp_w
- `gb300`: sms, mem_gb
- `a100-sxm-80`: full_die_sms
- `a100-sxm-40`: full_die_sms
- `a100-pcie-80`: full_die_sms
- `a100-pcie-40`: full_die_sms
- `l4`: full_die_sms
- `rtx-6000-ada`: dense.int8_tops, dense.bf16_tflops
- `rtx-pro-6000-ws`: dense.int8_tops, dense.fp8_tflops, dense.bf16_tflops
- `rtx-pro-6000-server`: dense.int8_tops
- `rtx-3090`: full_die_sms
- `rtx-a6000`: dense.int8_tops
- `rtx-a5000`: dense.int8_tops
- `rtx-a4000`: dense.int8_tops, full_die_sms
- `a10g`: cc
- `t4`: full_die_sms
- `v100-sxm2`: cc, full_die_sms
- `v100-pcie`: cc, full_die_sms
- `mi300x`: full_die_sms
- `mi325x`: full_die_sms

Notes on the unverified fields:
- **SM counts not printed by NVIDIA** (H100 NVL, H200, GB200, GB300, B300) are derived from FP32 figures or taken from the MPS blog. **B300 and GB300 are the least certain: 148 vs 160.** Waterline should measure them rather than trust the table.
- **Workstation tensor figures** (RTX 6000 Ada, RTX PRO 6000, RTX A6000/A5000/A4000): NVIDIA publishes only one sparse "AI TOPS" or "Tensor TFLOPS" number. The INT8, FP8 and BF16 dense values are derived from it using architecture ratios.
- **B300/GB300 INT8:** derived from a single HGX/NVL72 total, assuming it is a sparse figure.
- **B300/GB300 memory:** derived from the 2.1 TB HGX and 20 TB NVL72 totals. **GH200:** the bandwidth (4.0 TB/s for 96 GB) and compute come from secondary sources.
- **TDP** for B200 (1,000 W), B300 (1,100 W) and GB200 (1,200 W) comes from secondary sources.
- **cc for V100 (7.0) and A10G (8.6):** these parts are not on NVIDIA's current CUDA GPU page.
- **full_die_sms** for GA100 (128), GA102 (84), GA104 (48), TU104 (48), GV100 (84), AD104 (60) and the MI300 die (320 CUs) are from architecture whitepapers not re-checked for this table.

## 5. Sources

Specs:
- H100: https://www.nvidia.com/en-us/data-center/h100/, https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/, https://lenovopress.lenovo.com/lp1732-thinksystem-nvidia-h100-pcie-gen5-gpu, H100 NVL product brief https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/h100/PB-11773-001_v01.pdf
- H200: https://www.nvidia.com/en-us/data-center/h200/ ; GH200: https://www.nvidia.com/en-us/data-center/grace-hopper-superchip/
- Blackwell: https://www.nvidia.com/en-us/data-center/hgx/, https://www.nvidia.com/en-us/data-center/dgx-b200/, https://www.nvidia.com/en-us/data-center/gb200-nvl72/, https://www.nvidia.com/en-us/data-center/gb300-nvl72/, https://developer.nvidia.com/blog/inside-nvidia-blackwell-ultra-the-chip-powering-the-ai-factory-era/, https://developer.nvidia.com/blog/boost-gpu-memory-performance-with-no-code-changes-using-nvidia-cuda-mps/
- Compute capability: https://developer.nvidia.com/cuda-gpus
- A100: https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-us-nvidia-1758950-r4-web.pdf, https://www.nvidia.com/en-us/data-center/a100/
- Ada: https://images.nvidia.com/aem-dam/Solutions/geforce/ada/nvidia-ada-gpu-architecture.pdf (RTX 4090, L40, A40, L4, T4 appendices), https://www.nvidia.com/en-us/data-center/l40s/, https://www.nvidia.com/en-us/data-center/l40/, https://www.nvidia.com/en-us/data-center/l4/, https://www.nvidia.com/en-us/products/workstations/rtx-6000/, https://lenovopress.lenovo.com/lp1940-thinksystem-nvidia-rtx-6000-ada-48gb-pcie-active-gpu
- RTX Blackwell: https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf (RTX 5090/4090/3090 table), https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/, https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
- Ampere workstation and data center: https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/proviz-print-nvidia-rtx-a6000-datasheet-us-nvidia-1454980-r9-web%20(1).pdf, https://pnypartners.com/wp-content/uploads/NVIDIA-RTX-Positioning-Chart.pdf, https://www.nvidia.com/en-us/products/workstations/rtx-a5000/, https://www.nvidia.com/en-us/products/workstations/rtx-a4000/, https://www.nvidia.com/en-us/data-center/products/a10-gpu/, https://d1.awsstatic.com/product-marketing/ec2/NVIDIA_AWS_A10G_DataSheet_FINAL_02_17_2022.pdf
- V100: https://images.nvidia.com/content/technologies/volta/pdf/volta-v100-datasheet-update-us-1165301-r5.pdf
- AMD: https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html, https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html, https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html, https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/white-papers/amd-cdna-3-white-paper.pdf, https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/white-papers/amd-cdna-4-architecture-whitepaper.pdf, https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html

Rental evidence:
- https://getdeploying.com/gpus and per-GPU pages (`/gpus/nvidia-<model>`), 2026-09-25/26
- Vast.ai public offers API: https://console.vast.ai/api/v0/bundles/ (2026-09-26)
- RunPod pricing: https://www.runpod.io/pricing (updated 2026-09-13)
- SemiAnalysis ClusterMAX 3.0: https://newsletter.semianalysis.com/p/clustermax-30-the-industry-standard (2026-09-23)
- Silicon Data indices: https://www.silicondata.com/products/silicon-index
- Ornn OCPI: https://data.ornn.com/markets
- AIMultiple GPU rental index: https://aimultiple.com/gpu-index
- gputracker 2026 statistics: https://gputracker.dev/gpu-cloud-statistics-2026
