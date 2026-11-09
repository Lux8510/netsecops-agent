#!/usr/bin/env python3
"""Single offline entry point: core agent suite (labs) + v1 topology suite.

Both suites run from credential-free temporary copies. No model calls, no
Docker, no Containerlab, no network access. Exit code is non-zero if either
suite fails.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
RESULTS = ROOT / "review-results"


def _minimal_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME")
           if key in os.environ}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run_core_suite(folder: Path) -> dict:
    from run_clean_offline_tests import copy_source

    clean = folder / "core"
    clean.mkdir()
    copied = copy_source(clean)
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(clean / LAB / "tests"), "-v"],
        cwd=clean, env=_minimal_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, check=False,
    )
    output = completed.stdout + completed.stderr
    ran = re.search(r"Ran (\d+) tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    return {
        "suite": "core-agent",
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "tests_run": int(ran.group(1)) if ran else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "source_files": copied,
        "output": output,
        "exit_code": completed.returncode,
    }


def run_v1_suite() -> dict:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "v1/run_offline_tests.py")],
        cwd=ROOT, env=_minimal_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    output = completed.stdout + completed.stderr
    ran = re.search(r"Ran (\d+) tests", output)
    return {
        "suite": "v1-topology",
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "tests_run": int(ran.group(1)) if ran else None,
        "skipped": 0,
        "output": output,
        "exit_code": completed.returncode,
    }


def main() -> int:
    try:
        import fastapi  # noqa: F401
    except ImportError as exc:
        print(f"Missing optional dependency: {exc.name}. Install requirements-progress.txt "
              f"in a Python 3.12 virtualenv to run the event-service tests.")
        return 2

    started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="netsecops-offline-") as folder:
        suites = [run_core_suite(Path(folder)), run_v1_suite()]

    total = sum(item["tests_run"] or 0 for item in suites)
    skipped = sum(item["skipped"] for item in suites)
    failed = [item["suite"] for item in suites if item["status"] != "PASS"]
    report = {
        "schema_version": 1,
        "suite": "offline-all",
        "tested_at_utc": started.isoformat(timespec="seconds"),
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "status": "PASS" if not failed else "FAIL",
        "tests_run": total,
        "skipped": skipped,
        "failed_suites": failed,
        "suites": [{key: value for key, value in item.items() if key != "output"} for item in suites],
    }

    RESULTS.mkdir(exist_ok=True)
    directory = RESULTS / ("offline-all-" + started.strftime("%Y%m%dT%H%M%SZ"))
    directory.mkdir()
    for item in suites:
        (directory / f"{item['suite']}.log").write_text(item["output"], encoding="utf-8")
    (directory / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for item in suites:
        print(f"[{item['status']}] {item['suite']}: {item['tests_run']} tests, {item['skipped']} skipped")
    print(f"Total: {total} tests, {skipped} skipped; status: {report['status']}")
    print(f"Report: {directory / 'result.json'}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
