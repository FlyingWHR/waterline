"""Waterline API (FastAPI). Vercel loads `app` via [tool.vercel] entrypoint; locally: uvicorn api.app:app."""
import hmac
import json
import logging
import os
import secrets
import statistics
import time
from pathlib import Path
from typing import Any

import jwt
from eth_utils import keccak
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.challenge import Params
from core.specs import MODELS

from . import chain, world
from . import perf
from .check import (claimed_models, class_check, classify, deadline_s, draw_samples, gpu_label, grade,
                    throughput)
from .store import store

log = logging.getLogger("waterline.api")
SESSION_TTL = 3600
REPORT_TTL = 7 * 24 * 3600
VOTE_TTL = 365 * 24 * 3600
FRESH_S = 120
PASS, FAIL = 1, 2
INDEX, INDEX_MAX = "reports:index", 500
HEALTH_MAX = 256 * 1024  # bytes of JSON; the health report is advisory, so it is kept small

app = FastAPI(title="Waterline API")


def now():  # tests patch this to simulate a slow prover
    return time.time()


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
    health: dict[str, Any] | None = None  # advisory, reported by the machine; stored, never graded
    metrics: dict[str, dict[str, Any]] | None = Field(None, max_length=64)  # measured in the pod; never graded


@app.post("/api/check/start")
def check_start(body: StartIn):
    sid = secrets.token_urlsafe(16)
    seed = secrets.randbits(63)
    s = body.model_dump() | {"seed": seed, "t0": now(), "deadline_s": deadline_s(body.claimed_class, body.n, body.steps)}
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
    if len(json.dumps([body.health, body.metrics])) > HEALTH_MAX:
        raise HTTPException(413, f"The health report and metrics are larger than {HEALTH_MAX // 1024} KB.")
    if not store.add(f"reveal:{body.session_id}", 1, SESSION_TTL):
        raise HTTPException(409, "This session was already revealed.")

    p = Params(s["seed"], s["n"], s["steps"])
    reasons = []
    if s["elapsed_s"] > s["deadline_s"]:
        reasons.append(f"Answer locked in after {s['elapsed_s']:.1f} s; the deadline was {s['deadline_s']:.1f} s.")
    reasons += grade(p, s["root"], s["samples"], body.fingerprints, body.leaf_hashes, body.rows)
    work_ok = not reasons  # in time and re-graded: only then is the timing a verified number
    classification, class_reasons = class_check(s["claimed_class"], s["probes"], body.metrics)
    reasons += class_reasons
    measured = classify(s["probes"], body.metrics)  # on-chain class code of the best match

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
           "steps": s["steps"], **throughput(s["n"], s["steps"], s["elapsed_s"], s["claimed_class"]),
           "health": body.health and body.health | {"grade": "reported by the machine"}, "work_ok": work_ok,
           "via": None, "indexed": False, "indexed_at": None}
    rep |= perf_profile(rep, body.metrics, classification)
    if not reasons:
        try:
            rep["tx"] = chain.record(node, PASS, measured, rep["cores"], bytes.fromhex(rep["fingerprint"][2:]),
                                     keccak(text=rid), *_perf_onchain(rep))
            rep |= {"published": True, "status_text": "Published.", "via": chain.write_path()}
        except chain.ChainError as e:
            rep["status_text"] = "Publishing failed."
            log.error("publishing pass %s failed: %s", rid, e)
    store.put(f"report:{rid}", rep, REPORT_TTL)
    # ponytail: read-modify-write index; two reveals in the same instant can drop an id. Redis LPUSH if it bites.
    store.put(INDEX, [rid, *(store.get(INDEX) or [])][:INDEX_MAX], REPORT_TTL)
    return {k: rep[k] for k in ("report_id", "verdict", "measured_class", "reasons", "gpu_name", "node",
                                "published", "tx", "via")}


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
        tx = chain.record(node, FAIL, rep["measured_class"], rep["cores"], bytes.fromhex(rep["fingerprint"][2:]), vid,
                          *_perf_onchain(rep))
    except chain.ChainError as e:
        store.delete(vote_key)
        store.delete(pub_key)
        log.error("publishing fail %s failed: %s", rep["report_id"], e)
        return {"status": "approved", "published": False, "status_text": "Publishing failed; approve again."}
    via = chain.write_path()
    rep |= {"published": True, "tx": tx, "status_text": "Recorded on Marks.", "via": via}
    store.put(f"report:{rep['report_id']}", rep, REPORT_TTL)
    return _finish(body.device_id, d, {"status": "approved", "published": True, "tx": tx, "via": via,
                                       "status_text": "Recorded on Marks."})


# ---- control panel reads ------------------------------------------------------------------------------------
SUMMARY = ("report_id", "created_at", "gpu_name", "node", "cloud", "claimed_class", "measured_class", "verdict",
           "published", "tx", "status_text", "via", "indexed", "indexed_at")
