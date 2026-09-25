"""Waterline API (FastAPI). Vercel loads `app` via [tool.vercel] entrypoint; locally: uvicorn api.app:app."""
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

import jwt
from eth_utils import keccak
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.challenge import Params

from . import chain, world
from .check import classify, draw_samples, gpu_label, grade
from .store import store

log = logging.getLogger("waterline.api")
SESSION_TTL = 3600
REPORT_TTL = 7 * 24 * 3600
VOTE_TTL = 365 * 24 * 3600
FRESH_S = 120
PASS, FAIL = 1, 2
INDEX, INDEX_MAX = "reports:index", 500

app = FastAPI(title="Waterline API")


def now():  # tests patch this to simulate a slow prover
    return time.time()


def deadline_for(cls: int) -> float:
    table = json.loads(os.environ.get("DEADLINES", "{}"))  # e.g. {"1": 5.0, "2": 6.0, "3": 9.0}
    return float(table.get(str(cls), 5.0))


@app.exception_handler(HTTPException)
async def _http_error(_: Request, e: HTTPException):
    return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)


@app.exception_handler(RequestValidationError)
async def _bad_request(_: Request, e: RequestValidationError):
    err = e.errors()[0]
    where = ".".join(str(x) for x in err["loc"][1:]) or "body"
    return JSONResponse({"error": f"Invalid request: {where}: {err['msg']}."}, status_code=400)


def _get(key: str, what: str):
    v = store.get(key)
    if v is None:
        raise HTTPException(404, f"Unknown or expired {what}.")
    return v


# ---- check ------------------------------------------------------------------------------------------------
class StartIn(BaseModel):
    cloud: str = Field(pattern=r"^[a-z0-9-]{1,63}$")
    uuid: str = Field(min_length=1, max_length=128)
    claimed_class: int = Field(ge=1, le=3)
    n: int = Field(ge=8, le=32768)
    steps: int = Field(ge=1, le=1000)


class Probes(BaseModel):
    sms: int = Field(ge=0, le=65535)
    fp8: bool
    clock_ghz: float
    bw_tbs: float
    fingerprint: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    staircase: dict[int, float] | None = Field(None, max_length=1024)  # blocks -> ms, for the chart only


class CommitIn(BaseModel):
    session_id: str
    root: str = Field(pattern=r"^(0x)?[0-9a-fA-F]{64}$")
    probes: Probes


class RevealIn(BaseModel):
    session_id: str
    fingerprints: dict[str, list[str]]
    leaf_hashes: dict[str, str]
    rows: dict[str, list[int]]


@app.post("/api/check/start")
def check_start(body: StartIn):
    sid = secrets.token_urlsafe(16)
    seed = secrets.randbits(63)
    s = body.model_dump() | {"seed": seed, "t0": now(), "deadline_s": deadline_for(body.claimed_class)}
    store.put(f"sess:{sid}", s, SESSION_TTL)
    return {"session_id": sid, "seed": str(seed), "n": body.n, "steps": body.steps,
            "fp_key": str(Params(seed, body.n, body.steps).fp_key), "deadline_s": s["deadline_s"]}


@app.post("/api/check/commit")
def check_commit(body: CommitIn):
    t = now()
    s = _get(f"sess:{body.session_id}", "session")
    if not store.add(f"commit:{body.session_id}", 1, SESSION_TTL):
        raise HTTPException(409, "This session already has a commitment.")
    stair = body.probes.staircase or {}
    s |= {"root": body.root.lower().removeprefix("0x"), "probes": body.probes.model_dump(exclude={"staircase"}),
          "staircase": {str(k): stair[k] for k in sorted(stair)} or None,
          "elapsed_s": round(t - s["t0"], 3), "samples": draw_samples(s["n"], s["steps"])}
    store.put(f"sess:{body.session_id}", s, SESSION_TTL)
    return {"elapsed_s": s["elapsed_s"], "samples": [[st, r] for st, r, _ in s["samples"]]}


