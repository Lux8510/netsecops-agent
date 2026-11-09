#!/usr/bin/env python3
"""Run the v0.6 offline regression suite from a credential-free temp copy."""

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
    with tempfile.TemporaryDirectory(prefix="netsecops-v06-clean-") as folder:
        clean = Path(folder) / "netsecops-agent"
        clean.mkdir()
        copied = copy_source(clean)
        excluded = (clean / ".env", clean / ".git", clean / LAB / "agent-runs",
                    clean / LAB / "experiments", clean / LAB / "clab-netsecops-ospf",
                    clean / LAB / "tests/fixtures/v04-failures/normal-acceptance.json")
        if any(path.exists() for path in excluded):
            raise RuntimeError("Clean test copy includes excluded private or generated material")
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME")
               if key in os.environ}
        env["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(clean / LAB / "tests"), "-v"],
            cwd=clean, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, check=False,
        )
        output = completed.stdout + completed.stderr
    matched = re.search(r"Ran (\d+) tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    result = {
        "schema_version": 1, "version": "v0.6-demo", "tested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": sys.platform, "python_version": sys.version.split()[0], "source_files": copied,
        "tests_run": int(matched.group(1)) if matched else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "exit_code": completed.returncode, "status": "PASS" if completed.returncode == 0 else "FAIL",
    }
    directory = ROOT / "review-results" / ("v0.6-demo-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "unittest-output.txt").write_text(output, encoding="utf-8")
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Status: {result['status']}; Tests: {result['tests_run']}; skipped: {result['skipped']}")
    print(f"Result: {directory / 'result.json'}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
