"""A small stand-in for the Waterline API (docs/INTERFACES.md), grading with core/ for real."""
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from core.challenge import Params, leaf_hash, merkle_root, row_fingerprint, verify_row_entries


def classify(pr):
    return {(132, True): 1, (114, True): 2, (108, False): 3}.get((pr.get("sms"), pr.get("fp8")), 0)


class FakeApi:
    def __init__(self, world="approved"):
        self.world = world  # outcome of report/approve/poll
        self.sessions, self.calls = {}, []
        api = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.calls.append((self.path, body))
                code, out = api.handle(self.path, body)
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def handle(self, path, b):
        if path == "/api/check/start":
            p = Params(random.getrandbits(63), b.get("n", 16384), b.get("steps", 100))
            sid = f"s{len(self.sessions)}"
            self.sessions[sid] = {"p": p, "req": b}
            return 200, {"session_id": sid, "seed": str(p.seed), "n": p.n, "steps": p.steps,
                         "fp_key": str(p.fp_key), "deadline_s": 60.0}
        if path == "/api/check/commit":
            s = self.sessions.get(b["session_id"])
            if not s:
                return 404, {"error": "No such session."}
            s["root"], s["probes"] = b["root"], b["probes"]
            p = s["p"]
            s["samples"] = [[random.randrange(p.steps), random.randrange(p.n)] for _ in range(8)]
            return 200, {"elapsed_s": 0.1, "samples": s["samples"]}
        if path == "/api/check/reveal":
            s = self.sessions[b["session_id"]]
            p, reasons = s["p"], []
            leaves = [b["leaf_hashes"][str(i)] for i in range(p.steps)]
            if merkle_root(leaves) != s["root"]:
                reasons.append("root mismatch")
            for st, r in s["samples"]:
                fps = [int(x) for x in b["fingerprints"][str(st)]]
                row = b["rows"][f"{st}:{r}"]
                if leaf_hash(np.array(fps, dtype=np.uint64)) != leaves[st]:
                    reasons.append(f"leaf {st}")
                if row_fingerprint(row, p.fp_key) != fps[r]:
                    reasons.append(f"row fp {st}:{r}")
                if not verify_row_entries(p, st, r, row, random.sample(range(p.n), min(64, p.n))):
                    reasons.append(f"entries {st}:{r}")
            cls = classify(s["probes"])
            if cls != s["req"]["claimed_class"]:
                reasons.append("The measured class does not match the listing.")
            ok = not reasons
            return 200, {"report_id": "r1", "verdict": "pass" if ok else "fail", "measured_class": cls,
                         "reasons": reasons, "gpu_name": "gpu-12345678.cloud-b.waterline.eth",
                         "node": "0x" + "ab" * 32, "published": ok, "tx": "0xpass" if ok else None}
        if path in ("/api/world/login/start", "/api/report/approve/start"):
            return 200, {"device_id": "d1", "user_code": "WXYZ-1234",
                         "verification_uri_complete": "https://world.example/device?code=WXYZ-1234",
                         "expires_in": 600}
        if path == "/api/world/login/poll":
            return 200, {"status": "approved", "agent_token": "agent-tok"}
        if path == "/api/report/approve/poll":
            if self.world == "approved":
                return 200, {"status": "approved", "published": True, "tx": "0xfail",
                             "status_text": "suspect · 1 of 2 humans"}
            return 200, {"status": self.world}
        return 404, {"error": "Unknown path."}
