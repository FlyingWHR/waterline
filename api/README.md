# Waterline API

FastAPI app (`api/app.py`), one Vercel Function for every route. Contract: `docs/INTERFACES.md`.

| File | Job |
|---|---|
| `app.py` | routes: check start/commit/reveal, failure publishing, control panel reads (health, reports, gpus), MultiBaas webhook; serves `web/` |
| `check.py` | sampling (secret, after commit), grading via `core/`, class from probes, GPU label |
| `chain.py` | ENS namehash, `Marks.record` through MultiBaas (compose, sign locally, submit), else raw JSON-RPC, else dry-run |
| `store.py` | Redis (`REDIS_URL`) or in-memory store with expiry |

## Env vars

| Var | Needed for | Notes |
|---|---|---|
| `REDIS_URL` | Vercel | Upstash `rediss://…`. Unset = in-memory (one process only) |
| `MARKS_ADDRESS`, `REPORTER_KEY`, `SEPOLIA_RPC` | publishing | write path `rpc`. Nothing configured = `dry-run`: the call is logged, `tx` is `null` |
| `MB_URL`, `MB_API_KEY`, `REPORTER_KEY` | publishing | write path `multibaas` (wins over `rpc`): MultiBaas composes `record` (fills nonce + gas), the API checks the calldata, signs with `REPORTER_KEY`, MultiBaas submits. `MARKS_ADDRESS` optional here; when set, the composed `to` must match |
| `MB_MARKS_ALIAS`, `MB_MARKS_LABEL` | publishing, webhook | MultiBaas address alias / contract label of Marks, both default `marks` |
| `MB_WEBHOOK_SECRET` | `/api/webhooks/multibaas` | unset = every webhook call gets 401 |
| `MB_URL`, `MB_API_KEY` | `/api/gpus` | MultiBaas admin key, server-side only; unset = table from the API's own reports |
| `ENS_UNIVERSAL_RESOLVER` | `/api/health` | set on Vercel (`contracts/` is not bundled); locally read from `contracts/ens.sepolia.json` |
| `PUBLIC_SEPOLIA_RPC` | `/api/health` | keyless RPC the browser uses for ENS reads; default publicnode |
| `DEADLINES` | check | JSON per claimed class or model id, e.g. `{"1": 5.0}` (seconds); unset = formula in docs/METRICS.md. Print it on the pod with `python3 -m prover.calibrate` |
| `CHECK_STEPS` | check | steps when the prover sends none (default 100); with a `DEADLINES` entry, the only steps accepted for that class |

## Run locally

```
.venv/bin/pip install -r requirements.txt uvicorn pytest
.venv/bin/uvicorn api.app:app --port 8000
.venv/bin/python -m pytest tests/api -q
```

## Deploy (Vercel)

- Root `pyproject.toml` has `[tool.vercel] entrypoint = "api.app:app"`, so Vercel uses the FastAPI preset: one
  function for all routes, and files under `api/` do not become separate functions. Python 3.12+.
- `vercel.json` sets `maxDuration` and keeps `files/`, tests and other folders out of the bundle.
- Redis: `vercel integration add upstash` then check `vercel env ls` for `REDIS_URL` (if the integration only
  sets `KV_URL`, copy it into `REDIS_URL`).
- Secrets: `vercel env add REPORTER_KEY production` (and the others). Never in git.

## curl walkthrough

```
API=http://localhost:8000
curl -s -XPOST $API/api/check/start -H 'content-type: application/json' \
  -d '{"cloud":"cloud-b","uuid":"GPU-1234","claimed_class":1,"n":64,"steps":4}'
# -> {session_id, seed, n, steps, fp_key, deadline_s}; the clock starts now
curl -s -XPOST $API/api/check/commit -H 'content-type: application/json' \
  -d '{"session_id":"…","root":"<64 hex>","probes":{"sms":132,"fp8":true,"clock_ghz":1.98,"bw_tbs":3.2,"fingerprint":"0x<64 hex>"}}'
# -> {elapsed_s, samples:[[step,row],…]}
curl -s -XPOST $API/api/check/reveal -H 'content-type: application/json' \
  -d '{"session_id":"…","fingerprints":{"<step>":["<uint64>",…]},"leaf_hashes":{"0":"<hex>",…},"rows":{"<step>:<row>":[…]}}'
# -> {report_id, verdict, measured_class, reasons, gpu_name, node, published, tx, via, report_hash}   via: multibaas|rpc|dry-run|null
curl -s $API/api/reports/<report_id>/evidence   # canonical bytes; keccak256(body) == report_hash == waterline.report

curl -s -XPOST $API/api/report/publish -H 'content-type: application/json' \
  -d '{"report_id":"…","listing":"1x H100 80GB SXM5 · $2.49/h"}'
# -> {published, tx?, via?, status_text}; 409 when the listing reads as another GPU (resend with "report_anyway":true)
```

## MultiBaas webhook

Point a MultiBaas webhook (event `event.emitted`) at `POST /api/webhooks/multibaas`. The API checks
`X-MultiBaas-Signature` = hex HMAC-SHA256(`MB_WEBHOOK_SECRET`, raw body ‖ `X-MultiBaas-Timestamp`) and rejects
timestamps more than 5 min off (401). For each `Reported` event from Marks (alias `MB_MARKS_ALIAS` or address
`MARKS_ADDRESS`), the report with that node + tx hash gets `indexed: true, indexed_at` (shown in `/api/reports`).
Other events are ignored with 200: `{ok, indexed: <count>}`.

A full honest run (start, commit, reveal) is `tests/api/test_api.py::run_check`.
