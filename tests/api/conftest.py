import os

# Offline, deterministic: in-memory store, dry-run chain. Set before api.* is imported.
for k in ("REDIS_URL", "MARKS_ADDRESS", "REPORTER_KEY", "SEPOLIA_RPC", "DEADLINES"):
    os.environ.pop(k, None)
