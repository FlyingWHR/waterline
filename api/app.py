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

from eth_utils import keccak
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.challenge import Params
from core.specs import MODELS

from core import listing as listings
from core import classes as gpu_classes
from core import providers as providers_known

from . import chain, data
from . import perf
from .check import (claimed_models, class_check, classify, deadline_s, draw_samples, gpu_label, grade,
                    throughput)
from .store import redis_url, store

log = logging.getLogger("waterline.api")
SESSION_TTL = 3600
REPORT_TTL = None  # kept for good: a report is the evidence behind an onchain hash
PASS, FAIL, DEGRADED = 1, 2, 3
INDEX, INDEX_MAX = "reports:list", 500  # every report id, newest first; panel views scan the latest INDEX_MAX
CHECK_N = 16384
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


# Fields that change after the verdict (publishing, indexing); everything else is the frozen evidence.
# approved_*, mandate_*: legacy fields on older stored reports; kept here so their hashes still match.
MUTABLE = {"published", "tx", "via", "indexed", "indexed_at", "status_text", "report_hash", "provider_voter", "listing",
           "listing_reads_as", "approved_at", "approved_via", "mandate_id", "mandate_expires_at", "check_no", "check_name"}


def canonical(rep: dict) -> bytes:
    """The report as evidence: minus MUTABLE fields, sorted keys, no whitespace, UTF-8. Round-tripped through JSON
    first so the bytes match any later read from the store."""
    frozen = {k: v for k, v in json.loads(json.dumps(rep)).items() if k not in MUTABLE}
    return json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def report_hash(rep: dict) -> str:
    return "0x" + keccak(canonical(rep)).hex()


