#!/usr/bin/env python3
"""Run unittest from a temporary source-only copy, without keys or lab history."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
EXCLUDED = (".env", ".git", "experiments", "agent-runs", "diagnostic-acceptance",
            "clab-netsecops-ospf", "__pycache__", ".venv", "venv")


def copy_source(destination: Path) -> int:
    paths = [ROOT / "agent-settings.json", ROOT / ".env.example", ROOT / "README.md"]
    lab_dir = ROOT / LAB
    paths.extend(lab_dir.glob("*.py"))
    paths.extend([lab_dir / "topology.clab.yml", lab_dir / "deploy-and-verify.sh"])
    paths.extend((lab_dir / "configs").rglob("frr.conf"))
    paths.extend((lab_dir / "tests").glob("*.py"))
    paths.extend((lab_dir / "tests/fixtures").rglob("*"))
    # Keep the historical extract untouched locally, but do not copy its
    # personal workstation paths into a redistributable clean test tree.
    private_extract = lab_dir / "tests/fixtures/v04-failures/normal-acceptance.json"
    paths = [path for path in paths if path != private_extract]
    files = sorted({path for path in paths if path.is_file()})
    for path in files:
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts):
            raise RuntimeError(f"Excluded path selected: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return len(files)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="netsecops-v02-clean-") as folder:
        clean = Path(folder) / "netsecops-agent"
        clean.mkdir()
        count = copy_source(clean)
        for prohibited in (clean / ".env", clean / ".git", clean / LAB / "experiments",
                           clean / LAB / "agent-runs", clean / LAB / "diagnostic-acceptance",
                           clean / LAB / "clab-netsecops-ospf"):
            if prohibited.exists():
                raise RuntimeError("Clean copy contains an excluded directory or credential file")
        minimal_env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME")
                       if key in os.environ}
        minimal_env["PYTHONNOUSERSITE"] = "1"
        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(clean / LAB / "tests"), "-v"]
        completed = subprocess.run(command, cwd=clean, env=minimal_env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        output = completed.stdout + completed.stderr
    run_count = re.search(r"Ran (\d+) tests", output)
    skipped = re.search(r"skipped=(\d+)", output)
    result = {"schema_version": 1, "version": "v0.2-reliability",
              "tested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "execution_platform": sys.platform, "python_version": sys.version.split()[0],
              "source_only_files": count, "excluded_paths_absent": True,
              "tests_run": int(run_count.group(1)) if run_count else None,
              "skipped": int(skipped.group(1)) if skipped else 0,
              "exit_code": completed.returncode,
              "status": "PASS" if completed.returncode == 0 else "FAIL",
              "wsl_sigterm_test": "NOT_RUN" if sys.platform != "linux" else "SEE_TEST_OUTPUT"}
    output_dir = ROOT / "review-results" / ("v0.2-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
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
