#!/usr/bin/env bash
# Set the API's Vercel production env from .env without printing values. Re-runnable.
#   bash scripts/vercel_env.sh            # after `vercel link`
# Removes every non-Upstash var Vercel imported from .env.example (placeholders, script-only keys like DEPLOYER_KEY),
# then adds back only what the API reads. WORLD_MOCK / ALLOW_CLIENT_SIZES stay unset in production.
set -euo pipefail
cd "$(dirname "$0")/.."
vc() { env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy vercel "$@"; }

API_URL=${API_URL:-https://waterline-eth.vercel.app}
val() {  # value of $1 in .env (shell env wins), else default $2
  python3 - "$1" "${2:-}" <<'PY'
import os, shlex, sys
k, d = sys.argv[1], sys.argv[2]
v = os.environ.get(k)
if v is None:
    for l in open(".env"):
        a, e, b = l.strip().removeprefix("export ").partition("=")
        if e and a == k:
            w = shlex.split(b, comments=True); v = w[0] if w else ""
print(v or d, end="")
PY
}

for k in SEPOLIA_RPC PUBLIC_SEPOLIA_RPC DEPLOYER_KEY REPORTER_KEY REPORTER_ADDRESS NAME_SECRET NAME_LABEL MARKS_ADDRESS \
         ENS_UNIVERSAL_RESOLVER API_URL REDIS_URL VOTER_SECRET AGENT_TOKEN_SECRET ALLOW_CLIENT_SIZES CHECK_STEPS DEADLINES \
         WORLD_ISSUER WORLD_CLIENT_ID WORLD_CLIENT_SECRET WORLD_APP_ID WORLD_RP_ID WORLD_SIGNING_KEY WORLD_ENV WORLD_PRESET WORLD_MOCK WORLD_MOCK_DECISION WORLD_MOCK_SUB MB_URL MB_API_KEY \
         MB_MARKS_ALIAS MB_MARKS_LABEL MB_WEBHOOK_SECRET WATERLINE_API WATERLINE_STOP_CMD WATERLINE_HOME WATERLINE_POLL_S; do
  vc env rm "$k" --yes >/dev/null 2>&1 && echo "removed $k" || true
done

add() {  # add NAME VALUE (skipped when empty)
  [ -n "$2" ] || { echo "skip  $1 (empty in .env)"; return; }
  printf %s "$2" | vc env add "$1" production >/dev/null 2>&1 && echo "set   $1" || echo "FAIL  $1"
}
add SEPOLIA_RPC            "$(val SEPOLIA_RPC https://ethereum-sepolia-rpc.publicnode.com)"
add PUBLIC_SEPOLIA_RPC     "$(val PUBLIC_SEPOLIA_RPC https://ethereum-sepolia-rpc.publicnode.com)"
add ENS_UNIVERSAL_RESOLVER "$(val ENS_UNIVERSAL_RESOLVER 0xeEeEEEeE14D718C2B47D9923Deab1335E144EeEe)"
add REPORTER_KEY           "$(val REPORTER_KEY)"
add MARKS_ADDRESS          "$(val MARKS_ADDRESS)"
add API_URL                "$API_URL"
add VOTER_SECRET           "$(val VOTER_SECRET)"
add AGENT_TOKEN_SECRET     "$(val AGENT_TOKEN_SECRET)"
add MB_URL                 "$(val MB_URL)"
add MB_API_KEY             "$(val MB_API_KEY)"
add MB_MARKS_ALIAS         "$(val MB_MARKS_ALIAS marks)"
add MB_MARKS_LABEL         "$(val MB_MARKS_LABEL marks)"
add MB_WEBHOOK_SECRET      "$(val MB_WEBHOOK_SECRET)"
add CHECK_STEPS            "$(val CHECK_STEPS)"
add DEADLINES              "$(val DEADLINES)"
add WORLD_ISSUER           "$(val WORLD_ISSUER https://sandbox.auth.world.org)"
add WORLD_CLIENT_ID        "$(val WORLD_CLIENT_ID)"
add WORLD_CLIENT_SECRET    "$(val WORLD_CLIENT_SECRET)"
echo "Done. Deploy: vercel --prod"