# ---- check ------------------------------------------------------------------------------------------------
class StartIn(BaseModel):
    cloud: str = Field(pattern=r"^[a-z0-9-]{1,63}$")
    uuid: str = Field(min_length=1, max_length=128)
    claimed_class: int = Field(ge=1, le=gpu_classes.MAX)
    n: int = Field(CHECK_N, ge=8, le=32768)  # omitted -> the API's calibrated size (prover.run on a GPU omits both)
    steps: int = Field(default_factory=lambda: int(os.environ.get("CHECK_STEPS") or 100), ge=1, le=1000)
    # a periodic series (--every): the same renter re-checking one rental at jittered intervals
    series: str | None = Field(default=None, pattern=r"^[a-z0-9]{6,24}$")
    seq: int | None = Field(default=None, ge=1, le=100000)
    # what the renter pays, per GPU-hour, as they state it: joins price to delivered performance in the dataset
    price_usd_per_gpu_hour: float | None = Field(default=None, gt=0, le=1000)


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
    # The API decides how much work the exam is. If the prover could pick n/steps, a slow GPU would ask for a
    # tiny exam and finish inside the deadline's floor. Client sizes are for tests and the local CPU demo only.
    if os.environ.get("ALLOW_CLIENT_SIZES") != "1":
        body = body.model_copy(update={"n": CHECK_N, "steps": int(os.environ.get("CHECK_STEPS") or 100)})
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
    late = [f"Answer locked in after {s['elapsed_s']:.1f} s; the deadline was {s['deadline_s']:.1f} s."] \
        if s["elapsed_s"] > s["deadline_s"] else []
    work = grade(p, s["root"], s["samples"], body.fingerprints, body.leaf_hashes, body.rows)
    work_ok = not work  # re-graded correct: the API-clock timing is a verified number, in time or not
    classification, class_reasons = class_check(s["claimed_class"], s["probes"], body.metrics)
    measured = classify(s["probes"], body.metrics, s["claimed_class"])  # on-chain class code, the claim when consistent
    # Two layers. Class comes only from heat-proof probes (cores, FP8): wrong chip or wrong answers = fail, which
    # needs the listing. Heat, power caps and sharing only slow a chip: right chip, right answers, too slow = degraded,
    # published with its numbers like a pass, never counted toward failed.
    reasons = work + class_reasons + late
    verdict = "fail" if work or class_reasons else "degraded" if late else "pass"

    rid = secrets.token_hex(16)
    label, provider = gpu_label(s["uuid"]), f"{s['cloud']}.waterline.eth"
    name = f"{label}.{provider}"
    node = chain.namehash(name)
    prev = next((r for r in _reports(INDEX_MAX) if r["node"] == "0x" + node.hex()), None)
    fp = s["probes"]["fingerprint"].lower()
    rep = {"report_id": rid, "verdict": verdict, "measured_class": measured,
           # periodic checks carry their series and place in it; both sit inside the evidence hash
           **({"series": s["series"], "seq": s.get("seq")} if s.get("series") else {}),
           **({"price_usd_per_gpu_hour": s["price_usd_per_gpu_hour"]} if s.get("price_usd_per_gpu_hour") else {}),
           "reasons": reasons, "gpu_name": name, "node": "0x" + node.hex(), "published": False, "tx": None,
           "gpu_label": label, "uuid": s["uuid"], "provider": provider, "provider_node": "0x" + chain.namehash(provider).hex(),
           # an observation, never a verdict: the fingerprint is quantised timing, not yet proven stable across runs
           "fingerprint_changed": bool(prev) and prev.get("fingerprint") != fp,
           "cores": s["probes"]["sms"], "fingerprint": s["probes"]["fingerprint"].lower(),
           "created_at": int(now()), "cloud": s["cloud"], "claimed_class": s["claimed_class"],
           "status_text": "Waiting for the listing you rented. Nothing is published yet.",
           "probes": s["probes"], "staircase": s.get("staircase"), "elapsed_s": s["elapsed_s"],
           "deadline_s": s["deadline_s"], "samples": [[st, r] for st, r, _ in s["samples"]], "n": s["n"],
           "steps": s["steps"], **throughput(s["n"], s["steps"], s["elapsed_s"], s["claimed_class"]),
           "health": body.health and body.health | {"grade": "reported by the machine"}, "work_ok": work_ok,
           "via": None, "indexed": False, "indexed_at": None}
    rep |= perf_profile(rep, body.metrics, classification)
    rep["delivery"] = perf.delivery(body.health)  # advisory, from the machine's own report
    rep["report_hash"] = report_hash(rep)  # frozen with the verdict; GET /api/reports/{id}/evidence serves the bytes
    if verdict != "fail":
        try:
            tops_x10, pct_bps = _perf_onchain(rep)
            rep["tx"] = chain.record(s["cloud"], label, PASS if verdict == "pass" else DEGRADED, measured, rep["cores"],
                                     bytes.fromhex(fp[2:]), tops_x10=tops_x10, pct_bps=pct_bps,
                                     report_hash=bytes.fromhex(rep["report_hash"][2:]))
            rep |= _check_name(rep) | {"published": True, "via": chain.write_path(), "status_text": "Published." if verdict == "pass"
                    else "Published as degraded: the right chip, too slow. It never counts toward failed."}
        except chain.ChainError as e:
            rep["status_text"] = "Publishing failed."
            log.error("publishing pass %s failed: %s", rid, e)
    store.put(f"report:{rid}", rep, REPORT_TTL)
    store.push(INDEX, rid)
    return {k: rep[k] for k in ("report_id", "verdict", "measured_class", "reasons", "gpu_name", "node",
                                "published", "tx", "via", "report_hash", "delivery")}


# ---- failure reports: published with the listing the reporter rented -----------------------------------------------
class PublishIn(BaseModel):
    report_id: str
    listing: str | None = Field(default=None, max_length=2000)
    report_anyway: bool = False  # the listing reads as another class than the one reported, and the reporter insists


def _check_name(rep: dict) -> dict:
    """The name Marks gives this published check: <n>.<gpu name>, n counting this GPU's checks on the live contract."""
    key = f"checkno:{(os.environ.get('MARKS_ADDRESS') or 'dry').lower()}:{rep['node']}"
    n = (store.get(key) or 0) + 1  # ponytail: read-modify-write; two publishes of one GPU in the same instant could share n
    store.put(key, n, REPORT_TTL)
    return {"check_no": n, "check_name": f"{n}.{rep['gpu_name']}"}


def _hash_bytes(rep: dict) -> bytes:
    return bytes.fromhex((rep.get("report_hash") or report_hash(rep))[2:])  # reports from before report_hash existed


