"""Pure, deterministic synthetic inputs for the memory benchmark.

This module deliberately has no configuration, clock, storage or service
dependencies. The runner owns persistence, recent-message limits and update
barriers; ``update_before`` marks the read whose session must be updated.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any


def _integer(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def build_sessions(
    count: int,
    messages: int = 12,
    events: int = 20,
) -> list[dict[str, Any]]:
    """Build isolated sessions with summaries and chronological message/events.

    ``count`` must be positive; message and event counts may be zero. Summary
    payloads are roughly 1 KB of synthetic Chinese diagnostic context each.
    The runner reads the last six messages and starts without operations or
    artifacts; these are intentionally absent from the session envelope.
    """
    _integer("count", count, minimum=1)
    _integer("messages", messages, minimum=0)
    _integer("events", events, minimum=0)
    topics = ("mysql", "redis", "pod")
    event_types = ("TOOL_SUCCESS", "TOOL_FAILURE", "TASK_PROGRESS")
    result: list[dict[str, Any]] = []
    for session_index in range(count):
        session_id = f"bench-session-{session_index:04d}"
        semantic = {
            "session_id": session_id,
            "revision": 0,
            "user_preferences": {
                "language": "中文",
                "answer_style": "先给结论，再解释观察到的现象和证据。",
            },
            "environment": {
                "namespace": f"benchmark-{session_index:04d}",
                "database": "mysql",
                "cache": "redis",
                "runtime": "Kubernetes pod",
            },
            "summary": (
                f"当前合成会话为 {session_id}，用于验证多会话记忆读取和缓存隔离。"
                "用户正在排查服务启动后偶发的响应延迟，重点关注 mysql 连接池、"
                "redis 缓存命中率以及 pod 就绪状态。数据库保存完整会话事实，"
                "缓存用于减少重复读取。每项证据都需要关联当前会话，不能混用其他会话内容。"
                "用户习惯先查看应用日志中的请求编号，再比较数据库查询与缓存访问的耗时，"
                "并结合容器重启次数判断问题出现的时间范围。回复应保留关键字段及单位，"
                "将已确认的观察与后续检查计划写清楚。所有资源名称和诊断信息均为合成数据。"
            ),
        }
        task = {
            "session_id": session_id,
            "revision": 0,
            "current_goal": "核对 mysql、redis 和 pod 的状态，并验证当前会话记忆读取一致性。",
            "completed_work": [
                "已整理连接池状态及查询耗时样本。",
                "已记录缓存命中与容器就绪检查的观察结果。",
            ],
            "current_issue": "重复读取需要返回当前版本摘要，并保持事件和消息的会话边界。",
            "next_steps": ["比较缓存前后的读取耗时。", "更新摘要和事件后立即读取并核对版本。"],
            "summary": (
                f"任务属于 {session_id}，目前处于基准数据准备阶段。"
                "首先读取语义摘要与任务摘要，然后读取最近六条消息及符合查询条件的重要事件。"
                "mysql 中保存摘要和事件的持久化记录；redis 保存可失效的读取结果。"
                "pod 的日志检查用于提供与当前问题相关的事件背景。"
                "每轮更新将同时增加两个摘要的版本号并添加一条事件，随后读取必须看到新版本。"
                "尚未执行外部命令，也未生成文件产物；耗时比较应保持数据和请求顺序一致。"
            ),
        }
        session_messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    f"[{session_id}] 消息 {index:04d}："
                    f"核对 {topics[index % len(topics)]} 的诊断记录，"
                    "关联本会话的请求耗时、连接状态与检查结果。"
                ),
            }
            for index in range(messages)
        ]
        session_events = [
            {
                "event_type": event_types[index % len(event_types)],
                "content": (
                    f"[{session_id}] 事件 {index:04d}："
                    f"检查 {topics[index % len(topics)]} 的运行状态；"
                    "已记录连接响应、请求耗时及后续检查建议。"
                ),
                "importance": round(0.5 + 0.05 * (index % 11), 2),
                "metadata": {
                    "session_id": session_id,
                    "event_index": index,
                    "topic": topics[index % len(topics)],
                },
            }
            for index in range(events)
        ]
        result.append(
            {
                "id": session_id,
                "semantic": semantic,
                "task": task,
                "messages": session_messages,
                "events": session_events,
            }
        )
    return result


def build_schedule(
    scenario: str,
    sessions: int,
    requests: int,
    seed: int = 20260908,
) -> list[dict[str, Any]]:
    """Plan reads without executing them or mutating the generated sessions.

    Cold reads visit every session once, ignoring the positive ``requests``
    count. Warm reads cycle over the first max(1, sessions // 5) sessions.
    Mixed reads use about 80% hot sessions and 70% repeated queries, with
    rounded counts shuffled by a local RNG. A single session is always valid.
    Only mixed reads have update markers at indexes 20, 40, and so on.
    """
    if not isinstance(scenario, str):
        raise TypeError("scenario must be a string")
    if scenario not in {"cold", "warm", "mixed"}:
        raise ValueError("scenario must be cold, warm or mixed")
    _integer("sessions", sessions, minimum=1)
    _integer("requests", requests, minimum=1)
    _integer("seed", seed, minimum=0)
    hotspot_count = max(1, sessions // 5)
    if scenario == "cold":
        session_indexes = list(range(sessions))
        repeated_queries = [True] * sessions
    elif scenario == "warm":
        session_indexes = [index % hotspot_count for index in range(requests)]
        repeated_queries = [True] * requests
    else:
        rng = random.Random(seed)
        hot_reads = (requests * 8 + 5) // 10
        hot_flags = [True] * hot_reads + [False] * (requests - hot_reads)
        rng.shuffle(hot_flags)
        session_indexes = [
            rng.randrange(hotspot_count)
            if is_hot or hotspot_count == sessions
            else rng.randrange(hotspot_count, sessions)
            for is_hot in hot_flags
        ]
        repeated_count = (requests * 7 + 5) // 10
        repeated_queries = [True] * repeated_count + [False] * (requests - repeated_count)
        rng.shuffle(repeated_queries)
    return [
        {
            "index": index,
            "session_index": session_index,
            "query": "pod redis" if repeated else f"pod redis query-{index:04d}",
            "update_before": scenario == "mixed" and index > 0 and index % 20 == 0,
        }
        for index, (session_index, repeated) in enumerate(zip(session_indexes, repeated_queries))
    ]


def workload_digest(sessions: list, schedule: list) -> str:
    """SHA-256 of canonical JSON; dictionary insertion order is irrelevant."""
    if not isinstance(sessions, list) or not isinstance(schedule, list):
        raise TypeError("sessions and schedule must be lists")
    payload = json.dumps(
        {"sessions": sessions, "schedule": schedule},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
