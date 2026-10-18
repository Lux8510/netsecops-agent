#!/usr/bin/env python3
"""Bounded v0.3 live and replay acceptance; never passes reference labels to the Agent.

Run --preflight-only first to inspect the existing WSL lab without model calls or
faults. The full run rechecks that baseline before using the existing, bounded
live experiment and held-out r2 experiment entry points. All results are new;
historical acceptance and freeze files are left untouched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any

from agent_config import configuration_status, get_deepseek_api_key, load_settings
from agent_loop import render_trace, run_agent
from agent_tools import AgentTools, ReplayBackend
from collect_evidence import LAB_DIR, collect_evidence, ensure_wsl2_and_sudo, read_layout, utc_now
from deepseek_client import DeepSeekChatClient
from judge_agent_trace import verdict
from reliability_freeze_v03 import verify
from run_area_mismatch import (file_hash, healthy, metrics_from_evidence,
                               normalized_config, preflight, startup_files)
from run_stage5_acceptance import NEUTRAL_TASK


FIXTURES = LAB_DIR / "tests" / "fixtures" / "observations"
RUN_ROOT = LAB_DIR / "agent-runs"
HARNESSES = ("run_v03_real_acceptance.py", "run_live_agent_acceptance.py",
             "run_stage5_r2.py", "run_stage5_acceptance.py")


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def network_snapshot(root: Path, name: str) -> dict[str, Any]:
    """Capture only this lab and make configuration/health comparison possible."""
    layout = read_layout(LAB_DIR)
    containers = preflight(layout)
    output = root / name
    document = collect_evidence(LAB_DIR, output)
    metrics = metrics_from_evidence(layout, document)
    configs = {}
    startup = {}
    for node in layout.nodes:
        commands = document["nodes"][node]["commands"]
        running = next(item for item in commands if item["id"] == "running_config")
        if running["exit_code"] != 0 or running["error"]:
            raise RuntimeError(f"Cannot verify running configuration for {node}")
        configs[node] = dict(normalized_config(running["stdout"]))
        startup[node] = {kind: file_hash(path) for kind, path in startup_files(layout, node).items()}
    areas = {node: metrics[node]["link_area"] for node in layout.nodes}
    result = {"observations": str(output / "observations.json"), "containers": containers,
              "metrics": metrics, "healthy": healthy(metrics), "link_areas": areas,
              "areas_equal": len(set(areas.values())) == 1,
              "running_config_lines": configs, "startup_sha256": startup}
    save_json(root / f"{name}-check.json", result)
    return result


def distinct_new_dir(parent: Path, pattern: str, before: set[Path]) -> Path:
    found = {path.resolve() for path in parent.glob(pattern) if path.is_dir()} - before
    if len(found) != 1:
        raise RuntimeError(f"Expected one new {pattern} directory, found {len(found)}")
    return found.pop()


def run_existing_live() -> tuple[Path, dict[str, Any]]:
    import run_live_agent_acceptance as live
    parent = RUN_ROOT
    before = {path.resolve() for path in parent.glob("live-acceptance-*") if path.is_dir()}
    original_task = live.TASK
    try:
        # The existing live entry keeps its restoration and independent judge.
        # Only the user task becomes identical to every replay task.
        live.TASK = NEUTRAL_TASK
        exit_code = live.main()
    finally:
        live.TASK = original_task
    path = distinct_new_dir(parent, "live-acceptance-*", before) / "acceptance.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if exit_code != 0 and record.get("status") == "PASS":
        raise RuntimeError("Live entry exit code and saved status disagree")
    return path, record


def resumed_live(path: Path, freeze_sha256: str) -> tuple[Path, dict[str, Any]]:
    """Reuse a recorded, restored live attempt; never rerun its fault to fill gaps."""
    source = path.resolve()
    if source.name != "acceptance.json" or not source.is_relative_to(RUN_ROOT.resolve()):
        raise RuntimeError("Resume path must be an existing project-local acceptance.json")
    previous = json.loads(source.read_text(encoding="utf-8"))
    if (previous.get("code_version") != "v0.3-review-repair" or
            previous.get("freeze_sha256") != freeze_sha256 or
            previous.get("final_network") != "RESTORED" or
            previous.get("config_unchanged") is not True or
            previous.get("startup_unchanged") is not True or
            [row.get("case") for row in previous.get("rows") or []] !=
            ["normal-live", "area-mismatch-live"]):
        raise RuntimeError("Prior live attempt is not the restored v0.3 two-case record")
    live_path = Path(previous["live_acceptance"])
    if not live_path.resolve().is_relative_to(RUN_ROOT.resolve()):
        raise RuntimeError("Prior live acceptance is outside this project")
    live = json.loads(live_path.read_text(encoding="utf-8"))
    experiment = Path(live["experiment_dir"])
    experiment_record = json.loads((experiment / "experiment-record" / "experiment-record.json")
                                   .read_text(encoding="utf-8"))
    if (live.get("experiment_status") != "PASS" or live.get("final_network") != "RESTORED" or
            experiment_record.get("status") != "PASS" or
            experiment_record.get("config_restored") is not True or
            experiment_record.get("startup_unchanged") is not True or
            (experiment_record.get("target") or {}).get("node") != "r1" or
            (experiment_record.get("target") or {}).get("original_area") ==
            (experiment_record.get("target") or {}).get("fault_area") or
            not live.get("normal_trace") or not live.get("fault_trace")):
        raise RuntimeError("Prior live experiment or restoration cannot be verified")
    return live_path, live


def run_existing_batch() -> tuple[Path, dict[str, Any]]:
    import run_stage5_r2 as batch_module
    parent = LAB_DIR / "stage5-r2-tests"
    before = {path.resolve() for path in parent.glob("batch-*") if path.is_dir()}
    exit_code = batch_module.main()
    path = distinct_new_dir(parent, "batch-*", before) / "batch.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if exit_code != 0 and record.get("status") == "PASS":
        raise RuntimeError("Batch entry exit code and saved status disagree")
    return path, record


def run_existing_r2_replay(batch: Path) -> tuple[Path, dict[str, Any]]:
    import run_stage5_acceptance as replay_module
    parent = RUN_ROOT
    before = {path.resolve() for path in parent.glob("stage5-acceptance-*") if path.is_dir()}
    original_argv = sys.argv
    try:
        sys.argv = [str(LAB_DIR / "run_stage5_acceptance.py"), "--batch", str(batch)]
        exit_code = replay_module.main()
    finally:
        sys.argv = original_argv
    path = distinct_new_dir(parent, "stage5-acceptance-*", before) / "acceptance.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if exit_code != 0 and record.get("status") == "PASS":
        raise RuntimeError("Replay entry exit code and saved status disagree")
    return path, record


def row_from_trace(category: str, case: str, trace_path: Path, judgment: dict[str, Any],
                   *, source: str | None = None) -> dict[str, Any]:
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    usage = trace.get("usage_total") or {}
    return {"category": category, "case": case, "source": source,
            "trace": str(trace_path), "model": trace.get("model_reported"),
            "model_requests": len(trace.get("rounds") or []),
            "tool_calls": trace.get("tool_calls_executed"),
            "tool_sequence": [{"name": call.get("function", {}).get("name"),
                               "arguments": call.get("function", {}).get("arguments")}
                              for turn in trace.get("rounds") or []
                              for call in (turn.get("model_message") or {}).get("tool_calls") or []],
            "elapsed_seconds": trace.get("elapsed_seconds"),
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "model_raw_final": trace.get("model_raw_final"),
            "final_diagnosis": trace.get("final"),
            "guardrail_changes": trace.get("guardrail_changes") or [],
            "evidence_ids": (trace.get("final") or {}).get("evidence_ids") or [],
            "judge": judgment, "verdict": judgment.get("verdict"),
            "api_error": trace.get("error")}


def run_deficiency_case(root: Path, index: int, fixture: str, expected: str,
                        settings: Any, key: str) -> dict[str, Any]:
    case_dir = root / f"case-{index:02d}"
    case_dir.mkdir()
    source = FIXTURES / fixture
    copied = case_dir / "observations.json"
    shutil.copyfile(source, copied)
    before = digest(copied)
    model = DeepSeekChatClient(settings, key)
    trace = run_agent(NEUTRAL_TASK, model, AgentTools(ReplayBackend(copied)), settings, mode="replay")
    trace_path = case_dir / "agent-trace.json"
    save_json(trace_path, trace)
    (case_dir / "agent-report.zh-CN.md").write_text(render_trace(trace), encoding="utf-8")
    judgment = verdict(trace, expected)
    save_json(case_dir / "judge.json", judgment)
    row = row_from_trace("real-model replay", f"case-{index:02d}", trace_path, judgment,
                         source=str(copied))
    row["observations_sha256"] = before
    row["judge_path"] = str(case_dir / "judge.json")
    if digest(copied) != before:
        row["verdict"] = "FAIL"
        row["judge"]["problems"].append("Copied observations changed during acceptance")
    return row


def render(record: dict[str, Any]) -> str:
    lines = ["# v0.3 有界真实验收", "", f"状态：{record['status']}；最终网络：{record['final_network']}",
             f"代码版本：{record.get('code_version', '未核对')}；模型：{record.get('model', '未调用')}", "",
             "统一中性任务：" + NEUTRAL_TASK, "",
             "| 类别 | 场景 | 原始状态 | 保护后状态 | 独立判定 | 请求/工具 | 秒 | 输入/输出 token |",
             "|---|---|---|---|---|---:|---:|---:|"]
    for row in record["rows"]:
        raw = (row.get("model_raw_final") or {}).get("status", "未执行")
        final = (row.get("final_diagnosis") or {}).get("status", "未执行")
        lines.append(f"| {row['category']} | {row['case']} | {raw} | {final} | {row.get('verdict')} | "
                     f"{row.get('model_requests')}/{row.get('tool_calls')} | {row.get('elapsed_seconds')} | "
                     f"{row.get('input_tokens')}/{row.get('output_tokens')} |")
    lines += ["", "每行的原始响应、保护调整、evidence_id、工具名称与参数、独立评判见 acceptance.json 和对应 trace。",
              "故障标签只进入验收器；Agent 仅收到中性任务与单次工具观测。", "",
              "## 网络恢复", "", f"现场验收记录：{record.get('live_acceptance') or '未执行'}",
              f"r2 新观测批次：{record.get('r2_batch') or '未执行'}",
              f"最终只读观测：{record.get('network_after', {}).get('observations', '未取得')}",
              f"运行配置一致：{record.get('config_unchanged')}；启动配置一致：{record.get('startup_unchanged')}",
              "", "## 问题与未执行项", ""]
    lines += [f"- {item}" for item in record["errors"]] or ["- 无。"]
    if len(record["rows"]) < 7:
        lines.append(f"- 计划 7 项真实模型诊断；实际完成 {len(record['rows'])} 项，后续未执行。")
    lines += ["", "既有 v0.3 离线 57/0 结果单独保留；本次结果不等于通用诊断准确率。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true", help="no model call or fault injection")
    parser.add_argument("--resume-live", type=Path,
                        help="reuse one verified restored v0.3 live acceptance; do not repeat its fault")
    args = parser.parse_args()
    if args.preflight_only and args.resume_live:
        parser.error("--preflight-only and --resume-live are mutually exclusive")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = RUN_ROOT / f"v03-real-acceptance-{stamp}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {"schema_version": 1, "status": "IN_PROGRESS", "started_at_utc": utc_now(),
                              "code_version": None, "freeze_at_utc": None, "model": None,
                              "task": NEUTRAL_TASK, "preflight_only": args.preflight_only,
                              "key_status": "未配置", "rows": [], "errors": [],
                              "final_network": "NOT_CHECKED", "config_unchanged": None,
                              "startup_unchanged": None}
    path = root / "acceptance.json"
    baseline = None
    try:
        freeze = verify()
        record["code_version"] = freeze["version"]
        record["freeze_at_utc"] = freeze["frozen_at_utc"]
        record["freeze_manifest"] = str(LAB_DIR / "reliability-v0.3.json")
        record["freeze_sha256"] = digest(LAB_DIR / "reliability-v0.3.json")
        record["harness_sha256"] = {name: digest(LAB_DIR / name) for name in HARNESSES}
        settings = load_settings()
        record["model"] = settings.model
        record["limits"] = vars(settings)
        status = configuration_status()
        record["key_status"] = "已配置" if status["configured"] else "未配置"
        if not status["configured"]:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured; no model call or fault started")
        key = get_deepseek_api_key(required=True)
        ensure_wsl2_and_sudo()
        baseline = network_snapshot(root, "network-before")
        record["network_before"] = baseline
        save_json(path, record)
        if not baseline["healthy"] or not baseline["areas_equal"]:
            raise RuntimeError("Current lab baseline is not healthy; no model call or fault started")
        print("Preflight: PASS; freeze v0.3 verified; key 已配置; network healthy", flush=True)
        if args.preflight_only:
            record["status"] = "PASS"
            record["final_network"] = "HEALTHY (preflight only)"
            return 0

        live_path, live = (resumed_live(args.resume_live, record["freeze_sha256"])
                           if args.resume_live else run_existing_live())
        record["live_acceptance"] = str(live_path)
        if args.resume_live:
            record["resumed_from"] = str(args.resume_live.resolve())
        for case, trace_key, judge_key in (("normal-live", "normal_trace", "normal_judge"),
                                           ("area-mismatch-live", "fault_trace", "fault_judge")):
            if live.get(trace_key):
                trace = json.loads(Path(live[trace_key]).read_text(encoding="utf-8"))
                expected = "normal" if case == "normal-live" else "ospf_area_mismatch"
                judgment = verdict(trace, expected)
                record["rows"].append(row_from_trace("real-model live", case,
                    Path(live[trace_key]), judgment))
        save_json(path, record)
        if live.get("experiment_status") != "PASS" or live.get("final_network") != "RESTORED":
            record["errors"].extend(str(item) for item in live.get("errors") or [])
            raise RuntimeError("Live experiment or restoration failed; no new fault is started")
        if any(row["api_error"] for row in record["rows"]):
            raise RuntimeError("Live model API error; further model calls stopped")
        for row in record["rows"]:
            if row["verdict"] != "PASS":
                record["errors"].extend(f"{row['case']}: {item}"
                                        for item in row["judge"].get("problems") or [])
        verify()

        batch_path, batch = run_existing_batch()
        record["r2_batch"] = str(batch_path)
        save_json(path, record)
        if batch.get("status") != "PASS" or batch.get("final_network") != "RESTORED":
            record["errors"].extend(str(item) for item in batch.get("errors") or [])
            raise RuntimeError("New r2 observation batch failed; no replay model calls started")
        verify()

        replay_path, replay = run_existing_r2_replay(batch_path)
        record["r2_replay_acceptance"] = str(replay_path)
        for case in replay.get("cases") or []:
            if case.get("trace_path"):
                row = row_from_trace("real-model replay", case["case"], Path(case["trace_path"]),
                                     json.loads(Path(case["judge_path"]).read_text(encoding="utf-8")),
                                     source=case.get("observations"))
                row["rule_status"] = case.get("rule_status")
                row["observations_sha256"] = case.get("observations_sha256")
                row["verdict"] = case.get("verdict")
                record["rows"].append(row)
        save_json(path, record)
        if replay.get("status") != "PASS":
            for case in replay.get("cases") or []:
                if case.get("api_error"):
                    error = case["api_error"]
                    record["errors"].append(
                        f"{case['case']} API {error.get('category')}, HTTP {error.get('status_code')}")
                record["errors"].extend(f"{case['case']}: {item}" for item in case.get("problems") or [])
            if (len(replay.get("cases") or []) != 3 or
                    any(not case.get("trace_path") or case.get("api_error")
                        for case in replay.get("cases") or [])):
                raise RuntimeError("Three-case replay incomplete or API failed; remaining model calls stopped")
        verify()

        for index, fixture, expected in ((6, "missing-one-area.json", "insufficient_evidence"),
                                         (7, "failed-command.json", "collection_failure")):
            row = run_deficiency_case(root, index, fixture, expected, settings, key)
            record["rows"].append(row)
            save_json(path, record)
            if row["api_error"] or row["verdict"] != "PASS":
                if row["api_error"]:
                    record["errors"].append(
                        f"{row['case']} API {row['api_error'].get('category')}, "
                        f"HTTP {row['api_error'].get('status_code')}")
                record["errors"].extend(f"{row['case']}: {item}" for item in row["judge"].get("problems") or [])
                if row["api_error"]:
                    raise RuntimeError(f"{row['case']} API failed; further model calls stopped")
        verify()
    except BaseException as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if baseline is not None and not args.preflight_only:
            try:
                final = network_snapshot(root, "network-after")
                record["network_after"] = final
                record["config_unchanged"] = final["running_config_lines"] == baseline["running_config_lines"]
                record["startup_unchanged"] = final["startup_sha256"] == baseline["startup_sha256"]
                record["final_network"] = ("RESTORED" if final["healthy"] and final["areas_equal"] and
                                           record["config_unchanged"] and record["startup_unchanged"]
                                           else "NOT_CONFIRMED")
            except BaseException as exc:
                record["errors"].append(f"Final independent network check failed: {type(exc).__name__}: {exc}")
                record["final_network"] = "NOT_CONFIRMED"
        if not args.preflight_only:
            record["status"] = ("PASS" if len(record["rows"]) == 7 and
                                all(row["verdict"] == "PASS" for row in record["rows"]) and
                                record["final_network"] == "RESTORED" and not record["errors"]
                                else "FAIL")
        elif record["status"] == "IN_PROGRESS":
            record["status"] = "FAIL"
        record["finished_at_utc"] = utc_now()
        save_json(path, record)
        (root / "acceptance.md").write_text(render(record), encoding="utf-8")
        print(f"Status: {record['status']}")
        print(f"Final network: {record['final_network']}")
        print(f"Acceptance: {path}")
        for error in record["errors"]:
            print(f"Error: {error}", file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
