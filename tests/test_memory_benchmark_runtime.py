from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import socket

import pytest

from app import memory_benchmark as runtime


PREFIX = "memory-bench:012345abcdef:unit:"


def _no_service(*args, **kwargs):
    raise AssertionError("unit test attempted to access a service")


@pytest.fixture(autouse=True)
def no_services(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _no_service)
    monkeypatch.setattr(socket, "create_connection", _no_service)
    monkeypatch.setattr(runtime.mysql, "_connect", _no_service)
    monkeypatch.setattr(runtime.redis_module, "_create_client", _no_service)
    monkeypatch.setattr(runtime, "settings", replace(
        runtime.settings, mysql_database="business_database", mysql_host="unused.invalid",
        mysql_user="unit_test", mysql_password="", redis_host="unused.invalid", redis_password="",
    ))


@pytest.fixture
def bench():
    config = runtime.BenchmarkConfig(
        sessions=2, requests=4, rounds=1, concurrency=(1,),
        scenarios=("warm",), operations=("semantic",),
    )
    return runtime.MemoryBenchmark(config)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.fail_on = set()
        self.closed = 0

    def _record(self, command, *args, **kwargs):
        self.calls.append((command, args, kwargs))
        if command in self.fail_on:
            raise OSError("synthetic Redis error")

    def get(self, key):
        self._record("get", key)
        return self.data.get(key)

    def set(self, key, value, **kwargs):
        self._record("set", key, value, **kwargs)
        self.data[key] = value
        return True

    def delete(self, key):
        self._record("delete", key)
        return int(self.data.pop(key, None) is not None)

    def incr(self, key):
        self._record("incr", key)
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    def expire(self, key, seconds):
        self._record("expire", key, seconds)
        return key in self.data

    def close(self):
        self.closed += 1


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.entered = 0
        self.exited = []

    def execute(self, query, *args, **kwargs):
        self.calls.append((query, args, kwargs))
        if query.startswith("DROP"):
            raise OSError("synthetic SQL error")
        return 1

    def fetchone(self):
        return {"value": 1}

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *args):
        self.exited.append(args)
        return False


class FakeConnection:
    def __init__(self):
        self.raw_cursor = FakeCursor()
        self.cursor_calls = []
        self.closed = False

    def cursor(self, *args, **kwargs):
        self.cursor_calls.append((args, kwargs))
        return self.raw_cursor

    def close(self):
        self.closed = True


def test_measured_cache_none_is_true_bypass_without_client_factory(bench):
    cache = runtime.MeasuredCache(None, ttl=60)
    sample = runtime.new_sample()

    with bench._scope("A", PREFIX, sample):
        assert cache.get_json(cache.summary_key("sid", "semantic")) is None
        assert cache.get_json(cache.summary_key("sid", "task")) is None
        assert cache.get_json(cache.important_events_key("sid", "query", 10, 0.7)) is None
        cache.set_json(cache.summary_key("sid", "semantic"), "ignored")
        cache.bump_event_version("sid")
        cache.delete("key")

    assert cache.enabled is False
    assert sample["redis_commands"] == sample["redis_errors"] == 0
    assert sample["cache"] == {
        name: {"hits": 0, "misses": 0, "bypasses": 1}
        for name in ("semantic", "task", "events")
    }


def test_logical_cache_counts_exclude_event_version_lookups(bench):
    raw = FakeRedis()
    cache = runtime.MeasuredCache(runtime.NamespacedRedis(raw, PREFIX), ttl=60)
    summary_key = cache.summary_key("sid", "semantic")
    task_key = cache.summary_key("sid", "task")
    version_key = runtime.redis_module._event_version_key("sid")
    raw.data[PREFIX + version_key] = "3"
    event_key = cache.important_events_key("sid", "query", 10, 0.7)
    raw.data.update({
        PREFIX + summary_key: json.dumps("summary"),
        PREFIX + task_key: json.dumps({"invalid_summary_type": True}),
        PREFIX + event_key: json.dumps([{"content": "event"}]),
    })
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        assert cache.get_json(summary_key) == "summary"
        assert cache.get_json(task_key) == {"invalid_summary_type": True}
        assert cache.get_json(cache.important_events_key("sid", "query", 10, 0.7)) == [{"content": "event"}]
        assert cache.get_event_version("sid") == 3

    assert sample["redis_commands"] == 5
    assert sample["cache"]["semantic"] == {"hits": 1, "misses": 0, "bypasses": 0}
    assert sample["cache"]["task"] == {"hits": 0, "misses": 1, "bypasses": 0}
    assert sample["cache"]["events"] == {"hits": 1, "misses": 0, "bypasses": 0}


