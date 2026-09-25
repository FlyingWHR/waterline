import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "prover")]

from fake_api import FakeApi  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATERLINE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WATERLINE_POLL_S", "0.01")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def make_api():
    apis = []

    def make(**kw):
        apis.append(FakeApi(**kw))
        return apis[-1]
    yield make
    for a in apis:
        a.close()
