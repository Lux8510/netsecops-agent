#!/usr/bin/env python3
"""Package one v0.5 targeted acceptance run without secrets or older run directories."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from build_review_bundle_v02 import SECRET_PATTERNS


ROOT = Path(__file__).resolve().parent
LAB = ROOT / "labs" / "ospf-baseline"
RUN_ROOT = LAB / "agent-runs"
MANIFEST = LAB / "reliability-v0.5.json"
HARNESS = LAB / "run_v05_targeted_acceptance.py"
SETTINGS = ROOT / "agent-settings.json"
REVIEW_SOURCE = (
    "agent_config.py",
    "agent_loop.py",
    "agent_tools.py",
    "collect_evidence.py",
    "deepseek_client.py",
    "evidence_checks.py",
    "judge_agent_trace.py",
    "reliability_freeze_v05.py",
    "run_stage5_acceptance.py",
    "run_v03_real_acceptance.py",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add(entries: dict[str, bytes], path: Path) -> None:
    source = path.resolve()
    if not source.is_relative_to(ROOT.resolve()) or not source.is_file() or source.is_symlink():
        raise ValueError("Review input is outside the project, missing, or a symlink")
    relative = source.relative_to(ROOT.resolve()).as_posix()
    if any(part in (".env", ".git", ".venv", "venv", "__pycache__", "experiments")
           for part in Path(relative).parts) or relative in entries:
        raise ValueError("Unsafe or duplicate review path")
    data = source.read_bytes()
    try:
        content = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("Unexpected non-text file in the acceptance package") from None
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise RuntimeError("Credential-like content found; package was not created")
    entries[relative] = data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", type=Path, required=True,
                        help="acceptance.json from one v05-targeted-* run")
    args = parser.parse_args()
    source = args.acceptance.resolve()
    directory = source.parent
    if (source.name != "acceptance.json" or not directory.is_relative_to(RUN_ROOT.resolve()) or
            directory.parent != RUN_ROOT.resolve() or not directory.name.startswith("v05-targeted-")):
        parser.error("Only one project-local v05-targeted-*/acceptance.json is accepted")
    record = json.loads(source.read_text(encoding="utf-8"))
    if record.get("code_version") != "v0.5-successful-recheck":
        parser.error("Acceptance did not verify the v0.5 code version")
    if record.get("freeze_sha256") != digest(MANIFEST.read_bytes()):
        parser.error("Frozen manifest differs from the acceptance record")
    if record.get("harness_sha256") != digest(HARNESS.read_bytes()):
        parser.error("Acceptance harness differs from the recorded hash")
    output = ROOT / f"{directory.name}-review.zip"
    if output.exists():
        parser.error("Output ZIP already exists; refusing to overwrite")
    entries: dict[str, bytes] = {}
    for file in (MANIFEST, HARNESS, SETTINGS, Path(__file__), ROOT / "REVIEW_REPAIR_v0.5.md"):
        add(entries, file)
    for name in REVIEW_SOURCE:
        add(entries, LAB / name)
    for file in sorted(directory.rglob("*")):
        if file.is_file():
            add(entries, file)
    guide = ("# v0.5 targeted acceptance package\n\n"
             f"Run directory: `labs/ospf-baseline/agent-runs/{directory.name}/`\n\n"
             "Absolute paths in acceptance.json use the original project root; inside this ZIP, "
             "locate the same suffix after `netsecops-agent/`. Each case trace contains its "
             "actual model tool_calls, full saved raw tool evidence, model_raw_final, final, "
             "evidence_additions and token usage. The neighboring judge.json is the independent "
             "verdict. Network snapshots are read-only checks, not fault restoration.\n\n"
             "The packaged read-only Agent, evidence guardrail, and independent judge source "
             "allow inspection of the exact acceptance logic. The frozen manifest records "
             "business-source hashes; the new harness hash is recorded in acceptance.json.\n\n"
             f"Frozen manifest SHA256: `{digest(MANIFEST.read_bytes())}`\n\n"
             f"Acceptance harness SHA256: `{digest(HARNESS.read_bytes())}`\n\n"
             f"Packaging script SHA256: `{digest(Path(__file__).read_bytes())}`\n")
    entries["PACKAGE_GUIDE.md"] = guide.encode("utf-8")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise AssertionError("Acceptance ZIP failed readback validation")
    print(f"Package: {output}")
    print(f"Files: {len(entries)}")
    print(f"Size bytes: {output.stat().st_size}")
    print("Credential matches: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
