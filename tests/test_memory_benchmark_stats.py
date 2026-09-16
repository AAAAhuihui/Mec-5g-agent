from __future__ import annotations

import pytest

from app.memory_benchmark_stats import CACHE_NAMES, COUNTER_NAMES, compare_groups, summarize


def _sample(latency_ms: float, **overrides) -> dict:
    return {
        "latency_ms": latency_ms,
        "error": None,
        "mismatch": False,
        **{name: 0 for name in COUNTER_NAMES},
        "cache": {},
        **overrides,
    }


def test_empty_samples_have_no_invented_rates_or_latency() -> None:
    report = summarize([], wall_seconds=2.0)

    assert report["requests"] == report["errors"] == report["mismatches"] == 0
    assert report["error_rate"] is None
    assert report["correctness_rate"] is None
    assert report["latency_ms"] == {"mean": None, "p50": None, "p95": None}
    assert report["throughput_rps"] == 0.0
    assert report["totals"] == {name: 0 for name in COUNTER_NAMES}
    assert report["per_request"] == {name: None for name in COUNTER_NAMES}
    assert report["cache"] == {
        name: {"hits": 0, "misses": 0, "bypasses": 0, "hit_rate": None}
        for name in CACHE_NAMES
    }


def test_percentiles_interpolate_sorted_attempts_at_n_minus_one_index() -> None:
    report = summarize([_sample(value) for value in (40, 10, 30, 20)], wall_seconds=2.0)

    assert report["latency_ms"]["mean"] == 25.0
    assert report["latency_ms"]["p50"] == 25.0
    assert report["latency_ms"]["p95"] == pytest.approx(38.5)
    assert report["throughput_rps"] == 2.0


def test_single_sample_has_identical_mean_and_percentiles() -> None:
    assert summarize([_sample(3.25)], 1.0)["latency_ms"] == {
        "mean": 3.25, "p50": 3.25, "p95": 3.25
    }


def test_failed_and_mismatched_attempts_stay_in_latency_and_correctness_denominators() -> None:
    samples = [
        _sample(10, mysql_queries=1),
        _sample(20, error="timeout", mysql_queries=2),
        _sample(30, mismatch=True, mysql_queries=3),
        _sample(100, error="failed", mismatch=True, mysql_queries=4),
    ]
    report = summarize(samples, wall_seconds=2.0)

    assert report["requests"] == 4
    assert report["errors"] == report["mismatches"] == 2
    assert report["error_rate"] == 0.5
    assert report["correctness_rate"] == 0.25
    assert report["latency_ms"]["mean"] == 40.0
    assert report["latency_ms"]["p95"] == pytest.approx(89.5)
    assert report["totals"]["mysql_queries"] == 10
    assert report["per_request"]["mysql_queries"] == 2.5
    assert report["throughput_rps"] == 2.0


def test_empty_error_message_still_counts_as_an_error() -> None:
    report = summarize([_sample(1, error="")], 1.0)

    assert report["errors"] == 1
    assert report["correctness_rate"] == 0.0


def test_bypasses_are_neither_hits_nor_misses() -> None:
    report = summarize([
        _sample(1, cache={"semantic": {"bypasses": 10}}),
        _sample(1, cache={"semantic": {"hits": 3, "misses": 1}, "task": {"bypasses": 2}}),
    ], 1.0)

    assert report["cache"]["semantic"] == {
        "hits": 3, "misses": 1, "bypasses": 10, "hit_rate": 0.75,
    }
    assert report["cache"]["task"] == {
        "hits": 0, "misses": 0, "bypasses": 2, "hit_rate": None,
    }


def test_mixed_samples_aggregate_each_counter_and_cache_class() -> None:
    first = dict(zip(COUNTER_NAMES, (12, 3, 4, 3, 8, 5, 1)))
    second = dict(zip(COUNTER_NAMES, (6, 2, 2, 1, 4, 4, 0)))
    samples = [
        _sample(10, **first, cache={
            "semantic": {"hits": 1}, "task": {"misses": 1}, "events": {"misses": 1},
        }),
        _sample(20, **second, cache={
            "semantic": {"hits": 1}, "task": {"hits": 1}, "events": {"hits": 1},
        }),
    ]
    report = summarize(samples, wall_seconds=1.0)

    assert report["totals"] == dict(zip(COUNTER_NAMES, (18, 5, 6, 4, 12, 9, 1)))
    assert report["per_request"] == dict(zip(COUNTER_NAMES, (9, 2.5, 3, 2, 6, 4.5, 0.5)))
    assert report["cache"]["semantic"]["hit_rate"] == 1.0
    assert report["cache"]["task"]["hit_rate"] == 0.5
    assert report["cache"]["events"]["hit_rate"] == 0.5
    assert report["error_rate"] == 0.0
    assert report["correctness_rate"] == 1.0


@pytest.mark.parametrize("wall_seconds", [0.0, -1.0])
def test_nonpositive_wall_time_has_undefined_throughput(wall_seconds: float) -> None:
    assert summarize([_sample(1)], wall_seconds)["throughput_rps"] is None


def test_group_comparison_uses_per_request_queries_with_different_sample_counts() -> None:
    a = summarize([_sample(20, mysql_queries=10)], 1.0)
    b = summarize([_sample(5, mysql_queries=2), _sample(5, mysql_queries=2)], 1.0)

    assert compare_groups(a, b) == {
        "mean_speedup": 4.0, "p95_speedup": 4.0, "mysql_query_reduction": 0.8,
    }


def test_group_comparison_preserves_regressions_and_complete_query_elimination() -> None:
    a = summarize([_sample(5, mysql_queries=2)], 1.0)
    slower = summarize([_sample(10, mysql_queries=3)], 1.0)
    cached = summarize([_sample(1, mysql_queries=0)], 1.0)

    assert compare_groups(a, slower) == {
        "mean_speedup": 0.5, "p95_speedup": 0.5, "mysql_query_reduction": -0.5,
    }
    assert compare_groups(a, cached)["mysql_query_reduction"] == 1.0


def test_group_comparison_returns_none_for_zero_or_missing_denominators() -> None:
    empty = summarize([], 1.0)
    zeros = summarize([_sample(0)], 1.0)
    expected = {"mean_speedup": None, "p95_speedup": None, "mysql_query_reduction": None}

    assert compare_groups(zeros, zeros) == expected
    assert compare_groups(empty, zeros) == expected
    assert compare_groups(zeros, empty) == expected
