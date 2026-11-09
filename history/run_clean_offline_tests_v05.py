#!/usr/bin/env python3
"""Run v0.5 offline tests from a credential-free, source-only temporary copy."""

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


VERSION = "v0.5-successful-recheck"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="netsecops-v05-clean-") as folder:
        clean = Path(folder) / "netsecops-agent"
        clean.mkdir()
        count = copy_source(clean)
        prohibited = (".env", ".git", "experiments", "agent-runs", "diagnostic-acceptance",
                      "clab-netsecops-ospf", ".venv", "venv")
        if any((clean / name).exists() or (clean / LAB / name).exists() for name in prohibited):
            raise RuntimeError("Clean copy contains an excluded credential or history path")
        minimal_env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME")
                       if key in os.environ}
        minimal_env["PYTHONNOUSERSITE"] = "1"
        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(clean / LAB / "tests"), "-v"]
        completed = subprocess.run(command, cwd=clean, env=minimal_env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        output = completed.stdout + completed.stderr
    run_count = re.search(r"Ran (\d+) tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    result = {"schema_version": 1, "version": VERSION,
              "tested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "execution_platform": sys.platform, "python_version": sys.version.split()[0],
              "source_only_files": count, "excluded_paths_absent": True,
              "tests_run": int(run_count.group(1)) if run_count else None,
              "skipped": int(skipped.group(1)) if skipped else 0,
              "exit_code": completed.returncode,
              "status": "PASS" if completed.returncode == 0 else "FAIL"}
    output_dir = ROOT / "review-results" / ("v0.5-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "unittest-output.txt").write_text(output, encoding="utf-8")
    (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Status: {result['status']}")
    print(f"Tests: {result['tests_run']}; skipped: {result['skipped']}")
    print(f"Clean-copy source files: {count}")
    print(f"Result: {output_dir / 'result.json'}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
