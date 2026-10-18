#!/usr/bin/env python3
"""Bounded real-model/live-tool acceptance with guaranteed experiment restoration.

Run only in the existing Ubuntu/WSL2 lab. The experiment owns fault injection and
restoration; the Agent receives neither its record nor its expected root cause.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import uuid
from typing import Any

from agent_cli import save_trace
from agent_config import get_deepseek_api_key, load_settings
from agent_loop import run_agent
from agent_tools import AgentTools, LiveBackend
from collect_evidence import LAB_DIR, ensure_wsl2_and_sudo
from deepseek_client import DeepSeekChatClient
from judge_agent_trace import verdict
from run_area_mismatch import healthy, interrupt_handler, run_experiment


TASK = ("检查 r1 到 r2 Loopback 的连通性并解释异常原因。请自行选择必要的只读工具，"
        "在调用上限内优先核对 OSPF 区域、邻居、远端路由和双向 Loopback ping；不必检查与结论无关的接口。")


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ensure_wsl2_and_sudo()
    settings = load_settings()
    client = DeepSeekChatClient(settings, get_deepseek_api_key(required=True))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = LAB_DIR / "agent-runs" / f"live-acceptance-{stamp}-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {
        "status": "IN_PROGRESS", "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "normal_trace": None, "normal_judge": None, "fault_trace": None, "fault_judge": None,
        "experiment_dir": None, "experiment_status": "NOT_RUN", "final_network": "NOT_CHECKED",
        "errors": [],
    }
    record_path = output / "acceptance.json"
    save_json(record_path, record)

    def diagnose(label: str) -> tuple[Path, dict[str, Any]]:
        # No injection record, phase label, or expected outcome enters the Agent prompt.
        tools = AgentTools(LiveBackend(settings.command_timeout_seconds))
        trace = run_agent(TASK, client, tools, settings, mode="live")
        trace_path, _ = save_trace(trace, label)
        return trace_path, trace

    try:
        print("[1/3] Running read-only Agent against the current live lab", flush=True)
        normal_path, normal_trace = diagnose("live-normal")
        record["normal_trace"] = str(normal_path)
        record["normal_judge"] = verdict(normal_trace, "normal")
        save_json(record_path, record)
        if normal_trace.get("error"):
            error = normal_trace["error"]
            record["errors"].append(
                f"Normal live model request failed ({error.get('category')}, HTTP {error.get('status_code')}): "
                f"{error.get('message')}; fault injection was not started")
            return 1
        print(f"[1/3] Normal Agent status: {normal_trace['final']['status']}", flush=True)

        def at_stable_fault() -> None:
            print("[2/3] Fault stabilized; running read-only Agent before restoration", flush=True)
            fault_path, fault_trace = diagnose("live-fault")
            record["fault_trace"] = str(fault_path)
            record["fault_judge"] = verdict(fault_trace, "ospf_area_mismatch")
            save_json(record_path, record)
            if fault_trace.get("error"):
                error = fault_trace["error"]
                record["errors"].append(
                    f"Fault live model request failed ({error.get('category')}, HTTP {error.get('status_code')}): "
                    f"{error.get('message')}; restoring the lab")
                raise RuntimeError("Fault live model request failed; experiment must restore now")

        print("[2/3] Starting the bounded existing experiment; restoration is in its finally block", flush=True)
        previous_term = signal.signal(signal.SIGTERM, interrupt_handler)
        try:
            experiment_path, experiment = run_experiment(LAB_DIR / "experiments", on_fault_stable=at_stable_fault)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
        record["experiment_dir"] = str(experiment_path)
        record["experiment_status"] = experiment["status"]
        recovered = experiment.get("phases", {}).get("recovered", {})
        metrics = recovered.get("metrics")
        record["final_network"] = (
            "RESTORED" if experiment.get("config_restored") is True and
            experiment.get("startup_unchanged") is True and recovered.get("poll_matched") is True and
            isinstance(metrics, dict) and healthy(metrics) else "NOT_CONFIRMED"
        )
        record["final_recovery_metrics"] = metrics
        print(f"[3/3] Experiment: {experiment['status']}; final network: {record['final_network']}", flush=True)
        if experiment["status"] != "PASS":
            record["errors"].extend(experiment.get("errors", []))
        if record["fault_trace"] is None:
            record["errors"].append("Fault Agent trace was not produced")
    except BaseException as exc:
        record["errors"].append(f"{type(exc).__name__}: live acceptance did not complete")
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        passes = (record["normal_judge"] or {}).get("verdict") == "PASS" and \
                 (record["fault_judge"] or {}).get("verdict") == "PASS" and \
                 record["experiment_status"] == "PASS" and record["final_network"] == "RESTORED"
        record["status"] = "PASS" if passes and not record["errors"] else "FAIL"
        save_json(record_path, record)
        lines = ["# 真实模型现场验收", "", f"总状态：**{record['status']}**",
                 f"最终网络：**{record['final_network']}**", "",
                 f"正常 Agent：`{record['normal_trace'] or '未执行'}`；"
                 f"判定：`{(record['normal_judge'] or {}).get('verdict', '未执行')}`", "",
                 f"故障 Agent：`{record['fault_trace'] or '未执行'}`；"
                 f"判定：`{(record['fault_judge'] or {}).get('verdict', '未执行')}`", "",
                 f"故障与恢复实验：`{record['experiment_dir'] or '未执行'}`；"
                 f"状态：`{record['experiment_status']}`", "", "错误："]
        lines += [f"- {item}" for item in record["errors"]] or ["- 无。"]
        lines += ["", "完整调用、证据、token 用量见各自 trace.json；原始网络恢复证据见实验目录。", ""]
        (output / "acceptance.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"Status: {record['status']}")
        print(f"Final network: {record['final_network']}")
        print(f"Acceptance: {record_path}")
        print(f"Report: {output / 'acceptance.md'}")
        if record["experiment_dir"]:
            print(f"Experiment: {record['experiment_dir']}")
        for item in record["errors"]:
            print(f"Error: {item}", file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
