# Pod setup (Ubuntu, CUDA 12, H100 or A100)

The agent copies `core/` and `prover/` to `~/waterline` over scp. On a fresh pod, once:

```bash
nvidia-smi                                  # driver present, note the CUDA version (12.x)
python3 --version                           # 3.10+
python3 -m pip install -U pip
python3 -m pip install numpy cupy-cuda12x nvidia-ml-py   # nvidia-ml-py: health report (optional)
python3 -m pip install torch --index-url https://download.pytorch.org/whl/cu124
# most PyTorch pod images already ship torch with CUDA; check before reinstalling:
python3 -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))"
```

Check the GPU path matches the CPU maths bit for bit, and look at the probe numbers:

```bash
cd ~/waterline
python3 -m prover.gpu        # "gpu self-check: PASS", then SMs / fp8 / clock / bandwidth / fingerprint
```

Expected: H100 SXM 132 SMs + fp8 True; H100 PCIe 114 + True; A100 108 + False.
If the fingerprint changes between two runs on the same GPU, lower `QUANT` in `prover/gpu.py`.

Full run (the agent does this for you over SSH):

```bash
python3 -m prover.run --api https://<api host> --cloud cloud-b --claimed 1
```

Prints the API verdict JSON; writes `result.json` (probes + staircase timings) next to it.
Set the deadline from real numbers: the stderr line `computed 100 steps in X s` for each GPU class.
