"""Waterline agent: runs on the renter's laptop.

  python -m agent check --pod ssh://root@1.2.3.4:22 --cloud cloud-b --listing "H100 80GB SXM" \
      --pod-id abc123 --stop-cmd "runpodctl stop pod {pod_id}"
  python -m agent check --local --sim-sms 108 --cloud cloud-b --listing "H100 80GB SXM"   # offline, CPU
  python -m agent history
  python -m agent choose --listings listings.json [--max-price 3.0]

The LLM never decides; the history does. `choose` ranks on MultiBaas history and price only; Jev (or the rules)
only turns listing text into a claimed GPU class. On a FAIL the agent runs the rental's stop command, then
publishes the failure with the listing you rented.

Env (all optional): WATERLINE_API (default the live API), MB_URL + MB_API_KEY (history/choose read MultiBaas
directly; without them they read the same rows through the API), TYPESAFE_API_KEY (Jev; without it, built-in rules
read listings), WATERLINE_STOP_CMD (default --stop-cmd).
"""
import argparse
import json
import random
import re
import secrets
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from agent import history, listing
from core import classes
from prover.run import AMBER, DIM, GREEN, RED, ApiError, mark, paint, post

REPO = Path(__file__).resolve().parents[1]
REMOTE_DIR = "waterline"


def fact(key, value):
    """Same receipt style as the profiler's (prover.run), on stdout."""
    print(f"  {paint(DIM, key.ljust(9))} {value}")


def claimed_class(text, forced=None):
    if forced is not None:
        return forced
    code, conf, src = listing.parse(text)
    name = listing.CLASSES.get(code, "unknown")
    fact(src.lower(), f'"{text}" reads as {name} (confidence {conf:.2f})')
    if conf >= 0.9 and code:
        return code
    if not sys.stdin.isatty():
        raise SystemExit("Not sure what this listing is. Pass --claimed with the GPU, e.g. h100, h200, a100, l40s.")
    ans = input(f"Is this right? Enter to accept {name}, or type the GPU (h100, h100-pcie, h200, a100, ...): ").strip()
    return gpu_code(ans) if ans else code


def gpu_code(v):
    """--claimed as a slug (h200) or a class code (5)."""
    code = int(v) if str(v).isdigit() else classes.BY_SLUG.get(str(v).lower())
    if code not in classes.NAMES:
        raise SystemExit(f"Unknown GPU {v!r}. One of: {', '.join(sorted(classes.BY_SLUG))}")
    return code


def run_profiler(a, api, cls):
    """Returns the API's final JSON (the profiler's last stdout line). result.json lands in a.out."""
    args = ["--api", api, "--cloud", a.cloud, "--claimed", str(cls), "--no-mark"]  # the agent already printed it
    if getattr(a, "series", None):
        args += ["--series", a.series, "--seq", str(a.seq)]
    if a.n:
        args += ["--n", str(a.n)]
    if a.steps:
        args += ["--steps", str(a.steps)]
    if a.local:
        cmd = [sys.executable, "-m", "prover.run", *args, "--cpu", "--sms", str(a.sim_sms),
               "--out", str(Path(a.out).resolve())]
        out = subprocess.run(cmd, cwd=REPO, stdout=subprocess.PIPE, text=True)
    else:
        u = urlparse(a.pod)
        target = f"{u.username}@{u.hostname}" if u.username else u.hostname
        ssh = ["ssh", *(["-p", str(u.port)] if u.port else []), target]
        scp = ["scp", "-q", *(["-P", str(u.port)] if u.port else [])]
        have = subprocess.run([*ssh, f"test -f {REMOTE_DIR}/prover/run.py"]).returncode == 0
        if a.push or not have:
            print("Copying the profiler to the pod ...")
            subprocess.run([*ssh, f"mkdir -p {REMOTE_DIR}"], check=True)
            subprocess.run([*scp, "-r", str(REPO / "core"), str(REPO / "prover"), f"{target}:{REMOTE_DIR}/"],
                           check=True)
        remote = f"cd {REMOTE_DIR} && python3 -m prover.run {shlex.join(args)} --out result.json"
        out = subprocess.run([*ssh, remote], stdout=subprocess.PIPE, text=True)
        if out.returncode == 0:
            subprocess.run([*scp, f"{target}:{REMOTE_DIR}/result.json", a.out])
    if out.returncode != 0:
        raise SystemExit(f"The profiler stopped with an error (exit {out.returncode}); see above.")
    return json.loads(out.stdout.strip().splitlines()[-1])


