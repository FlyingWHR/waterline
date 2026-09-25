# contracts

`Marks` stores renter-sourced GPU reports and is the ENS resolver for `*.waterline.eth` (ENSIP-10 wildcard),
so `gpu-<id>.<cloud>.waterline.eth` resolves without registering each GPU. Only the reporter (the Waterline API)
can record; a failure needs a voter ID (one verified human per GPU).

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
