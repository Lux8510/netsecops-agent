#!/usr/bin/env python3
"""Build a fixed-allowlist, credential-scrubbed source review ZIP (no network calls)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parent
LAB = Path("labs/ospf-baseline")
ACCEPTANCE = LAB / "agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7"
BATCH = LAB / "stage5-r2-tests/batch-20261013T161857Z-72f15a49"
OUTPUT = ROOT / "review-bundle.zip"

SOURCE_FILES = (
    ".env.example", ".gitignore", "README.md", "REVIEW_GUIDE.md",
    "agent-settings.json", "build_review_bundle.py",
)
LAB_FILES = (
    "agent_cli.py", "agent_config.py", "agent_loop.py", "agent_tools.py",
    "build_agent_acceptance.py", "collect_evidence.py", "deepseek_client.py",
    "deploy-and-verify.sh", "diagnose_offline.py", "inspect_frr_stage5.py",
    "judge_agent_trace.py", "run_area_mismatch.py", "run_diagnosis_acceptance.py",
    "run_fault_scenario.py", "run_live_agent_acceptance.py",
    "run_stage5_acceptance.py", "run_stage5_r2.py", "stage5_freeze.py",
    "verify_restart.py", "stage5-freeze.json", "topology.clab.yml",
    "configs/r1/frr.conf", "configs/r2/frr.conf",
    "tests/test_agent_config.py", "tests/test_agent_runtime.py",
    "tests/test_diagnose_offline.py", "tests/test_judge_agent_trace.py",
    "tests/test_phase2.py", "tests/test_stage5.py",
)
CASE_SOURCE = {
    "case-01": LAB / "experiments/area_mismatch-r2-20261013T161857Z-595dc1c7/observations/fault/snapshot",
    "case-02": LAB / "experiments/admin_down-r2-20261013T162006Z-431a42ae/observations/fault/snapshot",
    "case-03": LAB / "experiments/ospf_passive-r2-20261013T162031Z-8fb41311/observations/fault/snapshot",
}
EXAMPLE_RUNS = (
    ("live-normal-20261013T143231Z-343f3366", ("trace.json", "report.zh-CN.md", "judge.json")),
    ("failed-command-20261013T142100Z-e4241eeb", ("trace.json", "report.zh-CN.md", "judge.json")),
    ("normal-20261013T085549Z-01fa065a", ("trace.json", "report.zh-CN.md")),
)
FAIL_INPUT = LAB / "diagnostic-acceptance/acceptance-20261013T081618Z-c77eb02e/failed-key-command/input-observations.json"
EARLY_INPUT = LAB / "experiments/area-mismatch-20261013T081041Z-3fbfc8c4/observations/normal/observations.json"

# High-confidence secret patterns. The literal source-code template that creates an
# Authorization header is not itself a captured header or credential.
SECRET_PATTERNS = (
    re.compile(r"(?i)(DEEPSEEK_API_KEY\s*[:=]\s*['\"]?)(sk-[A-Za-z0-9_-]{12,})"),
    re.compile(r"(?i)(Authorization\s*[:=]\s*['\"]?\s*Bearer\s+)([A-Za-z0-9._-]{12,})"),
    re.compile(r"(?<![A-Za-z0-9_-])(sk-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_])(gh[pousr]_[A-Za-z0-9]{20,})(?![A-Za-z0-9_])"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PATH_PREFIXES = (
    "/mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/",
    str(ROOT).replace("\\", "/") + "/",
    str(ROOT).replace("\\", "\\\\") + "\\\\",
    str(ROOT) + "\\",
)
OUTPUT_NAME = re.compile(r"r[12]__[a-z0-9_]+\.(?:stdout|stderr)\.txt\Z")
entries: dict[str, bytes] = {}
redactions = 0
synthesized_outputs = 0


def scrub(data: bytes) -> bytes:
    global redactions
    value = data.decode("utf-8")
    for prefix in PATH_PREFIXES:
        value = value.replace(prefix, "./")
        value = value.replace(prefix.rstrip("/\\"), ".")
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 2:
            value, count = pattern.subn(lambda match: match.group(1) + "[REDACTED]", value)
        else:
            value, count = pattern.subn("[REDACTED]", value)
        redactions += count
    return value.encode("utf-8")


def add(source: Path, destination: Path | None = None) -> None:
    source_path = ROOT / source
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    name = (destination or source).as_posix()
    if name in entries:
        raise ValueError(f"Duplicate archive path: {name}")
    if name.startswith("/") or ".." in Path(name).parts:
        raise ValueError(f"Unsafe archive path: {name}")
    entries[name] = scrub(source_path.read_bytes())


def add_observation(source: Path, destination: Path, *, raw_source: Path | None = None) -> None:
    """Copy JSON and only raw text explicitly referenced by its command records."""
    global synthesized_outputs
    add(source, destination)
    document = json.loads((ROOT / source).read_text(encoding="utf-8"))
    for node in document.get("nodes", {}).values():
        for item in node.get("commands", []):
            for stream in ("stdout", "stderr"):
                filename = item.get(f"{stream}_file")
                if not filename:
                    continue
                if not OUTPUT_NAME.fullmatch(filename):
                    raise ValueError(f"Non-whitelisted output filename in {source}")
                target = (destination.parent / filename).as_posix()
                if target in entries:
                    continue
                raw_dir = raw_source or source.parent
                original = ROOT / raw_dir / filename
                if original.is_file():
                    if original.read_text(encoding="utf-8") != str(item.get(stream) or ""):
                        raise ValueError(f"Raw output and inline observation differ: {source}/{filename}")
                    add(raw_dir / filename, destination.parent / filename)
                else:
                    # Older constructed fixtures contain inline command output only.
                    entries[target] = scrub(str(item.get(stream) or "").encode("utf-8"))
                    synthesized_outputs += 1


def validate() -> None:
    forbidden = {".env", ".git", ".venv", "venv", "__pycache__", "clab-netsecops-ospf"}
    for name, content in entries.items():
        if any(part in forbidden for part in Path(name).parts):
            raise AssertionError(f"Excluded path entered archive: {name}")
        sample = content.decode("utf-8")
        if any(pattern.search(sample) for pattern in SECRET_PATTERNS):
            raise AssertionError(f"Credential scan failed: {name}")

    for case in CASE_SOURCE:
        base = (ACCEPTANCE / case).as_posix()
        trace = json.loads(entries[f"{base}/agent-trace.json"])
        final = trace.get("final") or {}
        available = trace.get("evidence") or {}
        for evidence_id in final.get("evidence_ids") or []:
            if evidence_id not in available:
                raise AssertionError(f"Unresolvable citation {case}/{evidence_id}")
        observation = json.loads(entries[f"{base}/observations.json"])
        for node in observation.get("nodes", {}).values():
            for item in node.get("commands", []):
                for stream in ("stdout", "stderr"):
                    filename = item.get(f"{stream}_file")
                    if filename and f"{base}/{filename}" not in entries:
                        raise AssertionError(f"Missing raw output {case}/{filename}")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing archive: {OUTPUT}")
    for name in SOURCE_FILES:
        add(Path(name))
    for name in LAB_FILES:
        add(LAB / name)

    add(ACCEPTANCE / "acceptance.json")
    add(ACCEPTANCE / "acceptance.md")
    for case, source_dir in CASE_SOURCE.items():
        destination = ACCEPTANCE / case
        for name in ("agent-report.zh-CN.md", "agent-trace.json", "judge.json",
                     "rule-diagnosis.json", "rule-report.zh-CN.md"):
            add(destination / name)
        add_observation(destination / "observations.json", destination / "observations.json",
                        raw_source=source_dir)
        # Original frozen collector output, kept under its original relative path.
        add_observation(source_dir / "observations.json", source_dir / "observations.json")

    add(BATCH / "batch.json")
    add_observation(BATCH / "final-network/observations.json", BATCH / "final-network/observations.json")
    for run, names in EXAMPLE_RUNS:
        for name in names:
            add(LAB / "agent-runs" / run / name)
    add_observation(FAIL_INPUT, FAIL_INPUT)
    add_observation(EARLY_INPUT, EARLY_INPUT)

    # If a copied observation needed redaction/path normalization, keep the
    # review-copy checksum in the copied acceptance index consistent with it.
    index_name = (ACCEPTANCE / "acceptance.json").as_posix()
    index = json.loads(entries[index_name])
    for row in index.get("cases", []):
        path = (ACCEPTANCE / row["case"] / "observations.json").as_posix()
        row["observations_sha256"] = hashlib.sha256(entries[path]).hexdigest()
    entries[index_name] = scrub((json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    validate()
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(entries):
            archive.writestr(name, entries[name])
    print(f"Archive: {OUTPUT}")
    print(f"Files: {len(entries)}")
    print(f"Size bytes: {OUTPUT.stat().st_size}")
    print(f"Credential redactions in review copy: {redactions}")
    print(f"Inline-only raw output files materialized: {synthesized_outputs}")


if __name__ == "__main__":
    main()
