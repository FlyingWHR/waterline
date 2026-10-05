"""The dataset: one flat row per check, and per provider x model statistics. Pure functions over stored reports.

FIELDS is the data dictionary (served at /api/data/dictionary and written out in docs/DATA.md): every column with
its unit, its source and how far to trust it. "verified" = graded by the API against secret, timed work;
"measured" = timed on the pod by our profiler; "reported" = what the machine's own counters say; "stated" = what
the renter told us. Rows are point-in-time: a check's numbers never change after its verdict.
"""
import csv
import io
import statistics
import time

from core import providers

FIELDS = [
    # name, unit, trust, description
    ("report_id", "", "verified", "Check id; /api/reports/<id> has the full report, /api/reports/<id>/evidence its hashed bytes"),
    ("checked_at", "UTC", "verified", "When the API issued the verdict"),
    ("provider", "", "stated", "Provider as the renter named it (core/providers.json keeps names consistent)"),
    ("provider_known", "bool", "verified", "The provider name is on our list of 63 known providers"),
    ("gpu_name", "", "verified", "ENS name of the GPU: gpu-<first 8 hex of the NVIDIA UUID>.<provider>.waterline.eth"),
    ("gpu_uuid", "", "reported", "NVIDIA UUID as the host's driver reports it"),
    ("listed_model", "", "stated", "The model the listing promised (claimed class, resolved to a reference model)"),
    ("measured_model", "", "measured", "Closest reference model from cores, FP8, bandwidth and throughput"),
    ("verdict", "", "verified", "pass | degraded (right chip, too slow) | fail (wrong chip or wrong answers)"),
    ("spec_mismatch", "bool", "verified", "The measured chip class differs from the listing"),
    ("sms", "count", "measured", "Streaming multiprocessors counted by the timing staircase"),
    ("fp8", "bool", "measured", "FP8 tensor cores present"),
    ("int8_tops_verified", "TOPS", "verified", "Exam INT8 ops / API-clock seconds start to commit (a lower bound)"),
    ("pct_rating_int8_verified", "%", "verified", "int8_tops_verified as a share of the listed model's dense INT8 rating"),
    ("bf16_tflops", "TFLOPS", "measured", "BF16 8192^2 matmul, median of 20, L2 flushed"),
    ("pct_rating_bf16", "%", "measured", "bf16_tflops as a share of the listed model's dense BF16 rating"),
    ("hbm_copy_tbs", "TB/s", "measured", "Device memory copy, 4 GiB, (read+write)/time"),
    ("pct_rating_hbm", "%", "measured", "hbm_copy_tbs as a share of the listed model's memory bandwidth"),
    ("h2d_gbs", "GB/s", "measured", "Pinned 1 GiB host-to-device copy"),
    ("pct_rating_h2d", "%", "measured", "h2d_gbs as a share of the listed model's host link, per direction"),
    ("burn_seconds", "s", "measured", "Length of the sustained BF16 burn"),
    ("burn_tflops_mean", "TFLOPS", "measured", "Mean BF16 TFLOPS over the burn"),
    ("sustained_drop_pct", "%", "measured", "Mean TFLOPS of the first vs last window of a burn of 60 s or more"),
    ("throttle_reasons", "list", "reported", "NVML clock-event reasons seen during the burn, ';'-separated"),
    ("max_temp_c", "°C", "reported", "Highest GPU temperature during the burn"),
    ("pcie_gen", "", "reported", "PCIe generation under load"),
    ("pcie_width", "lanes", "reported", "PCIe width under load"),
    ("pcie_below_max", "bool", "reported", "PCIe link under load runs below the card's maximum generation or width"),
    ("mig", "bool", "reported", "MIG is enabled: a slice of the card"),
    ("ecc_uncorrected", "count", "reported", "Uncorrected memory errors since boot"),
    ("host_cpu_usable", "cores", "reported", "CPU cores the container may use: affinity capped by the cgroup quota"),
    ("host_cpu_visible", "cores", "reported", "CPU cores visible to the container"),
    ("host_ram_gib", "GiB", "reported", "RAM the container may use: cgroup limit, else MemTotal"),
    ("disk_write_mbs", "MB/s", "measured", "1 GiB sequential write with fsync, in the renter's work directory"),
    ("disk_read_mbs", "MB/s", "measured", "The same file read back after dropping it from the page cache"),
    ("download_mbs", "MB/s", "measured", "Repeated 25 MB fetches from speed.cloudflare.com for ~8 s"),
    ("findings", "list", "measured", "Delivery findings (cpu, memory, disk, network, sustained), ';'-separated"),
    ("price_usd_per_gpu_hour", "USD", "stated", "What the renter pays per GPU-hour"),
    ("usd_per_bf16_pflops_hour", "USD", "measured", "price / delivered BF16 PFLOPS: what an hour of delivered compute costs"),
    ("series", "", "verified", "Periodic series id when the renter re-checks one rental (--every)"),
    ("seq", "", "verified", "Place in the series"),
    ("published", "bool", "verified", "Recorded on Marks (Ethereum Sepolia)"),
    ("tx", "", "verified", "Marks transaction hash"),
    ("report_hash", "", "verified", "keccak256 of the canonical report; equals the GPU name's waterline.report record"),
    ("simulated", "bool", "verified", "A CPU test run, not a GPU (excluded from statistics)"),
]
COLUMNS = [f[0] for f in FIELDS]
QUANTILES = (10, 50, 90)


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _metric(rep, key, field="value"):
    return _num(((rep.get("metrics") or {}).get(key) or {}).get(field))


