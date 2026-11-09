#!/usr/bin/env python3
"""Create a whitelist-only, credential-scanned v0.5 offline review package."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

from build_review_bundle_v02 import SECRET_PATTERNS
from build_review_bundle_v04 import (ROOT, LAB, ROOT_FILES as V04_ROOT_FILES,
                                     LAB_FILES as V04_LAB_FILES, HISTORICAL as V04_HISTORICAL,
                                     add, sha)


OUTPUT = ROOT / "review-bundle-v0.5.zip"
ROOT_FILES = tuple(dict.fromkeys((*V04_ROOT_FILES,
    "REVIEW_REPAIR_v0.5.md", "REVIEW_GUIDE_v0.5.md",
    "run_clean_offline_tests_v05.py", "build_review_bundle_v05.py",
    "review-input-v0.4/test_review_recheck_v04.py",
)))
LAB_FILES = tuple(dict.fromkeys((*V04_LAB_FILES,
    "reliability_freeze_v05.py", "reliability-v0.5.json",
    "tests/test_review_recheck_v04.py",
)))
HISTORICAL = tuple(dict.fromkeys((*V04_HISTORICAL,
    Path("review-results/v0.4-offline-20261014T093031Z/result.json"),
    Path("review-results/v0.4-offline-20261014T093031Z/unittest-output.txt"),
    Path("review-results/v0.5-red-20261014/result.md"),
)))
ORIGINAL_TEST_SHA256 = "b1c3dd5bc4a9792dd837d17fba8e3898a26a8683b01a40f374f01efec84fe444"


def validate(entries: dict[str, bytes], manifest: dict) -> None:
    for name, data in entries.items():
        if any(pattern.search(data.decode("utf-8")) for pattern in SECRET_PATTERNS):
            raise AssertionError(f"Credential remained in review copy: {name}")
    if entries[".env.example"].strip() != b"DEEPSEEK_API_KEY=":
        raise AssertionError("Configuration example must be an empty placeholder")
    original = entries["review-input-v0.4/test_review_recheck_v04.py"]
    if sha(original) != ORIGINAL_TEST_SHA256:
        raise AssertionError("Attached reproduction test differs from the reviewed original")
    result_name = manifest["offline_validation"]["result"]
    result = json.loads(entries[result_name])
    if (result.get("version"), result.get("status"), result.get("execution_platform"),
            result.get("tests_run"), result.get("skipped")) != (
            "v0.5-successful-recheck", "PASS", "linux", 72, 0):
        raise AssertionError("Unexpected v0.5 clean-copy result")
    if sha(entries[result_name]) != manifest["offline_validation"]["sha256"]:
        raise AssertionError("Offline result differs from frozen manifest")
    for group in ("source_sha256", "tests_sha256", "historical_sha256"):
        for name, expected in manifest[group].items():
            relative = (ROOT / LAB / name).resolve().relative_to(ROOT).as_posix()
            if relative in entries and sha(entries[relative]) != expected:
                raise AssertionError(f"Frozen {group} hash differs in review copy: {relative}")
            if group != "historical_sha256" and relative not in entries:
                raise AssertionError(f"Frozen {group} source absent from review copy: {relative}")
    for name in ("normal-trace.json", "failed-collection-trace.json"):
        path = (LAB / "tests/fixtures/v04-failures" / name).as_posix()
        trace = json.loads(entries[path])
        if any(identifier not in trace.get("evidence", {})
               for identifier in (trace.get("final") or {}).get("evidence_ids") or []):
            raise AssertionError(f"Unresolvable historical evidence reference in {name}")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing review package: {OUTPUT}")
    sys.path.insert(0, str(ROOT / LAB))
    from reliability_freeze_v05 import verify
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
