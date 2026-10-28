#!/usr/bin/env python3
"""Export a small, reviewed set of saved runs; never call a model or lab tool.

This one-time publisher needs the local historical runs. The exported examples,
not those private directories, are what a clean clone and the web UI consume.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "ospf-baseline"
DEST = ROOT / "examples"
V05 = LAB / "agent-runs" / "v05-targeted-20261014T113312Z-a98f2b1e"
STAGE5 = LAB / "agent-runs" / "stage5-acceptance-20261013T162832Z-db38a0f7"

# No other local run is eligible for this small public demonstration set.
CASES = (
    ("normal", "正常基线", "历史现场只读诊断", V05 / "case-01", "v0.5 定向验收 case-01"),
    ("area-mismatch", "OSPF 区域不一致", "历史冻结观测回放", STAGE5 / "case-01", "Stage 5 历史回放 case-01"),
    ("admin-down", "互联接口管理关闭", "历史冻结观测回放", STAGE5 / "case-02", "Stage 5 历史回放 case-02"),
    ("collection-failure", "关键命令采集失败", "历史冻结观测回放", V05 / "case-02", "v0.5 定向验收 case-02"),
    ("missing-area", "缺少一端 Area 证据", "历史冻结观测回放", V05 / "case-03", "v0.5 定向验收 case-03"),
)

TRACE_KEYS = (
    "schema_version", "mode", "task", "started_at_utc", "finished_at_utc",
    "model_requested", "model_reported", "initial_topology", "settings",
    "rounds", "tool_calls_executed", "usage_per_request", "usage_total",
    "model_raw_final", "final", "guardrail_changes", "evidence_additions",
    "elapsed_seconds", "evidence", "error",
)
FORBIDDEN = re.compile(
    r"(?:[A-Za-z]:\\Users\\|/mnt/[a-z]/Users/|DEEPSEEK_API_KEY|"
    r"Authorization\s*[:=]|Bearer\s+\S+|sk-[A-Za-z0-9]{10,})",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export() -> list[dict]:
    (DEST / "cases").mkdir(parents=True, exist_ok=True)
    manifest = []
    for case_id, title, provenance, source, source_label in CASES:
        trace_path = source / "agent-trace.json"
        judge_path = source / "judge.json"
        original = json.loads(trace_path.read_text(encoding="utf-8"))
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        trace = {key: original[key] for key in TRACE_KEYS if key in original}
        for round_record in trace.get("rounds", []):
            round_record.pop("request_id", None)
        if original.get("schema_version") not in (1, 2):
            raise ValueError(f"Unsupported historical trace schema in {source_label}")
        if original.get("mode") not in ("live", "replay") or judge.get("verdict") != "PASS":
            raise ValueError(f"Unexpected source mode or judgment in {source_label}")
        record = {
            "schema_version": 1,
            "case_id": case_id,
            "title": title,
            "provenance": provenance,
            "source_label": source_label,
            "source_trace_sha256": sha256(trace_path),
            "source_judge_sha256": sha256(judge_path),
            "redactions": ["请求 ID 与本机文件路径未导出；原始命令输出和证据关联保留。"],
            "judge": {"verdict": judge["verdict"], "problems": judge.get("problems", [])},
            "trace": trace,
        }
        serialized = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if FORBIDDEN.search(serialized):
            raise ValueError(f"Potential credential or personal path in {source_label}; export aborted")
        # The output is a new, versioned derivative. The historical source is untouched.
        (DEST / "cases" / f"{case_id}.json").write_text(serialized, encoding="utf-8")
        manifest.append({
            "id": case_id, "title": title, "provenance": provenance,
            "run_mode": original["mode"], "started_at_utc": original.get("started_at_utc"),
            "path": f"cases/{case_id}.json", "source_label": source_label,
        })
    index = {"schema_version": 1, "collection": "v0.6-demo-examples",
             "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "cases": manifest}
    (DEST / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(f"Exported {len(export())} saved-run demo cases to {DEST}")
