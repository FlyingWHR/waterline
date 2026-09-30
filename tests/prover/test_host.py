from prover import health, host


def test_cpus_respects_a_cgroup_quota(monkeypatch):
    files = {"/sys/fs/cgroup/cpu.max": "500000 100000", "/proc/cpuinfo": "model name\t: AMD EPYC 7B13"}
    monkeypatch.setattr(host, "_read", files.get)
    monkeypatch.setattr(host.os, "sched_getaffinity", lambda _: set(range(64)), raising=False)
    assert host.cpus() == {"visible": 64, "quota": 5.0, "usable": 5.0, "model": "AMD EPYC 7B13"}
    files["/sys/fs/cgroup/cpu.max"] = "max 100000"
    assert host.cpus()["usable"] == 64 and host.cpus()["quota"] is None


def test_memory_takes_the_smaller_of_limit_and_total(monkeypatch):
    files = {"/proc/meminfo": "MemTotal:       263921440 kB", "/sys/fs/cgroup/memory.max": str(48 << 30)}
    monkeypatch.setattr(host, "_read", files.get)
    assert host.memory_gib() == 48.0
    files["/sys/fs/cgroup/memory.max"] = "max"
    assert host.memory_gib() == 251.7


def test_disk_times_a_real_file_and_cleans_up(tmp_path):
    d = host.disk(str(tmp_path), size=8 << 20, block=1 << 20)
    assert d["write_mbs"] > 0 and d["read_mbs"] > 0 and not list(tmp_path.iterdir())


def test_collect_turns_a_broken_part_into_a_note(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "disk", lambda p: 1 / 0)
    r = host.collect(gpus=2, disk_path=str(tmp_path), net=False)
    assert r["gpus"] == 2 and r["disk"] is None and "download_mbs" not in r
    assert r["notes"][0].startswith("disk: ZeroDivisionError")


def test_sustained_compares_first_and_last_windows():
    assert health.sustained([700.0] * 59) is None
    s = health.sustained([700.0] * 60 + [600.0] * 540)  # 10 min burn: 60 s windows
    assert s == {"window_s": 60, "first_tflops": 700.0, "last_tflops": 600.0, "drop_pct": 14.3}
    assert health.simulated(108, 600)["burn"]["sustained"]["drop_pct"] > 5
