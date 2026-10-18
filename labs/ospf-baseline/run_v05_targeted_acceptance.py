#!/usr/bin/env python3
"""One-shot v0.5 real-model acceptance; read-only live check and three replay cases.

No fault injection, configuration command, or business-source change is made.
The scenario labels below are harness metadata and never enter the Agent task.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import uuid
from typing import Any

from agent_config import configuration_status, get_deepseek_api_key, load_settings
from agent_loop import cited_admin_facts, render_trace, run_agent
from agent_tools import AgentTools, LiveBackend, ReplayBackend, check_deadline
from collect_evidence import LAB_DIR, ensure_wsl2_and_sudo, observation_mismatch, utc_now
from deepseek_client import DeepSeekChatClient
from evidence_checks import critical_history
from judge_agent_trace import verdict
from reliability_freeze_v05 import verify
from run_stage5_acceptance import NEUTRAL_TASK
from run_v03_real_acceptance import network_snapshot


FIXTURES = LAB_DIR / "tests" / "fixtures" / "observations"
RUN_ROOT = LAB_DIR / "agent-runs"
MANIFEST = LAB_DIR / "reliability-v0.5.json"
CASES = (
    ("case-01", "live-normal", None, "normal"),
    ("case-02", "replay-command-failure", "failed-command.json", "collection_failure"),
    ("case-03", "replay-missing-area", "missing-one-area.json", "insufficient_evidence"),
    ("case-04", "dynamic-successful-recheck", "stage5-case-02.json", "interface_admin_down"),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_error(exc: BaseException, key: str | None) -> str:
    message = str(exc)
    if key:
        message = message.replace(key, "[REDACTED]")
    message = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", message)
    return f"{type(exc).__name__}: {message[:500]}"


def verified_fixture(name: str, freeze: dict[str, Any], case_dir: Path) -> Path:
    source = FIXTURES / name
    relative = f"tests/fixtures/observations/{name}"
    if sha(source) != freeze["tests_sha256"][relative]:
        raise RuntimeError("Replay fixture differs from v0.5 freeze")
    copied = case_dir / "observations.json"
    shutil.copyfile(source, copied)
    if sha(copied) != sha(source):
        raise RuntimeError("Replay fixture changed while copying")
    return copied


class OneFailureThenDownBackend(ReplayBackend):
    """Only r2's first OSPF read fails; later reads use the same frozen down snapshot."""

    def __init__(self, snapshot: Path, failed_record: dict[str, Any]) -> None:
        super().__init__(snapshot)
        self.failed_record = deepcopy(failed_record)
        self.r2_ospf_reads = 0

    def get(self, node: str, key: str, *, deadline: float | None = None) -> dict[str, Any]:
        check_deadline(deadline)
        if node == "r2" and key == "ospf_interface":
            self.r2_ospf_reads += 1
            if self.r2_ospf_reads == 1:
                item = deepcopy(self.failed_record)
                mismatch = observation_mismatch(self.layout, node, key, item, self.details(deadline=deadline))
                if mismatch:
                    item["validation_error"] = mismatch
                    item["error"] = mismatch
                return item
        return super().get(node, key, deadline=deadline)


def dynamic_backend(snapshot: Path, freeze: dict[str, Any], case_dir: Path) -> OneFailureThenDownBackend:
    failed_fixture = FIXTURES / "failed-command.json"
    if sha(failed_fixture) != freeze["tests_sha256"]["tests/fixtures/observations/failed-command.json"]:
        raise RuntimeError("Failure fixture differs from v0.5 freeze")
    document = json.loads(failed_fixture.read_text(encoding="utf-8"))
    matches = [item for item in document["nodes"]["r2"]["commands"]
               if item.get("id") == "ospf_interface"]
    if len(matches) != 1 or matches[0].get("exit_code") == 0 or not matches[0].get("error"):
        raise RuntimeError("Frozen first-failure record is not a command execution failure")
    save_json(case_dir / "first-failure-observation.json", matches[0])
    save_json(case_dir / "replay-policy.json", {
        "node": "r2", "command_id": "ospf_interface", "first_read": "frozen execution failure",
        "later_reads": "frozen stage5-case-02 observation", "first_fixture_sha256": sha(failed_fixture),
        "snapshot_sha256": sha(snapshot), "agent_access": "tool result only; metadata not given to model",
    })
    return OneFailureThenDownBackend(snapshot, matches[0])


