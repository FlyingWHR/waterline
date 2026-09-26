"""Waterline one-line check, served by the API at /run. Paste into the rented pod's terminal:

  curl -fsSL <host>/run | python3 - <cloud> <what the listing promises: h100 | h100-pcie | a100>

Nothing is written to disk: the profiler (core/ + prover/) is fetched from the API and imported from memory.
It needs numpy and torch (every PyTorch pod image has them); cupy is installed once if the image lacks it.
"""
import argparse
import importlib.abc
import importlib.util
import io
import subprocess
import sys
import urllib.request
import zipfile

API = "__API__"
CLASSES = {"h100-sxm": 1, "h100": 1, "h100-pcie": 2, "a100": 3, "1": 1, "2": 2, "3": 3}

ap = argparse.ArgumentParser(prog="waterline", description="Check this GPU against its listing, from inside the rental.")
ap.add_argument("cloud", help="the provider, lowercase, e.g. cloud-b")
ap.add_argument("claimed", help="what the listing promises: h100 (SXM), h100-pcie or a100")
ap.add_argument("--api", default=API, help=argparse.SUPPRESS)
ap.add_argument("--cpu", action="store_true", help=argparse.SUPPRESS)  # no GPU: the offline test path
a, rest = ap.parse_known_args()
claimed = CLASSES.get(a.claimed.lower())
if claimed is None:
    sys.exit("waterline: the listing must be h100, h100-pcie or a100")


def need(module, package):
    try:
        __import__(module)
    except ImportError:
        if package is None:
            sys.exit(f"waterline: this pod has no {module}; use a PyTorch image")
        print(f"waterline: installing {package} (once per pod) ...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", package])


need("numpy", "numpy")
if not a.cpu:
    need("torch", None)
    need("cupy", "cupy-cuda12x")

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
from prover.run import main  # noqa: E402

sys.exit(main(["--api", a.api, "--cloud", a.cloud, "--claimed", str(claimed), *(["--cpu"] if a.cpu else []), *rest]))
