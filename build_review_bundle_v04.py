#!/usr/bin/env python3
"""Build a credential-scanned v0.4 review ZIP from explicit source/evidence paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile

from build_review_bundle_v02 import SECRET_PATTERNS, redact
from build_review_bundle_v03 import LAB_FILES as V03_LAB_FILES


ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
OUTPUT = ROOT / "review-bundle-v0.4.zip"
ROOT_FILES = (
    ".env.example", ".gitignore", "README.md", "agent-settings.json",
    "RELIABILITY_CHANGELOG.md", "REVIEW_REPAIR_v0.3.md", "REVIEW_GUIDE_v0.3.md",
    "V03_REAL_ACCEPTANCE_20261014.md", "REVIEW_REPAIR_v0.4.md", "REVIEW_GUIDE_v0.4.md",
    "run_clean_offline_tests.py", "run_clean_offline_tests_v03.py",
    "run_clean_offline_tests_v04.py", "build_review_bundle_v02.py",
    "build_review_bundle_v03.py", "build_review_bundle_v04.py",
)
LAB_FILES = tuple(dict.fromkeys((*V03_LAB_FILES,
    "evidence_checks.py", "reliability_freeze_v04.py", "reliability-v0.4.json",
    "run_v03_real_acceptance.py", "tests/test_v04_evidence_reconciliation.py",
    "tests/fixtures/v04-failures/README.md",
    "tests/fixtures/v04-failures/normal-trace.json",
    "tests/fixtures/v04-failures/normal-acceptance.json",
    "tests/fixtures/v04-failures/failed-collection-trace.json",
    "tests/fixtures/v04-failures/failed-collection-judge.json",
)))
HISTORICAL = (
    LAB / "agent-runs/live-acceptance-20261014T074430Z-ab8485ed/acceptance.json",
    LAB / "agent-runs/live-normal-20261014T074442Z-bffb7c92/trace.json",
    LAB / "agent-runs/v03-real-acceptance-20261014T074425Z-6d9425cf/acceptance.json",
    LAB / "agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/acceptance.json",
    LAB / "agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/case-07/agent-trace.json",
    LAB / "agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/case-07/judge.json",
    Path("review-results/v0.3-offline-20261014T031239Z/result.json"),
    Path("review-results/v0.4-red-20261014/result.md"),
)
FORBIDDEN = frozenset((".env", ".git", ".venv", "venv", "__pycache__",
                       "experiments", "clab-netsecops-ospf", "stage5-r2-tests"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add(entries: dict[str, bytes], path: Path) -> None:
    name = path.as_posix()
    if (path.is_absolute() or ".." in path.parts or name in entries or
            any(part in FORBIDDEN for part in path.parts)):
        raise ValueError(f"Unsafe or duplicate review path: {name}")
    source = ROOT / path
    if not source.is_file():
        raise FileNotFoundError(source)
    data, removed = redact(source.read_bytes())
    if removed:
        raise RuntimeError(f"Credential-like content in allowlisted file {name}; refuse altered source copy")
    entries[name] = data


def validate(entries: dict[str, bytes], manifest: dict) -> None:
    for name, data in entries.items():
        if any(pattern.search(data.decode("utf-8")) for pattern in SECRET_PATTERNS):
            raise AssertionError(f"Credential remained in review copy: {name}")
    if entries[".env.example"].strip() != b"DEEPSEEK_API_KEY=":
        raise AssertionError("Configuration example must be an empty placeholder")
    result_name = manifest["offline_validation"]["result"]
    result = json.loads(entries[result_name])
    if (result.get("version"), result.get("status"), result.get("execution_platform"),
            result.get("tests_run"), result.get("skipped")) != (
            "v0.4-evidence-reconciliation", "PASS", "linux", 66, 0):
        raise AssertionError("Unexpected v0.4 clean-copy result")
    if sha(entries[result_name]) != manifest["offline_validation"]["sha256"]:
        raise AssertionError("Offline result differs from frozen manifest")
    for group in ("source_sha256", "tests_sha256", "historical_sha256"):
        for name, expected in manifest[group].items():
            relative = (ROOT / LAB / name).resolve().relative_to(ROOT).as_posix()
            if relative in entries and sha(entries[relative]) != expected:
                raise AssertionError(f"Frozen {group} hash differs in review copy: {relative}")
            if group != "historical_sha256" and relative not in entries:
                raise AssertionError(f"Frozen {group} source absent from review copy: {relative}")
    for copy, original in (
        ("tests/fixtures/v04-failures/normal-trace.json",
         "agent-runs/live-normal-20261014T074442Z-bffb7c92/trace.json"),
        ("tests/fixtures/v04-failures/failed-collection-trace.json",
         "agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/case-07/agent-trace.json"),
    ):
        if entries[(LAB / copy).as_posix()] != entries[(LAB / original).as_posix()]:
            raise AssertionError("Regression trace copy differs from the preserved original")
    for name in ("normal-trace.json", "failed-collection-trace.json"):
        trace = json.loads(entries[(LAB / "tests/fixtures/v04-failures" / name).as_posix()])
        if any(identifier not in trace.get("evidence", {})
               for identifier in (trace.get("final") or {}).get("evidence_ids") or []):
            raise AssertionError(f"Unresolvable evidence reference in {name}")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing review package: {OUTPUT}")
    sys.path.insert(0, str(ROOT / LAB))
    from reliability_freeze_v04 import verify
    manifest = verify()
    entries: dict[str, bytes] = {}
    for name in ROOT_FILES:
        add(entries, Path(name))
    for name in LAB_FILES:
        add(entries, LAB / name)
    for path in HISTORICAL:
        add(entries, path)
    result = Path(manifest["offline_validation"]["result"])
    add(entries, result)
    add(entries, result.with_name("unittest-output.txt"))
    validate(entries, manifest)
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise AssertionError("Review ZIP failed readback validation")
    print(f"Archive: {OUTPUT}")
    print(f"Files: {len(entries)}")
    print(f"Size bytes: {OUTPUT.stat().st_size}")
    print("Credential matches: 0")


if __name__ == "__main__":
    main()