@pytest.mark.parametrize("payload", [None, "not-json", '"wrong type"', '[1]', '{}'])
def test_missing_malformed_or_wrong_type_event_payload_is_a_logical_miss(bench, payload):
    raw = FakeRedis()
    key = "agent:memory:events:session:v0:fingerprint"
    if payload is not None:
        raw.data[PREFIX + key] = payload
    cache = runtime.MeasuredCache(runtime.NamespacedRedis(raw, PREFIX), ttl=60)
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        cache.get_json(key)

    assert sample["cache"]["events"] == {"hits": 0, "misses": 1, "bypasses": 0}
    assert sample["redis_commands"] == 1
    assert sample["redis_errors"] == 0


def test_empty_valid_cache_values_still_count_as_hits(bench):
    raw = FakeRedis()
    cache = runtime.MeasuredCache(runtime.NamespacedRedis(raw, PREFIX), ttl=60)
    summary_key = cache.summary_key("sid", "semantic")
    event_key = "agent:memory:events:session:v0:fingerprint"
    raw.data.update({PREFIX + summary_key: '""', PREFIX + event_key: "[]"})
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        assert cache.get_json(summary_key) == ""
        assert cache.get_json(event_key) == []

    assert sample["cache"]["semantic"]["hits"] == 1
    assert sample["cache"]["events"]["hits"] == 1


def test_redis_errors_are_counted_while_cache_falls_back_to_miss(bench):
    raw = FakeRedis()
    raw.fail_on = {"get", "set"}
    cache = runtime.MeasuredCache(runtime.NamespacedRedis(raw, PREFIX), ttl=60)
    key = cache.summary_key("sid", "semantic")
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        assert cache.get_json(key) is None
        cache.set_json(key, "value")
        assert cache.get_event_version("sid") == 0

    assert sample["redis_commands"] == sample["redis_errors"] == 3
    assert sample["cache"]["semantic"] == {"hits": 0, "misses": 1, "bypasses": 0}
    assert sample["cache"]["events"] == {"hits": 0, "misses": 0, "bypasses": 0}


def test_measured_cache_forwards_default_and_explicit_payload_ttl():
    raw = FakeRedis()
    cache = runtime.MeasuredCache(raw, ttl=45)

    cache.set_json("default", {"value": "example"})
    cache.set_json("override", [], ttl_seconds=2)

    assert raw.calls[0][2] == {"ex": 45}
    assert raw.calls[1][2] == {"ex": 2}
    assert json.loads(raw.data["default"]) == {"value": "example"}


@pytest.mark.parametrize("prefix", [
    "", "agent:memory:", "memory-bench:012345abcdef:",
    "memory-bench:012345abcde:unit:", "memory-bench:012345ABCDEF:unit:",
    "memory-bench:012345abcdef:*:", "memory-bench:012345abcdef:../unit:",
    "memory-bench:012345abcdef:unit", "memory-bench:012345abcdef:unit:\n",
])
def test_redis_namespace_rejects_unscoped_or_malformed_prefixes(prefix):
    with pytest.raises(ValueError, match="invalid benchmark Redis prefix"):
        runtime.NamespacedRedis(FakeRedis(), prefix)


def test_redis_namespace_forwards_all_commands_and_counts_only_scoped_calls(bench):
    raw = FakeRedis()
    client = runtime.NamespacedRedis(raw, PREFIX)
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        assert client.set("payload", "value", ex=23) is True
        assert client.get("payload") == "value"
        assert client.incr("version") == 1
        assert client.expire("version", 31) is True
        assert client.delete("payload") == 1

    assert raw.calls == [
        ("set", (PREFIX + "payload", "value"), {"ex": 23}),
        ("get", (PREFIX + "payload",), {}),
        ("incr", (PREFIX + "version",), {}),
        ("expire", (PREFIX + "version", 31), {}),
        ("delete", (PREFIX + "payload",), {}),
    ]
    assert sample["redis_commands"] == 5
    assert sample["redis_errors"] == 0


