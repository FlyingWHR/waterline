# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Waterline: proof of delivered GPU compute for a 36-hour ETHGlobal hackathon. A rented GPU answers a seeded matmul challenge under a deadline, a CPU verifier spot-checks it and signs a mark, and an on-chain escrow releases or refunds the rent based on that mark. Payments go through x402 to a per-job CREATE2 address.

**`PLAN.md` is the source of truth for the event build**: narrative, partner picks (ENS, Curvegrid), architecture (five pieces: agent/, prover/, api/ on Vercel + Redis, contracts/ Marks on Ethereum Sepolia which is also the ENS resolver for *.waterline.eth, web/), the hour-by-hour solo build plan, demo rules and trust rules. Read it first.

`files/` is **pre-hackathon reference work**: ETHGlobal's start-fresh rule means event code gets rewritten at the event. What carries over is the design, the measurements, and `vectors.json`. `files/BUILD_ORDER.md` is the hour-by-hour rewrite plan; `files/STUDY.md` records which existing tools (gpu-fryer, Imbue cluster-health, etc.) were studied and what was taken from each.

## The snapshot is incomplete

`files/README.md` describes a larger layout than what is on disk. Missing here: `rng.py`, `ref.c`, `test_spike.py`, `scale.py`, `run_e2e.py`, `verify_vectors.py`, `test_broker.py`, `test_verdict.py`, `protocol.py`, and `contracts/` (`compile.js`, `evm_test.js`, `addr_check.js`). The Python modules are also flat here, but `prover.py` and `verifier.py` use package-relative imports (`from .challenge import ...`) because they belong in a `waterline/` package. So `run_all.sh` and most modules will not run as-is. Don't invent the missing files; ask the user for them.

## Commands (once the full tree is present)

```
pip install numpy cryptography pycryptodome
./run_all.sh               # everything that runs without a GPU or RPC
python3 verify_vectors.py  # any rewrite must still pass this
python3 gpu_check.py       # needs CUDA, cupy, torch; H100/A100 only
```

Contracts are tested on a local EVM (`@ethereumjs/vm` + `solc@0.8.26`) via `contracts/compile.js`, `evm_test.js`, `addr_check.js`. There is no Foundry or Hardhat.

## Architecture

Pipeline: `challenge` (shared maths) → `prover` (GPU does work) → `verifier` (CPU spot-checks) → `verdict` (classify) → `mark` (signed result) → `Waterline.sol` (settle). `broker` sits in front and handles payment.

- **`challenge.py`**: shared by prover and verifier. Step i computes `C_i = A_i @ B_i` with A and B generated from the seed by a counter-based generator (`rng.row/col/matrix/mix64`). Each output row is reduced to one **nonlinear** 64-bit fingerprint (sum of splitmix64(entry ^ key)), the fingerprints are hashed per step, and the steps are Merkle-rooted. The nonlinearity is deliberate: linear bucket sums let a patched row survive about 88% of checks. Don't "simplify" it back to a linear sum.
- **Generator agreement**: the CUDA kernel, `ref.c`, and the Python generator must produce identical values. The verifier recomputes individual rows and columns lazily, so it never builds full matrices at n=16384.
- **`prover.py`**: `CpuProver` (tested) and `GpuProver` (event path), plus structure probes and telemetry.
- **`verifier.py`**: issues the seed, enforces the deadline, samples rows and entries, grades the result, and signs with Ed25519.
- **`verdict.py`**: tells healthy, hot-but-genuine, A100-as-H100, PCIe-as-SXM, and time-sliced apart, and maps each to a refund policy. Throttle flags decide the verdict; throughput only explains it. Grading is relative to the advertised class and the cohort, not an absolute number.
- **`mark.py`**: mark schema, canonical JSON, hashing, and signatures. A third party can re-check a mark from the public key alone, so canonicalization must stay byte-stable.
- **`broker.py`**: x402 402 response bodies (fields match `@x402/core` 2.26), per-job CREATE2 `payTo`, and Intercepta screening (`intercepta_screen`/`should_pay`, hooked via `onBeforePaymentCreation`). The Python CREATE2 derivation must match `EscrowFactory` in Solidity byte for byte.
- **`Waterline.sol`**: `MarkRegistry`, `JobEscrow`, `EscrowFactory`, `TestUSDC`. Fail refunds, pass pays, a missing mark reverts, expiry refunds.
- **`gpu_check.py`**: GPU gates: matches the CPU, timing, SM-count staircase (expected steps at 132 / 114 / 108 for H100 SXM / H100 PCIe / A100), FP8, and bandwidth.

## Invariants

- `vectors.json` is frozen. It fixes generator outputs, `fp_key`, per-step fingerprints, and roots. Any change to the generator or the challenge maths must still reproduce it.
- Python and Solidity must agree on CREATE2 addresses and on mark hashing.