def _fail_report(report_id: str) -> dict:
    rep = _get(f"report:{report_id}", "report")
    if rep["verdict"] != "fail":
        raise HTTPException(409, "Only failed reports are published this way.")
    if rep["published"]:
        raise HTTPException(409, "This report is already published.")
    return rep


def _take_listing(rep: dict, listing: str | None, report_anyway: bool) -> dict:
    """A failure accuses the provider of misselling this GPU, so the reporter states what they rented in the
    listing's own words. It stays beside the report for anyone to compare with the provider's real listing."""
    listing = (listing or rep.get("listing") or "").strip()
    if len(listing) < 8:
        raise HTTPException(422, "Paste the listing you rented (its URL or text) before reporting this GPU.")
    # Reporting an A100 listing "as H100" is the cheap way to smear a provider: a confident contradiction stops
    # here unless the reporter insists, and the dashboard flags it either way.
    code, conf, src = listings.parse(listing)
    reads = {"class": code, "confidence": round(conf, 2), "source": src,
             "contradicts": bool(code) and code != rep["claimed_class"] and conf >= 0.8}
    if reads["contradicts"] and not report_anyway:
        raise HTTPException(409, f"Your listing reads as {listings.CLASSES[code]} ({src}), but you are reporting it as "
                                 f"{listings.CLASSES.get(rep['claimed_class'], 'another GPU')}. Check the listing, or report anyway.")
    rep |= {"listing": listing, "listing_reads_as": reads}
    store.put(f"report:{rep['report_id']}", rep, REPORT_TTL)
    return rep


@app.post("/api/report/publish")
def report_publish(body: PublishIn):
    return _publish_fail(_take_listing(_fail_report(body.report_id), body.listing, body.report_anyway))


def _publish_fail(rep: dict) -> dict:
    """A failure report onchain. Each report is its own voter, so two failure reports mark a GPU failed."""
    node = bytes.fromhex(rep["node"][2:])
    pnode = chain.namehash(f"{rep['cloud']}.waterline.eth")
    rid = rep["report_id"].encode()
    vid, pvid = keccak(b"gpu:" + rid + node), keccak(b"provider:" + rid + pnode)
    pub_key = f"published:{rep['report_id']}"
    if not store.add(pub_key, 1, REPORT_TTL):
        return {"published": False, "status_text": "This report is already published."}
    try:
        tops_x10, pct_bps = _perf_onchain(rep)
        tx = chain.record(rep["cloud"], rep["gpu_name"].split(".")[0], FAIL, rep["measured_class"], rep["cores"],
                          bytes.fromhex(rep["fingerprint"][2:]), vid, pvid, tops_x10, pct_bps, _hash_bytes(rep))
    except chain.ChainError as e:
        store.delete(pub_key)
        log.error("publishing fail %s failed: %s", rep["report_id"], e)
        return {"published": False, "retry": True, "status_text": "Publishing failed; try again."}
    via = chain.write_path()
    rep |= {"published": True, "tx": tx, "status_text": "Recorded on Marks.", "via": via, "provider_voter": pvid.hex()} \
        | _check_name(rep)
    store.put(f"report:{rep['report_id']}", rep, REPORT_TTL)
    return {"published": True, "tx": tx, "via": via, "status_text": "Recorded on Marks."}


# ---- control panel reads ------------------------------------------------------------------------------------
SUMMARY = ("report_id", "created_at", "gpu_name", "check_name", "uuid", "node", "cloud", "claimed_class", "measured_class", "verdict",
           "published", "tx", "status_text", "via", "indexed", "indexed_at", "report_hash", "series", "seq",
           "pct_of_spec")
# Reported(node 0, provider 1, gpuVoter 2, providerVoter 3, verdict 4, cls 5, cores 6, fingerprint 7, topsX10 8,
#          pctBps 9, at 10, passes 11, fails 12, active 13, reportHash 14) and
# ProviderTally(provider 0, gpus 1, failedGpus 2, humans 3, passes 4, fails 5, at 6):
# must match contracts/src/Marks.sol (tests/api/test_event_layout.py).
REPORTED = ("Reported(bytes32,bytes32,bytes32,bytes32,uint8,uint8,uint16,bytes32,uint32,uint16,uint64,uint32,uint32,"
            "uint32,bytes32)")
