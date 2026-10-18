#!/usr/bin/env python3
"""Independent v0.2 source manifest; never rewrites the historical Stage 5 freeze."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from collect_evidence import LAB_DIR


MANIFEST = LAB_DIR / "reliability-v0.2.json"
SOURCE_FILES = (
    "collect_evidence.py", "agent_tools.py", "agent_loop.py", "deepseek_client.py",
    "diagnose_offline.py", "judge_agent_trace.py", "run_fault_scenario.py",
    "run_stage5_r2.py", "run_stage5_acceptance.py", "reliability_freeze.py",
    "../../agent-settings.json",
)
HISTORICAL = (
    "stage5-freeze.json",
    "agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha((LAB_DIR / name).resolve()) for name in SOURCE_FILES}


def historical_hashes() -> dict[str, str]:
    return {name: sha(LAB_DIR / name) for name in HISTORICAL}


def test_hashes() -> dict[str, str]:
    paths = [*LAB_DIR.glob("tests/*.py"), *LAB_DIR.glob("tests/fixtures/**/*")]
    return {path.relative_to(LAB_DIR).as_posix(): sha(path) for path in sorted(paths) if path.is_file()}


def create() -> dict:
    if MANIFEST.exists():
        raise RuntimeError("v0.2 manifest already exists; refuse overwrite")
    results = sorted((LAB_DIR.parents[1] / "review-results").glob("v0.2-offline-*/result.json"))
    if not results:
        raise RuntimeError("Run the clean-copy offline tests before freezing v0.2")
    result = json.loads(results[-1].read_text(encoding="utf-8"))
    if result.get("status") != "PASS":
        raise RuntimeError("Latest clean-copy offline test result is not PASS")
    value = {"schema_version": 1, "version": "v0.2-reliability",
             "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "purpose": "offline reliability repair after immutable v0.1 acceptance",
             "source_sha256": source_hashes(), "tests_sha256": test_hashes(),
             "historical_sha256": historical_hashes(),
             "offline_validation": {"result": str(results[-1].relative_to(LAB_DIR.parents[1]).as_posix()),
                                    "sha256": sha(results[-1]), "tests_run": result.get("tests_run"),
                                    "skipped": result.get("skipped"),
                                    "wsl_sigterm_test": result.get("wsl_sigterm_test")},
             "validation_scope": "offline tests only; no v0.2 real-model or live-fault acceptance yet"}
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def verify() -> dict:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = (LAB_DIR.parents[1] / value.get("offline_validation", {}).get("result", ""))
    if (value.get("version") != "v0.2-reliability" or value.get("source_sha256") != source_hashes() or
            value.get("tests_sha256") != test_hashes() or value.get("historical_sha256") != historical_hashes() or
            not result.is_file() or value.get("offline_validation", {}).get("sha256") != sha(result)):
        raise RuntimeError("v0.2 source or historical reference changed; create a new manifest instead")
    return value


if __name__ == "__main__":
    manifest = create()
    print(f"Version: {manifest['version']}")
    print(f"Manifest: {MANIFEST}")
