#!/usr/bin/env python3
"""Run v0.7 core and SSE tests in a credential-free source-only temp copy."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from run_clean_offline_tests import ROOT, LAB, copy_source


def main() -> int:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
    except ImportError as exc:
        print(f"Missing local test dependency: {exc.name}; install requirements-progress.txt in a venv")
        return 2
    with tempfile.TemporaryDirectory(prefix="netsecops-v07-clean-") as folder:
        clean = Path(folder) / "netsecops-agent"
        clean.mkdir()
        copied = copy_source(clean)
        prohibited = (clean / ".env", clean / ".git", clean / LAB / "agent-runs",
                      clean / LAB / "experiments", clean / LAB / "clab-netsecops-ospf",
                      clean / LAB / "tests/fixtures/v04-failures/normal-acceptance.json")
        if any(path.exists() for path in prohibited):
            raise RuntimeError("Clean copy includes a private or generated path")
        minimal_env = {name: os.environ[name] for name in
                       ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME") if name in os.environ}
        minimal_env["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(clean / LAB / "tests"), "-v"],
            cwd=clean, env=minimal_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, check=False,
        )
        output = completed.stdout + completed.stderr
    count = re.search(r"Ran (\d+) tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    result = {
        "schema_version": 1, "version": "v0.7-progress",
        "tested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": sys.platform, "python_version": sys.version.split()[0],
        "source_files": copied, "tests_run": int(count.group(1)) if count else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "exit_code": completed.returncode, "status": "PASS" if completed.returncode == 0 else "FAIL",
    }
    directory = ROOT / "review-results" / ("v0.7-progress-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "unittest-output.txt").write_text(output, encoding="utf-8")
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Status: {result['status']}; Tests: {result['tests_run']}; skipped: {result['skipped']}")
    print(f"Result: {directory / 'result.json'}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