PROVIDER_TALLY = "ProviderTally(bytes32,uint32,uint32,uint32,uint32,uint32,uint64)"


def _only_marks(query: dict) -> dict:
    """Event queries match by event name across every contract MultiBaas knows (older Marks included): keep the live one."""
    addr = (os.environ.get("MARKS_ADDRESS") or "").lower()
    if not addr:
        return query
    return query | {"events": [e | {"filter": {"rule": "and", "children": [
        {"operator": "Equal", "value": addr, "fieldType": "contract_address"}]}} for e in query["events"]]}


def _mb_select(fields):
    return [{"type": "input", "inputIndex": i, "alias": a, **({"aggregator": g} if g else {})} for a, i, g in fields]


# passes/fails only grow (max = latest); active can drop on recovery, so it takes the latest event's value
MB_QUERY = {"events": [{"eventName": REPORTED, "select": _mb_select([
    ("node", 0, None), ("provider", 1, "last"), ("verdict", 4, "last"), ("cls", 5, "last"), ("cores", 6, "last"), ("tops_x10", 8, "last"),
    ("pct_bps", 9, "last"), ("at", 10, "max"), ("passes", 11, "max"), ("fails", 12, "max"), ("active", 13, "last")])}],
    "groupBy": "node", "orderBy": "at", "order": "DESC"}
MB_PROVIDER_QUERY = {"events": [{"eventName": PROVIDER_TALLY, "select": _mb_select([
    ("provider", 0, None), ("gpus", 1, "max"), ("failed_gpus", 2, "last"), ("humans", 3, "max"), ("passes", 4, "max"),
    ("fails", 5, "max"), ("at", 6, "max")])}], "groupBy": "provider", "orderBy": "at", "order": "DESC"}


def gpu_status(active: int, fails: int, passes: int, last_verdict=None) -> str:
    """Same rule as Marks.status: active failure reports since the last recovery decide; then a degraded latest check; a GPU
    that had failures and has no active ones has recovered (two passes after its last failure)."""
    if active >= 2:
        return "failed"
    if active == 1:
        return "suspect · 1 of 2 reports"
    if last_verdict in (DEGRADED, "degraded"):
        return "degraded"
    if fails > 0:
        return "recovered"
    return "pass" if passes > 0 else "unknown"


def provider_status(p: dict) -> str:
    """Same words as Marks.providerStatus: descriptive, a provider is judged GPU by GPU."""
    g, h = p["gpus"], p["humans"]
    return f"{p['failed_gpus']} of {g} GPU{'' if g == 1 else 's'} failed · {h} failure report{'' if h == 1 else 's'}"


def _reports(limit: int | None) -> list[dict]:
    """The latest `limit` reports (all of them with None), newest first."""
    return [r for r in store.many([f"report:{rid}" for rid in store.recent(INDEX, limit)]) if r]


def _universal_resolver():
    if os.environ.get("ENS_UNIVERSAL_RESOLVER"):
        return os.environ["ENS_UNIVERSAL_RESOLVER"]
    cfg = Path(__file__).resolve().parent.parent / "contracts" / "ens.sepolia.json"  # local runs; set the env on Vercel
    return json.loads(cfg.read_text()).get("UniversalResolver") if cfg.is_file() else None


@app.get("/api/health")
def health():
    live, reporter, mb = chain.live(), chain.reporter_address(), os.environ.get("MB_URL")
    return {"api": "ok", "store": "redis" if redis_url() else "memory",
            "chain": {"mode": "live" if live else "dry-run", "write_path": chain.write_path(), "chain_id": 11155111,
                      "marks": os.environ.get("MARKS_ADDRESS") or None, "reporter": reporter,
                      "reporter_balance_eth": chain.balance_eth(reporter) if live else None},
            "multibaas": {"configured": bool(mb and os.environ.get("MB_API_KEY")), "url": mb or None,
                          "webhook": bool(os.environ.get("MB_WEBHOOK_SECRET"))},
            "ens": {"parent": "waterline.eth", "universal_resolver": _universal_resolver(),
                    # public RPC for the browser's ENS reads; SEPOLIA_RPC may carry a key, so it is never shown
                    "rpc": os.environ.get("PUBLIC_SEPOLIA_RPC", "https://ethereum-sepolia-rpc.publicnode.com")}}


