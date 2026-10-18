#!/usr/bin/env python3
"""Run one bounded OSPF area-mismatch experiment against the existing lab."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from typing import Any, Callable

from collect_evidence import (
    LAB_DIR, EXPECTED_IMAGE, Layout, checked_area, collect_evidence,
    checked_name, command_argv, ensure_wsl2_and_sudo, execute, parse_node_config,
    read_layout, sections, utc_now,
)


FAULT_TIMEOUT_SECONDS = 90
RECOVERY_TIMEOUT_SECONDS = 90
POLL_COMMANDS = ("ospf_neighbors", "ospf_routes", "ping_peer_link", "ping_remote_loopback")


class ExperimentError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def host_command(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    if done.returncode != 0:
        raise ExperimentError(f"Command failed ({done.returncode}): {argv!r}; {done.stderr.strip()}")
    return done


def preflight(layout: Layout) -> dict[str, Any]:
    containers = [layout.container(node) for node in layout.nodes]
    raw = host_command(["sudo", "-n", "docker", "inspect", *containers]).stdout
    inspected = json.loads(raw)
    if len(inspected) != 2:
        raise ExperimentError("Expected exactly two existing Docker containers")
    summary = {}
    for node, item in zip(layout.nodes, inspected):
        if item.get("Name") != "/" + layout.container(node):
            raise ExperimentError(f"Container identity mismatch for {node}")
        if item.get("Config", {}).get("Image") != EXPECTED_IMAGE:
            raise ExperimentError(f"Unexpected FRR image on {node}")
        if item.get("State", {}).get("Running") is not True:
            raise ExperimentError(f"Existing {node} container is not running")
        summary[node] = {"container": layout.container(node), "running": True, "image": EXPECTED_IMAGE}
    return summary


def by_id(evidence: dict[str, Any], node: str, key: str) -> dict[str, Any]:
    matches = [item for item in evidence["nodes"][node]["commands"] if item["id"] == key]
    if len(matches) != 1:
        raise ExperimentError(f"Missing or duplicate {node}/{key} evidence")
    return matches[0]


def full_neighbor(output: str, router_id: str) -> bool:
    return any(
        line.split() and line.split()[0] == router_id and "Full" in line.split()[2]
        for line in output.splitlines() if len(line.split()) >= 3
    )


def learned_route(output: str, prefix: str) -> bool:
    return any(
        line.lstrip().startswith("O") and prefix in line
        for line in output.splitlines()
    )


def metrics_from_evidence(layout: Layout, evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("discovery_error") or "discovered" not in evidence:
        raise ExperimentError(f"Configuration discovery failed: {evidence.get('discovery_error')}")
    details = evidence["discovered"]
    metrics: dict[str, Any] = {}
    for node in layout.nodes:
        peer = layout.peer(node)
        for item in evidence["nodes"][node]["commands"]:
            if item["error"] or (item["id"] not in ("ping_peer_link", "ping_remote_loopback") and item["exit_code"] != 0):
                raise ExperimentError(f"Evidence command failed: {node}/{item['id']}: {item['error'] or item['stderr']}")
        ids = {key: by_id(evidence, node, key) for key in POLL_COMMANDS}
        metrics[node] = {
            "neighbor_full": full_neighbor(ids["ospf_neighbors"]["stdout"], details[peer]["router_id"]),
            "remote_loopback_route": learned_route(ids["ospf_routes"]["stdout"], details[peer]["loopback_prefix"]),
            "peer_link_ping": ids["ping_peer_link"]["exit_code"] == 0,
            "remote_loopback_ping": ids["ping_remote_loopback"]["exit_code"] == 0,
            "link_area": details[node]["link_area"],
        }
    return metrics


def healthy(metrics: dict[str, Any]) -> bool:
    return all(
        value["neighbor_full"] and value["remote_loopback_route"]
        and value["peer_link_ping"] and value["remote_loopback_ping"]
        for value in metrics.values()
    )


def fault_visible(metrics: dict[str, Any]) -> bool:
    return all(
        not value["neighbor_full"] and not value["remote_loopback_route"]
        and value["peer_link_ping"] and not value["remote_loopback_ping"]
        for value in metrics.values()
    )


def live_probe(layout: Layout, details: dict[str, dict[str, str]]) -> dict[str, Any]:
    sample: dict[str, Any] = {"collected_at_utc": utc_now(), "nodes": {}}
    for node in layout.nodes:
        commands = [execute(layout, node, key, details) for key in POLL_COMMANDS]
        sample["nodes"][node] = {"commands": commands}
    return sample


def poll_state(layout: Layout, details: dict[str, dict[str, str]], output: Path,
               target: str, timeout: int) -> tuple[bool, dict[str, Any] | None]:
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    latest_metrics = None
    matched = False
    try:
        while True:
            sample = live_probe(layout, details)
            samples.append(sample)
            latest_metrics = metrics_from_evidence(layout, {
                "nodes": sample["nodes"], "discovered": details, "discovery_error": None
            })
            matched = fault_visible(latest_metrics) if target == "fault" else healthy(latest_metrics)
            if matched or time.monotonic() >= deadline:
                break
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    finally:
        write_json(output, {"schema_version": 1, "samples": samples})
    return matched, latest_metrics


def link_areas(config: str, prefix: str) -> list[str]:
    expected = re.compile(r"^network\s+" + re.escape(prefix) + r"\s+area\s+(\S+)$")
    return [checked_area(match.group(1)) for line in sections(config).get("router ospf", [])
            if (match := expected.fullmatch(line))]


def interface_link_areas(config: str, interface: str) -> list[str]:
    expected = re.compile(r"^ip ospf area\s+(\d{1,3}(?:\.\d{1,3}){3})$")
    return [checked_area(match.group(1)) for line in sections(config).get(f"interface {checked_name(interface)}", [])
            if (match := expected.fullmatch(line))]


def running_config(layout: Layout, node: str) -> str:
    done = host_command(["sudo", "-n", "docker", "exec", layout.container(node),
                         "vtysh", "-c", "show running-config"])
    return done.stdout


def configure_network(layout: Layout, node: str, commands: list[str]) -> dict[str, Any]:
    allowed = re.compile(r"^(?:no )?network\s+\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\s+area\s+\d{1,3}(?:\.\d{1,3}){3}$")
    if not commands or not all(allowed.fullmatch(command) for command in commands):
        raise ExperimentError("Refusing a configuration command outside the fixed OSPF network template")
    argv = ["sudo", "-n", "docker", "exec", layout.container(node), "vtysh",
            "-c", "configure terminal", "-c", "router ospf"]
    for command in commands:
        argv += ["-c", command]
    argv += ["-c", "end"]
    done = host_command(argv, timeout=20)
    return {"at_utc": utc_now(), "command": argv, "exit_code": done.returncode,
            "stdout": done.stdout, "stderr": done.stderr}


def configure_interface_area(layout: Layout, node: str, interface: str,
                             commands: list[str]) -> dict[str, Any]:
    if node not in layout.nodes or interface != layout.interfaces[node]:
        raise ExperimentError("Unexpected interface target")
    allowed = re.compile(r"^(?:no )?ip ospf area \d{1,3}(?:\.\d{1,3}){3}$")
    if not commands or not all(allowed.fullmatch(command) and
                               checked_area(command.split()[-1]) == command.split()[-1]
                               for command in commands):
        raise ExperimentError("Refusing an interface command outside the fixed OSPF area template")
    argv = ["sudo", "-n", "docker", "exec", layout.container(node), "vtysh",
            "-c", "configure terminal", "-c", f"interface {checked_name(interface)}"]
    for command in commands:
        argv += ["-c", command]
    argv += ["-c", "end"]
    done = host_command(argv, timeout=20)
    return {"at_utc": utc_now(), "command": argv, "exit_code": done.returncode,
            "stdout": done.stdout, "stderr": done.stderr}


def choose_fault_area(original: str) -> str:
    for candidate in ("0.0.0.1", "0.0.0.2"):
        if candidate != original:
            return candidate
    raise ExperimentError("Could not choose a different OSPF area")


def restore_original(layout: Layout, node: str, prefix: str, original: str,
                     faulty: str, mode: str = "network", interface: str | None = None) -> dict[str, Any]:
    before = running_config(layout, node)
    if mode == "network":
        get_areas = lambda config: link_areas(config, prefix)
        remove = f"no network {prefix} area {faulty}"
        add = f"network {prefix} area {original}"
        apply = lambda commands: configure_network(layout, node, commands)
    elif mode == "interface" and interface == layout.interfaces[node]:
        get_areas = lambda config: interface_link_areas(config, interface)
        remove = f"no ip ospf area {faulty}"
        add = f"ip ospf area {original}"
        apply = lambda commands: configure_interface_area(layout, node, interface, commands)
    else:
        raise ExperimentError("Unrecognized OSPF configuration mode or interface")
    areas = get_areas(before)
    unexpected = [area for area in areas if area not in (original, faulty)]
    if unexpected:
        raise ExperimentError(f"Unexpected concurrent area change on {node}: {unexpected}")
    commands = []
    if faulty in areas:
        commands.append(remove)
    if original not in areas:
        commands.append(add)
    action = apply(commands) if commands else {"at_utc": utc_now(), "command": [], "note": "original statement already present"}
    after = running_config(layout, node)
    if get_areas(after) != [original]:
        raise ExperimentError(f"Original area statement not restored on {node}")
    return {"action": action, "config_after": after}


def normalized_config(config: str) -> Counter[str]:
    return Counter(line.strip() for line in config.splitlines()
                   if line.strip() and not line.startswith("Current configuration"))


def startup_files(layout: Layout, node: str) -> dict[str, Path]:
    return {
        "source": LAB_DIR / "configs" / node / "frr.conf",
        "deployed": LAB_DIR / f"clab-{layout.lab_name}" / node / "config" / "frr.conf",
    }


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manual_recovery(layout: Layout, node: str, prefix: str, original: str, faulty: str,
                    mode: str = "network", interface: str | None = None) -> str:
    container = layout.container(node)
    if mode == "network":
        context = "router ospf"
        remove = f"no network {prefix} area {faulty}"
        add = f"network {prefix} area {original}"
    elif mode == "interface" and interface == layout.interfaces[node]:
        context = f"interface {checked_name(interface)}"
        remove = f"no ip ospf area {faulty}"
        add = f"ip ospf area {original}"
    else:
        raise ExperimentError("Unrecognized OSPF recovery mode or interface")
    return (
        f"在 Ubuntu/WSL2 中核对 `{container}` 的运行配置：\n"
        f"`sudo docker exec {container} vtysh -c 'show running-config'`\n\n"
        f"若仍有错误区域，执行：\n"
        f"`sudo docker exec {container} vtysh -c 'configure terminal' -c '{context}' "
        f"-c '{remove}' -c '{add}' -c 'end'`\n\n"
        f"再用两端的 `show ip ospf neighbor`、`show ip route ospf` 和远端 Loopback ping 验证恢复。"
        f"原配置快照在本次实验的 `experiment-record/original-{node}-running-config.txt`。"
    )


def value(value: bool | None) -> str:
    return "是" if value is True else "否" if value is False else "未采集"


def render_report(record: dict[str, Any], run_dir: Path) -> None:
    labels = {"normal": "正常基线", "fault": "区域不一致", "recovered": "恢复后"}
    lines = ["# OSPF 区域不一致实验对比报告", "",
             f"- 实验时间：`{record['started_at_utc']}`", f"- 状态：**{record['status']}**", "",
             "故障名称、注入命令和预期根因见 `experiment-record/experiment-record.json`；"
             "未来诊断 Agent 只读取 `observations/` 下的原始观测。", "",
             "## 预期与实际观察", "",
             "| 阶段 | 预期 | 实际观察 |", "|---|---|---|"]
    expected = {
        "normal": "邻居 FULL；学习远端 /32；互联与远端 Loopback 均可 ping",
        "fault": "邻居非 FULL；远端 /32 消失；互联地址仍可 ping；远端 Loopback 不通",
        "recovered": "邻居重新 FULL；远端 /32 恢复；两类 ping 恢复",
    }
    for stage in ("normal", "fault", "recovered"):
        item = record.get("phases", {}).get(stage, {})
        metrics = item.get("metrics")
        if metrics:
            observed = "; ".join(
                f"{node}: area={m['link_area']}, FULL={value(m['neighbor_full'])}, /32路由={value(m['remote_loopback_route'])}, "
                f"互联ping={value(m['peer_link_ping'])}, Loopback ping={value(m['remote_loopback_ping'])}"
                for node, m in metrics.items()
            )
        else:
            observed = item.get("note", "未采集")
        lines.append(f"| {labels[stage]} | {expected[stage]} | {observed} |")
    lines += ["", "## 验收", "", record.get("conclusion", "未完成"), ""]
    if record.get("errors"):
        lines += ["## 偏差及错误", ""]
        lines += [f"- {error}" for error in record["errors"]]
        lines.append("")
    lines += ["## 证据位置", "",
              "- 正常：`observations/normal/observations.json`",
              "- 故障：`observations/fault/snapshot/observations.json` 与 `observations/fault/poll.json`",
              "- 恢复：`observations/recovered/snapshot/observations.json` 与 `observations/recovered/poll.json`",
              "- 每条命令的原始标准输出和错误输出与对应 JSON 同目录。", ""]
    if record.get("manual_recovery"):
        lines += ["## 手动恢复", "", record["manual_recovery"], ""]
    (run_dir / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


def interrupt_handler(signum: int, _frame: Any) -> None:
    raise InterruptedError(f"Received signal {signum}; restoring the lab")


def run_experiment(output_root: Path, *, on_fault_stable: Callable[[], None] | None = None) -> tuple[Path, dict[str, Any]]:
    ensure_wsl2_and_sudo()
    layout = read_layout(LAB_DIR)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"area-mismatch-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    obs_root = run_dir / "observations"
    obs_root.mkdir()
    secret_root = run_dir / "experiment-record"
    secret_root.mkdir()
    record: dict[str, Any] = {
        "schema_version": 1, "scenario": "one-sided OSPF link-area mismatch",
        "started_at_utc": utc_now(), "finished_at_utc": None,
        "status": "IN_PROGRESS", "phases": {}, "errors": [], "actions": [],
        "expected_root_cause": "One router's interconnect subnet belongs to a different OSPF area than its peer",
        "preflight": None, "manual_recovery": None,
    }
    record_path = secret_root / "experiment-record.json"
    write_json(record_path, record)
    injected = False
    original_config = None
    details = None
    startup_hashes: dict[str, str] = {}
    target_node = layout.nodes[0]
    original_area = None
    fault_area = None
    prefix = None
    try:
        record["preflight"] = preflight(layout)
        baseline = collect_evidence(LAB_DIR, obs_root / "normal")
        normal_metrics = metrics_from_evidence(layout, baseline)
        record["phases"]["normal"] = {"metrics": normal_metrics, "passed": healthy(normal_metrics)}
        if not healthy(normal_metrics):
            raise ExperimentError("Normal baseline failed; no fault was injected")
        details = baseline["discovered"]
        if any(details[node]["ospf_mode"] not in ("network", "interface") for node in layout.nodes):
            raise ExperimentError("Unsupported OSPF configuration mode")
        if details[layout.nodes[0]]["link_area"] != details[layout.nodes[1]]["link_area"]:
            raise ExperimentError("The two link areas already differ; no fault was injected")
        original_config = by_id(baseline, target_node, "running_config")["stdout"]
        (secret_root / f"original-{target_node}-running-config.txt").write_text(original_config, encoding="utf-8")
        for label, path in startup_files(layout, target_node).items():
            original_bytes = path.read_bytes()
            (secret_root / f"original-{label}-startup-frr.conf").write_bytes(original_bytes)
            startup_hashes[label] = hashlib.sha256(original_bytes).hexdigest()
        record["startup_sha256_before"] = startup_hashes
        prefix = details[target_node]["link_prefix"]
        original_area = details[target_node]["link_area"]
        fault_area = choose_fault_area(original_area)
        mode = details[target_node]["ospf_mode"]
        link_interface = details[target_node]["link_interface"]
        record["target"] = {"node": target_node, "interface": layout.interfaces[target_node],
                            "subnet": prefix, "original_area": original_area, "fault_area": fault_area,
                            "ospf_mode": mode}
        record["manual_recovery"] = manual_recovery(layout, target_node, prefix, original_area, fault_area,
                                                    mode, link_interface)
        write_json(record_path, record)

        try:
            injected = True  # restoration is required even if the command only partly applies
            if mode == "network":
                record["actions"].append(configure_network(layout, target_node, [
                    f"no network {prefix} area {original_area}",
                    f"network {prefix} area {fault_area}",
                ]))
                active_areas = link_areas(running_config(layout, target_node), prefix)
            else:
                record["actions"].append(configure_interface_area(layout, target_node, link_interface, [
                    f"no ip ospf area {original_area}",
                    f"ip ospf area {fault_area}",
                ]))
                active_areas = interface_link_areas(running_config(layout, target_node), link_interface)
            if active_areas != [fault_area]:
                raise ExperimentError("Fault area did not become active; restoration is required")
            (obs_root / "fault").mkdir(exist_ok=True)
            fault_details = {node: dict(info) for node, info in details.items()}
            fault_details[target_node]["link_area"] = fault_area
            matched, last_metrics = poll_state(layout, fault_details, obs_root / "fault" / "poll.json",
                                               "fault", FAULT_TIMEOUT_SECONDS)
            record["phases"]["fault"] = {"poll_matched": matched, "last_poll_metrics": last_metrics}
            fault_evidence = collect_evidence(LAB_DIR, obs_root / "fault" / "snapshot")
            fault_metrics = metrics_from_evidence(layout, fault_evidence)
            record["phases"]["fault"]["metrics"] = fault_metrics
            record["phases"]["fault"]["passed"] = matched and fault_visible(fault_metrics)
            if not record["phases"]["fault"]["passed"]:
                record["errors"].append("故障观测未完全符合预期；原始轮询和快照已保留")
            elif on_fault_stable is not None:
                # External read-only diagnostics run only after fault stabilization.
                # No experiment record or expected root cause is passed to the callback.
                on_fault_stable()
        except BaseException as exc:
            record["errors"].append(f"注入或故障观测出错：{type(exc).__name__}: {exc}")
            if not (obs_root / "fault" / "snapshot").exists():
                try:
                    (obs_root / "fault").mkdir(exist_ok=True)
                    fault_evidence = collect_evidence(LAB_DIR, obs_root / "fault" / "snapshot")
                    record["phases"]["fault"] = {"metrics": metrics_from_evidence(layout, fault_evidence), "passed": False}
                except BaseException as capture_exc:
                    record["errors"].append(f"故障快照采集也失败：{capture_exc}")
        finally:
            if injected:
                previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
                previous_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    record["restore_started_at_utc"] = utc_now()
                    try:
                        restored = restore_original(layout, target_node, prefix, original_area, fault_area,
                                                    mode, link_interface)
                        record["actions"].append(restored["action"])
                        record["config_restored"] = normalized_config(restored["config_after"]) == normalized_config(original_config)
                        if not record["config_restored"]:
                            record["errors"].append("恢复后的完整运行配置与原始配置仍有差异")
                    except BaseException as restore_exc:
                        record["errors"].append(f"恢复配置失败：{type(restore_exc).__name__}: {restore_exc}")
                        record["config_restored"] = False
                    try:
                        after_hashes = {label: file_hash(path) for label, path in startup_files(layout, target_node).items()}
                        record["startup_sha256_after"] = after_hashes
                        record["startup_unchanged"] = after_hashes == startup_hashes
                        if not record["startup_unchanged"]:
                            record["errors"].append("启动配置文件发生变化；请核对是否含故障区域")
                    except BaseException as hash_exc:
                        record["startup_unchanged"] = False
                        record["errors"].append(f"无法核对启动配置文件：{hash_exc}")
                    try:
                        (obs_root / "recovered").mkdir(exist_ok=True)
                        matched, last_metrics = poll_state(layout, details, obs_root / "recovered" / "poll.json",
                                                           "healthy", RECOVERY_TIMEOUT_SECONDS)
                        record["phases"]["recovered"] = {"poll_matched": matched, "last_poll_metrics": last_metrics}
                    except BaseException as poll_exc:
                        record["errors"].append(f"恢复轮询失败：{type(poll_exc).__name__}: {poll_exc}")
                    try:
                        recovered_evidence = collect_evidence(LAB_DIR, obs_root / "recovered" / "snapshot")
                        recovered_metrics = metrics_from_evidence(layout, recovered_evidence)
                        phase = record["phases"].setdefault("recovered", {})
                        phase["metrics"] = recovered_metrics
                        phase["passed"] = (phase.get("poll_matched") is True and healthy(recovered_metrics)
                                           and record["config_restored"] and record["startup_unchanged"])
                    except BaseException as capture_exc:
                        record["errors"].append(f"恢复快照采集失败：{type(capture_exc).__name__}: {capture_exc}")
                finally:
                    signal.signal(signal.SIGINT, previous_int)
                    signal.signal(signal.SIGTERM, previous_term)
        all_passed = all(record["phases"].get(stage, {}).get("passed") for stage in ("normal", "fault", "recovered"))
        record["status"] = "PASS" if all_passed and not record["errors"] else "FAIL"
        record["conclusion"] = (
            "三阶段均满足预期，原始运行配置已恢复，最终网络正常。" if record["status"] == "PASS" else
            "实验未完整通过。请核对错误、恢复快照和手动恢复步骤；不得据此认定网络已恢复。"
        )
    except BaseException as exc:
        record["errors"].append(f"实验停止：{type(exc).__name__}: {exc}")
        record["status"] = "FAIL"
        record["conclusion"] = "实验未完整通过；请查看已采集证据和错误。"
    finally:
        record["finished_at_utc"] = utc_now()
        write_json(record_path, record)
        render_report(record, run_dir)
    return run_dir, record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=LAB_DIR / "experiments",
                        help="directory for new, uniquely named experiment runs")
    args = parser.parse_args()
    previous_term = signal.signal(signal.SIGTERM, interrupt_handler)
    try:
        run_dir, record = run_experiment(args.output_root.resolve())
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    print(f"Experiment: {run_dir}")
    print(f"Status: {record['status']}")
    print(f"Report: {run_dir / 'comparison.md'}")
    if record["status"] != "PASS":
        for error in record["errors"]:
            print(error, file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