@app.post("/api/check/reveal")
def check_reveal(body: RevealIn):
    s = _get(f"sess:{body.session_id}", "session")
    if "root" not in s:
        raise HTTPException(409, "Commit before revealing.")
    if not store.add(f"reveal:{body.session_id}", 1, SESSION_TTL):
        raise HTTPException(409, "This session was already revealed.")

    p = Params(s["seed"], s["n"], s["steps"])
    reasons = []
    if s["elapsed_s"] > s["deadline_s"]:
        reasons.append(f"Missed the deadline: {s['elapsed_s']} s > {s['deadline_s']} s.")
    reasons += grade(p, s["root"], s["samples"], body.fingerprints, body.leaf_hashes, body.rows)
    measured = classify(s["probes"])
    if measured != s["claimed_class"]:
        reasons.append(f"Measured class {measured} does not match claimed class {s['claimed_class']}.")

    rid = secrets.token_hex(16)
    name = f"{gpu_label(s['uuid'])}.{s['cloud']}.waterline.eth"
    node = chain.namehash(name)
    rep = {"report_id": rid, "verdict": "fail" if reasons else "pass", "measured_class": measured,
           "reasons": reasons, "gpu_name": name, "node": "0x" + node.hex(), "published": False, "tx": None,
           "cores": s["probes"]["sms"], "fingerprint": s["probes"]["fingerprint"].lower(),
           "created_at": int(now()), "cloud": s["cloud"], "claimed_class": s["claimed_class"],
           "status_text": "Waiting for a human approval. Nothing is published yet.",
           "probes": s["probes"], "staircase": s.get("staircase"), "elapsed_s": s["elapsed_s"],
           "deadline_s": s["deadline_s"], "samples": [[st, r] for st, r, _ in s["samples"]], "n": s["n"],
           "steps": s["steps"]}
    if not reasons:
        try:
            rep["tx"] = chain.record(node, PASS, measured, rep["cores"], bytes.fromhex(rep["fingerprint"][2:]),
                                     keccak(text=rid))
            rep |= {"published": True, "status_text": "Published."}
        except chain.ChainError as e:
            rep["status_text"] = "Publishing failed."
            log.error("publishing pass %s failed: %s", rid, e)
    store.put(f"report:{rid}", rep, REPORT_TTL)
    # ponytail: read-modify-write index; two reveals in the same instant can drop an id. Redis LPUSH if it bites.
    store.put(INDEX, [rid, *(store.get(INDEX) or [])][:INDEX_MAX], REPORT_TTL)
    return {k: rep[k] for k in ("report_id", "verdict", "measured_class", "reasons", "gpu_name", "node",
                                "published", "tx")}


# ---- World: login and approval (device grant) --------------------------------------------------------------
class DeviceIn(BaseModel):
    device_id: str


class ApproveIn(BaseModel):
    report_id: str
    agent_token: str


def _new_device(extra: dict) -> dict:
    try:
        d = world.device_start()
    except world.Unavailable:
        raise HTTPException(503, "World is unavailable; try again shortly.")
    did = secrets.token_urlsafe(16)
    store.put(f"dev:{did}", extra | {"device_code": d["device_code"], "interval": d["interval"], "next_poll": 0},
              d["expires_in"])
    return {"device_id": did, "user_code": d["user_code"],
            "verification_uri_complete": d["verification_uri_complete"], "expires_in": d["expires_in"]}


def _poll(device_id: str):
    """-> (device record, status, claims). Terminal results are cached on the record (tokens never are)."""
    key = f"dev:{device_id}"
    d = _get(key, "device_id")
    if "result" in d or now() < d["next_poll"]:
        return d, "pending", None
    try:
        status, claims = world.device_poll(d["device_code"])
    except world.Unavailable:
        raise HTTPException(503, "World is unavailable; this is not an approval. Poll again.")
    except jwt.PyJWTError:
        status, claims = "denied", None  # World's answer did not verify
    if status == "slow_down":
        d["interval"] += 5
        status = "pending"
    d["next_poll"] = now() + d["interval"]
    store.put(key, d, 1200)
    return d, status, claims


def _finish(device_id: str, d: dict, result: dict) -> dict:
    d["result"] = {k: v for k, v in result.items() if k != "agent_token"}
    store.put(f"dev:{device_id}", d, 1200)
    return result


@app.post("/api/world/login/start")
def login_start():
    return _new_device({"kind": "login"})


@app.post("/api/world/login/poll")
def login_poll(body: DeviceIn):
    d, status, claims = _poll(body.device_id)
    if d.get("kind") != "login":
        raise HTTPException(404, "Unknown or expired device_id.")
    if "result" in d:
        return d["result"]
    if status == "pending":
        return {"status": "pending"}
    if status != "approved":
        return _finish(body.device_id, d, {"status": status})
    return _finish(body.device_id, d, {"status": "approved", "agent_token": world.make_agent_token(claims["sub"])})


