"""Host report: what the machine around the GPU gives it (CPU cores, RAM, disk, download speed). Stdlib only.

Reported by the machine and advisory, like the health report; the API turns it into delivery findings
(api/perf.delivery). The container's own limits count, not the host's: a cgroup CPU quota or memory limit is
what the renter's workload actually gets.
"""
import os
import shutil
import time
import urllib.request

NET_URL = "https://speed.cloudflare.com/__down?bytes=25000000"  # the endpoint refuses more per request
MB = 1e6


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def cpus():
    """Cores this container may use: the affinity mask, capped by a cgroup CPU quota (v2, else v1)."""
    visible = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    quota = None
    v2 = (_read("/sys/fs/cgroup/cpu.max") or "").split()
    if len(v2) == 2 and v2[0] != "max":
        quota = int(v2[0]) / int(v2[1])
    else:
        q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q and p and int(q) > 0:
            quota = int(q) / int(p)
    model = next((line.split(":", 1)[1].strip() for line in (_read("/proc/cpuinfo") or "").splitlines()
                  if line.startswith("model name")), None)
    usable = min(visible, quota) if quota else visible
    return {"visible": visible, "quota": round(quota, 1) if quota else None, "usable": round(usable, 1), "model": model}


def memory_gib():
    """RAM this container may use: the cgroup limit when set, else MemTotal."""
    total = next((int(line.split()[1]) * 1024 for line in (_read("/proc/meminfo") or "").splitlines()
                  if line.startswith("MemTotal:")), None)
    lim = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    lim = int(lim) if lim and lim.isdigit() else None
    usable = min(x for x in (total, lim) if x) if (total or lim) else None
    return round(usable / 2**30, 1) if usable else None


def disk(path=".", size=1 << 30, block=64 << 20):
    """Sequential write (fsync'd) and read of `size` bytes in `path`, MB/s. Random data, so compressing file
    systems can't flatter it; the read drops the file from the page cache first so it comes from the device."""
    f = os.path.join(path, f".waterline-disk-{os.getpid()}")
    data = os.urandom(block)
    try:
        t = time.perf_counter()
        with open(f, "wb") as fh:
            for _ in range(size // block):
                fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        write = size / (time.perf_counter() - t) / MB
        fd = os.open(f, os.O_RDONLY)
        try:
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            t = time.perf_counter()
            while os.read(fd, block):
                pass
            read = size / (time.perf_counter() - t) / MB
        finally:
            os.close(fd)
    finally:
        if os.path.exists(f):
            os.remove(f)
    return {"path": os.path.abspath(path), "write_mbs": round(write), "read_mbs": round(read),
            "free_gib": round(shutil.disk_usage(path).free / 2**30, 1)}


def download_mbs(url=NET_URL, seconds=8.0, timeout=10):
    """Download speed in MB/s: repeated fetches from a public speed-test endpoint for about `seconds`."""
    req = urllib.request.Request(url, headers={"User-Agent": "waterline-profiler"})  # the default Python agent is refused
    got, t = 0, time.perf_counter()
    while time.perf_counter() - t < seconds:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            while chunk := r.read(1 << 20):
                got += len(chunk)
    return round(got / (time.perf_counter() - t) / MB, 1)


def collect(gpus=1, disk_path=".", net=True):
    """Real report. A part that fails becomes a note."""
    rep, notes = {"grade": "reported by the machine", "gpus": gpus}, []
    for key, fn in (("cpu", cpus), ("memory_gib", memory_gib), ("disk", lambda: disk(disk_path)),
                    ("download_mbs", download_mbs if net else None)):
        if fn is None:
            continue
        try:
            rep[key] = fn()
        except Exception as e:  # advisory: never break the check
            rep[key] = None
            notes.append(f"{key}: {type(e).__name__}: {str(e)[:160]}")
    return rep | ({"notes": notes} if notes else {})


def simulated(starved=False):
    """CPU mode: a plausible host, clearly marked simulated. `starved` plays a cheap marketplace box."""
    return {"grade": "reported by the machine", "source": "simulated", "gpus": 1,
            "cpu": {"visible": 64, "quota": 5.0 if starved else None, "usable": 5.0 if starved else 64,
                    "model": "AMD EPYC 7B13 64-Core Processor"},
            "memory_gib": 48.0 if starved else 256.0,
            "disk": {"path": "/workspace", "write_mbs": 140 if starved else 1900, "read_mbs": 180 if starved else 2600,
                     "free_gib": 50.0},
            "download_mbs": 11.5 if starved else 240.0}
