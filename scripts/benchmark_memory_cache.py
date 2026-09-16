"""CLI for the isolated, no-LLM memory read benchmark (plan-only by default)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.memory_benchmark import BenchmarkConfig, MemoryBenchmark, OPERATIONS, SCENARIOS


def markdown_report(report: dict) -> str:
    def number(value):
        return "N/A" if value is None else f"{value:.3f}"
    lines = ["# 记忆读取 A/B 测评", "",
             f"状态：{'完成，所执行检查通过' if report.get('passed') else '存在失败/数据不一致，先检查明细'}。不调用 LLM。", "",
             f"专用数据库（保留）：`{report['plan']['database']}`", "",
             "A = 真正绕过 Redis；B = 真实 Redis。以下为各轮实测，不跨场景合并分位数。", "",
             "| 接口 / 场景 | 并发 | 轮次 | 组 | 读取数 | Mean ms | P50 ms | P95 ms | SQL/次 | 连接/次 | RPS | 错误/不一致 |",
             "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for pair in report["runs"]:
        for mode in ("A", "B"):
            group = pair[mode]
            lines.append(f"| {pair['operation']} / {pair['scenario']} | {pair['concurrency']} | {pair['round']} | {mode} | "
                         f"{group['requests']} | {number(group['latency_ms']['mean'])} | {number(group['latency_ms']['p50'])} | "
                         f"{number(group['latency_ms']['p95'])} | {number(group['per_request']['mysql_queries'])} | "
                         f"{number(group['per_request']['mysql_connections'])} | {number(group['throughput_rps'])} | "
                         f"{group['errors']} / {group['mismatches']} |")
    lines += ["", "## 业务命中率与相对收益", "",
              "版本键不算业务命中；旁路的命中率为 N/A。加速比 A/B > 1 表示 B 更快。", "",
              "| 接口 / 场景 | 并发 / 轮次 | B semantic | B task | B events | Mean 加速比 | P95 加速比 | SQL 减少比例 |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for pair in report["runs"]:
        group = pair["B"]
        comparison = pair["comparison"]
        rates = " | ".join(number(group["cache"][key]["hit_rate"]) for key in ("semantic", "task", "events"))
        lines.append(f"| {pair['operation']} / {pair['scenario']} | {pair['concurrency']} / {pair['round']} | {rates} | "
                     f"{number(comparison['mean_speedup'])} | {number(comparison['p95_speedup'])} | "
                     f"{number(comparison['mysql_query_reduction'])} |")
    lines += ["", "## 故障与一致性检查", ""]
    if not report["checks"]:
        lines.append("未执行故障与一致性专项检查（不代表这些检查通过）。")
    for check in report["checks"]:
        lines.append(f"- {'PASS' if check['passed'] else 'FAIL'} `{check['name']}` / `{check['operation']}`："
                     f"{number(check['latency_ms'])} ms，Redis 异常 {check['redis_errors']}，"
                     f"错误 {check['error']}，结果不一致 {check['mismatch']}。")
    lines += ["", "## 解释限制", "",
              "- 冷缓存每个会话只读一次；MySQL 已预热，不是磁盘冷启动。",
              "- 混合负载为合成数据：80% 热点会话、70% 固定问题；每 20 次读后更新一次目标会话。",
              "- 写入位于并发读取批次之间，读指标不含写入耗时；不是读写竞争测试。",
              "- 原实现每次回源的表结构检查、连接建立均计入；mysql_schema_selects 和 mysql_ddl 单独记录。",
              "- 完整 memory 读取不包含重要事件，消息/操作/artifact 仍读 MySQL；操作和 artifact 测试表为空。",
              "- 故障为测试客户端注入超时，不代表真实网络故障等待时长。",
              "- recovery_after_failed_cache_write 失败表示缓存维护失败后可能返回旧数据，不能只引用速度而忽略它。",
              "- 原始逐请求数据、版本、TTL、数据库/Redis 地址和 workload digest 见配套 JSON。",
              "- 本报告不衡量回答质量、LLM Token 或完整 Agent 响应时间。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", action="store_true", help="Create a NEW dedicated DB and run real MySQL/Redis tests")
    action.add_argument("--plan", action="store_true", help="Only print the plan; no connections (default)")
    parser.add_argument("--sessions", type=int, default=50)
    parser.add_argument("--requests", type=int, default=1000, help="Reads per warm/mixed group; cold always reads each session once")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", default="1,5,10")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--operations", default=",".join(OPERATIONS))
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--fault-delay-ms", type=float, default=20)
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--database", help="Must be a NEW memory_bench_<12 lowercase hex digits> database")
    parser.add_argument("--mysql-host")
    parser.add_argument("--mysql-port", type=int)
    parser.add_argument("--redis-host")
    parser.add_argument("--redis-port", type=int)
    parser.add_argument("--output", help="New JSON report path; also writes same-name .md. Existing reports are never overwritten")
    args = parser.parse_args(argv)
    try:
        config = BenchmarkConfig(sessions=args.sessions, requests=args.requests, rounds=args.rounds,
                                 concurrency=tuple(int(x.strip()) for x in args.concurrency.split(",")),
                                 scenarios=tuple(x.strip() for x in args.scenarios.split(",")),
                                 operations=tuple(x.strip() for x in args.operations.split(",")),
                                 seed=args.seed, fault_delay_ms=args.fault_delay_ms, run_checks=not args.skip_checks)
        bench = MemoryBenchmark(config, database=args.database, mysql_host=args.mysql_host,
                                mysql_port=args.mysql_port, redis_host=args.redis_host, redis_port=args.redis_port)
        print(json.dumps(bench.plan(), ensure_ascii=False, indent=2))
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    if not args.run:
        print("PLAN ONLY: no service connections, no fixtures written, no LLM calls.")
        return 0
    output = Path(args.output or f"reports/memory_benchmark_{bench.run_id}.json")
    markdown = output.with_suffix(".md")
    if output.suffix.lower() != ".json" or output.exists() or markdown.exists():
        parser.error("--output must be a new .json path, with no existing same-name .md file")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = bench.run(progress=lambda line: print(line, flush=True))
    except (Exception, KeyboardInterrupt) as exc:
        failure = {**bench.report, "completed": False, "passed": False, "error_type": type(exc).__name__, "stage": bench.stage, "plan": bench.plan(),
                   "database_created": bench.created_database,
                   "note": "No passwords/raw SQL are logged. Check service connectivity and CREATE DATABASE privileges. "
                           "Any newly created test database is retained; production data was not selected as a fixture target."}
        with output.open("x", encoding="utf-8") as handle:
            json.dump(failure, handle, ensure_ascii=False, indent=2)
        print(f"Benchmark interrupted at {bench.stage} ({type(exc).__name__}); diagnostic report: {output}", file=sys.stderr)
        return 1
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with markdown.open("x", encoding="utf-8") as handle:
        handle.write(markdown_report(report))
    print(f"JSON: {output}\nMarkdown: {markdown}\nRetained test DB: {bench.database}")
    if not report["passed"]:
        print("Measurements saved. Some checks failed; inspect consistency and Redis-error details before quoting performance.")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