def _fail_report(report_id: str) -> dict:
    rep = _get(f"report:{report_id}", "report")
    if rep["verdict"] != "fail":
        raise HTTPException(409, "Only failed reports need approval.")
    if rep["published"]:
        raise HTTPException(409, "This report is already published.")
    return rep


@app.post("/api/report/approve/start")
def approve_start(body: ApproveIn):
    sub = world.agent_sub(body.agent_token)
    if sub is None:
        raise HTTPException(401, "The agent token is invalid or expired; log in again.")
    rep = _fail_report(body.report_id)
    if store.get(f"vote:{world.voter_id(sub, bytes.fromhex(rep['node'][2:])).hex()}"):
        raise HTTPException(409, "This human has already reported this GPU.")
    return _new_device({"kind": "approve", "report_id": body.report_id, "sub": sub})


@app.post("/api/report/approve/poll")
def approve_poll(body: DeviceIn):
    d, status, claims = _poll(body.device_id)
    if d.get("kind") != "approve":
        raise HTTPException(404, "Unknown or expired device_id.")
    if "result" in d:
        return d["result"]
    if status == "pending":
        return {"status": "pending"}
    if status != "approved":
        return _finish(body.device_id, d, {"status": status, "published": False})

    def refuse(text):
        return _finish(body.device_id, d, {"status": "approved", "published": False, "status_text": text})

    if now() - claims["auth_time"] >= FRESH_S:
        return refuse("The approval was not fresh; approve again.")
    if not hmac.compare_digest(claims["sub"].encode(), d["sub"].encode()):
        return refuse("The approving human is not the logged-in human.")
    rep = _fail_report(d["report_id"])
    node = bytes.fromhex(rep["node"][2:])
    vid = world.voter_id(claims["sub"], node)
    vote_key, pub_key = f"vote:{vid.hex()}", f"published:{rep['report_id']}"
    if not store.add(vote_key, 1, VOTE_TTL):
        return refuse("This human has already reported this GPU.")
    if not store.add(pub_key, 1, REPORT_TTL):
        store.delete(vote_key)
        return refuse("This report is already published.")
    try:
        tx = chain.record(node, FAIL, rep["measured_class"], rep["cores"], bytes.fromhex(rep["fingerprint"][2:]), vid)
    except chain.ChainError as e:
        store.delete(vote_key)
        store.delete(pub_key)
        log.error("publishing fail %s failed: %s", rep["report_id"], e)
        return {"status": "approved", "published": False, "status_text": "Publishing failed; approve again."}
    rep |= {"published": True, "tx": tx, "status_text": "Recorded on Marks."}
    store.put(f"report:{rep['report_id']}", rep, REPORT_TTL)
    return _finish(body.device_id, d, {"status": "approved", "published": True, "tx": tx,
                                       "status_text": "Recorded on Marks."})


# ---- control panel reads ------------------------------------------------------------------------------------
SUMMARY = ("report_id", "created_at", "gpu_name", "node", "cloud", "claimed_class", "measured_class", "verdict",
           "published", "tx", "status_text")
# Reported(node 0, voterId 1, verdict 2, cls 3, cores 4, fingerprint 5, at 6, passes 7, fails 8, humans 9).
# Tallies only grow, so max = latest; per-report fields use last.
MB_QUERY = {"events": [{
    "eventName": "Reported(bytes32,bytes32,uint8,uint8,uint16,bytes32,uint64,uint32,uint32,uint32)",
    "select": [{"type": "input", "inputIndex": i, "alias": a, **({"aggregator": g} if g else {})}
               for a, i, g in [("node", 0, None), ("cls", 3, "last"), ("cores", 4, "last"), ("at", 6, "max"),
                               ("passes", 7, "max"), ("fails", 8, "max"), ("humans", 9, "max")]]}],
    "groupBy": "node", "orderBy": "at", "order": "DESC"}


def gpu_status(humans: int, passes: int) -> str:
    """Same rule as Marks.text(waterline.status)."""
    if humans >= 2:
        return "failed"
    if humans == 1:
        return "suspect · 1 of 2 humans"
    return "pass" if passes > 0 else "unknown"


