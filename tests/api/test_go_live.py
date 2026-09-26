"""scripts/multibaas_link.py against a fake MultiBaas (paths and bodies per data.multibaas.com/api/v0/openapi.yaml)."""
import json

import httpx
import pytest

from scripts import multibaas_link as ml

MARKS = "0x" + "4d" * 20


@pytest.fixture
def fake(monkeypatch, tmp_path):
    for k, v in {"MB_URL": "https://mb.test", "MB_API_KEY": "admin-key", "MARKS_ADDRESS": MARKS,
                 "API_URL": "https://waterline.test/"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MB_MARKS_ALIAS", raising=False)
    monkeypatch.delenv("MB_MARKS_LABEL", raising=False)
    monkeypatch.setattr(ml, "load_env", lambda: None)
    run = tmp_path / "run-latest.json"  # forge broadcast: the Marks CREATE and its receipt
    run.write_text(json.dumps({"transactions": [{"hash": "0xaa", "transactionType": "CREATE", "contractName": "Marks",
                                                 "contractAddress": MARKS.upper().replace("0X", "0x")}],
                               "receipts": [{"transactionHash": "0xaa", "blockNumber": "0x8a0b1c"}]}))
    monkeypatch.setattr(ml, "BROADCAST", tmp_path)
    state = {"seen": [], "hooks": [], "alias": None, "versions": set()}

    def request(method, url, json=None, headers=None, timeout=None):
        path = url.removeprefix("https://mb.test/api/v0")
        state["seen"].append((method, path, json))
        assert headers["Authorization"] == "Bearer admin-key"
        if method == "GET" and path.startswith("/webhooks"):
            return httpx.Response(200, json={"status": 200, "message": "success", "result": state["hooks"]})
        if path == "/webhooks":
            state["hooks"].append(json | {"id": 7, "secret": "whsec-123"})
            return httpx.Response(200, json={"status": 200, "message": "success", "result": state["hooks"][-1]})
        if path == "/contracts/marks":
            if json["version"] in state["versions"]:
                return httpx.Response(409, json={"status": 409, "message": "contract version already exists"})
            state["versions"].add(json["version"])
        if method == "GET" and path == "/chains/ethereum/addresses/marks":
            return httpx.Response(200, json={"status": 200, "message": "success", "result": state["alias"]})
        if path == "/chains/ethereum/addresses":  # like the real one on a re-run: the alias exists
            if state["alias"]:
                return httpx.Response(400, json={"status": 400, "message": "duplicate key value"})
            state["alias"] = {"alias": "marks", "address": json["address"], "chain": "ethereum", "contracts": []}
        if path == "/chains/ethereum/addresses/marks/contracts":
            if state["alias"]["contracts"]:
                return httpx.Response(400, json={"status": 400, "message": "duplicate key value"})
            state["alias"]["contracts"].append({"label": json["label"], "name": "Marks", "version": json["version"]})
        return httpx.Response(200, json={"status": 200, "message": "success", "result": {}})
    monkeypatch.setattr(ml.httpx, "request", request)
    return state


def test_links_marks_and_prints_the_secret_once(fake, capsys):
    assert ml.main([]) == 0
    (m1, contract, c), (m2, addr, a), (m3, link, lk), (_, list_hooks, _), (m4, hook, w) = fake["seen"]
    assert (m1, contract) == ("POST", "/contracts/marks") and c["contractName"] == "Marks" and c["label"] == "marks"
    assert any(e.get("name") == "record" for e in json.loads(c["rawAbi"])) and c["version"].startswith("1-")
    assert c["bin"].startswith("0x6") and len(c["bin"]) > 1000  # MultiBaas rejects a contract without bytecode
    assert (addr, a) == ("/chains/ethereum/addresses", {"alias": "marks", "address": MARKS})
    assert link == "/chains/ethereum/addresses/marks/contracts"
    assert lk == {"label": "marks", "version": c["version"], "startingBlock": str(0x8a0b1c)}
    assert list_hooks.startswith("/webhooks?") and (m4, hook) == ("POST", "/webhooks")
    assert w == {"url": "https://waterline.test/api/webhooks/multibaas", "label": "waterline", "subscriptions": ["event.emitted"]}
    out = capsys.readouterr().out
    assert out.count("whsec-123") == 1 and "MB_WEBHOOK_SECRET" in out
    fake["seen"].clear()
    assert ml.main([]) == 0  # re-run: contract version exists, webhook exists -> no new webhook, no secret
    assert [p for m, p, _ in fake["seen"] if m == "POST"] == ["/contracts/marks", "/chains/ethereum/addresses",
                                                              "/chains/ethereum/addresses/marks/contracts"]
    out = capsys.readouterr().out
    assert "whsec-123" not in out and "✗" not in out and out.count("already there") == 2 and "already uploaded" in out


def test_existing_alias_to_another_address_is_an_error(fake, capsys):
    fake["alias"] = {"alias": "marks", "address": "0x" + "99" * 20, "contracts": [{"label": "marks"}]}
    assert ml.main(["--skip-webhook"]) == 1
    assert "✗ POST /chains/ethereum/addresses" in capsys.readouterr().out


def test_skip_webhook_links_only(fake, capsys, monkeypatch):
    assert ml.main(["--skip-webhook"]) == 0
    assert not any(p.startswith("/webhooks") for _, p, _ in fake["seen"])
    monkeypatch.delenv("API_URL")  # unset API_URL means the same
    fake["seen"].clear()
    assert ml.main([]) == 0 and not any(p.startswith("/webhooks") for _, p, _ in fake["seen"])
    assert "Webhook skipped" in capsys.readouterr().out


def test_dry_run_sends_nothing(fake, capsys, monkeypatch):
    monkeypatch.setattr(ml, "BROADCAST", ml.ROOT / "nowhere")
    assert ml.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert not fake["seen"] and out.count("POST https://mb.test/api/v0/") == 4 and '"startingBlock": "-100"' in out


def test_missing_env_is_a_plain_message(fake, monkeypatch, capsys):
    monkeypatch.delenv("MARKS_ADDRESS")
    assert ml.main([]) == 1 and "Set MARKS_ADDRESS" in capsys.readouterr().out


def test_env_file_parsing(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# c\nA_X=1   # note\nB_X='{\"1\": 7.5}'\nC_X=\nexport D_X=no\nE_X=\"a b\"\n")
    for k in ("A_X", "B_X", "C_X", "D_X", "E_X"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("E_X", "shell wins")
    ml.load_env(f)
    import os
    assert (os.environ["A_X"], os.environ["B_X"], os.environ["C_X"], os.environ["E_X"]) == ("1", '{"1": 7.5}', "", "shell wins")
    assert os.environ["D_X"] == "no"