def _flags(rep: dict, reps: list[dict]) -> list[dict]:
    """Advisory flags on a failure report, computed on read: what to weigh before trusting it. They change no
    count or status, and the stored record never changes."""
    if rep.get("verdict") != "fail":
        return []
    out, reads = [], rep.get("listing_reads_as") or {}
    if rep.get("listing") and not reads.get("class"):
        out.append({"kind": "listing_no_gpu", "text": f"The reporter's listing names no GPU ({reads.get('source', 'Jev')} found none)."})
    if reads.get("contradicts"):
        out.append({"kind": "listing_contradicts", "text": "The reporter's listing reads as another GPU than the one reported; they reported anyway."})
    other = next((r for r in reps if r["node"] == rep["node"] and r["report_id"] != rep["report_id"]
                  and r.get("verdict") in ("pass", "degraded") and r.get("claimed_class") != rep.get("claimed_class")), None)
    if other:
        out.append({"kind": "card_listed_twice", "report_id": other["report_id"],
                    "text": f"The same card passed as a listed {listings.CLASSES.get(other['claimed_class'], 'other GPU')} "
                            f"in another check; this report lists it as {listings.CLASSES.get(rep.get('claimed_class'), 'another GPU')}."})
    return out


@app.get("/api/reports")
def reports(limit: int = Query(50, ge=1, le=INDEX_MAX)):
    reps = _reports(INDEX_MAX)
    return [{k: r.get(k) for k in SUMMARY} | {"flags": _flags(r, reps)} for r in reps[:limit]]


@app.get("/api/reports/{report_id}")
def report(report_id: str):
    rep, reps = _get(f"report:{report_id}", "report"), _reports(INDEX_MAX)
    return rep | {"flags": _flags(rep, reps)}


@app.get("/api/reports/by-hash/{report_hash}")
def report_by_hash(report_hash: str):
    """ENS -> the check: a GPU name's waterline.report text record (or any Reported event's reportHash) finds it."""
    h = report_hash.lower()
    for r in _reports(INDEX_MAX):  # ponytail: scans the 500-report index; a hash:<h> key if the index grows
        if (r.get("report_hash") or "").lower() == h:
            return {k: r.get(k) for k in SUMMARY}
    raise HTTPException(404, "No check with that report hash.")


@app.get("/api/reports/{report_id}/evidence")
def evidence(report_id: str):
    """The exact canonical bytes; keccak256(body) must equal the GPU name's waterline.report text record."""
    rep = _get(f"report:{report_id}", "report")
    hdr = {"X-Waterline-Report-Hash": rep["report_hash"]} if rep.get("report_hash") else {}
    return Response(canonical(rep), media_type="application/json",
                    headers=hdr | {"Content-Disposition": f'inline; filename="waterline-report-{report_id}.json"'})


def _mb_gpus() -> list[dict]:
    r = httpx.post(os.environ["MB_URL"].rstrip("/") + "/api/v0/queries", json=_only_marks(MB_QUERY), timeout=15,
                   headers={"Authorization": f"Bearer {os.environ['MB_API_KEY']}"})
    r.raise_for_status()
    out = []
    for row in r.json()["result"]["rows"]:
        g = {k: int(row.get(k) or 0) for k in ("cls", "cores", "passes", "fails", "active")}
        pct, tops = int(row.get("pct_bps") or 0), int(row.get("tops_x10") or 0)  # the latest check, as written onchain
        out.append(g | {"node": _hex0x(row["node"]), "provider_node": _hex0x(row.get("provider") or ""),
                        "last_verdict": int(row.get("verdict") or 0), "pct_of_spec": pct / 100 if pct else None,
                        "tops": tops / 10 if tops else None,
                        "humans": g["fails"], "last_at": int(row.get("at") or 0)})
    return out