def _reports(limit: int) -> list[dict]:
    reps = (store.get(f"report:{rid}") for rid in (store.get(INDEX) or [])[:limit])
    return [r for r in reps if r]


def _universal_resolver():
    if os.environ.get("ENS_UNIVERSAL_RESOLVER"):
        return os.environ["ENS_UNIVERSAL_RESOLVER"]
    cfg = Path(__file__).resolve().parent.parent / "contracts" / "ens.sepolia.json"  # local runs; set the env on Vercel
    return json.loads(cfg.read_text()).get("UniversalResolver") if cfg.is_file() else None


@app.get("/api/health")
def health():
    live, reporter, mb = chain.live(), chain.reporter_address(), os.environ.get("MB_URL")
    return {"api": "ok", "store": "redis" if os.environ.get("REDIS_URL") else "memory",
            "chain": {"mode": "live" if live else "dry-run", "chain_id": 11155111,
                      "marks": os.environ.get("MARKS_ADDRESS") or None, "reporter": reporter,
                      "reporter_balance_eth": chain.balance_eth(reporter) if live else None},
            "world": {"mode": "mock" if world.mock_on() else "live", "issuer": world.issuer()},
            "multibaas": {"configured": bool(mb and os.environ.get("MB_API_KEY")), "url": mb or None},
            "ens": {"parent": "waterline.eth", "universal_resolver": _universal_resolver(),
                    # public RPC for the browser's ENS reads; SEPOLIA_RPC may carry a key, so it is never shown
                    "rpc": os.environ.get("PUBLIC_SEPOLIA_RPC", "https://ethereum-sepolia-rpc.publicnode.com")}}


@app.get("/api/reports")
def reports(limit: int = Query(50, ge=1, le=INDEX_MAX)):
    return [{k: r.get(k) for k in SUMMARY} for r in _reports(limit)]


@app.get("/api/reports/{report_id}")
def report(report_id: str):
    return _get(f"report:{report_id}", "report")


def _mb_gpus() -> list[dict]:
    r = httpx.post(os.environ["MB_URL"].rstrip("/") + "/api/v0/queries", json=MB_QUERY, timeout=15,
                   headers={"Authorization": f"Bearer {os.environ['MB_API_KEY']}"})
    r.raise_for_status()
    out = []
    for row in r.json()["result"]["rows"]:
        node = str(row["node"]).lower()
        g = {k: int(row.get(k) or 0) for k in ("cls", "cores", "passes", "fails", "humans")}
        out.append(g | {"node": node if node.startswith("0x") else "0x" + node, "last_at": int(row.get("at") or 0)})
    return out


def _local_gpus(reps: list[dict]) -> list[dict]:
    """Mirror of Marks' tallies from our own published reports (each published fail is a distinct human)."""
    g = {}
    for r in sorted(reps, key=lambda r: r.get("created_at", 0)):
        if not r["published"]:
            continue
        x = g.setdefault(r["node"], {"node": r["node"], "passes": 0, "fails": 0, "humans": 0})
        x["passes" if r["verdict"] == "pass" else "fails"] += 1
        x["humans"] += r["verdict"] == "fail"
        x |= {"cls": r["measured_class"], "cores": r.get("cores"), "last_at": r.get("created_at")}
    return sorted(g.values(), key=lambda x: x["last_at"] or 0, reverse=True)


@app.get("/api/gpus")
def gpus():
    reps = _reports(INDEX_MAX)
    source, error, rows = "local", None, None
    if os.environ.get("MB_URL") and os.environ.get("MB_API_KEY"):
        try:
            rows, source = _mb_gpus(), "multibaas"
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            log.error("MultiBaas query failed: %s", e)
            error = "MultiBaas did not answer; showing this API's own records."
    rows = _local_gpus(reps) if rows is None else rows
    names = {r["node"]: r["gpu_name"] for r in reps}
    for x in rows:
        x |= {"gpu_name": names.get(x["node"]), "status": gpu_status(x["humans"], x["passes"])}
    return {"source": source, "error": error, "gpus": rows}


# Static page (web/) served from the same deployment; Vercel promotes StaticFiles mounts to its CDN.
# Mounted last: routes are matched in order, so every /api/* route above wins over the mount.
_web = Path(__file__).resolve().parent.parent / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
