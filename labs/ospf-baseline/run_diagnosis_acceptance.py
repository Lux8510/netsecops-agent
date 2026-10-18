#!/usr/bin/env python3
"""Test the offline classifier on one complete three-phase observation set."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from diagnose_offline import diagnose, render_chinese_report


LAB_DIR = Path(__file__).resolve().parent


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def selected_command(document: dict, node: str, key: str) -> dict:
    matches = [item for item in document["nodes"][node]["commands"] if item.get("id") == key]
    if len(matches) != 1:
        raise ValueError(f"Expected one {node}/{key} command")
    return matches[0]


def run(experiment: Path) -> tuple[Path, dict]:
    root = experiment / "observations"
    raw = {
        "normal": json.loads((root / "normal" / "observations.json").read_text(encoding="utf-8")),
        "mismatch": json.loads((root / "fault" / "snapshot" / "observations.json").read_text(encoding="utf-8")),
        "recovered": json.loads((root / "recovered" / "snapshot" / "observations.json").read_text(encoding="utf-8")),
    }
    missing_area = copy.deepcopy(raw["mismatch"])
    area_command = selected_command(missing_area, "r2", "ospf_interface")
    original = area_command["stdout"]
    area_command["stdout"] = original.replace(", Area 0.0.0.0", "", 1)
    if area_command["stdout"] == original:
        raise ValueError("Could not construct missing-area case from r2 output")
    command_failed = copy.deepcopy(raw["mismatch"])
    failed_item = selected_command(command_failed, "r2", "ospf_interface")
    failed_item["exit_code"] = 1
    failed_item["error"] = "simulated command execution failure"
    failed_item["stderr"] = "simulated command execution failure"
    symptom_only = copy.deepcopy(raw["normal"])
    for node in symptom_only["nodes"]:
        selected_command(symptom_only, node, "ospf_neighbors")["stdout"] = ""
        selected_command(symptom_only, node, "ospf_routes")["stdout"] = ""
        item = selected_command(symptom_only, node, "ping_remote_loopback")
        item["exit_code"] = 1
        item["stderr"] = "Network unreachable"
    cases = {
        "normal": (raw["normal"], "normal"),
        "mismatch": (raw["mismatch"], "ospf_area_mismatch"),
        "recovered": (raw["recovered"], "normal"),
        "missing-one-area": (missing_area, "insufficient_evidence"),
        "failed-key-command": (command_failed, "collection_failure"),
        "symptoms-only": (symptom_only, "insufficient_evidence"),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = LAB_DIR / "diagnostic-acceptance" / f"acceptance-{stamp}-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    results = {"schema_version": 1, "source_experiment": str(experiment), "cases": {}}
    for name, (document, expected) in cases.items():
        case_dir = output / name
        case_dir.mkdir()
        # The classifier receives only this observation object, never expected or source path.
        save_json(case_dir / "input-observations.json", document)
        diagnosis = diagnose(document)
        save_json(case_dir / "diagnosis.json", diagnosis)
        (case_dir / "report.zh-CN.md").write_text(render_chinese_report(diagnosis), encoding="utf-8")
        results["cases"][name] = {
            "expected": expected, "actual": diagnosis["status"],
            "passed": diagnosis["status"] == expected,
        }
    results["status"] = "PASS" if all(case["passed"] for case in results["cases"].values()) else "FAIL"
    save_json(output / "acceptance.json", results)
    lines = ["# 离线诊断验收", "", f"结果：**{results['status']}**", "",
             "`expected` 只存在于验收记录，未传给诊断程序。", "",
             "| 输入 | 预期状态 | 实际状态 | 验收 |", "|---|---|---|---|"]
    for name, value in results["cases"].items():
        lines.append(f"| {name} | `{value['expected']}` | `{value['actual']}` | {'通过' if value['passed'] else '失败'} |")
    lines += ["", "每组的 `input-observations.json`、`diagnosis.json` 和 `report.zh-CN.md` 在对应子目录。", ""]
    (output / "acceptance.md").write_text("\n".join(lines), encoding="utf-8")
    return output, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path, required=True)
    args = parser.parse_args()
    output, results = run(args.experiment_dir.resolve())
    print(f"Acceptance: {output}")
    print(f"Status: {results['status']}")
    print(f"Report: {output / 'acceptance.md'}")
    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