def check_every(a, api):
    """A periodic series: re-check the same rental at jittered intervals. Passes and degraded results keep publishing
    on their own; the first failure stops paying and is reported, which ends the series."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", a.every.strip().lower())
    if not m:
        raise SystemExit("--every takes a duration like 90s, 30m or 2h")
    gap = float(m[1]) * {"s": 1, "m": 60, "h": 3600, "": 60}[m[2]]
    a.series, a.seq, rc = secrets.token_hex(4), 0, 0
    try:
        while a.times is None or a.seq < a.times:
            a.seq += 1
            rc = check(a, api, first=a.seq == 1)
            if rc == 1 or (a.times is not None and a.seq >= a.times):  # a failure ends the rental, so the series
                break
            wait = gap * random.uniform(0.8, 1.2)
            print(paint(DIM, f"  ↻ next check in ~{f'{wait / 60:.0f} min' if wait >= 90 else f'{wait:.0f} s'} "
                             "(jittered, so the host can't time it) · Ctrl-C to stop"))
            time.sleep(wait)
    except KeyboardInterrupt:
        pass
    print(paint(DIM, f"  ↻ series {a.series} ended after {a.seq} check{'s' if a.seq != 1 else ''}"))
    return rc


def check(a, api, first=True):
    if first:
        mark()
    cls = a.claimed = claimed_class(a.listing, a.claimed)  # read the listing once; a series reuses it
    claim = listing.CLASSES.get(cls, cls)
    print(paint(DIM, f"  · checking {'this machine (CPU)' if a.local else a.pod} against the listing: {claim}"))
    rv = run_profiler(a, api, cls)  # prints its own receipt: the verdict, the ENS names, where it landed
    measured = listing.CLASSES.get(rv["measured_class"], "unknown")
    if rv["verdict"] == "pass":
        fact("agent", paint(GREEN, "keep this rental: it is the listed chip, in time"))
        return 0
    if rv["verdict"] == "degraded":  # heat, power or sharing: a true property of this rental, not a fraud claim
        fact("agent", paint(AMBER, "you pay for full speed and get less; ending the rental is your call"))
        return 2
    fact("agent", paint(RED, f"listed as {claim}, measures as {measured}: stopping the rental"))
    stop_paying(a.stop_cmd or os.environ.get("WATERLINE_STOP_CMD"), a.pod_id)
    web_link = f"{api.rstrip('/')}/#/check/{rv['report_id']}"
    # a failure accuses the provider of misselling this GPU: say what you rented, in the listing's own words
    text = "" if a.web else a.listing or (input("  paste the listing you rented (its URL or text): ").strip() if sys.stdin.isatty() else "")
    if not text:  # a failure report needs the listing; without one here, it is added on the web
        fact("report", f"publish it on the web, with the listing you rented: {web_link}")
        return 1
    code, _, src = listing.parse(text)
    if code:
        fact(src.lower(), f"your listing reads as {listing.CLASSES[code]}"
             + (": the claim stands" if code == cls else paint(AMBER, f": not {claim}")))
    body = {"report_id": rv["report_id"], "listing": text}
    try:
        return published(post(api, "/api/report/publish", body))
    except ApiError as e:
        if "report anyway" not in str(e):
            raise
        fact("agent", paint(AMBER, str(e).split(": ", 1)[-1]))  # Jev read the listing as another GPU
        if not sys.stdin.isatty() or input("  report anyway? [y/N] ").strip().lower() != "y":
            fact("agent", "not reported: nothing published")
            return 1
        return published(post(api, "/api/report/publish", body | {"report_anyway": True}))


def published(r):
    if r.get("published"):
        tx = r.get("tx") or "dry run, no transaction"
        fact("onchain", paint(GREEN, "published") + f" · {tx}" + (f" via {r['via']}" if r.get("via") not in (None, "dry-run") else "")
             + f" · {r.get('status_text', 'failure recorded')}")
    else:
        fact("onchain", paint(AMBER, f"not published: {r.get('status_text', 'no reason given')}"))
    return 1


def stop_paying(cmd, pod_id):
    """On a FAIL, end the rental right away (before reporting). Never called on a PASS."""
    if not cmd:
        fact("agent", "FAIL: end this rental now (no stop command configured).")
        return False
    if "{pod_id}" in cmd and not pod_id:
        fact("agent", "FAIL: end this rental now (a stop command is set but no --pod-id was given).")
        return False
    # the template is the renter's own config; only the pod id is quoted in
    rc = subprocess.run(cmd.replace("{pod_id}", shlex.quote(pod_id or "")), shell=True).returncode
    if rc:
        fact("agent", f"The stop command failed (exit {rc}): end rental {pod_id} yourself now.")
        return False
    fact("agent", f"Stopped paying: rental {pod_id} ended.")
    return True


def show_history(a):
    rows = history.fetch(api=a.api)
    if not rows:
        print("No reports yet.")
    for node, r in rows.items():
        cls = listing.CLASSES.get(int(r.get("cls") or 0), "unknown")
        name = (r.get("gpu_name") or f"{node[:10]}…").removesuffix(".waterline.eth")  # names come with the API's rows
        print(f"{name:28}  {cls:9}  cores {r.get('cores')}  passes {r.get('passes')}  fails {r.get('fails')}"
              f"  {history.status(r)}")
    return 0


def choose(a):
    items = json.loads(Path(a.listings).read_text())
    pick, skipped = history.choose(items, history.fetch(api=a.api), a.max_price, history.fetch_providers(api=a.api))
    for li, why in skipped:
        print(f"skipping {li['gpu'].removesuffix('.waterline.eth')}: {why}")
    if not pick:
        print("No listing is safe to rent.")
        return 1
    print(f"Rent {pick['gpu'].removesuffix('.waterline.eth')} at {pick['price']}/h. History: {pick['history']}.")
    if pick.get("listing"):  # reading only: the claimed class is what `check` will hold the GPU to
        code, conf, src = listing.parse(pick["listing"])
        print(f'Listing "{pick["listing"]}" reads as {listing.CLASSES.get(code, "unknown")} ({src}, confidence {conf:.2f}).')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent", description="Waterline agent: check a rented GPU, read GPU history, pick a listing.")
    ap.add_argument("--api", default=os.environ.get("WATERLINE_API", "https://waterline-eth.vercel.app"),
                    help="Waterline API (default: env WATERLINE_API, else the live one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a rented GPU; on FAIL stop paying, then publish the failure")
    c.add_argument("--pod", help="ssh://user@host:port")
    c.add_argument("--local", action="store_true", help="run the profiler here in CPU mode (offline demo)")
    c.add_argument("--sim-sms", type=int, default=132, help="--local only: SM count to report (108 = A100)")
    c.add_argument("--cloud", required=True, help="the provider you rent from, e.g. runpod, vastai, lambda")
    c.add_argument("--listing", default="", help='the listing you rented, in its own words, e.g. "1x H100 80GB SXM5"')
    c.add_argument("--claimed", type=gpu_code, help="skip listing parsing: the GPU, e.g. h100, h100-pcie, h200, a100, l40s")
    c.add_argument("--push", action="store_true", help="re-copy core/ and prover/ to the pod")
    c.add_argument("--n", type=int, help="matrix size (default: sized for the claimed GPU)")
    c.add_argument("--steps", type=int, help="challenge steps (default: sized for the claimed GPU)")
    c.add_argument("--out", default="result.json", help="local copy of the profiler's result.json")
    c.add_argument("--pod-id", help="the rental's id at the cloud, fills {pod_id} in the stop command")
    c.add_argument("--stop-cmd", help="on FAIL, run this at once to stop paying, e.g. 'runpodctl stop pod {pod_id}' "
                                      "(default: env WATERLINE_STOP_CMD). Never run on PASS.")
    c.add_argument("--web", action="store_true", help="on FAIL, leave publishing to the web panel")
    c.add_argument("--every", help="a periodic series: re-check at this interval (e.g. 30m), jittered ±20%%, until Ctrl-C")
    c.add_argument("--times", type=int, help="with --every: stop after this many checks")
    sub.add_parser("history", help="GPU history from MultiBaas: passes, failures, status")
    ch = sub.add_parser("choose", help="pick a listing from MultiBaas history only (no LLM ranking)")
    ch.add_argument("--listings", required=True, help='JSON list of {"gpu": name, "listing": text, "price": n}')
    ch.add_argument("--max-price", type=float, help="skip listings above this price per hour")
    a = ap.parse_args(argv)
    if a.cmd == "check" and not (a.local or a.pod):
        ap.error("check needs --pod or --local")
    if a.cmd == "check" and a.claimed is None and not a.listing:
        ap.error("check needs --listing or --claimed")
    try:
        if a.cmd == "check":
            return check_every(a, a.api) if a.every else check(a, a.api)
        return show_history(a) if a.cmd == "history" else choose(a)
    except ApiError as e:
        print(f"Waterline API error: {e}")
        return 2
    except OSError as e:  # URLError, refused, DNS, TLS
        print(f"Can't reach {a.api}: {getattr(e, 'reason', e)}. Check the address, or set WATERLINE_API.")
        return 2
