from __future__ import annotations

import copy
import json
import random

import pytest

from app.memory_benchmark_workload import build_schedule, build_sessions, workload_digest


def test_sessions_have_complete_isolated_payloads() -> None:
    sessions = build_sessions(3)

    assert [session["id"] for session in sessions] == [
        "bench-session-0000", "bench-session-0001", "bench-session-0002"
    ]
    for session in sessions:
        assert set(session) == {"id", "semantic", "task", "messages", "events"}
        for summary_name in ("semantic", "task"):
            summary = session[summary_name]
            assert summary["session_id"] == session["id"]
            assert summary["revision"] == 0
            assert 600 <= len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) <= 2500
        assert len(session["messages"]) == 12
        assert {message["role"] for message in session["messages"]} == {"user", "assistant"}
        assert all(session["id"] in message["content"] for message in session["messages"])
        assert len(session["events"]) == 20
        assert {event["metadata"]["topic"] for event in session["events"]} == {"mysql", "redis", "pod"}
        assert len({event["importance"] for event in session["events"]}) > 1
        for index, event in enumerate(session["events"]):
            assert set(event) == {"event_type", "content", "importance", "metadata"}
            assert event["metadata"]["event_index"] == index
            assert event["metadata"]["session_id"] == session["id"]
            assert session["id"] in event["content"]
            assert 0.5 <= event["importance"] <= 1.0

    sessions[0]["semantic"]["environment"]["database"] = "changed"
    assert sessions[1]["semantic"]["environment"]["database"] == "mysql"


def test_message_and_event_counts_can_be_customized_or_empty() -> None:
    session = build_sessions(1, messages=6, events=3)[0]
    assert len(session["messages"]) == 6
    assert len(session["events"]) == 3
    empty = build_sessions(1, messages=0, events=0)[0]
    assert empty["messages"] == []
    assert empty["events"] == []


def test_cold_reads_each_session_once_and_ignores_request_count() -> None:
    schedule = build_schedule("cold", sessions=7, requests=100)

    assert len(schedule) == 7
    assert [item["session_index"] for item in schedule] == list(range(7))
    assert [item["index"] for item in schedule] == list(range(7))
    assert all(item["query"] == "pod redis" for item in schedule)
    assert not any(item["update_before"] for item in schedule)


def test_warm_rotates_hot_sessions_without_updates() -> None:
    schedule = build_schedule("warm", sessions=10, requests=45)

    assert len(schedule) == 45
    assert [item["session_index"] for item in schedule[:6]] == [0, 1, 0, 1, 0, 1]
    assert {item["session_index"] for item in schedule} == {0, 1}
    assert all(item["query"] == "pod redis" for item in schedule)
    assert not any(item["update_before"] for item in schedule)


def test_mixed_covers_hot_cold_queries_and_periodic_update_targets() -> None:
    schedule = build_schedule("mixed", sessions=10, requests=100)

    assert len(schedule) == 100
    assert sum(item["session_index"] < 2 for item in schedule) == 80
    assert sum(item["query"] == "pod redis" for item in schedule) == 70
    unique_queries = [item["query"] for item in schedule if item["query"] != "pod redis"]
    assert len(set(unique_queries)) == 30
    assert all(query.startswith("pod redis query-") for query in unique_queries)
    assert [item["index"] for item in schedule if item["update_before"]] == [20, 40, 60, 80]
    for index, item in enumerate(schedule):
        assert set(item) == {"index", "session_index", "query", "update_before"}
        assert item["index"] == index
        assert 0 <= item["session_index"] < 10
        assert isinstance(item["update_before"], bool)


@pytest.mark.parametrize("scenario", ["cold", "warm", "mixed"])
@pytest.mark.parametrize("session_count", [1, 2, 4, 6])
def test_small_session_pools_never_select_out_of_bounds(scenario: str, session_count: int) -> None:
    schedule = build_schedule(scenario, sessions=session_count, requests=41)
    assert schedule
    assert all(0 <= item["session_index"] < session_count for item in schedule)


def test_deterministic_generation_and_seed_variation_do_not_change_global_rng() -> None:
    global_state = random.getstate()
    first_sessions = build_sessions(10)
    first_schedule = build_schedule("mixed", 10, 100, seed=42)

    assert first_sessions == build_sessions(10)
    assert first_schedule == build_schedule("mixed", 10, 100, seed=42)
    assert first_schedule != build_schedule("mixed", 10, 100, seed=43)
    assert random.getstate() == global_state
    assert workload_digest(first_sessions, first_schedule) == workload_digest(
        build_sessions(10), build_schedule("mixed", 10, 100, seed=42)
    )


def test_digest_is_order_stable_and_sensitive_to_workload_changes() -> None:
    sessions = build_sessions(1)
    schedule = build_schedule("mixed", 1, 41)
    original_sessions = copy.deepcopy(sessions)
    original_schedule = copy.deepcopy(schedule)
    digest = workload_digest(sessions, schedule)

    assert len(digest) == 64
    assert int(digest, 16) >= 0
    reordered = [dict(reversed(list(session.items()))) for session in sessions]
    assert workload_digest(reordered, schedule) == digest
    assert sessions == original_sessions
    assert schedule == original_schedule
    sessions[0]["semantic"]["revision"] = 1
    assert workload_digest(sessions, schedule) != digest
    schedule[0]["query"] = "mysql"
    assert workload_digest(original_sessions, schedule) != digest


@pytest.mark.parametrize("value", [True, False, 1.5, "2", None])
def test_non_integer_counts_are_rejected(value) -> None:
    with pytest.raises(TypeError):
        build_sessions(value)
    with pytest.raises(TypeError):
        build_sessions(1, messages=value)
    with pytest.raises(TypeError):
        build_sessions(1, events=value)
    with pytest.raises(TypeError):
        build_schedule("mixed", value, 1)
    with pytest.raises(TypeError):
        build_schedule("mixed", 1, value)
    with pytest.raises(TypeError):
        build_schedule("mixed", 1, 1, seed=value)


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_session_or_request_counts_are_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        build_sessions(value)
    with pytest.raises(ValueError):
        build_schedule("mixed", value, 1)
    with pytest.raises(ValueError):
        build_schedule("cold", 1, value)


def test_negative_optional_counts_and_invalid_scenarios_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_sessions(1, messages=-1)
    with pytest.raises(ValueError):
        build_sessions(1, events=-1)
    with pytest.raises(ValueError):
        build_schedule("mixed", 1, 1, seed=-1)
    with pytest.raises(ValueError):
        build_schedule("unknown", 1, 1)
    with pytest.raises(TypeError):
        build_schedule(None, 1, 1)
    with pytest.raises(TypeError):
        workload_digest({}, [])
    with pytest.raises(TypeError):
        workload_digest([], {})
