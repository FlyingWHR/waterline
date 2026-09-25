import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

from fake_api import FakeApi  # noqa: E402


@pytest.fixture
def api():
    a = FakeApi()
    yield a
    a.close()