def _hex0x(v) -> str:
    """bytes32 from MultiBaas as 0x hex: it answers hex, bare hex, or a list of byte values ("[253, 55, ...]")."""
    if isinstance(v, str) and v.startswith("["):
        v = json.loads(v)
    if isinstance(v, list):
        return "0x" + bytes(int(b) for b in v).hex()
    v = str(v).lower()
    return v if v.startswith("0x") else "0x" + v


def _mb_providers() -> list[dict]:
    r = httpx.post(os.environ["MB_URL"].rstrip("/") + "/api/v0/queries", json=_only_marks(MB_PROVIDER_QUERY), timeout=15,
                   headers={"Authorization": f"Bearer {os.environ['MB_API_KEY']}"})
    r.raise_for_status()
    return [{k: int(row.get(k) or 0) for k in ("gpus", "failed_gpus", "humans", "passes", "fails")}
            | {"provider_node": _hex0x(row["provider"]), "last_at": int(row.get("at") or 0)}
            for row in r.json()["result"]["rows"]]


def _local_tallies(reps: list[dict]) -> tuple[list[dict], list[dict]]:
    """Mirror of Marks' GPU and provider tallies from our own published reports, replayed in order (each published
    fail is its own voter, on its GPU and on its provider)."""
    g, prov = {}, {}
    for r in sorted(reversed(reps), key=lambda r: r.get("created_at", 0)):  # reps are newest first; ties keep order
        if not r["published"]:
            continue
        pnode = r.get("provider_node") or "0x" + chain.namehash(f"{r['cloud']}.waterline.eth").hex()
        p = prov.setdefault(pnode, {"provider_node": pnode, "gpus": 0, "failed_gpus": 0, "humans": 0, "passes": 0,
                                    "fails": 0, "degraded": 0, "voters": set()})
        if r["node"] not in g:
            p["gpus"] += 1
        x = g.setdefault(r["node"], {"node": r["node"], "provider_node": pnode, "passes": 0, "fails": 0, "active": 0,
                                     "degraded": 0, "since_fail": 0})
        x["last_verdict"] = r["verdict"]
        was = x["active"] >= 2
        if r["verdict"] == "pass":
            x["passes"] += 1
            p["passes"] += 1
            x["since_fail"] += 1
            if x["active"] and x["since_fail"] >= 2:
                x["active"] = 0
        elif r["verdict"] == "degraded":
            x["degraded"] += 1
            p["degraded"] += 1
        else:
            x["fails"] += 1
            p["fails"] += 1
            x["active"] += 1
            x["since_fail"] = 0
            p["voters"].add(r.get("provider_voter") or r["report_id"])
        p["failed_gpus"] += (x["active"] >= 2) - was
        p["humans"] = len(p["voters"])
        tops_x10, pct_bps = _perf_onchain(r)  # the same pair Marks stores
        x |= {"cls": r["measured_class"], "cores": r.get("cores"), "last_at": r.get("created_at"), "humans": x["fails"],
              "pct_of_spec": pct_bps / 100 if pct_bps else None, "tops": tops_x10 / 10 if tops_x10 else None}
        p["last_at"] = r.get("created_at")
    gpus_ = [{k: v for k, v in x.items() if k != "since_fail"} for x in g.values()]
    provs = [{k: v for k, v in p.items() if k != "voters"} for p in prov.values()]
    by_time = lambda x: x.get("last_at") or 0  # noqa: E731
    return sorted(gpus_, key=by_time, reverse=True), sorted(provs, key=by_time, reverse=True)


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
    rows = _local_tallies(reps)[0] if rows is None else rows
    names = {r["node"]: r["gpu_name"] for r in reps}
    listed = {}
    for r in reps:  # newest first: the latest listing claim per GPU
        listed.setdefault(r["node"], r.get("claimed_class"))
    for x in rows:
        x |= {"gpu_name": names.get(x["node"]), "listed_class": listed.get(x["node"]),
              "status": gpu_status(x["active"], x["fails"], x["passes"], x.get("last_verdict"))}
    return {"source": source, "error": error, "gpus": rows}


