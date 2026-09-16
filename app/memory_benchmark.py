"""Isolated, real MySQL/Redis memory-read benchmark; never invokes an LLM.

Run only in a standalone process. Scoped dependency patches leave application
code unchanged, including its per-read schema checks and client lifecycle.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import platform
import re
import time
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from app.agent import memory, memory_repositories as repositories, mysql_memory_store as mysql
from app.agent import redis_cache_repository as redis_module
from app.config import settings
from app.llm.deepseek_client import DeepSeekClient
from app.memory_benchmark_stats import compare_groups, summarize
from app.memory_benchmark_workload import build_schedule, build_sessions, workload_digest


OPERATIONS = ("semantic", "task", "events", "memory")
SCENARIOS = ("cold", "warm", "mixed")
COUNTERS = ("mysql_queries", "mysql_connections", "mysql_selects", "mysql_schema_selects",
            "mysql_ddl", "redis_commands", "redis_errors")
_REQUEST: ContextVar = ContextVar("memory_benchmark_request", default=None)


@dataclass(frozen=True)
class BenchmarkConfig:
    sessions: int = 50
    requests: int = 1000
    rounds: int = 3
    concurrency: tuple = (1, 5, 10)
    scenarios: tuple = SCENARIOS
    operations: tuple = OPERATIONS
    seed: int = 20260908
    fault_delay_ms: float = 20.0
    run_checks: bool = True

    def validate(self) -> None:
        for name in ("sessions", "requests", "rounds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not self.concurrency or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in self.concurrency):
            raise ValueError("concurrency must contain positive integers")
        if not self.scenarios or not set(self.scenarios) <= set(SCENARIOS):
            raise ValueError("unknown or empty scenarios")
        if not self.operations or not set(self.operations) <= set(OPERATIONS):
            raise ValueError("unknown or empty operations")
        if not math.isfinite(self.fault_delay_ms) or not 0 <= self.fault_delay_ms <= 1000:
            raise ValueError("fault_delay_ms must be between 0 and 1000")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        for name in ("concurrency", "operations", "scenarios"):
            if len(getattr(self, name)) != len(set(getattr(self, name))):
                raise ValueError(f"{name} must not contain duplicates")


def new_sample() -> dict:
    return {**dict.fromkeys(COUNTERS, 0), "error": None, "mismatch": False,
            "cache": {k: dict(hits=0, misses=0, bypasses=0) for k in OPERATIONS[:3]}}


def increment(name: str) -> None:
    request = _REQUEST.get()
    if request and request.get("sample") is not None:
        sample = request["sample"]
        sample[name] = sample.get(name, 0) + 1


class CursorMeter:
    def __init__(self, cursor: Any):
        self.cursor = cursor

    def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        increment("mysql_queries")
        sql = query.strip().upper()
        if sql.startswith("SELECT"):
            increment("mysql_selects")
            if "INFORMATION_SCHEMA" in sql:
                increment("mysql_schema_selects")
        elif sql.startswith(("CREATE", "ALTER", "DROP", "TRUNCATE")):
            increment("mysql_ddl")
        return self.cursor.execute(query, *args, **kwargs)

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class ConnectionMeter:
    def __init__(self, connection: Any):
        self.connection = connection

    def cursor(self, *args, **kwargs):
        return CursorMeter(self.connection.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self.connection, name)


class NamespacedRedis:
    """Only application keys are prefixed; no global Redis commands are used."""
    def __init__(self, client: Any, prefix: str, fault_delay_ms: float | None = None):
        if not re.fullmatch(r"memory-bench:[a-f0-9]{12}:[A-Za-z0-9:_-]+:", prefix):
            raise ValueError("invalid benchmark Redis prefix")
        self.client, self.prefix, self.fault_delay_ms = client, prefix, fault_delay_ms

    def _call(self, command: str, key: str, *args, **kwargs):
        increment("redis_commands")
        try:
            if self.fault_delay_ms is not None:
                time.sleep(self.fault_delay_ms / 1000)
                raise TimeoutError("benchmark-injected Redis timeout")
            return getattr(self.client, command)(self.prefix + key, *args, **kwargs)
        except Exception:
            increment("redis_errors")
            raise

    def get(self, key):
        return self._call("get", key)

    def set(self, key, value, **kwargs):
        return self._call("set", key, value, **kwargs)

    def delete(self, key):
        return self._call("delete", key)

    def incr(self, key):
        return self._call("incr", key)

    def expire(self, key, seconds):
        return self._call("expire", key, seconds)


class MeasuredCache(redis_module.RedisCacheRepository):
    def __init__(self, client: Any | None, ttl: int):
        # None means true bypass here, not the production constructor fallback.
        self._client = client
        self.ttl = ttl

    def get_json(self, key: str):
        value = super().get_json(key)
        category = None
        if key.startswith("agent:memory:summary:"):
            category = "semantic" if ":semantic:" in key else "task"
            hit = isinstance(value, str)
        elif key.startswith("agent:memory:events:"):
            category = "events"
            hit = isinstance(value, list) and all(isinstance(item, dict) for item in value)
        request = _REQUEST.get()
        if category and request and request.get("sample") is not None:
            kind = "bypasses" if not self.enabled else "hits" if hit else "misses"
            request["sample"]["cache"][category][kind] += 1
        return value

    def set_json(self, key, value, ttl_seconds=None):
        return super().set_json(key, value, ttl_seconds=ttl_seconds or self.ttl)


def validate_database_name(database: str, business_database: str) -> None:
    if database.casefold() == business_database.casefold() or not re.fullmatch(r"memory_bench_[a-f0-9]{12}", database):
        raise ValueError("benchmark requires a NEW database named memory_bench_<12 lowercase hex digits>")


def canonical(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def frozen_clock(anchor: datetime):
    class DateMeta(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, datetime)

    class FrozenDateTime(datetime, metaclass=DateMeta):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)
    return FrozenDateTime


class MemoryBenchmark:
    def __init__(self, config: BenchmarkConfig, *, database: str | None = None,
                 mysql_host: str | None = None, mysql_port: int | None = None,
                 redis_host: str | None = None, redis_port: int | None = None):
        config.validate()
        self.config = config
        self.run_id = uuid4().hex[:12]
        self.database = database or f"memory_bench_{self.run_id}"
        validate_database_name(self.database, settings.mysql_database)
        self.settings = replace(settings, mysql_database=self.database,
                                mysql_host=mysql_host or settings.mysql_host,
                                mysql_port=mysql_port or settings.mysql_port,
                                redis_host=redis_host or settings.redis_host,
                                redis_port=redis_port or settings.redis_port)
        self.fixtures = build_sessions(config.sessions)
        self.anchor = datetime.now(timezone.utc).replace(microsecond=0)
        self.base_prefix = f"memory-bench:{self.run_id}:"
        self.original_connect = mysql._connect
        self.created_database = False
        self.control_client = None
        self.revisions: dict = {}
        self.checks: list = []
        self.report: dict = {}
        self.stage = "plan"

    def plan(self) -> dict:
        workloads = {}
        for scenario in self.config.scenarios:
            schedule = build_schedule(scenario, self.config.sessions, self.config.requests, self.config.seed)
            workloads[scenario] = {
                "reads_per_group": len(schedule), "update_batches": sum(s["update_before"] for s in schedule),
                "digest": workload_digest(self.fixtures, schedule),
                "unique_session_queries": len({(s["session_index"], s["query"]) for s in schedule}),
            }
        return {"database": self.database, "redis_prefix": self.base_prefix,
                "sessions": self.config.sessions, "messages_per_session": 12, "events_per_session": 20,
                "operations": list(self.config.operations), "concurrency": list(self.config.concurrency),
                "rounds": self.config.rounds, "workloads": workloads,
                "timed_reads": sum(w["reads_per_group"] for w in workloads.values()) * 2 *
                    len(self.config.operations) * len(self.config.concurrency) * self.config.rounds,
                "llm_calls": 0, "connects_to_services": False}

    def _connect(self, database=None):
        increment("mysql_connections")
        return ConnectionMeter(self.original_connect(database))

    def _cache(self):
        request = _REQUEST.get()
        if not request or request["mode"] == "A":
            return MeasuredCache(None, self.settings.redis_context_ttl_seconds)
        raw = redis_module._create_client()
        if raw is None:
            raise RuntimeError("real Redis client could not be constructed")
        request["clients"].append(raw)
        client = NamespacedRedis(raw, request["prefix"], request.get("fault_delay_ms"))
        return MeasuredCache(client, request.get("ttl", self.settings.redis_context_ttl_seconds))

    @contextmanager
    def _scope(self, mode, prefix, sample=None, **options):
        request = dict(mode=mode, prefix=prefix, sample=sample, clients=[], **options)
        token = _REQUEST.set(request)
        try:
            yield
        finally:
            _REQUEST.reset(token)
            for client in request["clients"]:
                try:
                    client.close()
                except Exception:
                    pass

    @contextmanager
    def environment(self):
        def forbid_llm(*args, **kwargs):
            raise AssertionError("LLM is forbidden in the memory benchmark")
        with ExitStack() as stack:
            stack.enter_context(patch.object(mysql, "settings", self.settings))
            stack.enter_context(patch.object(redis_module, "settings", self.settings))
            stack.enter_context(patch.object(mysql, "_connect", self._connect))
            stack.enter_context(patch.object(memory, "SummaryRepository", lambda: repositories.SummaryRepository(self._cache())))
            stack.enter_context(patch.object(memory, "_CONVERSATIONS", {}))
            stack.enter_context(patch.object(repositories, "datetime", frozen_clock(self.anchor)))
            for method in ("__init__", "chat", "json_chat", "tool_call"):
                stack.enter_context(patch.object(DeepSeekClient, method, forbid_llm))
            yield

    def preflight(self):
        """PING Redis before any SQL writes; CREATE fails if DB already exists."""
        self.stage = "redis_preflight"
        if not self.settings.redis_host:
            raise ValueError("REDIS_HOST is empty; B must use a real Redis server")
        self.control_client = redis_module._create_client()
        if self.control_client is None or not self.control_client.ping():
            raise RuntimeError("Redis preflight failed")
        import pymysql
        self.stage = "mysql_connect_and_create_new_database"
        # Bounded preflight; measured reads retain the application's connector.
        conn = pymysql.connect(host=self.settings.mysql_host, port=self.settings.mysql_port,
                               user=self.settings.mysql_user, password=self.settings.mysql_password,
                               connect_timeout=5, read_timeout=5, write_timeout=5,
                               autocommit=True, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            self.created_database = True
        finally:
            conn.close()
        mysql.ensure_schema()
        self.stage = "ready"

    def seed(self):
        if not self.created_database:
            raise RuntimeError("refusing to seed a database not created by this run")
        conn = self.original_connect(self.database)
        try:
            # Fixture setup is outside all read timers. Keep autocommit off
            # across the AUTO_INCREMENT DDL so the subsequent INSERTs share
            # one commit, instead of forcing an fsync for every fixture row.
            conn.autocommit(False)
            with conn.cursor() as cur:
                # Exact fixture sessions only, in a database created exclusively by this run.
                for fixture in self.fixtures:
                    cur.execute("DELETE FROM chat_sessions WHERE id = %s", (fixture["id"],))
                for table in ("chat_messages", "chat_events"):
                    cur.execute(f"ALTER TABLE {table} AUTO_INCREMENT = 1")
                for fixture in self.fixtures:
                    sid = fixture["id"]
                    cur.execute("INSERT INTO chat_sessions (id,title,summary,semantic_summary,task_summary) VALUES (%s,%s,%s,%s,%s)",
                                (sid, "Redis benchmark fixture", "", canonical(fixture["semantic"]), canonical(fixture["task"])))
                    for i, msg in enumerate(fixture["messages"]):
                        cur.execute("INSERT INTO chat_messages (session_id,role,content,evidence_sources,created_at) VALUES (%s,%s,%s,%s,%s)",
                                    (sid, msg["role"], msg["content"], "[]", self._timestamp(i)))
                    for i, event in enumerate(fixture["events"]):
                        cur.execute("INSERT INTO chat_events (session_id,event_type,content,importance,metadata,created_time) VALUES (%s,%s,%s,%s,%s,%s)",
                                    (sid, event["event_type"], event["content"], event["importance"], canonical(event["metadata"]), self._timestamp(i)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.revisions = dict.fromkeys((f["id"] for f in self.fixtures), 0)

    def _timestamp(self, index):
        return (self.anchor - timedelta(days=7) + timedelta(minutes=index)).replace(tzinfo=None)

    def read(self, operation, session_index, query):
        sid = self.fixtures[session_index]["id"]
        if operation == "memory":
            return memory.get_or_create_memory(sid)
        cache = self._cache()
        if operation == "semantic":
            return repositories.SummaryRepository(cache).get_semantic_summary(sid)
        if operation == "task":
            return repositories.SummaryRepository(cache).get_task_summary(sid)
        if operation == "events":
            return repositories.EventRepository(cache).get_important_events(sid, query=query, limit=10, min_importance=0.7)
        raise ValueError(f"unknown operation: {operation}")

    def update(self, session_index, mode, prefix, **options):
        fixture = self.fixtures[session_index]
        sid = fixture["id"]
        revision = self.revisions[sid] + 1
        with self._scope(mode, prefix, **options):
            repo = repositories.SummaryRepository(self._cache())
            repo.update_semantic_summary(sid, canonical({**fixture["semantic"], "revision": revision}))
            repo.update_task_summary(sid, canonical({**fixture["task"], "revision": revision}))
            repositories.EventRepository(self._cache()).create_event(
                sid, event_type="IMPORTANT_FACT", importance=1.0,
                content=f"{sid} pod redis mysql confirmed revision {revision}",
                metadata={"session_id": sid, "revision": revision})
        # Deterministic event time for identical recency ranking in A and B.
        conn = self.original_connect(self.database)
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE chat_events SET created_time=%s WHERE session_id=%s AND JSON_EXTRACT(metadata,'$.revision')=%s",
                            (self.anchor.replace(tzinfo=None), sid, revision))
        finally:
            conn.close()
        self.revisions[sid] = revision

    def expected(self, operation, item):
        with self._scope("A", self.base_prefix + "oracle:"):
            value = self.read(operation, item["session_index"], item["query"])
        sid = self.fixtures[item["session_index"]]["id"]
        revision = self.revisions[sid]
        # Independent fixture assertions in addition to cache-vs-MySQL equality.
        if operation in ("semantic", "task"):
            parsed = json.loads(value)
            assert parsed["session_id"] == sid and parsed["revision"] == revision
        elif operation == "events":
            assert value and all(e["session_id"] == sid for e in value)
            if revision:
                assert any(e["metadata"].get("revision") == revision for e in value)
        else:
            assert value.conversation_id == sid
            assert value.semantic_summary["revision"] == value.task_summary["revision"] == revision
            assert value.semantic_summary["session_id"] == value.task_summary["session_id"] == sid
            expected_messages = self.fixtures[item["session_index"]]["messages"][-memory.RECENT_MESSAGE_LIMIT:]
            assert [(m.role, m.content) for m in value.messages] == [(m["role"], m["content"]) for m in expected_messages]
            assert not value.operations and not value.artifacts
        return canonical(value)

    def measure(self, operation, item, mode, prefix, expected, **options):
        sample = new_sample()
        sample.update(index=item["index"], session_index=item["session_index"],
                      query_digest=hashlib.sha256(item["query"].encode()).hexdigest()[:16])
        with self._scope(mode, prefix, sample, **options):
            started = time.perf_counter()
            try:
                result = self.read(operation, item["session_index"], item["query"])
            except Exception as exc:
                sample["error"] = type(exc).__name__  # no credentials or raw SQL in reports
                result = None
            sample["latency_ms"] = (time.perf_counter() - started) * 1000
            if sample["error"] is None:
                actual = canonical(result)
                sample["mismatch"] = actual != expected
                if sample["mismatch"]:
                    sample["expected_digest"] = hashlib.sha256(expected.encode()).hexdigest()
                    sample["actual_digest"] = hashlib.sha256(actual.encode()).hexdigest()
                    sid = self.fixtures[item["session_index"]]["id"]
                    sample["expected_revision"] = self.revisions.get(sid)
        return sample

    def clear_prefix(self, prefix):
        if (not prefix.startswith(self.base_prefix) or prefix == self.base_prefix
                or not re.fullmatch(r"memory-bench:[a-f0-9]{12}:[A-Za-z0-9:_-]+:", prefix)):
            raise ValueError("refusing unscoped Redis cleanup")
        # Collect before deleting so a SCAN traversal is not perturbed by our deletion.
        keys = list(self.control_client.scan_iter(match=prefix + "*", count=500))
        for start in range(0, len(keys), 200):
            self.control_client.delete(*keys[start:start + 200])

    def group(self, operation, scenario, concurrency, round_index, mode, schedule):
        self.seed()
        prefix = f"{self.base_prefix}{operation}:{scenario}:{concurrency}:{round_index}:{mode}:"
        samples = []
        read_wall = 0.0
        update_wall = 0.0
        # Warm both SQL/client paths with the same reads; cold only clears Redis afterwards.
        initial = [{"index": i, "session_index": i, "query": "pod redis", "update_before": False}
                   for i in range(self.config.sessions)]
        # Concurrent reads within epochs; writes are barriers, not racing writes.
        epochs = []
        for item in schedule:
            if not epochs or item["update_before"]:
                epochs.append([])
            epochs[-1].append(item)
        try:
            for item in initial:
                with self._scope(mode, prefix):
                    self.read(operation, item["session_index"], item["query"])
            if scenario == "cold" and mode == "B":
                self.clear_prefix(prefix)
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(lambda _: time.perf_counter(), range(concurrency)))
                for epoch in epochs:
                    if epoch[0]["update_before"]:
                        start = time.perf_counter()
                        self.update(epoch[0]["session_index"], mode, prefix)
                        update_wall += time.perf_counter() - start
                    # Oracle reads are untimed and always bypass Redis. Deduplicate per epoch.
                    expected = {}
                    for item in epoch:
                        key = (item["session_index"], item["query"] if operation == "events" else "")
                        if key not in expected:
                            expected[key] = self.expected(operation, item)
                    if scenario == "warm":
                        # Refresh immediately after untimed oracle work so short TTLs
                        # do not expire solely while preparing expected values.
                        for session_index, query in expected:
                            with self._scope(mode, prefix):
                                self.read(operation, session_index, query)
                    def run_one(item):
                        key = (item["session_index"], item["query"] if operation == "events" else "")
                        return self.measure(operation, item, mode, prefix, expected[key])
                    start = time.perf_counter()
                    samples.extend(pool.map(run_one, epoch))
                    read_wall += time.perf_counter() - start
        finally:
            if mode == "B":
                self.clear_prefix(prefix)
        result = summarize(samples, read_wall)
        result.update(mode=mode, samples=samples, read_batch_wall_seconds=read_wall,
                      update_wall_seconds=update_wall,
                      update_batches=sum(i["update_before"] for i in schedule))
        return result

    def correctness_checks(self):
        self.seed()
        item = {"index": 0, "session_index": 0, "query": "pod redis"}
        prefix = self.base_prefix + "checks:"
        def check(name, operation, *, allow_redis_errors=False, require_hit=False, expected_value=None, **options):
            expected = self.expected(operation, item) if expected_value is None else expected_value
            sample = self.measure(operation, item, "B", prefix, expected, **options)
            passed = not sample["error"] and not sample["mismatch"]
            if not allow_redis_errors:
                passed = passed and sample["redis_errors"] == 0
            if require_hit:
                categories = ("semantic", "task") if operation == "memory" else (operation,)
                passed = passed and all(sample["cache"][category]["hits"] == 1 for category in categories)
            record = dict(name=name, operation=operation, passed=passed, **sample)
            self.checks.append(record)
            return record
        try:
            for operation in OPERATIONS:
                check("initial_read", operation)
            self.update(0, "B", prefix)
            for operation in OPERATIONS:
                check("update_then_read", operation)
            with self._scope("B", prefix):
                repositories.SummaryRepository(self._cache()).invalidate(self.fixtures[0]["id"])
                repositories.EventRepository(self._cache()).invalidate(self.fixtures[0]["id"])
            for operation in OPERATIONS:
                check("explicit_invalidation", operation)
            self.clear_prefix(prefix)
            # Confirm each cache interface immediately after filling it. Do not
            # spend an untimed full MySQL oracle read inside a one-second TTL,
            # or require a slow aggregate loader to finish before that TTL.
            for operation in OPERATIONS[:3]:
                expected = self.expected(operation, item)
                check("before_ttl_expiry", operation, ttl=1, expected_value=expected)
                check("confirmed_hit_before_ttl", operation, ttl=1, require_hit=True, expected_value=expected)
            time.sleep(1.2)  # excluded from latency; real Redis payload-key expiration
            for operation in OPERATIONS:
                result = check("after_ttl_expiry", operation)
                # Semantic/task may already have been refilled before the full memory read.
                if operation != "memory":
                    result["passed"] = result["passed"] and result["cache"][operation]["misses"] == 1
            for operation in OPERATIONS:
                result = check("injected_read_timeout", operation, allow_redis_errors=True,
                               fault_delay_ms=self.config.fault_delay_ms)
                result["passed"] = result["passed"] and result["redis_errors"] > 0 and result["mysql_queries"] > 0
            for operation in OPERATIONS:
                check("recovered_read", operation)
                check("confirmed_hit_after_recovery", operation, require_hit=True)
            # Test a real design risk: a DB write succeeds while Redis maintenance fails.
            self.update(0, "B", prefix, fault_delay_ms=self.config.fault_delay_ms)
            for operation in OPERATIONS:
                check("recovery_after_failed_cache_write", operation)
        finally:
            self.clear_prefix(prefix)

    def run(self, progress=print):
        report = {"kind": "real_mysql_redis_memory_reads", "plan": self.plan(), "runs": [], "checks": [],
                  "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                  "mysql_host": self.settings.mysql_host, "mysql_port": self.settings.mysql_port,
                                  "redis_host": self.settings.redis_host, "redis_port": self.settings.redis_port,
                                  "redis_db": self.settings.redis_db,
                                  "redis_ttl_seconds": self.settings.redis_context_ttl_seconds,
                                  "redis_socket_timeout_seconds": self.settings.redis_socket_timeout_seconds},
                  "method": {"llm_calls": 0, "mysql_schema_checks": "unchanged, included in timing/counts",
                             "client_lifecycle": "production-style new Redis client per repository; explicit close after timing",
                             "oracle": "uncached MySQL reads plus fixture assertions, outside timers",
                             "event_ranking_clock": self.anchor.isoformat(),
                             "writes": "serialized between read epochs; excluded from read metrics",
                             "throughput": "attempts / read-batch wall time, includes dispatch/validation/cleanup",
                             "fault": "client-injected timeouts, not actual network/server shutdown"}}
        self.report = report
        report["checks"] = self.checks  # retain completed checks even on interruption
        with self.environment():
            try:
                self.preflight()
                report["plan"]["connects_to_services"] = True
                import redis
                import pymysql
                report["environment"].update(redis_py=redis.__version__, pymysql=pymysql.__version__)
                for concurrency in self.config.concurrency:
                    for round_index in range(self.config.rounds):
                        for operation in self.config.operations:
                            for scenario in self.config.scenarios:
                                schedule = build_schedule(scenario, self.config.sessions, self.config.requests, self.config.seed)
                                pair = dict(operation=operation, scenario=scenario, concurrency=concurrency, round=round_index + 1)
                                order = ("A", "B") if round_index % 2 == 0 else ("B", "A")
                                for mode in order:
                                    self.stage = f"{operation}/{scenario}/c{concurrency}/round{round_index + 1}/{mode}"
                                    progress(f"{operation}/{scenario} c={concurrency} round={round_index + 1} {mode}")
                                    pair[mode] = self.group(operation, scenario, concurrency, round_index, mode, schedule)
                                pair["comparison"] = compare_groups(pair["A"], pair["B"])
                                report["runs"].append(pair)
                                progress("RESULT " + json.dumps({
                                    "operation": operation, "scenario": scenario,
                                    "concurrency": concurrency, "round": round_index + 1,
                                    "A_mean_ms": pair["A"]["latency_ms"]["mean"],
                                    "B_mean_ms": pair["B"]["latency_ms"]["mean"],
                                    "errors": pair["A"]["errors"] + pair["B"]["errors"],
                                    "mismatches": pair["A"]["mismatches"] + pair["B"]["mismatches"],
                                }))
                if self.config.run_checks:
                    self.stage = "correctness_checks"
                    progress("Running isolated TTL, invalidation and fault checks...")
                    self.correctness_checks()
                report["checks"] = self.checks
                report["completed"] = True
                report["passed"] = all(not group["errors"] and not group["mismatches"]
                                       and group["totals"]["redis_errors"] == 0
                                       for pair in report["runs"] for group in (pair["A"], pair["B"])) and all(c["passed"] for c in self.checks)
            finally:
                if self.control_client is not None:
                    self.control_client.close()
        report["database_retained"] = self.database
        return report
