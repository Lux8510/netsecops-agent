#!/usr/bin/env python3
"""Held-out r2 rule-versus-real-model acceptance on identical frozen snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from agent_config import get_deepseek_api_key, load_settings
from agent_loop import render_trace, run_agent
from agent_tools import AgentTools, ReplayBackend
from collect_evidence import LAB_DIR, utc_now
from deepseek_client import DeepSeekChatClient
from diagnose_offline import diagnose, render_chinese_report
from judge_agent_trace import verdict
from reliability_freeze_v05 import VERSION, verify


# Same task for all cases. No fault type or target node is disclosed to the model.
NEUTRAL_TASK = "检查这个双路由器实验的 OSPF 和双向 Loopback 连通性；若异常，仅凭只读观测鉴别原因，证据不足时明确说明。"
CASES = (("area_mismatch", "ospf_area_mismatch"),
         ("admin_down", "interface_admin_down"),
         ("ospf_passive", "ospf_passive"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_batch() -> Path:
    paths = sorted((LAB_DIR / "stage5-r2-tests").glob("batch-*/batch.json"))
    if not paths:
        raise RuntimeError("No r2 held-out batch exists")
    return paths[-1]


def prepare_cases(batch_path: Path, freeze_at: str, root: Path) -> list[dict]:
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    if (batch.get("status") != "PASS" or batch.get("final_network") != "RESTORED" or
            batch.get("target") != "r2" or batch.get("freeze_at_utc") != freeze_at or
            batch.get("code_version") != VERSION):
        raise RuntimeError("Batch is not a completed, frozen, recovered r2 test")
    rows = batch.get("experiments")
    if not isinstance(rows, list) or [item.get("fault") for item in rows] != [item[0] for item in CASES]:
        raise RuntimeError("Batch does not contain exactly the three expected independent r2 cases")
    prepared = []
    for index, ((fault, expected), item) in enumerate(zip(CASES, rows), 1):
        experiment = Path(item["experiment"])
        # Label metadata remains here in the harness; the Agent receives only one copied observation file.
        metadata = json.loads((experiment / "experiment-record" / "experiment-record.json").read_text(encoding="utf-8"))
        if (metadata.get("fault") != fault or metadata.get("target") != "r2" or
                metadata.get("status") != "PASS" or not metadata.get("phases", {}).get("recovered", {}).get("passed") or
                metadata.get("started_at_utc", "") < freeze_at):
            raise RuntimeError(f"Case {index} is not an independent post-freeze recovered observation")
        source = experiment / "observations" / "fault" / "snapshot" / "observations.json"
        case_dir = root / f"case-{index:02d}"
        case_dir.mkdir()
        frozen = case_dir / "observations.json"
        shutil.copyfile(source, frozen)
        prepared.append({"case": f"case-{index:02d}", "expected": expected,
                         "source_experiment": str(experiment), "observations": str(frozen),
                         "observations_sha256": digest(frozen), "directory": case_dir})
    return prepared


def render(result: dict) -> str:
    lines = ["# Stage 5 冻结后 r2 鉴别诊断验收", "",
             f"状态：`{result['status']}`；批次末尾网络：`{result['final_network']}`", "",
             "三个场景均使用同一任务描述；规则基线与 Agent 逐场景读取同一份只读冻结观测。",
             "故障标签只进入独立评判器，不进入模型提示词或工具结果。", "",
             "| 场景 | 规则基线 | 模型原始 | 保护后 | 独立验收 | 模型 | 请求/工具 | 秒 | 输入/输出 token |",
             "|---|---|---|---|---|---|---:|---:|---:|"]
    for row in result["cases"]:
        lines.append("| {case} | {rule} | {raw} | {final} | {verdict} | {model} | {requests}/{tools} | {elapsed} | {input}/{output} |".format(
            case=row["case"], rule=row.get("rule_status", "未执行"), raw=row.get("model_raw_status", "未执行"),
            final=row.get("final_status", "未执行"), verdict=row.get("verdict", "未执行"),
            model=row.get("model", "—"), requests=row.get("model_requests", 0),
            tools=row.get("tool_calls", 0), elapsed=row.get("elapsed_seconds", "—"),
            input=row.get("input_tokens", "—"), output=row.get("output_tokens", "—")))
    lines += ["", "## 证据与保护层", ""]
    for row in result["cases"]:
        lines += [f"### {row['case']}", "", f"预期仅供独立评判：`{row['expected']}`；冻结观测 SHA256：`{row.get('observations_sha256', '—')}`"]
        if row.get("trace_path"):
            lines.append(f"轨迹：`{row['trace_path']}`；规则报告：`{row['rule_report']}`；评判：`{row['judge_path']}`")
        lines.append(f"引用：{', '.join(row.get('evidence_ids') or []) or '无'}")
        for change in row.get("guardrail_changes") or []:
            lines.append(f"- 保护层调整 `{change['field']}`：{change['reason']}")
        if not row.get("guardrail_changes"):
            lines.append("- 保护层未修改原始诊断。")
        for problem in row.get("problems") or []:
            lines.append(f"- 验收问题：{problem}")
        lines.append("")
    lines += ["说明：这是三个受控故障的有界验收，不能作为通用诊断准确率。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, help="completed batch.json from run_stage5_r2.py")
    args = parser.parse_args()
    freeze = verify()
    settings = load_settings()
    key = get_deepseek_api_key(required=True)
    batch_path = (args.batch or latest_batch()).resolve()
    root = LAB_DIR / "agent-runs" / ("stage5-acceptance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                     + "-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "code_version": freeze["version"],
              "started_at_utc": utc_now(), "finished_at_utc": None,
              "freeze_at_utc": freeze["frozen_at_utc"], "batch": str(batch_path),
              "task": NEUTRAL_TASK, "final_network": "NOT_VERIFIED", "status": "IN_PROGRESS", "cases": []}
    stopped = False
    try:
        prepared = prepare_cases(batch_path, freeze["frozen_at_utc"], root)
        result["final_network"] = "RESTORED"
        model = DeepSeekChatClient(settings, key)
        for case in prepared:
            verify()
            row = {name: value for name, value in case.items() if name != "directory"}
            result["cases"].append(row)
            if stopped:
                row["verdict"] = "NOT_EXECUTED"
                continue
            path = Path(case["observations"])
            before = digest(path)
            document = json.loads(path.read_text(encoding="utf-8"))
            rule = diagnose(document)
            rule_dir = case["directory"]
            (rule_dir / "rule-diagnosis.json").write_text(json.dumps(rule, ensure_ascii=False, indent=2) + "\n",
                                                           encoding="utf-8")
            rule_report = rule_dir / "rule-report.zh-CN.md"
            rule_report.write_text(render_chinese_report(rule), encoding="utf-8")
            row["rule_status"] = rule["status"]
            row["rule_report"] = str(rule_report)
            tools = AgentTools(ReplayBackend(path))
            trace = run_agent(NEUTRAL_TASK, model, tools, settings, mode="replay")
            trace_path = rule_dir / "agent-trace.json"
            trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (rule_dir / "agent-report.zh-CN.md").write_text(render_trace(trace), encoding="utf-8")
            row["trace_path"] = str(trace_path)
            judgment = verdict(trace, case["expected"])
            judge_path = rule_dir / "judge.json"
            judge_path.write_text(json.dumps(judgment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            row["judge_path"] = str(judge_path)
            row.update({"model_raw_status": (trace.get("model_raw_final") or {}).get("status"),
                        "model_raw_final": trace.get("model_raw_final"),
                        "final_status": trace["final"]["status"], "final_diagnosis": trace["final"],
                        "guardrail_changes": trace.get("guardrail_changes") or [],
                        "evidence_ids": trace["final"].get("evidence_ids") or [],
                        "model": trace.get("model_reported"), "model_requests": len(trace["rounds"]),
                        "tool_calls": trace["tool_calls_executed"],
                        "elapsed_seconds": trace["elapsed_seconds"],
                        "input_tokens": trace["usage_total"]["prompt_tokens"],
                        "output_tokens": trace["usage_total"]["completion_tokens"],
                        "total_tokens": trace["usage_total"]["total_tokens"],
                        "verdict": judgment["verdict"] if rule["status"] == case["expected"] else "FAIL",
                        "problems": judgment["problems"] +
                                    ([] if rule["status"] == case["expected"] else ["Rule baseline differs from reference"]),
                        "api_error": trace.get("error")})
            if digest(path) != before or before != case["observations_sha256"]:
                row["verdict"] = "FAIL"
                row["problems"].append("Frozen observations changed during acceptance")
            if trace.get("error"):
                stopped = True
            print(f"{case['case']}: rule={row['rule_status']} raw={row['model_raw_status']} final={row['final_status']} verdict={row['verdict']}", flush=True)
        verify()
        result["status"] = "PASS" if len(result["cases"]) == 3 and all(
            row.get("verdict") == "PASS" for row in result["cases"]) else "FAIL"
    except BaseException as exc:
        result["status"] = "FAIL"
        result["harness_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["finished_at_utc"] = utc_now()
        output = root / "acceptance.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = root / "acceptance.md"
        report.write_text(render(result), encoding="utf-8")
        print(f"Status: {result['status']}")
        print(f"Final network: {result['final_network']} (from completed WSL batch)")
        print(f"Acceptance: {output}")
        print(f"Report: {report}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