@app.get("/api/gpu-classes")
def gpu_class_table():
    """The GPU classes a listing can claim: code (onchain), slug (what a renter types), name, accepted models."""
    return gpu_classes.TABLE


@app.get("/api/providers/known")
def known_providers():
    """The standard provider labels (core/providers.json), for pickers. Advisory: any name is still accepted."""
    return providers_known.KNOWN


@app.get("/api/providers")
def providers():
    """Reputation per provider (<cloud>.waterline.eth): the roll-up of its GPUs' records."""
    reps = _reports(INDEX_MAX)
    source, error, rows = "local", None, None
    if os.environ.get("MB_URL") and os.environ.get("MB_API_KEY"):
        try:
            rows, source = _mb_providers(), "multibaas"
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            log.error("MultiBaas provider query failed: %s", e)
            error = "MultiBaas did not answer; showing this API's own records."
    rows = _local_tallies(reps)[1] if rows is None else rows
    names = {"0x" + chain.namehash(f"{r['cloud']}.waterline.eth").hex(): f"{r['cloud']}.waterline.eth" for r in reps}
    for x in rows:
        name = names.get(x["provider_node"])
        x |= {"name": name, "status": provider_status(x), "listed": bool(name) and providers_known.listed(name.split(".")[0])}
    return {"source": source, "error": error, "providers": rows}


# ---- the dataset: rows, statistics, dictionary (docs/DATA.md) ------------------------------------------------------
def _rows(since: str | None, include_simulated: bool) -> list[dict]:
    rows = [data.row(r if r.get("metrics") is not None else r | perf_profile(r, None)) for r in _reports(None)]
    return [r for r in rows if (include_simulated or not r["simulated"]) and (not since or r["checked_at"] >= since)]


@app.get("/api/data/checks")
def data_checks(format: str = Query("json", pattern="^(json|csv)$"), since: str | None = None,
                include_simulated: bool = False):
    """One row per check, newest first. since: an ISO date or timestamp (UTC), e.g. 2026-10-01."""
    rows = _rows(since, include_simulated)
    if format == "csv":
        return Response(data.to_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="waterline-checks.csv"'})
    return {"generated_at": int(now()), "columns": data.COLUMNS, "rows": rows}


@app.get("/api/data/summary")
def data_summary(since: str | None = None):
    """Per provider x listed model: sample size, freshness, delivered-performance distributions and rates."""
    return {"generated_at": int(now()), "since": since, "groups": data.summary(_rows(since, False))}


@app.get("/api/data/dictionary")
def data_dictionary():
    return [{"name": n, "unit": u, "trust": t, "description": d} for n, u, t, d in data.FIELDS]


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
            node = _hex0x(e["inputs"][0]["value"])  # Reported's first input is the node (hex or a byte list)
            hits.add((node, str(data["transaction"]["txHash"]).lower()))
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


# ---- one-line check: the pod runs `curl -fsSL <api>/run.py | python3 - ...`; nothing of ours is written to disk ----
_ROOT = Path(__file__).resolve().parent.parent
_BUNDLE = {}


@app.get("/api/bundle")
def bundle():
    """core/ and prover/ as a zip, imported from memory by /run.py."""
    if "zip" not in _BUNDLE:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for pkg in ("core", "prover"):
                for f in sorted((_ROOT / pkg).glob("*")):
                    if f.suffix == ".py" or f.name in ("gpu_specs.json", "providers.json", "gpu_classes.json"):
                        z.write(f, f"{pkg}/{f.name}")
        _BUNDLE["zip"] = buf.getvalue()
    return Response(_BUNDLE["zip"], media_type="application/zip")


@app.get("/run")
def run_py(request: Request):
    api = (os.environ.get("API_URL") or str(request.base_url)).rstrip("/")
    table = json.dumps(gpu_classes.BY_SLUG | {str(c): c for c in gpu_classes.NAMES})
    return Response((_ROOT / "api/oneline.py").read_text().replace("__API__", api).replace("__CLASSES__", table),
                    media_type="text/x-python")


# web/ from the same deployment (Vercel serves StaticFiles mounts from its CDN). Mounted last, so every route
# above wins over it.
_web = Path(__file__).resolve().parent.parent / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