def row(rep: dict) -> dict:
    """One check as a flat, point-in-time row (COLUMNS)."""
    hr = rep.get("health") or {}
    dev, burn, host, mem = hr.get("device") or {}, hr.get("burn") or {}, hr.get("host") or {}, hr.get("memory") or {}
    pcie, cpu, disk = dev.get("pcie") or {}, host.get("cpu") or {}, host.get("disk") or {}
    g, mg, w, mw = (_num(pcie.get(k)) for k in ("gen", "max_gen", "width", "max_width"))
    bf16, price = _metric(rep, "bf16_tflops"), _num(rep.get("price_usd_per_gpu_hour"))
    unc = ((mem.get("ecc_errors") or {}).get("volatile") or {}).get("uncorrected")
    r = {
        "report_id": rep.get("report_id"),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rep.get("created_at") or 0)),
        "provider": rep.get("cloud"), "provider_known": providers.listed(rep.get("cloud") or ""),
        "gpu_name": rep.get("gpu_name"), "gpu_uuid": rep.get("uuid"),
        "listed_model": rep.get("claimed_model"),
        "measured_model": (rep.get("classification") or {}).get("best_match"),
        "verdict": rep.get("verdict"),
        "spec_mismatch": rep.get("measured_class") not in (None, rep.get("claimed_class")),
        "sms": (rep.get("probes") or {}).get("sms"), "fp8": (rep.get("probes") or {}).get("fp8"),
        "int8_tops_verified": _metric(rep, "int8_tops_verified"),
        "pct_rating_int8_verified": _metric(rep, "int8_tops_verified", "pct_of_spec"),
        "bf16_tflops": bf16, "pct_rating_bf16": _metric(rep, "bf16_tflops", "pct_of_spec"),
        "hbm_copy_tbs": _metric(rep, "hbm_copy_tbs"), "pct_rating_hbm": _metric(rep, "hbm_copy_tbs", "pct_of_spec"),
        "h2d_gbs": _metric(rep, "h2d_gbs"), "pct_rating_h2d": _metric(rep, "h2d_gbs", "pct_of_spec"),
        "burn_seconds": burn.get("seconds"), "burn_tflops_mean": (burn.get("tflops") or {}).get("mean"),
        "sustained_drop_pct": (burn.get("sustained") or {}).get("drop_pct"),
        "throttle_reasons": ";".join(burn.get("reasons_seen") or []),
        "max_temp_c": burn.get("max_temp_c"),
        "pcie_gen": pcie.get("gen"), "pcie_width": pcie.get("width"),
        "pcie_below_max": bool((g and mg and g < mg) or (w and mw and w < mw)),
        "mig": dev.get("mig") == "enabled", "ecc_uncorrected": unc,
        "host_cpu_usable": cpu.get("usable"), "host_cpu_visible": cpu.get("visible"),
        "host_ram_gib": host.get("memory_gib"),
        "disk_write_mbs": disk.get("write_mbs"), "disk_read_mbs": disk.get("read_mbs"),
        "download_mbs": host.get("download_mbs"),
        "findings": ";".join(f["kind"] for f in rep.get("delivery") or []),
        "price_usd_per_gpu_hour": price,
        "usd_per_bf16_pflops_hour": round(price / (bf16 / 1000), 2) if price and bf16 else None,
        "series": rep.get("series"), "seq": rep.get("seq"),
        "published": bool(rep.get("published")), "tx": rep.get("tx"), "report_hash": rep.get("report_hash"),
        "simulated": hr.get("source") == "simulated",
    }
    return {k: r.get(k) for k in COLUMNS}


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
    return buf.getvalue()