# Reported(node 0, voterId 1, verdict 2, cls 3, cores 4, fingerprint 5, topsX10 6, pctBps 7, at 8, passes 9,
#          fails 10, humans 11): must match contracts/src/Marks.sol (checked in tests/api/test_event_layout.py).
# Tallies only grow, so max = latest; per-report fields use last.
REPORTED = "Reported(bytes32,bytes32,uint8,uint8,uint16,bytes32,uint32,uint16,uint64,uint32,uint32,uint32)"
MB_QUERY = {"events": [{
    "eventName": REPORTED,
    "select": [{"type": "input", "inputIndex": i, "alias": a, **({"aggregator": g} if g else {})}
               for a, i, g in [("node", 0, None), ("cls", 3, "last"), ("cores", 4, "last"), ("tops_x10", 6, "last"),
                               ("pct_bps", 7, "last"), ("at", 8, "max"), ("passes", 9, "max"), ("fails", 10, "max"),
                               ("humans", 11, "max")]]}],
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
            "chain": {"mode": "live" if live else "dry-run", "write_path": chain.write_path(), "chain_id": 11155111,
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


# ---- MultiBaas webhook: Reported events mark our reports as indexed ------------------------------------------------
WEBHOOK_MAX_AGE_S = 300


@app.post("/api/webhooks/multibaas")
async def multibaas_webhook(request: Request):
    """Signature per docs.curvegrid.com/multibaas/webhooks: hex HMAC-SHA256(secret, raw body + timestamp string)."""
    secret, raw = os.environ.get("MB_WEBHOOK_SECRET"), await request.body()
    ts, sig = request.headers.get("X-MultiBaas-Timestamp", ""), request.headers.get("X-MultiBaas-Signature", "")
    good = secret and hmac.new(secret.encode(), raw + ts.encode(), "sha256").hexdigest()
    if not (good and ts.isdigit() and abs(now() - int(ts)) <= WEBHOOK_MAX_AGE_S
            and hmac.compare_digest(good.encode(), sig.strip().lower().encode())):
        raise HTTPException(401, "Bad, missing or stale webhook signature.")
    try:
        events = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "The webhook body is not JSON.")
    marks = {os.environ.get("MB_MARKS_ALIAS") or "marks", (os.environ.get("MARKS_ADDRESS") or "").lower()} - {""}
    hits = set()
    for ev in events if isinstance(events, list) else [events]:
        try:
            if (ev.get("event") or ev.get("eventType")) != "event.emitted":
                continue
            data = ev["data"]
            e = data["event"]
            c = e.get("contract") or data.get("contract") or {}
            if e.get("name") != "Reported" or not {str(c.get("addressLabel")), str(c.get("address")).lower()} & marks:
                continue
            node = str(e["inputs"][0]["value"]).lower()  # Reported's first input is the node
            hits.add(("0x" + node.removeprefix("0x"), str(data["transaction"]["txHash"]).lower()))
        except (AttributeError, KeyError, IndexError, TypeError):
            continue  # not an event we understand: ignore it
    marked = 0
    # ponytail: scans the last INDEX_MAX reports; keep a tx -> report_id key if the record grows past that
    for r in _reports(INDEX_MAX) if hits else []:
        if (r["node"], (r.get("tx") or "").lower()) in hits and not r.get("indexed"):
            store.put(f"report:{r['report_id']}", r | {"indexed": True, "indexed_at": int(now())}, REPORT_TTL)
            marked += 1
    return {"ok": True, "indexed": marked}


# ---- performance profile and comparisons (docs/INTERFACES.md "Performance profile", docs/METRICS.md) ----------
SPECS = json.loads((Path(__file__).resolve().parent.parent / "core" / "gpu_specs.json").read_text())
COHORT_MIN, FEW_MIN = perf.COHORT_MIN, 3


def _sig(x):
    return None if x is None else float(f"{x:.4g}")


def _work_ok(rep: dict) -> bool:
    return rep.get("work_ok", rep.get("verdict") == "pass")  # reports from before work_ok existed


def perf_profile(rep: dict, sent: dict | None, classification: dict | None = None) -> dict:
    """-> {metrics, classification, claimed_model}. Metrics are annotated against the LISTED model (what the renter
    paid for); perf.annotate re-stamps trust and drops any verified number the pod sent. Only work the API
    re-graded in time gets int8_tops_verified."""
    ids = claimed_models(rep.get("claimed_class"))
    listed = ids[0] if ids else None
    metrics = perf.annotate(sent, listed)
    if _work_ok(rep):
        metrics["int8_tops_verified"] = perf.verified_int8(rep["n"], rep["steps"], rep["elapsed_s"], listed)
    c = classification or class_check(rep.get("claimed_class"), rep.get("probes"), sent)[0]
    return {"metrics": metrics, "classification": c | {"claimed": listed, "claimed_models": ids},
            "claimed_model": listed}


def _perf_onchain(rep: dict) -> tuple[int, int]:
    """(topsX10, pctBps) for Marks.record; zeros when there is no verified number."""
    v = (rep.get("metrics") or {}).get("int8_tops_verified") or {}
    return chain.encode_perf(v.get("value"), v.get("pct_of_spec"))


def _profile(rep: dict) -> tuple[dict, dict]:
    """Stored profile, or one rebuilt for reports made before metrics existed."""
    if rep.get("metrics") is None or rep.get("classification") is None:
        rep = rep | perf_profile(rep, None)
    return rep["metrics"], rep["classification"]


def _med(xs):
    return _sig(statistics.median(xs)) if xs else None


def _model_row(m: dict) -> dict:
    d = m.get("dense") or {}
    return {"id": m["id"], "name": m["name"], "vendor": m["vendor"], "sms": m["sms"], "fp8": m["fp8"],
            "mem_gb": m["mem_gb"], "bw_tbs": m["bw_tbs"], "int8_tops": d.get("int8_tops"),
            "bf16_tflops": d.get("bf16_tflops"), "fp8_tflops": d.get("fp8_tflops"),
            "unverified": m.get("unverified", []), "sources": m.get("sources", []), "note": m.get("note")}


@app.get("/api/models")
def models():
    return {"generated": SPECS["generated"], "notes": SPECS["notes"], "models": [_model_row(m) for m in SPECS["models"]],
            "confusable_pairs": SPECS["confusable_pairs"]}


@app.get("/api/compare/{report_id}")
def compare(report_id: str):
    rep = _get(f"report:{report_id}", "report")
    metrics, cl = _profile(rep)
    best = cl.get("best_match")
    # ponytail: scans the last INDEX_MAX reports per call; keep a per-model index if the record grows past that
    past = [r | dict(zip(("metrics", "classification"), _profile(r))) for r in _reports(INDEX_MAX) if _work_ok(r)]
    me = rep | {"metrics": metrics, "classification": cl}
    cohort = (perf.cohort(me, past) if best else {"n": 0, "note": "no closest model"}) | {"model_id": best, "min_n": COHORT_MIN}
    if "percentiles" in cohort:  # the values behind each rank, for the distribution strip
        same = [r for r in past if r["classification"].get("best_match") == best and r["report_id"] != report_id]
        cohort["values"] = {k: sorted(v for r in same if isinstance(v := (r["metrics"].get(k) or {}).get("value"), (int, float)))
                            for k in cohort["percentiles"]}
    return {"report_id": report_id, "metrics": metrics, "classification": cl,
            "vs_spec": {k: x["pct_of_spec"] for k, x in metrics.items() if x.get("pct_of_spec") is not None},
            "vs_models": [_model_row(m) for m in SPECS["models"]], "cohort": cohort}


@app.get("/api/leaderboard")
def leaderboard(model: str | None = Query(None, max_length=64)):
    if model and model not in MODELS:
        raise HTTPException(404, "Unknown model; see /api/models for the ids.")
    groups: dict[tuple, list] = {}
    for r in _reports(INDEX_MAX):
        mid = (claimed_models(r.get("claimed_class")) or [None])[0]  # grouped by what the cloud listed
        if mid:
            groups.setdefault((mid, r.get("cloud")), []).append(r)
    counts = {}
    for (mid, _), rs in groups.items():
        counts[mid] = counts.get(mid, 0) + len(rs)
    rows = []
    for (mid, cloud), rs in groups.items():
        if model and mid != model:
            continue
        ok = [_profile(r)[0].get("int8_tops_verified") for r in rs]
        tops = [x["value"] for x in ok if x and x.get("value") is not None]
        pcts = [x["pct_of_spec"] for x in ok if x and x.get("pct_of_spec") is not None]
        rows.append({"model": mid, "cloud": cloud, "n": len(rs), "n_verified": len(tops),
                     "median_tops": _med(tops), "tops_range": [min(tops), max(tops)] if tops else None,
                     "median_pct_of_spec": _med(pcts), "pct_range": [min(pcts), max(pcts)] if pcts else None,
                     "pass_rate": round(sum(r["verdict"] == "pass" for r in rs) / len(rs), 3),
                     "few": len(rs) < FEW_MIN})
    ranked = sorted((x for x in rows if not x["few"]), key=lambda x: (x["model"], -(x["median_tops"] or 0)))
    for x in ranked:  # ranks count within one listed model
        x["rank"] = sum(y["model"] == x["model"] for y in ranked[:ranked.index(x)]) + 1
    few = sorted((x for x in rows if x["few"]), key=lambda x: (x["model"], -x["n"], x["cloud"]))
    return {"model": model, "min_n": FEW_MIN, "rows": ranked + [x | {"rank": None} for x in few],
            "models": [{"id": k, "name": MODELS[k]["name"], "n": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]}


# Static page (web/) served from the same deployment; Vercel promotes StaticFiles mounts to its CDN.
# Mounted last: routes are matched in order, so every /api/* route above wins over the mount.
_web = Path(__file__).resolve().parent.parent / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