def test_injected_timeout_never_calls_underlying_client(bench, monkeypatch):
    delays = []
    monkeypatch.setattr(runtime.time, "sleep", delays.append)
    raw = FakeRedis()
    client = runtime.NamespacedRedis(raw, PREFIX, fault_delay_ms=25)
    sample = runtime.new_sample()

    with bench._scope("B", PREFIX, sample):
        with pytest.raises(TimeoutError, match="benchmark-injected"):
            client.get("key")

    assert delays == [0.025]
    assert raw.calls == []
    assert sample["redis_commands"] == sample["redis_errors"] == 1


def test_cursor_and_connection_meters_forward_calls_and_count_attempted_sql(bench):
    raw = FakeConnection()
    connection = runtime.ConnectionMeter(raw)
    sample = runtime.new_sample()
    queries = [
        " SELECT value FROM fixture WHERE id=%s",
        "\nselect COLUMN_NAME from information_schema.COLUMNS",
        "CREATE TABLE fixture (id INT)", "ALTER TABLE fixture ADD value INT",
        "INSERT INTO fixture VALUES (1, 2)", "UPDATE fixture SET value=3",
    ]

    with bench._scope("A", PREFIX, sample):
        with connection.cursor("cursor-kind", buffered=False) as cursor:
            assert isinstance(cursor, runtime.CursorMeter)
            for query in queries:
                assert cursor.execute(query, (1,), option=True) == 1
            assert cursor.fetchone() == {"value": 1}
        with pytest.raises(OSError, match="synthetic SQL"):
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE fixture")
        connection.close()

    assert raw.cursor_calls == [(("cursor-kind",), {"buffered": False}), ((), {})]
    assert raw.raw_cursor.calls[0] == (queries[0], ((1,),), {"option": True})
    assert raw.raw_cursor.entered == len(raw.raw_cursor.exited) == 2
    assert raw.raw_cursor.exited[1][0] is OSError
    assert raw.closed is True
    assert sample["mysql_queries"] == 7
    assert sample["mysql_selects"] == 2
    assert sample["mysql_schema_selects"] == 1
    assert sample["mysql_ddl"] == 3
    assert sample["mysql_connections"] == 0  # Wrapping is not a connection attempt.


def test_connection_attempt_counter_includes_failed_connects(bench, monkeypatch):
    raw = FakeConnection()
    attempts = []

    def connect(database):
        attempts.append(database)
        if len(attempts) == 2:
            raise OSError("synthetic connection error")
        return raw

    monkeypatch.setattr(bench, "original_connect", connect)
    sample = runtime.new_sample()
    with bench._scope("A", PREFIX, sample):
        assert bench._connect(bench.database).connection is raw
        with pytest.raises(OSError, match="synthetic connection"):
            bench._connect(bench.database)

    assert attempts == [bench.database, bench.database]
    assert sample["mysql_connections"] == 2


@pytest.mark.parametrize("database", [
    "business_database", "memory_bench_012345ABCDEf", "memory_bench_012345abcde",
    "memory_bench_012345abcdef_extra", "memory_bench_012345abcdef`; DROP DATABASE business_database;--",
])
def test_benchmark_rejects_nonisolated_database_names(database):
    with pytest.raises(ValueError, match="requires a NEW database"):
        runtime.MemoryBenchmark(runtime.BenchmarkConfig(sessions=1), database=database)


def test_database_guard_rejects_business_database_even_when_name_matches_pattern():
    with pytest.raises(ValueError, match="requires a NEW database"):
        runtime.validate_database_name("memory_bench_012345abcdef", "MEMORY_BENCH_012345ABCDEF")


