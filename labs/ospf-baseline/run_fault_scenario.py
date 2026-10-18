#!/usr/bin/env python3
"""Bounded, single-fault experiment. Only this file can change live FRR config."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from typing import Any

from collect_evidence import LAB_DIR, collect_evidence, ensure_wsl2_and_sudo, read_layout, sections, utc_now
from run_area_mismatch import (
    ExperimentError, by_id, choose_fault_area, configure_interface_area,
    configure_network, healthy, interface_link_areas, link_areas,
    metrics_from_evidence, normalized_config, preflight, running_config,
    startup_files, write_json,
)


FAULTS = ("area_mismatch", "admin_down", "ospf_passive")
FAULT_TIMEOUT_SECONDS = 90
RECOVERY_TIMEOUT_SECONDS = 90
POLL_COMMANDS = ("ospf_neighbors", "ospf_routes", "ping_peer_link", "ping_remote_loopback")


def interface_lines(config: str, interface: str) -> list[str]:
    lines = sections(config).get(f"interface {interface}")
    if lines is None:
        raise ExperimentError(f"Interface {interface} missing from running config")
    return lines


def configure_setting(layout: Any, node: str, interface: str, setting: str) -> dict[str, Any]:
    if node not in layout.nodes or interface != layout.interfaces[node]:
        raise ExperimentError("Configuration target is not a lab interconnect interface")
    if setting not in ("shutdown", "no shutdown", "ip ospf passive", "no ip ospf passive"):
        raise ExperimentError("Configuration command is not in the fixed fault/restore allowlist")
    argv = ["sudo", "-n", "docker", "exec", layout.container(node), "vtysh",
            "-c", "configure terminal", "-c", f"interface {interface}",
            "-c", setting, "-c", "end"]
    done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=20, check=False)
    action = {"at_utc": utc_now(), "command": argv, "exit_code": done.returncode,
              "stdout": done.stdout, "stderr": done.stderr}
    if done.returncode != 0 or re.search(r"(?im)^(?:%\s*)?(?:unknown command|invalid input|error:)\b", done.stdout):
        raise ExperimentError(f"FRR rejected {setting!r}: exit={done.returncode}; {done.stdout.strip()} {done.stderr.strip()}")
    return action


def fault_stable(metrics: dict[str, Any], fault: str) -> bool:
    lost_ospf = all(not item["neighbor_full"] and not item["remote_loopback_route"]
                    and not item["remote_loopback_ping"] for item in metrics.values())
    if not lost_ospf:
        return False
    if fault == "admin_down":
        return True  # A down link may fail both direct-link pings; record actual result.
    return all(item["peer_link_ping"] for item in metrics.values())


def poll(layout: Any, details: dict[str, dict[str, str]], output: Path,
         fault: str | None, timeout: int) -> tuple[bool, dict[str, Any] | None]:
    from collect_evidence import execute

    samples: list[dict[str, Any]] = []
    latest = None
    matched = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            sample = {"collected_at_utc": utc_now(), "nodes": {}}
            for node in layout.nodes:
                sample["nodes"][node] = {"commands": [execute(layout, node, key, details)
                                                       for key in POLL_COMMANDS]}
            samples.append(sample)
            latest = metrics_from_evidence(layout, {"nodes": sample["nodes"], "discovered": details,
                                                    "discovery_error": None})
            matched = fault_stable(latest, fault) if fault else healthy(latest)
            if matched or time.monotonic() >= deadline:
                break
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    finally:
        write_json(output, {"schema_version": 1, "samples": samples})
    return matched, latest


def direct_fault_present(layout: Any, evidence: dict[str, Any], fault: str,
                         target: str, changed_area: str | None) -> bool:
    details = evidence["discovered"]
    iface = layout.interfaces[target]
    config = by_id(evidence, target, "running_config")["stdout"]
    lines = interface_lines(config, iface)
    if fault == "area_mismatch":
        return (details[target]["link_area"] == changed_area and
                details[target]["link_area"] != details[layout.peer(target)]["link_area"])
    if fault == "ospf_passive":
        return "ip ospf passive" in lines
    output = by_id(evidence, target, "frr_interface")["stdout"]
    return ("shutdown" in lines and
            re.search(r"(?m)^Interface\s+" + re.escape(iface) + r"\s+is\s+down\b", output) is not None and
            re.search(r"(?m)^\s*Shutdown config is\s+(?!Not specified by CLI|off|disabled).+", output,
                      re.IGNORECASE) is not None)


def manual_recovery(layout: Any, target: str, fault: str, area: str | None,
                    changed_area: str | None, mode: str, prefix: str) -> str:
    iface = layout.interfaces[target]
    container = layout.container(target)
    if fault == "admin_down":
        context, commands = f"interface {iface}", ["no shutdown"]
    elif fault == "ospf_passive":
        context, commands = f"interface {iface}", ["no ip ospf passive"]
    elif mode == "interface":
        context, commands = f"interface {iface}", [f"no ip ospf area {changed_area}", f"ip ospf area {area}"]
    else:
        context, commands = "router ospf", [f"no network {prefix} area {changed_area}",
                                             f"network {prefix} area {area}"]
    command = (f"sudo docker exec {container} vtysh -c 'configure terminal' -c '{context}' " +
               " ".join(f"-c '{item}'" for item in commands) + " -c 'end'")
    return (f"仅在确认自动恢复失败后，在 Ubuntu/WSL2 核对 `{container}` 的 `show running-config`，"
            f"对照 `experiment-record/original-{target}-running-config.txt`。"
            f"若故障命令仍在，执行 `{command}`；再双向检查邻居、OSPF /32 路由、互联与 Loopback ping。")


@contextmanager
def experiment_signal_handlers():
    """Make imported and CLI experiments share the same recoverable signal path."""
    previous_int = signal.signal(signal.SIGINT, interrupt_handler)
    previous_term = signal.signal(signal.SIGTERM, interrupt_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def run_experiment(fault: str, target: str, output_root: Path) -> tuple[Path, dict[str, Any]]:
    with experiment_signal_handlers():
        return _run_experiment(fault, target, output_root)


def _run_experiment(fault: str, target: str, output_root: Path) -> tuple[Path, dict[str, Any]]:
    if fault not in FAULTS or target not in ("r1", "r2"):
        raise ValueError("Only one of the three fixed faults on r1 or r2 is allowed")
    ensure_wsl2_and_sudo()
    layout = read_layout(LAB_DIR)
    if target not in layout.nodes:
        raise ExperimentError("Target is not a topology node")
    run_dir = output_root / (f"{fault}-{target}-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    observations = run_dir / "observations"
    observations.mkdir()
    secret = run_dir / "experiment-record"
    secret.mkdir()
    record: dict[str, Any] = {"schema_version": 1, "fault": fault, "target": target,
                              "started_at_utc": utc_now(), "finished_at_utc": None,
                              "status": "IN_PROGRESS", "phases": {}, "actions": [], "errors": [],
                              "expected_root_cause": {"area_mismatch": "different interconnect OSPF Areas",
                                                      "admin_down": "target interconnect administratively shut",
                                                      "ospf_passive": "target interconnect OSPF passive"}[fault]}
    record_path = secret / "experiment-record.json"
    write_json(record_path, record)
    injected = False
    original_config = None
    details = None
    area = changed_area = None
    mode = prefix = None
    startup_hashes: dict[str, str] = {}
    try:
        record["preflight"] = preflight(layout)
        # The official docs/expected image are 10.7.1; confirm the actual FRR binary too.
        for node in layout.nodes:
            done = subprocess.run(["sudo", "-n", "docker", "exec", layout.container(node), "vtysh",
                                   "-c", "show version"], capture_output=True, text=True,
                                  errors="replace", timeout=15, check=False)
            if done.returncode != 0 or not re.search(r"(?m)^FRRouting 10\.7\.1(?:_git)?\b", done.stdout):
                raise ExperimentError(f"Unexpected FRR version on {node}; no fault injected")
        baseline = collect_evidence(LAB_DIR, observations / "normal")
        normal_metrics = metrics_from_evidence(layout, baseline)
        record["phases"]["normal"] = {"metrics": normal_metrics, "passed": healthy(normal_metrics)}
        if not healthy(normal_metrics):
            raise ExperimentError("Existing network baseline is not healthy; no fault injected")
        details = baseline["discovered"]
        if details[layout.nodes[0]]["link_area"] != details[layout.nodes[1]]["link_area"]:
            raise ExperimentError("Link Areas already differ; no fault injected")
        iface = layout.interfaces[target]
        original_config = by_id(baseline, target, "running_config")["stdout"]
        original_lines = interface_lines(original_config, iface)
        process_lines = sections(original_config).get("router ospf", [])
        if any(item in original_lines for item in ("shutdown", "no shutdown", "ip ospf passive", "no ip ospf passive")):
            raise ExperimentError("Target interface has an existing explicit admin/passive state; no fault injected")
        if any(item in process_lines for item in (f"passive-interface {iface}", "passive-interface default")):
            raise ExperimentError("Target interface is already passive; no fault injected")
        (secret / f"original-{target}-running-config.txt").write_text(original_config, encoding="utf-8")
        for label, path in startup_files(layout, target).items():
            data = path.read_bytes()
            (secret / f"original-{label}-startup-frr.conf").write_bytes(data)
            startup_hashes[label] = hashlib.sha256(data).hexdigest()
        record["startup_sha256_before"] = startup_hashes
        area = details[target]["link_area"]
        mode = details[target]["ospf_mode"]
        prefix = details[target]["link_prefix"]
        changed_area = choose_fault_area(area) if fault == "area_mismatch" else None
        record["interface"] = iface
        record["manual_recovery"] = manual_recovery(layout, target, fault, area, changed_area, mode, prefix)
        write_json(record_path, record)
        try:
            injected = True  # Even a partially applied command must trigger restoration.
            if fault == "area_mismatch" and mode == "interface":
                action = configure_interface_area(layout, target, iface,
                                                  [f"no ip ospf area {area}", f"ip ospf area {changed_area}"])
            elif fault == "area_mismatch" and mode == "network":
                action = configure_network(layout, target,
                                           [f"no network {prefix} area {area}",
                                            f"network {prefix} area {changed_area}"])
            elif fault == "admin_down":
                action = configure_setting(layout, target, iface, "shutdown")
            elif fault == "ospf_passive":
                action = configure_setting(layout, target, iface, "ip ospf passive")
            else:
                raise ExperimentError("Unsupported fault or OSPF mode")
            record["actions"].append(action)
            current = running_config(layout, target)
            if fault == "admin_down" and "shutdown" not in interface_lines(current, iface):
                raise ExperimentError("shutdown did not enter target running configuration")
            if fault == "ospf_passive" and "ip ospf passive" not in interface_lines(current, iface):
                raise ExperimentError("passive did not enter target running configuration")
            if fault == "area_mismatch":
                areas = (interface_link_areas(current, iface) if mode == "interface"
                         else link_areas(current, prefix))
                if areas != [changed_area]:
                    raise ExperimentError("New Area did not enter target running configuration")
            fault_dir = observations / "fault"
            fault_dir.mkdir()
            poll_details = {node: dict(info) for node, info in details.items()}
            if changed_area:
                poll_details[target]["link_area"] = changed_area
            matched, latest = poll(layout, poll_details, fault_dir / "poll.json", fault, FAULT_TIMEOUT_SECONDS)
            fault_doc = collect_evidence(LAB_DIR, fault_dir / "snapshot")
            fault_metrics = metrics_from_evidence(layout, fault_doc)
            direct = direct_fault_present(layout, fault_doc, fault, target, changed_area)
            passed = matched and fault_stable(fault_metrics, fault) and direct
            record["phases"]["fault"] = {"poll_matched": matched, "last_poll_metrics": latest,
                                            "metrics": fault_metrics, "direct_evidence_present": direct,
                                            "passed": passed}
            if not passed:
                record["errors"].append("故障未满足稳定性或直接证据条件；保留原始证据")
        except BaseException as exc:
            record["errors"].append(f"故障阶段异常：{type(exc).__name__}: {exc}")
            if not (observations / "fault" / "snapshot").exists():
                try:
                    (observations / "fault").mkdir(exist_ok=True)
                    collect_evidence(LAB_DIR, observations / "fault" / "snapshot")
                except BaseException as capture_exc:
                    record["errors"].append(f"故障快照补采失败：{capture_exc}")
        finally:
            if injected:
                previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
                previous_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    record["restore_started_at_utc"] = utc_now()
                    try:
                        if fault == "area_mismatch" and mode == "interface":
                            action = configure_interface_area(layout, target, iface,
                                                              [f"no ip ospf area {changed_area}",
                                                               f"ip ospf area {area}"])
                        elif fault == "area_mismatch":
                            action = configure_network(layout, target,
                                                       [f"no network {prefix} area {changed_area}",
                                                        f"network {prefix} area {area}"])
                        else:
                            action = configure_setting(layout, target, iface,
                                                       "no shutdown" if fault == "admin_down"
                                                       else "no ip ospf passive")
                        record["actions"].append(action)
                        after = running_config(layout, target)
                        record["config_restored"] = normalized_config(after) == normalized_config(original_config)
                        if not record["config_restored"]:
                            record["errors"].append("恢复后运行配置与原始快照不同")
                    except BaseException as exc:
                        record["config_restored"] = False
                        record["errors"].append(f"配置恢复失败：{type(exc).__name__}: {exc}")
                    try:
                        after_hashes = {label: hashlib.sha256(path.read_bytes()).hexdigest()
                                        for label, path in startup_files(layout, target).items()}
                        record["startup_sha256_after"] = after_hashes
                        record["startup_unchanged"] = after_hashes == startup_hashes
                        if not record["startup_unchanged"]:
                            record["errors"].append("启动配置发生变化，请人工核对")
                    except BaseException as exc:
                        record["startup_unchanged"] = False
                        record["errors"].append(f"启动配置哈希核对失败：{exc}")
                    recovered_dir = observations / "recovered"
                    recovered_dir.mkdir(exist_ok=True)
                    try:
                        matched, latest = poll(layout, details, recovered_dir / "poll.json", None,
                                               RECOVERY_TIMEOUT_SECONDS)
                        recovered = collect_evidence(LAB_DIR, recovered_dir / "snapshot")
                        metrics = metrics_from_evidence(layout, recovered)
                        record["phases"]["recovered"] = {"poll_matched": matched,
                                                            "last_poll_metrics": latest, "metrics": metrics,
                                                            "passed": matched and healthy(metrics) and
                                                            record["config_restored"] and record["startup_unchanged"]}
                    except BaseException as exc:
                        record["errors"].append(f"恢复验证失败：{type(exc).__name__}: {exc}")
                finally:
                    signal.signal(signal.SIGINT, previous_int)
                    signal.signal(signal.SIGTERM, previous_term)
        record["status"] = ("PASS" if not record["errors"] and all(record["phases"].get(stage, {}).get("passed")
                                                                for stage in ("normal", "fault", "recovered"))
                            else "FAIL")
    except BaseException as exc:
        record["errors"].append(f"实验停止：{type(exc).__name__}: {exc}")
        record["status"] = "FAIL"
    finally:
        record["finished_at_utc"] = utc_now()
        write_json(record_path, record)
        lines = ["# 单故障实验报告", "", f"状态：{record['status']}",
                 f"场景：{fault}；目标：{target}", "",
                 "故障标签与注入操作仅存于 `experiment-record/`；诊断仅使用 `observations/`。", ""]
        for phase in ("normal", "fault", "recovered"):
            phase_record = record["phases"].get(phase, {})
            lines += [f"## {phase}", "", f"通过：{phase_record.get('passed', False)}",
                      f"实际观测：`{phase_record.get('metrics')}`", ""]
        lines += ["## 错误与恢复", ""]
        lines += [f"- {item}" for item in record["errors"]] or ["- 无。"]
        lines += ["", record.get("manual_recovery", "未注入故障，无需恢复。"), ""]
        (run_dir / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return run_dir, record


def interrupt_handler(signum: int, _frame: Any) -> None:
    raise InterruptedError(f"Received signal {signum}; restore in finally")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=FAULTS, required=True)
    parser.add_argument("--target", choices=("r1", "r2"), required=True)
    parser.add_argument("--output-root", type=Path, default=LAB_DIR / "experiments")
    args = parser.parse_args()
    path, record = run_experiment(args.fault, args.target, args.output_root.resolve())
    print(f"Experiment: {path}")
    print(f"Status: {record['status']}")
    print(f"Final network: {'RESTORED' if record.get('phases', {}).get('recovered', {}).get('passed') else 'NOT VERIFIED'}")
    print(f"Report: {path / 'comparison.md'}")
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
