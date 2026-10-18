#!/usr/bin/env python3
"""Build the v0.2 reliability review package from a strict file allowlist."""

from __future__ import annotations

from pathlib import Path
import json
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
OUTPUT = ROOT / "review-bundle-v0.2.zip"
WSL_RESULT = Path("review-results/v0.2-offline-20261014T015728Z")
OLD_ACCEPTANCE = LAB / "agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.json"

ROOT_FILES = (
    ".env.example", ".gitignore", "README.md", "RELIABILITY_CHANGELOG.md",
    "REVIEW_GUIDE_v0.2.md", "agent-settings.json", "run_clean_offline_tests.py",
    "build_review_bundle_v02.py",
)
LAB_FILES = (
    "agent_cli.py", "agent_config.py", "agent_loop.py", "agent_tools.py",
    "build_agent_acceptance.py", "collect_evidence.py", "deepseek_client.py",
    "diagnose_offline.py", "inspect_frr_stage5.py", "judge_agent_trace.py",
    "reliability_freeze.py", "run_area_mismatch.py", "run_diagnosis_acceptance.py",
    "run_fault_scenario.py", "run_live_agent_acceptance.py",
    "run_stage5_acceptance.py", "run_stage5_r2.py", "stage5_freeze.py",
    "verify_restart.py", "stage5-freeze.json", "reliability-v0.2.json",
    "deploy-and-verify.sh", "topology.clab.yml",
    "configs/r1/frr.conf", "configs/r2/frr.conf",
    "tests/mock_signal_child.py", "tests/test_agent_config.py",
    "tests/test_agent_runtime.py", "tests/test_diagnose_offline.py",
    "tests/test_judge_agent_trace.py", "tests/test_phase2.py",
    "tests/test_reliability.py", "tests/test_stage5.py",
    "tests/fixtures/README.md", "tests/fixtures/configs/r1-startup-frr.conf",
    "tests/fixtures/configs/r2-startup-frr.conf",
    "tests/fixtures/observations/area-fault.json",
    "tests/fixtures/observations/failed-command.json",
    "tests/fixtures/observations/missing-one-area.json",
    "tests/fixtures/observations/normal.json",
    "tests/fixtures/observations/recovered.json",
    "tests/fixtures/observations/stage5-case-01.json",
    "tests/fixtures/observations/stage5-case-02.json",
    "tests/fixtures/observations/stage5-case-03.json",
    "tests/fixtures/traces/normal.json",
    "tests/fixtures/traces/stage5-case-01.json",
    "tests/fixtures/traces/stage5-case-02.json",
    "tests/fixtures/traces/stage5-case-03.json",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(DEEPSEEK_API_KEY\s*[:=]\s*['\"]?)(sk-[A-Za-z0-9_-]{12,})"),
    re.compile(r"(?i)(Authorization\s*[:=]\s*['\"]?\s*Bearer\s+)([A-Za-z0-9._-]{12,})"),
    re.compile(r"(?<![A-Za-z0-9_-])(sk-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_])(gh[pousr]_[A-Za-z0-9]{20,})(?![A-Za-z0-9_])"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
               r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
FORBIDDEN_PARTS = frozenset({".env", ".git", ".venv", "venv", "__pycache__",
                             "experiments", "clab-netsecops-ospf", "stage5-r2-tests"})


def redact(data: bytes) -> tuple[bytes, int]:
    value = data.decode("utf-8")
    count = 0
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 2:
            value, removed = pattern.subn(lambda match: match.group(1) + "[REDACTED]", value)
        else:
            value, removed = pattern.subn("[REDACTED]", value)
        count += removed
    return value.encode("utf-8"), count


def add(entries: dict[str, bytes], name: Path) -> int:
    relative = name.as_posix()
    if (name.is_absolute() or ".." in name.parts or any(part in FORBIDDEN_PARTS for part in name.parts)
            or relative in entries):
        raise ValueError(f"Unsafe or duplicate allowlist path: {relative}")
    source = ROOT / name
    if not source.is_file():
        raise FileNotFoundError(source)
    content, count = redact(source.read_bytes())
    entries[relative] = content
    return count


def validate(entries: dict[str, bytes]) -> None:
    for name, content in entries.items():
        if any(pattern.search(content.decode("utf-8")) for pattern in SECRET_PATTERNS):
            raise AssertionError(f"Credential remained in review copy: {name}")
    if entries[".env.example"].strip() != b"DEEPSEEK_API_KEY=":
        raise AssertionError("Configuration example is not an empty key placeholder")
    result = json.loads(entries[(WSL_RESULT / "result.json").as_posix()])
    if (result.get("status"), result.get("execution_platform"), result.get("tests_run"),
            result.get("skipped")) != ("PASS", "linux", 54, 0):
        raise AssertionError("Unexpected WSL clean-copy test result")
    for number in range(1, 4):
        trace = json.loads(entries[f"{LAB.as_posix()}/tests/fixtures/traces/stage5-case-{number:02d}.json"])
        evidence = trace.get("evidence") or {}
        for identifier in (trace.get("final") or {}).get("evidence_ids") or []:
            if identifier not in evidence:
                raise AssertionError(f"Fixture citation cannot be resolved: {number}/{identifier}")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite review package: {OUTPUT}")
    sys.path.insert(0, str(ROOT / LAB))
    from reliability_freeze import verify
    verify()
    entries: dict[str, bytes] = {}
    count = 0
    for filename in ROOT_FILES:
        count += add(entries, Path(filename))
    for filename in LAB_FILES:
        count += add(entries, LAB / filename)
    for filename in ("result.json", "unittest-output.txt"):
        count += add(entries, WSL_RESULT / filename)
    count += add(entries, OLD_ACCEPTANCE)  # Historical index only; not a v0.2 model run.
    validate(entries)
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise AssertionError("Review ZIP failed readback validation")
    print(f"Archive: {OUTPUT}")
    print(f"Files: {len(entries)}")
    print(f"Size bytes: {OUTPUT.stat().st_size}")
    print(f"Credential redactions in review copy: {count}")


if __name__ == "__main__":
    main()
