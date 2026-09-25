import os

# Offline, deterministic: in-memory store, dry-run chain, mock World. Set before api.* is imported.
for k in ("REDIS_URL", "MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC", "DEADLINES"):
    os.environ.pop(k, None)
os.environ |= {"WORLD_MOCK": "1", "AGENT_TOKEN_SECRET": "test-agent-secret", "VOTER_SECRET": "test-voter-secret",
               "WORLD_CLIENT_ID": "app_test", "WORLD_CLIENT_SECRET": "test"}
