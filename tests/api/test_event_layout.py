"""The Reported event layout is hard-coded in the API and the agent; both must match the contract."""
import re
from pathlib import Path

from agent.history import EVENT
from api.app import REPORTED

ROOT = Path(__file__).resolve().parents[2]


def contract_signature():
    src = (ROOT / "contracts/src/Marks.sol").read_text()
    body = re.search(r"event Reported\((.*?)\);", src, re.S).group(1)
    types = [p.split()[0] for p in body.replace("\n", " ").split(",") if p.strip()]
    return "Reported(" + ",".join(types) + ")"


def test_api_and_agent_match_the_contract():
    assert REPORTED == contract_signature()
    assert EVENT == contract_signature()