def trace_row(case: str, category: str, trace: dict[str, Any], path: Path) -> dict[str, Any]:
    usage = trace.get("usage_total") or {}
    return {"case": case, "category": category, "trace": str(path),
            "model": trace.get("model_reported"), "model_requests": len(trace.get("rounds") or []),
            "tool_calls": trace.get("tool_calls_executed"), "elapsed_seconds": trace.get("elapsed_seconds"),
            "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "model_raw_final": trace.get("model_raw_final"), "final_diagnosis": trace.get("final"),
            "evidence_additions": trace.get("evidence_additions") or [],
            "guardrail_changes": trace.get("guardrail_changes") or [],
            "evidence_ids": (trace.get("final") or {}).get("evidence_ids") or [],
            "tool_sequence": [{"name": call.get("function", {}).get("name"),
                               "arguments": call.get("function", {}).get("arguments")}
                              for turn in trace.get("rounds") or []
                              for call in (turn.get("model_message") or {}).get("tool_calls") or []],
            "api_error": trace.get("error")}


def judge_dynamic(trace: dict[str, Any], tools: AgentTools,
                  backend: OneFailureThenDownBackend) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = tools.evidence
    attempts = [(identifier, item) for identifier, item in evidence.items()
                if item.get("tool") == "query_ospf_interface" and
                item.get("arguments") == {"node": "r2", "interface": tools.layout.interfaces["r2"]}]
    active, superseded = critical_history(tools.layout, evidence)
    first_failure = bool(attempts and attempts[0][1].get("status") == "collection_failed")
    successful_recheck = bool(backend.r2_ospf_reads >= 2 and first_failure and
                              any(item["failed_evidence_id"] == attempts[0][0] and
                                  item["node"] == "r2" and item["tool"] == "query_ospf_interface"
                                  for item in superseded))
    admin_obtained = cited_admin_facts(list(evidence), tools).get("r2")
    admin_cited = cited_admin_facts((trace.get("final") or {}).get("evidence_ids") or [], tools).get("r2")
    checks = {"r2_ospf_reads": backend.r2_ospf_reads,
              "r2_ospf_evidence_ids": [identifier for identifier, _ in attempts],
              "first_execution_failure": first_failure,
              "valid_successful_recheck": successful_recheck,
              "active_critical_failures": active, "superseded_attempts": superseded,
              "admin_evidence_obtained": admin_obtained, "admin_evidence_cited": admin_cited,
              "path_status": "EXERCISED" if successful_recheck and admin_obtained else "NOT_EXERCISED"}
    if checks["path_status"] == "EXERCISED":
        return verdict(trace, "interface_admin_down"), checks
    if first_failure and not successful_recheck:
        judgment = verdict(trace, "collection_failure")
        checks["current_failure_judge"] = judgment["verdict"]
        return judgment, checks
    if successful_recheck:
        # A missing direct admin observation cannot be promoted to a root-cause PASS.
        checks["resolved_failure_not_reasserted"] = (
            not any(item["node"] == "r2" and item["tool"] == "query_ospf_interface" for item in active)
            and (trace.get("final") or {}).get("status") != "collection_failure")
        judgment = verdict(trace, "interface_admin_down")
        checks["admin_judge_without_direct_evidence"] = judgment["verdict"]
        return judgment, checks
    return {"verdict": "NOT_EXERCISED", "expected": None,
            "actual": (trace.get("final") or {}).get("status"),
            "problems": ["Model did not execute the targeted first r2 OSPF query"]}, checks


