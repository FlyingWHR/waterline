"""The one-line check: /run serves the bootstrap with this API's address; /api/bundle carries everything it imports."""
import io
import zipfile

from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)


def test_run_serves_the_bootstrap_pointing_here():
    r = client.get("/run")
    assert r.status_code == 200 and 'API = "http://testserver"' in r.text
    assert "__API__" not in r.text.split('"""', 2)[2]  # substituted outside the docstring


def test_bundle_has_the_profiler_and_its_data():
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/bundle").content))
    names = set(z.namelist())
    assert {"core/__init__.py", "core/challenge.py", "core/rng.py", "core/specs.py", "core/gpu_specs.json",
            "prover/__init__.py", "prover/run.py", "prover/gpu.py"} <= names
    assert not any(n.endswith("vectors.json") or "__pycache__" in n for n in names)
