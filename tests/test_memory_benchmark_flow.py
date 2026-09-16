"""Harness integration tests with in-memory dependencies, NOT performance results."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import fnmatch
import json

import pytest

from app import memory_benchmark as benchmark
from app.memory_benchmark_workload import build_schedule


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.deadlines = {}
        self.clock = 0

    def get(self, key):
        if self.deadlines.get(key, float("inf")) <= self.clock:
            self.delete(key)
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value
        self.deadlines[key] = self.clock + ex

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.deadlines.pop(key, None)

    def incr(self, key):
        value = int(self.get(key) or 0) + 1
        self.values[key] = str(value)  # decode_responses=True returns strings on GET
        return value

    def expire(self, key, seconds):
        self.deadlines[key] = self.clock + seconds

    def scan_iter(self, match, count):
        return iter([key for key in self.values if fnmatch.fnmatchcase(key, match)])

    def close(self):
        pass


@pytest.fixture
def fake_runtime(monkeypatch):
    bench = benchmark.MemoryBenchmark(benchmark.BenchmarkConfig(sessions=5, requests=45, rounds=1, concurrency=(1, 3)))
    redis = FakeRedis()
    store = {}
    bench.control_client = redis
    monkeypatch.setattr(benchmark.redis_module, "_create_client", lambda: redis)

    def count_read():
        benchmark.increment("mysql_connections")
        benchmark.increment("mysql_queries")
        benchmark.increment("mysql_selects")

    def seed():
        store.clear()
        for fixture in bench.fixtures:
            sid = fixture["id"]
            store[sid] = {
                "id": sid, "summary": "", "semantic_summary": benchmark.canonical(fixture["semantic"]),
                "task_summary": benchmark.canonical(fixture["task"]),
                "messages": [{**msg, "created_at": bench._timestamp(index), "evidence_sources": []}
                             for index, msg in enumerate(fixture["messages"])],
                "events": [{**event, "id": index + 1, "session_id": sid, "created_time": bench._timestamp(index)}
                           for index, event in enumerate(fixture["events"])],
            }
        bench.revisions = dict.fromkeys(store, 0)

    def get_session(sid):
        count_read()
        return copy.deepcopy(store.get(sid))

    def session_exists(sid):
        count_read()
        return sid in store

    def list_messages(sid, limit):
        count_read()
        return copy.deepcopy(store[sid]["messages"][-limit:])

    def list_recent_events(sid, limit):
        count_read()
        events = sorted(store[sid]["events"], key=lambda e: (e["created_time"], e["id"]), reverse=True)
        return copy.deepcopy(events[:limit])

    def create_event(sid, **kwargs):
        store[sid]["events"].append({**kwargs, "session_id": sid, "id": len(store[sid]["events"]) + 1,
                                     "created_time": bench.anchor.replace(tzinfo=None)})

    def no_rows(*args, **kwargs):
        count_read()
        return []

    monkeypatch.setattr(bench, "seed", seed)
    monkeypatch.setattr(benchmark.mysql, "get_session", get_session)
    monkeypatch.setattr(benchmark.mysql, "session_exists", session_exists)
    monkeypatch.setattr(benchmark.mysql, "list_messages", list_messages)
    monkeypatch.setattr(benchmark.mysql, "list_recent_events", list_recent_events)
    monkeypatch.setattr(benchmark.mysql, "create_event", create_event)
    monkeypatch.setattr(benchmark.mysql, "list_operations", no_rows)
    monkeypatch.setattr(benchmark.mysql, "list_session_artifacts", no_rows)
    monkeypatch.setattr(benchmark.mysql, "reconcile_session_artifacts", no_rows)
    monkeypatch.setattr(benchmark.mysql, "update_semantic_summary", lambda sid, value: store[sid].update(semantic_summary=value))
    monkeypatch.setattr(benchmark.mysql, "update_task_summary", lambda sid, value: store[sid].update(task_summary=value))

    class NoOpCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

    class NoOpConnection:
        def cursor(self):
            return NoOpCursor()

        def close(self):
            pass

    # Only update()'s deterministic timestamp normalization uses this connector.
    monkeypatch.setattr(bench, "original_connect", lambda database=None: NoOpConnection())
    return bench, redis, store


@pytest.mark.parametrize("operation", benchmark.OPERATIONS)
@pytest.mark.parametrize("scenario", benchmark.SCENARIOS)
@pytest.mark.parametrize("concurrency", [1, 3])
def test_ab_workload_correctness_and_isolation(fake_runtime, operation, scenario, concurrency):
    bench, redis, store = fake_runtime
    schedule = build_schedule(scenario, 5, 45)
    with bench.environment():
        a = bench.group(operation, scenario, concurrency, 0, "A", schedule)
        b = bench.group(operation, scenario, concurrency, 0, "B", schedule)
    assert a["requests"] == b["requests"] == (5 if scenario == "cold" else 45)
    assert a["mismatches"] == b["mismatches"] == a["errors"] == b["errors"] == 0
    assert a["totals"]["redis_commands"] == a["totals"]["redis_errors"] == 0
    assert not redis.values  # each group cleans only its own namespace
    if scenario == "cold":
        assert all(counts["hits"] == 0 for counts in b["cache"].values())
    if scenario == "warm":
        categories = ("semantic", "task") if operation == "memory" else (operation,)
        assert all(b["cache"][category]["hit_rate"] == 1 for category in categories)
        assert b["totals"]["mysql_queries"] < a["totals"]["mysql_queries"]
    if operation == "memory":
        assert b["cache"]["events"]["hits"] == b["cache"]["events"]["misses"] == 0
        assert b["totals"]["mysql_queries"] > 0
    if operation == "events" and scenario == "mixed":
        assert b["cache"]["events"]["hits"] > 0
        assert b["cache"]["events"]["misses"] > 0


def test_fault_suite_detects_failed_write_staleness(fake_runtime, monkeypatch):
    bench, redis, store = fake_runtime
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: setattr(redis, "clock", redis.clock + seconds))
    with bench.environment():
        bench.correctness_checks()
    failures = [check for check in bench.checks if not check["passed"]]
    assert len(failures) == 4
    assert all(check["name"] == "recovery_after_failed_cache_write" and check["mismatch"] for check in failures)
    assert not redis.values
    recovered = [check for check in bench.checks if check["name"] == "confirmed_hit_after_recovery"]
    assert len(recovered) == 4 and all(check["passed"] and check["redis_errors"] == 0 for check in recovered)


def test_cleanup_never_deletes_foreign_keys(fake_runtime):
    bench, redis, store = fake_runtime
    own_prefix = bench.base_prefix + "group:"
    redis.values.update({own_prefix + "some-key": "test", "real-production-key": "keep"})
    bench.clear_prefix(own_prefix)
    assert redis.values == {"real-production-key": "keep"}
    with pytest.raises(ValueError):
        bench.clear_prefix("real-production-")
    with pytest.raises(ValueError):
        bench.clear_prefix(bench.base_prefix)


def test_ttl_hit_check_does_not_include_slow_mysql_oracle(fake_runtime, monkeypatch):
    bench, redis, store = fake_runtime
    original_expected = bench.expected
    def slow_expected(*args, **kwargs):
        redis.clock += 1.1
        return original_expected(*args, **kwargs)
    monkeypatch.setattr(bench, 'expected', slow_expected)
    monkeypatch.setattr(benchmark.time, 'sleep', lambda seconds: setattr(redis, 'clock', redis.clock + seconds))
    with bench.environment():
        bench.correctness_checks()
    ttl_hits = [c for c in bench.checks if c['name'] == 'confirmed_hit_before_ttl']
    assert len(ttl_hits) == 3 and all(c['passed'] for c in ttl_hits)
    assert all(c['name'] == 'recovery_after_failed_cache_write' for c in bench.checks if not c['passed'])


def test_cli_plan_does_not_connect(monkeypatch, capsys):
    from scripts import benchmark_memory_cache as cli
    monkeypatch.setattr(cli.MemoryBenchmark, "run", lambda *a, **k: pytest.fail("must not run"))
    assert cli.main(["--plan", "--sessions", "5", "--requests", "30", "--rounds", "1", "--concurrency", "1"]) == 0
    assert "PLAN ONLY" in capsys.readouterr().out


def test_cli_rejects_existing_report_before_run(monkeypatch, tmp_path):
    from scripts import benchmark_memory_cache as cli
    target = tmp_path / "existing.json"
    target.touch()
    monkeypatch.setattr(cli.MemoryBenchmark, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["--run", "--output", str(target)])
    assert caught.value.code == 2


def test_cli_saves_measurements_when_staleness_is_detected(fake_runtime, monkeypatch, tmp_path):
    from scripts import benchmark_memory_cache as cli
    bench, redis, store = fake_runtime
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: setattr(redis, "clock", redis.clock + seconds))
    monkeypatch.setattr(bench, "preflight", lambda: None)
    monkeypatch.setattr(cli, "MemoryBenchmark", lambda *args, **kwargs: bench)
    target = tmp_path / "report.json"
    assert cli.main(["--run", "--output", str(target)]) == 2
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["completed"] is True and report["passed"] is False
    assert report["runs"] and report["checks"]
    assert all(pair["A"]["errors"] == pair["B"]["errors"] == 0 for pair in report["runs"])
    assert "recovery_after_failed_cache_write" in target.with_suffix(".md").read_text(encoding="utf-8")
    assert "mysql_password" not in report["environment"]