def quantiles(xs):
    """{p10, p50, p90} of the non-null values, or None. Inclusive method, so small samples stay inside their range."""
    xs = sorted(x for x in map(_num, xs) if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return {f"p{q}": round(xs[0], 2) for q in QUANTILES}
    cuts = statistics.quantiles(xs, n=100, method="inclusive")
    return {f"p{q}": round(cuts[q - 1], 2) for q in QUANTILES}


def _rate(rows, pred, has=lambda r: True):
    """Share of rows (with the input present) where pred holds, and how many rows it rests on."""
    pool = [r for r in rows if has(r)]
    return {"rate": round(sum(1 for r in pool if pred(r)) / len(pool), 3) if pool else None, "n": len(pool)}


def _found(kind):
    return lambda r: kind in (r["findings"] or "").split(";")


def summary(rows: list[dict]) -> list[dict]:
    """Per provider x listed model: sample size, freshness, delivered-performance distributions, and the rates
    renters care about. Simulated (CPU test) rows are left out. Rates carry their own n: an input missing from
    older checks doesn't count against a provider."""
    groups = {}
    for r in rows:
        if not r["simulated"]:
            groups.setdefault((r["provider"], r["listed_model"]), []).append(r)
    out = []
    for (provider, model), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_card = {}
        for r in rs:
            if r["bf16_tflops"] is not None and r["gpu_uuid"]:
                by_card.setdefault(r["gpu_uuid"], []).append(r["bf16_tflops"])
        cvs = [100 * statistics.pstdev(v) / statistics.fmean(v) for v in by_card.values() if len(v) >= 2]
        times = sorted(r["checked_at"] for r in rs)
        out.append({
            "provider": provider, "listed_model": model, "checks": len(rs),
            "gpus": len({r["gpu_uuid"] for r in rs if r["gpu_uuid"]}),
            "first": times[0], "last": times[-1],
            "verdicts": {v: sum(r["verdict"] == v for r in rs) for v in ("pass", "degraded", "fail")},
            "pct_rating_bf16": quantiles(r["pct_rating_bf16"] for r in rs),
            "pct_rating_hbm": quantiles(r["pct_rating_hbm"] for r in rs),
            "pct_rating_int8_verified": quantiles(r["pct_rating_int8_verified"] for r in rs),
            "spec_mismatch": _rate(rs, lambda r: r["spec_mismatch"]),
            "sustained_throttle": _rate(rs, _found("sustained"), lambda r: r["sustained_drop_pct"] is not None),
            "cpu_starved": _rate(rs, _found("cpu"), lambda r: r["host_cpu_usable"] is not None),
            "disk_slow": _rate(rs, _found("disk"), lambda r: r["disk_write_mbs"] is not None),
            "network_slow": _rate(rs, _found("network"), lambda r: r["download_mbs"] is not None),
            "pcie_below_max": _rate(rs, lambda r: r["pcie_below_max"], lambda r: r["pcie_gen"] is not None),
            "card_variation_cv_pct": {"median": round(statistics.median(cvs), 2) if cvs else None, "cards": len(cvs)},
            "price_usd_per_gpu_hour": quantiles(r["price_usd_per_gpu_hour"] for r in rs),
            "usd_per_bf16_pflops_hour": quantiles(r["usd_per_bf16_pflops_hour"] for r in rs),
        })
    return out
