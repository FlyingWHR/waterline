"""Waterline one-line check, served by the API at /run. Paste into the rented pod's terminal:

  curl -fsSL <host>/run | python3 - <cloud> <the GPU the listing promises: h100, h200, b200, l40s, a100, ...>
  curl -fsSL <host>/run | python3 - <cloud> h100 --gpu 3   # a multi-GPU pod: check the fourth GPU
  curl -fsSL <host>/run | python3 - <cloud> h100 --every 30m   # a periodic series, until Ctrl-C (or --times N)
  curl -fsSL <host>/run | python3 - calibrate      # once, on the reference H100: prints the exam size to set

Nothing is written to disk: the profiler (core/ + prover/) is fetched from the API and imported from memory.
It needs numpy and torch (every PyTorch pod image has them); cupy is installed once if the image lacks it.
"""
import argparse
import importlib.abc
import random
import re
import secrets
import time
import importlib.util
import io
import subprocess
import json
import sys
import urllib.request
import zipfile

API = "__API__"
_TABLE = '__CLASSES__'  # the API fills in core/gpu_classes.json as {slug: code}
CLASSES = json.loads(_TABLE) if _TABLE.startswith("{") else {"h100": 1, "h100-sxm": 1, "h100-pcie": 2, "a100": 3}

ap = argparse.ArgumentParser(prog="waterline", description="Check this GPU against its listing, from inside the rental.")
ap.add_argument("cloud", help="the provider, lowercase, e.g. cloud-b")
ap.add_argument("claimed", help="the GPU the listing promises, e.g. h100, h200, b200, l40s, a100, rtx-4090")
ap.add_argument("--gpu", help="on a multi-GPU pod, which GPU to check (0, 1, ...); default the first")
ap.add_argument("--every", help="check again at this interval, e.g. 30m or 2h (jittered ±20%%), until Ctrl-C")
ap.add_argument("--times", type=int, help="with --every: stop after this many checks")
ap.add_argument("--api", default=API, help=argparse.SUPPRESS)
ap.add_argument("--cpu", action="store_true", help=argparse.SUPPRESS)  # no GPU: the offline test path
calibrating = sys.argv[1:2] == ["calibrate"]
a, rest = (ap.parse_known_args(["calibrate", "h100", *sys.argv[2:]]) if calibrating else ap.parse_known_args())
claimed = CLASSES.get(a.claimed.lower())
if claimed is None:
    sys.exit("waterline: unknown GPU. One of: " + ", ".join(sorted(k for k in CLASSES if not k.isdigit())))


def need(module, package):
    try:
        __import__(module)
    except ImportError:
        if package is None:
            sys.exit(f"waterline: this pod has no {module}; use a PyTorch image")
        print(f"waterline: installing {package} (once per pod) ...", file=sys.stderr)
        pip = [sys.executable, "-m", "pip", "install", "-q", package]
        if subprocess.call(pip) and subprocess.call(pip + ["--break-system-packages"]):  # PEP 668 system Pythons
            sys.exit(f"waterline: couldn't install {package}; run: {' '.join(pip)}")


if a.gpu is not None:
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = a.gpu  # before torch or cupy start CUDA
need("numpy", "numpy")
if not a.cpu:
    need("torch", None)
    import torch
    if not torch.cuda.is_available():
        sys.exit("waterline: no GPU is visible to PyTorch on this pod")
    if not hasattr(torch, "_int_mm"):
        sys.exit(f"waterline: PyTorch {torch.__version__} has no INT8 matmul; use a PyTorch 2.1+ image")
    need("cupy", "cupy-cuda11x" if str(torch.version.cuda).startswith("11") else "cupy-cuda12x")

with urllib.request.urlopen(a.api.rstrip("/") + "/api/bundle", timeout=60) as r:
    bundle = zipfile.ZipFile(io.BytesIO(r.read()))
names = set(bundle.namelist())


class Memory(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Imports core.* and prover.* straight from the downloaded zip."""

    def find_spec(self, name, path=None, target=None):
        base = name.replace(".", "/")
        for f, package in ((base + "/__init__.py", True), (base + ".py", False)):
            if f in names:
                return importlib.util.spec_from_loader(name, self, origin=f, is_package=package)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        f = module.__spec__.origin
        module.__file__ = "waterline/" + f
        exec(compile(bundle.read(f), module.__file__, "exec"), module.__dict__)

    def get_data(self, path):  # pkgutil.get_data, e.g. core/gpu_specs.json
        return bundle.read(path.removeprefix("waterline/"))


sys.meta_path.insert(0, Memory())
if calibrating:
    from prover.calibrate import main as calibrate  # noqa: E402
    sys.exit(calibrate([*(["--cpu"] if a.cpu else []), *rest]))
from prover.run import DIM, main, paint  # noqa: E402

args = ["--api", a.api, "--cloud", a.cloud, "--claimed", str(claimed), *(["--cpu"] if a.cpu else []), *rest]
if not a.every:
    sys.exit(main(args))

# A periodic series: one id, numbered checks, jittered gaps so the host can't time the next one.
m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", a.every.strip().lower())
if not m:
    sys.exit("waterline: --every takes a duration like 90s, 30m or 2h")
gap = float(m[1]) * {"s": 1, "m": 60, "h": 3600, "": 60}[m[2]]
if gap < 60 and not a.cpu:
    sys.exit("waterline: --every must be at least 1m")
series, n = secrets.token_hex(4), 0
try:
    while a.times is None or n < a.times:
        n += 1
        main(args + ["--series", series, "--seq", str(n)] + (["--no-mark"] if n > 1 else []))
        if a.times is not None and n >= a.times:
            break
        wait = gap * random.uniform(0.8, 1.2)
        print(paint(DIM, f"  ↻ next check in ~{f'{wait / 60:.0f} min' if wait >= 90 else f'{wait:.0f} s'} (jittered, so the host can't time it) · Ctrl-C to stop"),
              file=sys.stderr, flush=True)
        time.sleep(wait)
except KeyboardInterrupt:
    pass
print(paint(DIM, f"  ↻ series {series} ended after {n} check{'s' if n != 1 else ''}"), file=sys.stderr, flush=True)
