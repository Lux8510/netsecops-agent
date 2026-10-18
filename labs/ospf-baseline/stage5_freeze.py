#!/usr/bin/env python3
"""Freeze and verify Stage 5 diagnosis logic, neutral task, and acceptance criteria."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from collect_evidence import LAB_DIR


FREEZE_PATH = LAB_DIR / "stage5-freeze.json"
FREEZE_FILES = (
    "collect_evidence.py", "agent_tools.py", "agent_loop.py", "deepseek_client.py", "diagnose_offline.py",
    "judge_agent_trace.py", "run_fault_scenario.py", "run_stage5_r2.py",
    "run_stage5_acceptance.py", "../../agent-settings.json",
)


def hashes() -> dict[str, str]:
    return {name: hashlib.sha256((LAB_DIR / name).resolve().read_bytes()).hexdigest()
            for name in FREEZE_FILES}


def freeze() -> dict:
    if FREEZE_PATH.exists():
        raise RuntimeError("Freeze already exists; do not overwrite a prior acceptance version")
    value = {"schema_version": 1, "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "purpose": "r2 held-out validation after r1 development",
             "source_sha256": hashes(),
             "criteria": ["direct root-cause citation", "same frozen observations for rules and Agent",
                          "model tool_calls equal executed calls", "limits unchanged", "recovery verified"]}
    FREEZE_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def verify(path: Path = FREEZE_PATH) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("source_sha256") != hashes():
        raise RuntimeError("Frozen Stage 5 code/settings changed; do not run held-out r2 acceptance")
    return value


if __name__ == "__main__":
    result = freeze()
    print(f"Frozen at: {result['frozen_at_utc']}")
    print(f"Manifest: {FREEZE_PATH}")