def test_benchmark_plan_is_deterministic_and_never_connects(bench):
    plan = bench.plan()

    assert plan == bench.plan()
    assert plan["database"] == bench.database
    assert plan["redis_prefix"] == bench.base_prefix
    assert plan["timed_reads"] == 8
    assert plan["workloads"]["warm"]["reads_per_group"] == 4
    assert len(plan["workloads"]["warm"]["digest"]) == 64
    assert plan["connects_to_services"] is False
    assert plan["llm_calls"] == 0
    assert bench.created_database is False
    with pytest.raises(RuntimeError, match="not created by this run"):
        bench.seed()


def test_environment_restores_objects_after_exception_and_blocks_every_llm_entry(bench):
    targets = [
        (runtime.mysql, "settings"), (runtime.redis_module, "settings"),
        (runtime.mysql, "_connect"), (runtime.memory, "SummaryRepository"),
        (runtime.memory, "_CONVERSATIONS"), (runtime.repositories, "datetime"),
        *[(runtime.DeepSeekClient, name) for name in ("__init__", "chat", "json_chat", "tool_call")],
    ]
    originals = [(obj, name, getattr(obj, name)) for obj, name in targets]
    with pytest.raises(RuntimeError, match="exit environment"):
        with bench.environment():
            assert runtime.mysql.settings is bench.settings
            assert runtime.redis_module.settings is bench.settings
            assert runtime.memory._CONVERSATIONS is not originals[4][2]
            assert runtime.repositories.datetime.now(timezone.utc) == bench.anchor
            with pytest.raises(AssertionError, match="LLM is forbidden"):
                runtime.DeepSeekClient()
            instance = object.__new__(runtime.DeepSeekClient)
            for method in ("chat", "json_chat", "tool_call"):
                with pytest.raises(AssertionError, match="LLM is forbidden"):
                    getattr(instance, method)([])
            raise RuntimeError("exit environment")

    for obj, name, original in originals:
        assert getattr(obj, name) is original


def test_scope_restores_parent_request_and_closes_only_its_own_clients(bench, monkeypatch):
    raw = FakeRedis()
    monkeypatch.setattr(runtime.redis_module, "_create_client", lambda: raw)
    outer = runtime.new_sample()
    inner = runtime.new_sample()

    with bench._scope("A", PREFIX, outer):
        runtime.increment("mysql_queries")
        with bench._scope("B", PREFIX, inner):
            cache = bench._cache()
            assert cache.enabled is True
            runtime.increment("mysql_queries")
        runtime.increment("mysql_queries")
        assert raw.closed == 1

    assert outer["mysql_queries"] == 2
    assert inner["mysql_queries"] == 1
    assert runtime._REQUEST.get() is None


def test_frozen_clock_preserves_isinstance_for_real_naive_and_aware_datetimes():
    anchor = datetime(2026, 9, 8, 8, 30, tzinfo=timezone.utc)
    frozen = runtime.frozen_clock(anchor)
    local_timezone = timezone(timedelta(hours=8))

    assert frozen.now(timezone.utc) == anchor
    assert frozen.now(local_timezone) == anchor.astimezone(local_timezone)
    assert frozen.now().tzinfo is None
    assert frozen.now() == anchor.replace(tzinfo=None)
    assert isinstance(datetime(2020, 1, 1), frozen)
    assert isinstance(anchor, frozen)
    assert not isinstance("2026-09-08", frozen)


def test_measure_separates_mismatches_failures_and_request_counters(bench, monkeypatch):
    outcomes = iter([{"value": "actual"}, OSError("sensitive synthetic details"), {"value": "expected"}])

    def read(operation, session_index, query):
        runtime.increment("mysql_queries")
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bench, "read", read)
    item = {"index": 0, "session_index": 0, "query": "test query"}
    expected = runtime.canonical({"value": "expected"})
    mismatch, failed, passed = [bench.measure("semantic", item, "A", PREFIX, expected) for _ in range(3)]

    assert mismatch["mismatch"] is True and mismatch["error"] is None
    assert failed["error"] == "OSError" and failed["mismatch"] is False
    assert passed["error"] is None and passed["mismatch"] is False
    assert "sensitive synthetic details" not in json.dumps(failed)
    for sample in (mismatch, failed, passed):
        assert sample["mysql_queries"] == 1
        assert sample["latency_ms"] >= 0
        assert len(sample["query_digest"]) == 16
    assert mismatch["cache"] is not failed["cache"]
    assert runtime._REQUEST.get() is None
