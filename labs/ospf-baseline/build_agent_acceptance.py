#!/usr/bin/env python3
"""Build a unified, independent acceptance table from saved traces only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

from collect_evidence import LAB_DIR
from judge_agent_trace import verdict


REPLAY_CASES = (
    ("正常", "normal-20261013T085804Z-7f07762f", "normal"),
    ("区域不一致", "mismatch-20261013T085844Z-e52d75aa", "ospf_area_mismatch"),
    ("缺少一端 Area", "missing-area-20261013T142045Z-8adf2bed", "insufficient_evidence"),
    ("关键命令失败", "failed-command-20261013T142100Z-e4241eeb", "collection_failure"),
)


def read_trace(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def saved_live_trace(saved_path: str) -> Path:
    direct = Path(saved_path)
    if direct.is_file():
        return direct
    # WSL stores /mnt/c/... paths; the same workspace is C:\... from Windows.
    # Resolve only a trace in this project's agent-runs, never an arbitrary path.
    folder = direct.parent.name
    if direct.name != "trace.json" or not folder.startswith("live-"):
        raise ValueError("Unexpected live trace path in acceptance record")
    candidate = LAB_DIR / "agent-runs" / folder / "trace.json"
    if not candidate.is_file():
        raise FileNotFoundError("Live trace not found in this project workspace")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-acceptance", type=Path, help="run_live_agent_acceptance.py 的 acceptance.json")
    args = parser.parse_args()
    rows: list[dict] = []
    tested = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(LAB_DIR / "tests"), "-q"],
                            capture_output=True, text=True, timeout=30, check=False)
    count = re.search(r"Ran (\d+) tests", tested.stdout + tested.stderr)
    rows.append({"category": "模拟测试", "case": f"本地自动测试（{count.group(1) if count else '未知'} 项）",
                 "verdict": "PASS" if tested.returncode == 0 else "FAIL",
                 "count": int(count.group(1)) if count else None, "model": "mock/no API",
                 "model_requests": 0, "tool_calls": 0, "elapsed_seconds": None,
                 "input_tokens": 0, "output_tokens": 0, "diagnosis": "不计入真实模型验收", "raw_model_status": None,
                 "final_network": "未触及", "trace": None})
    for label, folder, expected in REPLAY_CASES:
        path = LAB_DIR / "agent-runs" / folder / "trace.json"
        result = verdict(read_trace(path), expected)
        rows.append({"category": "真实模型＋回放工具", "case": label,
                     "verdict": result["verdict"], "model": result["model"],
                     "model_requests": result["model_requests"], "tool_calls": result["tool_calls"],
                     "elapsed_seconds": result["elapsed_seconds"],
                     "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                     "diagnosis": result["actual"], "raw_model_status": result["model_raw_status"],
                     "final_network": "回放，不触及现场", "trace": str(path), "problems": result["problems"]})
    live_record = None
    if args.live_acceptance:
        live_record = json.loads(args.live_acceptance.read_text(encoding="utf-8"))
    for label, trace_key, judge_key in (("当前正常", "normal_trace", "normal_judge"),
                                        ("稳定区域故障", "fault_trace", "fault_judge")):
        saved_path = live_record.get(trace_key) if live_record else None
        if saved_path:
            path = saved_live_trace(saved_path)
            result = verdict(read_trace(path), "normal" if label == "当前正常" else "ospf_area_mismatch")
            rows.append({"category": "真实模型＋现场工具", "case": label, "verdict": result["verdict"],
                         "model": result["model"], "model_requests": result["model_requests"],
                         "tool_calls": result["tool_calls"], "elapsed_seconds": result["elapsed_seconds"],
                         "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                         "diagnosis": result["actual"], "raw_model_status": result["model_raw_status"],
                         "final_network": live_record.get("final_network"), "trace": str(path),
                         "problems": result["problems"]})
        else:
            rows.append({"category": "真实模型＋现场工具", "case": label, "verdict": "NOT_RUN",
                         "model": None, "model_requests": None, "tool_calls": None,
                         "elapsed_seconds": None, "input_tokens": None, "output_tokens": None,
                         "diagnosis": None, "raw_model_status": None,
                         "final_network": live_record.get("final_network") if live_record else "未检查",
                         "trace": None})
    complete = all(row["verdict"] == "PASS" for row in rows) and \
               live_record is not None and live_record.get("status") == "PASS" and \
               live_record.get("final_network") == "RESTORED"
    document = {"status": "PASS" if complete else "INCOMPLETE_OR_FAIL",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "live_acceptance": str(args.live_acceptance) if args.live_acceptance else None,
                "rows": rows}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = LAB_DIR / "agent-runs" / f"unified-acceptance-{stamp}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "acceptance.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 第四阶段统一验收表", "", f"总状态：**{document['status']}**", "",
             "| 类别 | 测试 | 判定 | 实际模型 | 请求 | 工具 | 秒 | 输入 token | 输出 token | Agent 结果（模型原始状态） | 最终网络 |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---|---|"]
    for row in rows:
        show = lambda value: "—" if value is None else str(value)
        lines.append("| " + " | ".join(show(value) for value in
                     (row["category"], row["case"], row["verdict"], row["model"],
                      row["model_requests"], row["tool_calls"], row["elapsed_seconds"],
                      row["input_tokens"], row["output_tokens"],
                      f"{show(row['diagnosis'])}（{show(row['raw_model_status'])}）", row["final_network"])) + " |")
    lines += ["", "模拟测试不计入真实模型验收；回放不触及现场网络。完整 tool_calls、工具返回与引用支持核验见各 trace.json。",
              "失败和未执行项目不会被计为 PASS。", ""]
    (directory / "acceptance.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Status: {document['status']}")
    print(f"Table: {directory / 'acceptance.md'}")
    print(f"JSON: {directory / 'acceptance.json'}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
