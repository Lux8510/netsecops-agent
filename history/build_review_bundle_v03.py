#!/usr/bin/env python3
"""Build a credential-scanned v0.3 source-only review ZIP from explicit files."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

from build_review_bundle_v02 import SECRET_PATTERNS, redact


ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
OUTPUT = ROOT / "review-bundle-v0.3.zip"
RESULT = Path("review-results/v0.3-offline-20261014T031239Z")
OLD_RESULT = Path("review-results/v0.2-offline-20261014T015728Z")
OLD_ACCEPTANCE = LAB / "agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.json"
ROOT_FILES = (
    ".env.example", ".gitignore", "README.md", "RELIABILITY_CHANGELOG.md",
    "REVIEW_REPAIR_v0.3.md", "REVIEW_GUIDE_v0.3.md", "agent-settings.json",
    "run_clean_offline_tests.py", "run_clean_offline_tests_v03.py",
    "build_review_bundle_v02.py", "build_review_bundle_v03.py",
)
LAB_FILES = (
    "agent_cli.py", "agent_config.py", "agent_loop.py", "agent_tools.py",
    "build_agent_acceptance.py", "collect_evidence.py", "deepseek_client.py",
    "diagnose_offline.py", "inspect_frr_stage5.py", "judge_agent_trace.py",
    "reliability_freeze.py", "reliability_freeze_v03.py", "run_area_mismatch.py",
    "run_diagnosis_acceptance.py", "run_fault_scenario.py", "run_live_agent_acceptance.py",
    "run_stage5_acceptance.py", "run_stage5_r2.py", "stage5_freeze.py", "verify_restart.py",
    "stage5-freeze.json", "reliability-v0.2.json", "reliability-v0.3.json",
    "deploy-and-verify.sh", "topology.clab.yml", "configs/r1/frr.conf", "configs/r2/frr.conf",
    "tests/mock_signal_child.py", "tests/test_agent_config.py", "tests/test_agent_runtime.py",
    "tests/test_diagnose_offline.py", "tests/test_judge_agent_trace.py",
    "tests/test_phase2.py", "tests/test_reliability.py", "tests/test_review_regressions_v02.py",
    "tests/test_stage5.py", "tests/fixtures/README.md",
    "tests/fixtures/configs/r1-startup-frr.conf", "tests/fixtures/configs/r2-startup-frr.conf",
    "tests/fixtures/observations/area-fault.json",
    "tests/fixtures/observations/failed-command.json",
    "tests/fixtures/observations/missing-one-area.json",
    "tests/fixtures/observations/normal.json",
    "tests/fixtures/observations/recovered.json",
    "tests/fixtures/observations/stage5-case-01.json",
    "tests/fixtures/observations/stage5-case-02.json",
    "tests/fixtures/observations/stage5-case-03.json",
    "tests/fixtures/traces/normal.json", "tests/fixtures/traces/stage5-case-01.json",
    "tests/fixtures/traces/stage5-case-02.json", "tests/fixtures/traces/stage5-case-03.json",
)
FORBIDDEN_PARTS = frozenset({".env", ".git", ".venv", "venv", "__pycache__",
                             "experiments", "clab-netsecops-ospf", "stage5-r2-tests"})


def add(entries: dict[str, bytes], path: Path) -> int:
    name = path.as_posix()
    if path.is_absolute() or ".." in path.parts or name in entries or any(
            part in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError(f"Unsafe or duplicate allowlist path: {name}")
    source = ROOT / path
    if not source.is_file():
        raise FileNotFoundError(source)
    value, redactions = redact(source.read_bytes())
    entries[name] = value
    return redactions


def validate(entries: dict[str, bytes], manifest: dict) -> None:
    for name, value in entries.items():
        if any(pattern.search(value.decode("utf-8")) for pattern in SECRET_PATTERNS):
            raise AssertionError(f"Credential remained in review copy: {name}")
    if entries[".env.example"].strip() != b"DEEPSEEK_API_KEY=":
        raise AssertionError("Configuration example must contain only an empty placeholder")
    result = json.loads(entries[(RESULT / "result.json").as_posix()])
    if (result.get("version"), result.get("status"), result.get("execution_platform"),
            result.get("tests_run"), result.get("skipped")) != (
            "v0.3-review-repair", "PASS", "linux", 57, 0):
        raise AssertionError("Unexpected WSL clean-copy result")
    for group in ("source_sha256", "tests_sha256", "historical_sha256"):
        for name in manifest[group]:
            relative = (ROOT / LAB / name).resolve().relative_to(ROOT).as_posix()
            if relative not in entries:
                raise AssertionError(f"Manifest reference not included in review copy: {relative}")
    for number in range(1, 4):
        name = f"{LAB.as_posix()}/tests/fixtures/traces/stage5-case-{number:02d}.json"
        trace = json.loads(entries[name])
        if any(identifier not in trace.get("evidence", {})
               for identifier in (trace.get("final") or {}).get("evidence_ids") or []):
            raise AssertionError(f"Fixture {number} has an unresolvable evidence_id")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing review package: {OUTPUT}")
    sys.path.insert(0, str(ROOT / LAB))
    from reliability_freeze_v03 import verify
    manifest = verify()
    entries: dict[str, bytes] = {}
    redactions = 0
    for name in ROOT_FILES:
        redactions += add(entries, Path(name))
    for name in LAB_FILES:
        redactions += add(entries, LAB / name)
    for path in (RESULT / "result.json", RESULT / "unittest-output.txt",
                 OLD_RESULT / "result.json", OLD_ACCEPTANCE):
        redactions += add(entries, path)
    validate(entries, manifest)
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise AssertionError("ZIP failed readback validation")
    print(f"Archive: {OUTPUT}")
    print(f"Files: {len(entries)}")
    print(f"Size bytes: {OUTPUT.stat().st_size}")
    print(f"Credential redactions in review copy: {redactions}")


if __name__ == "__main__":
    main()
