#!/usr/bin/env bash
# Deploy Marks and register <NAME_LABEL or waterline>.eth with Marks as its resolver (ENSv2, Sepolia).
#   scripts/deploy_marks.sh                  # deploy + commit, wait 65 s, register
#   scripts/deploy_marks.sh --register-only  # retry just the register step (same NAME_SECRET, Marks from deployments/sepolia.json)
# Reads .env (DEPLOYER_KEY, REPORTER_ADDRESS, NAME_SECRET, SEPOLIA_RPC, optional NAME_LABEL). Prints no secrets.
# Idempotency: DeployAndCommit refuses ("name is taken: set NAME_LABEL") once the name is registered, before it
# sends anything, so a re-run can't deploy a second Marks for a name we already own. If someone else holds
# waterline.eth, NAME_LABEL=<other> works for the contract, but the API names GPUs under waterline.eth
# (api/app.py), so change that too: last resort only.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$root/.env" ] || { echo "No .env: cp .env.example .env and fill it in."; exit 1; }
set -a; . "$root/.env"; set +a
for v in SEPOLIA_RPC DEPLOYER_KEY REPORTER_ADDRESS NAME_SECRET; do
  [ -n "${!v:-}" ] || { echo "Set $v in .env first."; exit 1; }
done
cd "$root/contracts"

if [ "${1:-}" != "--register-only" ]; then
  forge script script/Deploy.s.sol:DeployAndCommit --rpc-url sepolia --broadcast
  echo "Waiting 65 s: ENS needs at least 60 s between commit and register ..."
  sleep 65
fi
forge script script/Deploy.s.sol:Register --rpc-url sepolia --broadcast \
  || { echo "Register failed. Wait a minute and run: scripts/deploy_marks.sh --register-only"; exit 1; }

marks=$(python3 -c 'import json; print(json.load(open("deployments/sepolia.json"))["marks"])')
echo
echo "MARKS_ADDRESS=$marks"
echo "Next: put MARKS_ADDRESS=$marks in .env (and on Vercel: vercel env add MARKS_ADDRESS production),"
echo "      then link it in MultiBaas right away (the free plan looks back only 100 blocks):"
echo "      .venv/bin/python scripts/multibaas_link.py"
