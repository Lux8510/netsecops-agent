#!/usr/bin/env python3
"""Independent v0.3 manifest; never rewrites the v0.1 or v0.2 freeze records."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from collect_evidence import LAB_DIR


ROOT = LAB_DIR.parents[1]
MANIFEST = LAB_DIR / "reliability-v0.3.json"
SOURCE_FILES = (
    "collect_evidence.py", "agent_tools.py", "agent_loop.py", "deepseek_client.py",
    "diagnose_offline.py", "judge_agent_trace.py", "run_fault_scenario.py",
    "run_stage5_r2.py", "run_stage5_acceptance.py", "reliability_freeze_v03.py",
    "../../agent-settings.json", "../../run_clean_offline_tests.py",
    "../../run_clean_offline_tests_v03.py",
)
HISTORICAL = (
    "stage5-freeze.json", "reliability-v0.2.json",
    "agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.json",
    "../../review-results/v0.2-offline-20261014T015728Z/result.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha((LAB_DIR / name).resolve()) for name in SOURCE_FILES}


def historical_hashes() -> dict[str, str]:
    return {name: sha((LAB_DIR / name).resolve()) for name in HISTORICAL}


def test_hashes() -> dict[str, str]:
    paths = [*LAB_DIR.glob("tests/*.py"), *LAB_DIR.glob("tests/fixtures/**/*")]
    return {path.relative_to(LAB_DIR).as_posix(): sha(path) for path in sorted(paths) if path.is_file()}


def create() -> dict:
    if MANIFEST.exists():
        raise RuntimeError("v0.3 manifest already exists; refuse overwrite")
    results = sorted((ROOT / "review-results").glob("v0.3-offline-*/result.json"))
    if not results:
        raise RuntimeError("Run the v0.3 clean-copy offline tests in WSL before freezing")
    result = json.loads(results[-1].read_text(encoding="utf-8"))
    if (result.get("version") != "v0.3-review-repair" or result.get("status") != "PASS" or
            result.get("execution_platform") != "linux" or result.get("tests_run") != 57 or
            result.get("skipped") != 0):
        raise RuntimeError("Latest v0.3 clean-copy result is not a full WSL 57/0 PASS")
    value = {"schema_version": 1, "version": "v0.3-review-repair",
             "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "purpose": "three offline P2 review fixes following immutable v0.1/v0.2 records",
             "source_sha256": source_hashes(), "tests_sha256": test_hashes(),
             "historical_sha256": historical_hashes(),
             "offline_validation": {"result": results[-1].relative_to(ROOT).as_posix(),
                                    "sha256": sha(results[-1]), "tests_run": 57, "skipped": 0},
             "validation_scope": "offline only; no v0.3 real-model or live-fault acceptance"}
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def verify() -> dict:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = ROOT / value.get("offline_validation", {}).get("result", "")
    if (value.get("version") != "v0.3-review-repair" or value.get("source_sha256") != source_hashes() or
            value.get("tests_sha256") != test_hashes() or
            value.get("historical_sha256") != historical_hashes() or
            not result.is_file() or value.get("offline_validation", {}).get("sha256") != sha(result)):
        raise RuntimeError("v0.3 source or historical reference changed; make a new manifest")
    return value


if __name__ == "__main__":
    manifest = create()
    print(f"Version: {manifest['version']}")
    print(f"Manifest: {MANIFEST}")
