#!/usr/bin/env python3
"""Freeze the v0.5 offline recheck repair without rewriting older records."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from collect_evidence import LAB_DIR


ROOT = LAB_DIR.parents[1]
MANIFEST = LAB_DIR / "reliability-v0.5.json"
VERSION = "v0.5-successful-recheck"
SOURCE_FILES = (
    "collect_evidence.py", "agent_tools.py", "agent_loop.py", "evidence_checks.py",
    "deepseek_client.py", "diagnose_offline.py", "judge_agent_trace.py",
    "run_fault_scenario.py", "run_stage5_r2.py", "run_stage5_acceptance.py",
    "reliability_freeze_v05.py", "../../agent-settings.json",
    "../../run_clean_offline_tests.py", "../../run_clean_offline_tests_v05.py",
    "../../build_review_bundle_v02.py", "../../build_review_bundle_v03.py",
    "../../build_review_bundle_v04.py", "../../build_review_bundle_v05.py",
    "../../review-input-v0.4/test_review_recheck_v04.py",
)
HISTORICAL = (
    "stage5-freeze.json", "reliability-v0.2.json", "reliability-v0.3.json",
    "reliability-v0.4.json", "../../review-bundle-v0.4.zip",
    "../../review-results/v0.4-offline-20261014T093031Z/result.json",
    "../../review-results/v0.5-red-20261014/result.md",
    "agent-runs/v03-real-acceptance-20261014T074425Z-6d9425cf/acceptance.json",
    "agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/acceptance.json",
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
        raise RuntimeError("v0.5 manifest already exists; refuse overwrite")
    results = sorted((ROOT / "review-results").glob("v0.5-offline-*/result.json"))
    valid = []
    for path in results:
        result = json.loads(path.read_text(encoding="utf-8"))
        if (result.get("version") == VERSION and result.get("status") == "PASS" and
                result.get("execution_platform") == "linux" and
                result.get("tests_run") == 72 and result.get("skipped") == 0 and
                result.get("excluded_paths_absent") is True):
            valid.append(path)
    if not valid:
        raise RuntimeError("Run all 72 v0.5 clean-copy offline tests in WSL before freezing")
    path = valid[-1]
    value = {"schema_version": 1, "version": VERSION,
             "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "purpose": "offline fix for successful FRR down/OSPF-disabled recheck",
             "source_sha256": source_hashes(), "tests_sha256": test_hashes(),
             "historical_sha256": historical_hashes(),
             "offline_validation": {"result": path.relative_to(ROOT).as_posix(),
                                    "sha256": sha(path), "tests_run": 72, "skipped": 0},
             "validation_scope": "offline only; no v0.5 real-model or live-fault acceptance"}
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def verify() -> dict:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = ROOT / value.get("offline_validation", {}).get("result", "")
    if (value.get("version") != VERSION or value.get("source_sha256") != source_hashes() or
            value.get("tests_sha256") != test_hashes() or
            value.get("historical_sha256") != historical_hashes() or
            not result.is_file() or value.get("offline_validation", {}).get("sha256") != sha(result)):
        raise RuntimeError("v0.5 source or historical reference changed; make a new manifest")
    return value


if __name__ == "__main__":
    manifest = create()
    print(f"Version: {manifest['version']}")
    print(f"Manifest: {MANIFEST}")
