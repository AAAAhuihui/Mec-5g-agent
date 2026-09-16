"""Pure aggregation for memory-cache benchmark observations.

Every attempt, including failed or mismatched attempts, contributes to latency,
throughput, and counter summaries. Percentiles use linear interpolation at
zero-based index ``(n - 1) * q`` in the sorted samples (the R-7 convention).
Undefined ratios and empty latency summaries are represented by ``None``.
"""

from __future__ import annotations

from statistics import fmean
from typing import Any


COUNTER_NAMES = (
    "mysql_queries",
    "mysql_connections",
    "mysql_selects",
    "mysql_schema_selects",
    "mysql_ddl",
    "redis_commands",
    "redis_errors",
)
CACHE_NAMES = ("semantic", "task", "events")


def summarize(samples: list[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    """Aggregate attempts; correctness requires both no error and no mismatch.

    Cache bypasses are excluded from the hit-rate denominator. Throughput uses
    the group's measured wall time, not the sum of individual request times,
    so overlapping requests are handled correctly. Nonpositive wall time has
    no defined throughput. Missing cache classes or counters contribute zero.
    """
    requests = len(samples)
    errors = sum(sample.get("error") is not None for sample in samples)
    mismatches = sum(bool(sample.get("mismatch", False)) for sample in samples)
    correct = sum(
        sample.get("error") is None and not sample.get("mismatch", False)
        for sample in samples
    )
    latencies = sorted(float(sample["latency_ms"]) for sample in samples)
    totals = {
        name: sum(sample.get(name, 0) for sample in samples)
        for name in COUNTER_NAMES
    }
    cache = {}
    for name in CACHE_NAMES:
        counts = {
            kind: sum(
                sample.get("cache", {}).get(name, {}).get(kind, 0)
                for sample in samples
            )
            for kind in ("hits", "misses", "bypasses")
        }
        cache[name] = {
            **counts,
            "hit_rate": _ratio(counts["hits"], counts["hits"] + counts["misses"]),
        }

    return {
        "requests": requests,
        "errors": errors,
        "error_rate": _ratio(errors, requests),
        "mismatches": mismatches,
        "correctness_rate": _ratio(correct, requests),
        "latency_ms": {
            "mean": fmean(latencies) if latencies else None,
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
        "throughput_rps": requests / wall_seconds if wall_seconds > 0 else None,
        "totals": totals,
        "per_request": {name: _ratio(total, requests) for name, total in totals.items()},
        "cache": cache,
    }


def compare_groups(a: dict[str, Any], b: dict[str, Any]) -> dict[str, float | None]:
    """Compare summarized baseline A with candidate B; speedup > 1 is faster."""
    query_ratio = _ratio(
        b["per_request"]["mysql_queries"], a["per_request"]["mysql_queries"]
    )
    return {
        "mean_speedup": _ratio(a["latency_ms"]["mean"], b["latency_ms"]["mean"]),
        "p95_speedup": _ratio(a["latency_ms"]["p95"], b["latency_ms"]["p95"]),
        "mysql_query_reduction": None if query_ratio is None else 1.0 - query_ratio,
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _percentile(sorted_values: list[float], quantile: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)