def render(record: dict[str, Any]) -> str:
    lines = ["# v0.5 真实模型定向验收", "",
             f"状态：{record['status']}；最终网络：{record['final_network']}",
             f"代码版本：{record.get('code_version') or '未核对'}；模型：{record.get('model') or '未调用'}", "",
             "统一中性任务：" + NEUTRAL_TASK, "",
             "| 场景 | 模型原始 | 保护后 | 独立判定 | 路径 | 请求/工具 | 秒 | 输入/输出 token |",
             "|---|---|---|---|---|---:|---:|---:|"]
    for row in record["cases"]:
        lines.append("| {case} | {raw} | {final} | {verdict} | {path} | {requests}/{tools} | {elapsed} | {input}/{output} |".format(
            case=row["case"], raw=(row.get("model_raw_final") or {}).get("status", "未执行"),
            final=(row.get("final_diagnosis") or {}).get("status", "未执行"),
            verdict=row.get("verdict", "NOT_EXECUTED"), path=row.get("path_status", "—"),
            requests=row.get("model_requests", 0), tools=row.get("tool_calls", 0),
            elapsed=row.get("elapsed_seconds", "—"), input=row.get("input_tokens", "—"),
            output=row.get("output_tokens", "—")))
    lines += ["", "各 case/agent-trace.json 含模型实际 tool_calls、完整原始工具输出和 evidence_id；",
              "case/judge.json 含独立判定；acceptance.json 含模型原始/保护后结果、补引来源与 token 汇总。",
              "动态回放的 replay-policy.json 只供验收复核，从未作为模型输入。", "",
              "## 网络检查", "", "未注入故障，因此这里只报告只读健康与配置是否未变，绝不称故障恢复。",
              f"运行配置未变：{record.get('running_config_unchanged')}；启动文件未变：{record.get('startup_unchanged')}",
              "", "## 问题", ""]
    lines += [f"- {item}" for item in record.get("errors") or []] or ["- 无。"]
    lines += ["", "单次有界验收不能作为通用诊断准确率；v0.1–v0.5 历史记录不覆盖。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true", help="verify freeze/key status/live baseline; no API")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = RUN_ROOT / f"v05-targeted-{stamp}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    path = root / "acceptance.json"
    record: dict[str, Any] = {
        "schema_version": 1, "status": "IN_PROGRESS", "started_at_utc": utc_now(),
        "preflight_only": args.preflight_only, "code_version": None, "freeze_sha256": None,
        "harness_sha256": sha(Path(__file__)), "task": NEUTRAL_TASK,
        "key_status": "未配置", "model": None, "limits": None,
        "cases": [{"case": case, "category": category, "expected_for_judge_only": expected,
                   "verdict": "NOT_EXECUTED"} for case, category, _, expected in CASES],
        "errors": [], "final_network": "NOT_CHECKED",
        "running_config_unchanged": None, "startup_unchanged": None,
    }
    save_json(path, record)
    baseline = None
    baseline_accepted = False
    key: str | None = None
    try:
        freeze = verify()
        record["code_version"] = freeze["version"]
        record["freeze_sha256"] = sha(MANIFEST)
        settings = load_settings()
        record["model"] = settings.model
        record["limits"] = vars(settings)
        record["key_status"] = "已配置" if configuration_status()["configured"] else "未配置"
        if record["key_status"] != "已配置":
            raise RuntimeError("DEEPSEEK_API_KEY is not configured; no model request started")
        ensure_wsl2_and_sudo()
        baseline = network_snapshot(root, "network-before")
        record["network_before"] = baseline
        save_json(path, record)
        if not baseline["healthy"] or not baseline["areas_equal"]:
            raise RuntimeError("Current lab baseline is unhealthy; no model request started")
        baseline_accepted = True
        print("Preflight: PASS; freeze v0.5 verified; key 已配置; network healthy", flush=True)
        if args.preflight_only:
            record["status"] = "PASS"
            record["final_network"] = "HEALTHY (preflight only)"
            return 0
        key = get_deepseek_api_key(required=True)
        model = DeepSeekChatClient(settings, key)
        for index, (case, category, fixture, expected) in enumerate(CASES):
            row = record["cases"][index]
            verify()
            case_dir = root / case
            case_dir.mkdir()
            if fixture is None:
                tools = AgentTools(LiveBackend(settings.command_timeout_seconds))
                mode = "live"
            else:
                copied = verified_fixture(fixture, freeze, case_dir)
                row["observations"] = str(copied)
                row["observations_sha256"] = sha(copied)
                backend = (dynamic_backend(copied, freeze, case_dir) if index == 3
                           else ReplayBackend(copied))
                tools = AgentTools(backend)
                mode = "replay"
            trace = run_agent(NEUTRAL_TASK, model, tools, settings, mode=mode)
            trace_path = case_dir / "agent-trace.json"
            save_json(trace_path, trace)
            (case_dir / "agent-report.zh-CN.md").write_text(render_trace(trace), encoding="utf-8")
            row.update(trace_row(case, category, trace, trace_path))
            if fixture is not None and sha(case_dir / "observations.json") != row["observations_sha256"]:
                row["verdict"] = "FAIL"
                row["problems"] = ["Frozen replay observations changed during acceptance"]
            if index == 3:
                judgment, checks = judge_dynamic(trace, tools, backend)
                row["dynamic_checks"] = checks
                row["path_status"] = checks["path_status"]
                row["judge_expected"] = judgment["expected"]
                row["judge"] = judgment
                save_json(case_dir / "judge.json", judgment)
                if row["verdict"] != "FAIL":
                    if checks["path_status"] == "EXERCISED":
                        row["verdict"] = judgment["verdict"]
                    elif checks["first_execution_failure"] and not checks["valid_successful_recheck"]:
                        row["verdict"] = "NOT_EXERCISED" if judgment["verdict"] == "PASS" else "FAIL"
                    elif checks["valid_successful_recheck"]:
                        row["verdict"] = (
                            "NOT_EXERCISED" if checks["resolved_failure_not_reasserted"] and
                            (trace.get("final") or {}).get("status") in ("insufficient_evidence", "incomplete")
                            else "FAIL")
                    else:
                        row["verdict"] = (
                            "NOT_EXERCISED" if (trace.get("final") or {}).get("status") in
                            ("insufficient_evidence", "incomplete") else "FAIL")
            else:
                judgment = verdict(trace, expected)
                row["judge_expected"] = expected
                row["judge"] = judgment
                save_json(case_dir / "judge.json", judgment)
                if row["verdict"] != "FAIL":
                    row["verdict"] = judgment["verdict"]
            save_json(path, record)
            print(f"{case}: raw={(trace.get('model_raw_final') or {}).get('status')} "
                  f"final={(trace.get('final') or {}).get('status')} verdict={row['verdict']}", flush=True)
            if trace.get("error"):
                error = trace["error"]
                record["errors"].append(f"{case}: API/local {error.get('category')}, HTTP {error.get('status_code')}")
                print("Model/API error: further cases will not be called", flush=True)
                break
        verify()
    except Exception as exc:
        record["errors"].append(safe_error(exc, key))
    finally:
        if baseline_accepted and not args.preflight_only:
            try:
                after = network_snapshot(root, "network-after")
                record["network_after"] = after
                record["running_config_unchanged"] = (
                    after["running_config_lines"] == baseline["running_config_lines"])
                record["startup_unchanged"] = after["startup_sha256"] == baseline["startup_sha256"]
                record["final_network"] = (
                    "HEALTHY_UNCHANGED" if after["healthy"] and after["areas_equal"] and
                    record["running_config_unchanged"] and record["startup_unchanged"]
                    else "NOT_CONFIRMED")
            except Exception as exc:
                record["errors"].append("Final read-only network check: " + safe_error(exc, key))
                record["final_network"] = "NOT_CONFIRMED"
        if not args.preflight_only:
            if (record["final_network"] != "HEALTHY_UNCHANGED" or record["errors"] or
                    any(row.get("verdict") == "FAIL" for row in record["cases"])):
                record["status"] = "FAIL"
            elif all(row.get("verdict") == "PASS" for row in record["cases"]):
                record["status"] = "PASS"
            else:
                record["status"] = "INCOMPLETE"
        elif record["status"] == "IN_PROGRESS":
            record["status"] = "FAIL"
        record["finished_at_utc"] = utc_now()
        save_json(path, record)
        (root / "acceptance.md").write_text(render(record), encoding="utf-8")
        print(f"Status: {record['status']}")
        print(f"Final network: {record['final_network']}")
        print(f"Acceptance: {path}")
        print(f"Report: {root / 'acceptance.md'}")
        for error in record["errors"]:
            print(f"Error: {error}", file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
