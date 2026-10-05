"""JSON key-value store with optional expiry (ttl=None keeps a key for good): Redis when REDIS_URL is set, else process memory (local runs and tests)."""
import json
import os
import threading
import time


def _until(ttl):
    return float("inf") if ttl is None else time.time() + ttl


class MemoryStore:
    # ponytail: per-process only; on Vercel every instance has its own, so set REDIS_URL there.
    def __init__(self):
        self._d, self._lists, self._lock = {}, {}, threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is None or v[1] < time.time():
                self._d.pop(key, None)
                return None
            return json.loads(v[0])

    def put(self, key, value, ttl):
        with self._lock:
            self._d[key] = (json.dumps(value), _until(ttl))

    def add(self, key, value, ttl):
        """Set only if absent. True if this call created it (the atomic once-only guard)."""
        with self._lock:
            v = self._d.get(key)
            if v is not None and v[1] >= time.time():
                return False
            self._d[key] = (json.dumps(value), _until(ttl))
            return True

    def delete(self, key):
        with self._lock:
            self._d.pop(key, None)

    def push(self, key, item):
        """Prepend to a list that never expires (newest first)."""
        with self._lock:
            self._lists.setdefault(key, []).insert(0, item)

    def recent(self, key, n=None):
        with self._lock:
            xs = self._lists.get(key, [])
            return list(xs if n is None else xs[:n])

    def many(self, keys):
        return [self.get(k) for k in keys]


class RedisStore:
    def __init__(self, url):
        import redis
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key):
        v = self._r.get(key)
        return None if v is None else json.loads(v)

    def put(self, key, value, ttl):
        self._r.set(key, json.dumps(value), ex=None if ttl is None else max(1, int(ttl)))

    def add(self, key, value, ttl):
        return bool(self._r.set(key, json.dumps(value), ex=None if ttl is None else max(1, int(ttl)), nx=True))

    def delete(self, key):
        self._r.delete(key)

    def push(self, key, item):
        self._r.lpush(key, json.dumps(item))

    def recent(self, key, n=None):
        return [json.loads(x) for x in self._r.lrange(key, 0, -1 if n is None else n - 1)]

    def many(self, keys):
        # ponytail: one MGET per call; page it if the record grows past ~10k reports
        return [None if v is None else json.loads(v) for v in (self._r.mget(keys) if keys else [])]


def redis_url() -> str | None:
    """REDIS_URL, or the prefixed name Vercel's Upstash integration creates (e.g. myproject_REDIS_URL)."""
    if os.environ.get("REDIS_URL"):
        return os.environ["REDIS_URL"]
    return next((v for k, v in sorted(os.environ.items()) if k.endswith("_REDIS_URL") and v), None)


store = RedisStore(redis_url()) if redis_url() else MemoryStore()
