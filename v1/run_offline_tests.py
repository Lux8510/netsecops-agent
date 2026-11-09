#!/usr/bin/env python3
"""Run v1 phase-one tests in a temporary copy containing only the v1 directory."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="netsecops-v1-offline-") as temp:
        target_root = Path(temp)
        shutil.copytree(ROOT / "v1", target_root / "v1",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "results"))
        if (target_root / ".env").exists() or (target_root / "labs").exists():
            raise RuntimeError("v1 clean copy unexpectedly includes credentials or historical labs")
        env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "HOME", "TEMP", "TMP")
               if key in os.environ}
        env["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "v1/tests", "-v"],
            cwd=target_root, env=env, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=90, check=False,
        )
        print(result.stdout, end="")
        print(result.stderr, end="")
        print(f"v1 clean-copy status: {'PASS' if result.returncode == 0 else 'FAIL'}")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
