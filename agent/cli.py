"""Waterline agent: runs on the renter's laptop.

  python -m agent login
  python -m agent check --pod ssh://root@1.2.3.4:22 --cloud cloud-b --listing "H100 80GB SXM" \
      --pod-id abc123 --stop-cmd "runpodctl stop pod {pod_id}"
  python -m agent check --local --sim-sms 108 --cloud cloud-b --listing "H100 80GB SXM"   # offline, CPU
  python -m agent history
  python -m agent choose --listings listings.json [--max-price 3.0]

The LLM never decides; the history does. `choose` ranks on MultiBaas history and price only; Jev (or the rules)
only reads listing text into a claimed GPU class. On a FAIL the agent stops paying: it runs the stop command for
the rental before asking for your World approval.

Env: WATERLINE_API (API base URL), MB_URL + MB_API_KEY (history/choose), TYPESAFE_API_KEY (optional Jev),
WATERLINE_STOP_CMD (default --stop-cmd).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from agent import history, listing
from prover.run import ApiError, post

REPO = Path(__file__).resolve().parents[1]
REMOTE_DIR = "waterline"


def home():
    return Path(os.environ.get("WATERLINE_HOME", Path.home() / ".waterline"))


def poll_s():
    return float(os.environ.get("WATERLINE_POLL_S", "3"))


def load_token():
    f = home() / "agent.json"
    return json.loads(f.read_text()).get("agent_token") if f.exists() else None


def world_flow(api, start_path, start_body, poll_path, what):
    """Device grant: show the code and link, poll until the human decides. Returns the last poll answer."""
    d = post(api, start_path, start_body)
    link = d["verification_uri_complete"]
    link = api.rstrip("/") + link if link.startswith("/") else link  # API without API_URL set (local dev)
    print(f"\n{what}: open {link}\n  (or enter code {d['user_code']} in World App)")
    deadline = time.time() + int(d.get("expires_in", 600))
    while time.time() < deadline:
        try:
            r = post(api, poll_path, {"device_id": d["device_id"]})
        except OSError:  # a dropped connection (URLError, SSL EOF) is not an answer: keep waiting
            time.sleep(poll_s())
            continue
        if r["status"] != "pending":
            return r
        time.sleep(poll_s())
    return {"status": "expired"}


def login(api):
    r = world_flow(api, "/api/world/login/start", {}, "/api/world/login/poll", "Log in with World")
    if r["status"] != "approved":
        print(f"Login {r['status']}.")
        return None
    home().mkdir(parents=True, exist_ok=True)
    f = home() / "agent.json"
    f.write_text(json.dumps({"agent_token": r["agent_token"]}))
    f.chmod(0o600)
    print("Logged in. Your agent can now ask you to approve failure reports.")
    return r["agent_token"]


def claimed_class(text, forced=None):
    if forced is not None:
        return forced
    code, conf, src = listing.parse(text)
    name = listing.CLASSES.get(code, "unknown")
    print(f'Listing "{text}" reads as {name} ({src}, confidence {conf:.2f}).')
    if conf >= 0.9 and code:
        return code
    if not sys.stdin.isatty():
        raise SystemExit("Not sure what this listing is. Pass --claimed 1|2|3 (1 H100 SXM, 2 H100 PCIe, 3 A100).")
    ans = input(f"Is this right? Enter to accept {name}, or type 1 (H100 SXM), 2 (H100 PCIe), 3 (A100): ").strip()
    return int(ans) if ans else code


def run_profiler(a, api, cls):
    """Returns the API's final JSON (the profiler's last stdout line). result.json lands in a.out."""
    args = ["--api", api, "--cloud", a.cloud, "--claimed", str(cls)]
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


def check(a, api):
    cls = claimed_class(a.listing, a.claimed)
    print(f"Checking {'locally (CPU)' if a.local else a.pod} as {listing.CLASSES.get(cls, cls)} ...")
    rv = run_profiler(a, api, cls)
    measured = listing.CLASSES.get(rv["measured_class"], "unknown")
    print(f"\nGPU: {rv['gpu_name']}")
    if rv["verdict"] == "pass":
        print(f"PASS: it did the work in time and measures as {measured}.")
        print(f"Published on chain: {rv.get('tx') or ('dry run: no transaction' if rv.get('via') == 'dry-run' else 'pending')}")
        return 0
    if rv["verdict"] == "degraded":  # heat, power or sharing: a true property of this rental, not a fraud claim
        print(f"DEGRADED: it is the {measured} it was listed as and its answers are correct, but it is too slow:")
        for r in rv.get("reasons", []):
            print(f"  - {r}")
        print(f"Published on chain without an approval (it never counts toward failed): {rv.get('tx') or ('dry run: no transaction' if rv.get('via') == 'dry-run' else 'pending')}")
        print("You are paying for full speed. Ending this rental is your call; the agent only stops paying on a FAIL.")
        return 2
    print(f"FAIL: listed as {listing.CLASSES.get(cls, cls)}, measures as {measured}.")
    for r in rv.get("reasons", []):
        print(f"  - {r}")
    stop_paying(a.stop_cmd or os.environ.get("WATERLINE_STOP_CMD"), a.pod_id)
    print("\nNothing is published yet. A failure goes on the record only with your fresh approval.")
    token = load_token() or login(api)
    if not token:
        print("Not logged in: nothing published.")
        return 1
    # a failure accuses the provider of misselling this GPU: say what you rented, in the listing's own words
    text = a.listing or input("Paste the listing you rented (its URL or text): ").strip()
    print(f"You are reporting: listed as {listing.CLASSES.get(cls, cls)}, measures as {measured}. "
          "This report is tied to your World ID and shown with the listing above.")
    r = world_flow(api, "/api/report/approve/start",
                   {"report_id": rv["report_id"], "agent_token": token, "listing": text},
                   "/api/report/approve/poll", "Approve this failure report")
    if r["status"] == "approved" and r.get("published"):
        tx = f" (tx {r['tx']})" if r.get("tx") else ""
        print(f"Approved. Published: {r.get('status_text', 'fail recorded')}{tx}")
    elif r["status"] == "approved":
        print(f"Approved, but not published: {r.get('status_text', 'no reason given')}")
    else:
        print(f"{r['status'].capitalize()}: nothing published.")
    return 1


def stop_paying(cmd, pod_id):
    """On a FAIL, end the rental right away (before any approval). Never called on a PASS."""
    if not cmd:
        print("FAIL: end this rental now (no stop command configured).")
        return False
    if "{pod_id}" in cmd and not pod_id:
        print("FAIL: end this rental now (a stop command is set but no --pod-id was given).")
        return False
    # the template is the renter's own config; only the pod id is quoted in
    rc = subprocess.run(cmd.replace("{pod_id}", shlex.quote(pod_id or "")), shell=True).returncode
    if rc:
        print(f"The stop command failed (exit {rc}): end rental {pod_id} yourself now.")
        return False
    print(f"Stopped paying: rental {pod_id} ended.")
    return True


def show_history(a):
    rows = history.fetch()
    if not rows:
        print("No reports yet.")
    for node, r in rows.items():
        cls = listing.CLASSES.get(int(r.get("cls") or 0), "unknown")
        print(f"{node[:10]}…  {cls:9}  cores {r.get('cores')}  passes {r.get('passes')}  fails {r.get('fails')}"
              f"  {history.status(r)}")
    return 0


def choose(a):
    items = json.loads(Path(a.listings).read_text())
    pick, skipped = history.choose(items, history.fetch(), a.max_price, history.fetch_providers())
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
    ap = argparse.ArgumentParser(prog="agent", description="Waterline agent. The LLM never decides; the history does.")
    ap.add_argument("--api", default=os.environ.get("WATERLINE_API", "http://localhost:3000"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in with World once")
    c = sub.add_parser("check", help="check a rented GPU; on FAIL stop paying, then ask for your World approval")
    c.add_argument("--pod", help="ssh://user@host:port")
    c.add_argument("--local", action="store_true", help="run the profiler here in CPU mode (offline demo)")
    c.add_argument("--sim-sms", type=int, default=132, help="--local only: SM count to report (108 = A100)")
    c.add_argument("--cloud", required=True)
    c.add_argument("--listing", default="")
    c.add_argument("--claimed", type=int, help="skip listing parsing: 1 H100 SXM, 2 H100 PCIe, 3 A100")
    c.add_argument("--push", action="store_true", help="re-copy core/ and prover/ to the pod")
    c.add_argument("--n", type=int)
    c.add_argument("--steps", type=int)
    c.add_argument("--out", default="result.json", help="local copy of the profiler's result.json")
    c.add_argument("--pod-id", help="the rental's id at the cloud, fills {pod_id} in the stop command")
    c.add_argument("--stop-cmd", help="on FAIL, run this at once to stop paying, e.g. 'runpodctl stop pod {pod_id}' "
                                      "(default: env WATERLINE_STOP_CMD). Never run on PASS.")
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
        if a.cmd == "login":
            return 0 if login(a.api) else 1
        if a.cmd == "check":
            return check(a, a.api)
        return show_history(a) if a.cmd == "history" else choose(a)
    except ApiError as e:
        print(f"Waterline API error: {e}")
        return 2
