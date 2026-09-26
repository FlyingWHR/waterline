# contracts

`Marks` stores renter-sourced GPU reports and is the ENS resolver for `*.waterline.eth` (ENSIP-10 wildcard),
so `gpu-<id>.<cloud>.waterline.eth` resolves without registering each GPU. Only the reporter (the Waterline API)
can record; a failure carries two voter IDs (one verified person, counted once per GPU and once per provider).

```
forge test --no-match-contract EnsFork                                  # unit tests
forge test --match-contract EnsFork --fork-url $SEPOLIA_RPC             # real ENSv2 on a Sepolia fork
```

Deploy (ENSv2 addresses live in `ens.sepolia.json`, from contracts-v2 tag `sepolia-deployment-2026-09-15`):
```
export DEPLOYER_KEY=0x… REPORTER_ADDRESS=0x… NAME_SECRET=0x$(openssl rand -hex 32) SEPOLIA_RPC=…
forge script script/Deploy.s.sol:DeployAndCommit --rpc-url sepolia --broadcast   # writes deployments/sepolia.json
sleep 65
forge script script/Deploy.s.sol:Register --rpc-url sepolia --broadcast
```
Then link `Marks` in MultiBaas right away with a `startingBlock` (the free plan only looks back 100 blocks).

Upgrade an already-registered name (a new `Marks`, `waterline.eth` repointed to it, class names set from
`core/gpu_classes.json`):
```
forge script script/Deploy.s.sol:Redeploy --rpc-url "$SEPOLIA_RPC" --broadcast   # needs DEPLOYER_KEY, REPORTER_ADDRESS
MB_MARKS_ALIAS=<new alias> .venv/bin/python scripts/multibaas_link.py            # link it right away
```
Live: `0x02D4Bd37B5C0Bef47C2DD92c784906178e19A8d8` (MultiBaas alias `marks3`). Class names are an admin-set table
(`setClassNames`, ROLE_CLASSES at the root), so a new GPU class needs no redeploy.
