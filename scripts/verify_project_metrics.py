"""Run deterministic project metric verification.

Usage:
    .venv\\Scripts\\python.exe scripts\\verify_project_metrics.py
    .venv\\Scripts\\python.exe scripts\\verify_project_metrics.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.metrics import collect_project_metrics, format_project_metrics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify deterministic project metrics.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args()

    report = collect_project_metrics()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_project_metrics(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
