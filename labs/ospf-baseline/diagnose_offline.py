#!/usr/bin/env python3
"""Offline rules diagnosis from one collector observations.json file only."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any

from collect_evidence import observation_mismatch, parse_node_config, read_layout, sections


REQUIRED = (
    "running_config", "interfaces_ipv4", "interconnect_link", "frr_interface",
    "ospf_interface", "ospf_neighbors", "routes", "ospf_routes",
    "ping_peer_link", "ping_remote_loopback",
)
PING_IDS = {"ping_peer_link", "ping_remote_loopback"}
AREA_LINE = re.compile(
    r"^\s*Internet Address\s+(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2}),.*\bArea\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$"
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cite(node: str, item: dict[str, Any], excerpt: str, finding: str) -> dict[str, Any]:
    return {
        "node": node, "command_id": item.get("id"), "command": item.get("command"),
        "exit_code": item.get("exit_code"), "output_excerpt": excerpt.strip(), "finding": finding,
    }


def result(status: str, conclusion: str, evidence: list[dict[str, Any]],
           missing: list[str], checks: list[str], suggestion: str,
           collected_at: str | None) -> dict[str, Any]:
    return {
        "schema_version": 1, "diagnosed_at_utc": now_utc(),
        "input_collected_at_utc": collected_at, "status": status,
        "conclusion": conclusion, "supporting_evidence": evidence,
        "missing_information": missing, "next_read_only_checks": checks,
        "repair_suggestion": suggestion,
    }


def collection_failure(why: list[str], citations: list[dict[str, Any]], collected_at: str | None) -> dict[str, Any]:
    return result(
        "collection_failure", "采集失败或数据不可用；不能把命令错误解释为网络故障。",
        citations, why,
        ["检查对应采集命令的退出码、stderr 和容器运行状态。", "修复采集路径后重新运行只读采集器。"],
        "先恢复采集能力；当前证据不支持修改路由配置。", collected_at,
    )


def command_map(node: str, value: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    missing: list[str] = []
    if not isinstance(value, dict) or not isinstance(value.get("commands"), list):
        return {}, [f"{node}: 缺少命令记录列表"]
    mapped: dict[str, dict[str, Any]] = {}
    for item in value["commands"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        key = item["id"]
        if key in mapped:
            missing.append(f"{node}/{key}: 命令记录重复")
        mapped[key] = item
    for key in REQUIRED:
        if key not in mapped:
            missing.append(f"{node}/{key}: 缺少命令记录")
    return mapped, missing


def interface_fact(node: str, item: dict[str, Any], expected: dict[str, Any],
                   missing: list[str]) -> tuple[str, ipaddress.IPv4Interface] | None:
    output = item.get("stdout")
    if not isinstance(output, str):
        missing.append(f"{node}/ospf_interface: 没有标准输出")
        return None
    interface = expected.get("link_interface")
    if not isinstance(interface, str) or not interface:
        missing.append(f"{node}: 缺少互联接口标识")
        return None
    if not re.search(r"(?m)^" + re.escape(interface) + r" is up\s*$", output):
        missing.append(f"{node}/ospf_interface: 未确认互联接口 {interface} 为 up")
        return None
    candidates = [AREA_LINE.fullmatch(line) for line in output.splitlines()]
    candidates = [match for match in candidates if match]
    if len(candidates) != 1:
        missing.append(f"{node}/ospf_interface: 缺少唯一的互联地址及 Area 行")
        return None
    address, length, area = candidates[0].groups()
    try:
        address_object = ipaddress.IPv4Interface(f"{address}/{length}")
        area = str(ipaddress.IPv4Address(area))
    except ValueError:
        missing.append(f"{node}/ospf_interface: 地址或 Area 格式无效")
        return None
    if expected.get("link_address") != address or expected.get("link_area") != area:
        missing.append(f"{node}: 接口实际地址/区域与采集器发现的运行配置不一致")
        return None
    expected_command = ["vtysh", "-c", f"show ip ospf interface {interface}"]
    argv = item.get("command")
    if not isinstance(argv, list) or argv[-3:] != expected_command:
        missing.append(f"{node}/ospf_interface: 命令目标接口无法核实")
        return None
    return area, address_object


def has_full_neighbor(output: str, router_id: str) -> bool:
    return any(
        len(parts) >= 3 and parts[0] == router_id and parts[2].startswith("Full")
        for line in output.splitlines() if (parts := line.split())
    )


def has_ospf_route(output: str, prefix: str) -> bool:
    return any(line.lstrip().startswith("O") and prefix in line for line in output.splitlines())


def short_line(output: str, pattern: str) -> str:
    match = re.search(pattern, output, re.MULTILINE)
    if match:
        return match.group(0)
    # A citation must not present an inferred sentence as though it were raw command output.
    return output.strip().splitlines()[0] if output.strip() else "(空输出)"


def direct_interface_faults(node: str, commands: dict[str, dict[str, Any]],
                            interface: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Use scoped config and effective FRR admin output, never missing neighbors."""
    config = commands["running_config"]
    frr = commands["frr_interface"]
    parts = sections(str(config.get("stdout") or ""))
    interface_lines = parts.get(f"interface {interface}")
    if interface_lines is None:
        return [], [], [f"{node}: 运行配置没有互联接口 {interface} 段"]
    interface_block = "\n".join([f"interface {interface}", *interface_lines])
    router_lines = parts.get("router ospf", [])
    shutdown = "shutdown" in interface_lines
    frr_output = str(frr.get("stdout") or "")
    frr_heading = re.search(r"(?m)^Interface\s+" + re.escape(interface) + r"\s+is\s+down\b.*$", frr_output)
    shutdown_line = re.search(r"(?m)^\s*Shutdown config is\s+(.+)$", frr_output)
    effective_shutdown = (frr_heading is not None and shutdown_line is not None and
                          shutdown_line.group(1).strip().lower() not in
                          ("not specified by cli", "off", "no", "disabled"))
    admin: list[dict[str, Any]] = []
    missing: list[str] = []
    if shutdown:
        admin.append(cite(node, config, interface_block, f"{interface} 配置了 shutdown"))
        if effective_shutdown:
            admin.append(cite(node, frr, frr_heading.group(0) + "\n" + shutdown_line.group(0),
                              f"{interface} 的有效管理状态为关闭"))
        else:
            missing.append(f"{node}/{interface}: 配置有 shutdown，但 FRR 接口管理状态未确认关闭")
    passive_line = next((line for line in interface_lines if line == "ip ospf passive"), None)
    process_line = next((line for line in router_lines if line == f"passive-interface {interface}"), None)
    passive: list[dict[str, Any]] = []
    if passive_line:
        passive.append(cite(node, config, interface_block,
                            f"{interface} 的运行配置含 ip ospf passive"))
    elif process_line:
        passive.append(cite(node, config, "router ospf\n " + process_line,
                            f"{interface} 的 OSPF 进程配置为 passive"))
    return admin, passive, missing


def diagnose(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        return collection_failure(["输入不是 JSON 对象"], [], None)
    collected_at = document.get("collected_at_utc") if isinstance(document.get("collected_at_utc"), str) else None
    if document.get("schema_version") != 1 or not isinstance(document.get("nodes"), dict):
        return collection_failure(["观测数据格式或版本不可用"], [], collected_at)
    nodes = document["nodes"]
    if len(nodes) != 2 or not all(isinstance(node, str) for node in nodes):
        return collection_failure(["本基线要求恰好两台路由器的观测数据"], [], collected_at)
    layout = read_layout()
    if document.get("lab") != layout.lab_name or set(nodes) != set(layout.nodes):
        return collection_failure(["观测节点或实验名称与固定拓扑不符"], [], collected_at)
    node_names = sorted(nodes)
    mapped: dict[str, dict[str, dict[str, Any]]] = {}
    missing: list[str] = []
    failures: list[str] = []
    failure_evidence: list[dict[str, Any]] = []
    for node in node_names:
        mapped[node], missing_here = command_map(node, nodes[node])
        missing.extend(missing_here)
        for key in REQUIRED:
            item = mapped[node].get(key)
            if item is None:
                continue
            code = item.get("exit_code")
            error = item.get("error")
            bad_code = code not in (0, 1) if key in PING_IDS else code != 0
            if error or bad_code:
                reason = f"{node}/{key}: 执行失败，退出码={code}，错误={error or item.get('stderr') or '未给出'}"
                failures.append(reason)
                failure_evidence.append(cite(node, item, str(item.get("stderr") or error or ""), "采集命令失败"))
            elif key in PING_IDS and code == 1:
                combined = str(item.get("stdout") or "") + "\n" + str(item.get("stderr") or "")
                if not re.search(r"Network unreachable|[1-9]\d*% packet loss|100% packet loss|0 packets received", combined):
                    missing.append(f"{node}/{key}: ping 返回 1，但没有可辨认的网络不可达输出")
            if key not in PING_IDS:
                mismatch = observation_mismatch(layout, node, key, item)
                if mismatch:
                    missing.append(f"{node}/{key}: {mismatch}")
    if document.get("discovery_error"):
        failures.append(f"配置发现失败：{document['discovery_error']}")
    if failures:
        return collection_failure(failures, failure_evidence, collected_at)
    if missing:
        return result(
            "insufficient_evidence", "观测字段缺失或相互矛盾，无法确认根因。", [], missing,
            ["补采缺失命令，尤其是两端互联接口的 `show ip ospf interface <接口>`。",
             "只读核对两端 `show running-config` 和 `show ip ospf neighbor`。"],
            "暂不建议修改配置。", collected_at,
        )
    discovered = document.get("discovered")
    if not isinstance(discovered, dict) or set(discovered) != set(node_names):
        return result("insufficient_evidence", "缺少两端配置发现结果，无法确认根因。", [],
                      ["缺少两端的互联接口、地址及区域发现结果"],
                      ["重新采集两端 `show running-config` 与 `show ip ospf interface <接口>`。"],
                      "暂不建议修改配置。", collected_at)
    for node in node_names:
        try:
            parsed = parse_node_config(str(mapped[node]["running_config"].get("stdout") or ""),
                                       layout.interfaces[node])
        except (ValueError, KeyError) as exc:
            missing.append(f"{node}: 无法从有效运行配置核对发现结果（{type(exc).__name__}）")
            continue
        if not isinstance(discovered[node], dict) or any(
                key in parsed and value != parsed[key] for key, value in discovered[node].items()):
            missing.append(f"{node}: 采集器发现结果与运行配置不一致")
        for key in PING_IDS:
            mismatch = observation_mismatch(layout, node, key, mapped[node][key], discovered)
            if mismatch:
                missing.append(f"{node}/{key}: {mismatch}")
    if missing:
        return result("insufficient_evidence", "观测命令或地址与实际配置不一致，不能确认根因。", [], missing,
                      ["只读重采运行配置、互联接口和 ping 命令。"], "暂不建议修改配置。", collected_at)
    admin_evidence: list[dict[str, Any]] = []
    passive_evidence: list[dict[str, Any]] = []
    for node in node_names:
        info = discovered[node]
        if not isinstance(info, dict) or not isinstance(info.get("link_interface"), str):
            missing.append(f"{node}: 缺少互联接口发现结果")
            continue
        admin, passive, direct_missing = direct_interface_faults(node, mapped[node], info["link_interface"])
        admin_evidence.extend(admin)
        passive_evidence.extend(passive)
        missing.extend(direct_missing)
    if missing:
        return result("insufficient_evidence", "接口直接证据不完整，无法确认根因。",
                      admin_evidence + passive_evidence, missing,
                      ["只读核对互联接口的 `show interface` 与 `show running-config`。"],
                      "暂不建议修改配置。", collected_at)
    if admin_evidence and passive_evidence:
        return result("insufficient_evidence", "同时发现管理关闭与 passive 配置，不能归因为单一故障。",
                      admin_evidence + passive_evidence, ["存在多个直接异常，需要分别核实"],
                      ["只读复核两端接口及 OSPF 运行配置。"], "不要基于单故障假设修改配置。", collected_at)
    if admin_evidence:
        return result("interface_admin_down", "直接证据支持一侧互联接口被管理关闭。",
                      admin_evidence, [], ["只读复核该接口管理状态及运行配置。"],
                      "确认维护意图后，恢复该接口原有管理状态；本程序不执行修改。", collected_at)
    if passive_evidence:
        return result("ospf_passive", "直接证据支持一侧互联接口启用了 OSPF passive。",
                      passive_evidence, [], ["只读复核该接口的有效 passive 状态与运行配置。"],
                      "确认设计要求后，恢复该接口原有非 passive 状态；本程序不执行修改。", collected_at)
    facts: dict[str, tuple[str, ipaddress.IPv4Interface]] = {}
    area_evidence: list[dict[str, Any]] = []
    for node in node_names:
        info = discovered[node]
        if not isinstance(info, dict):
            missing.append(f"{node}: 配置发现对象无效")
            continue
        item = mapped[node]["ospf_interface"]
        fact = interface_fact(node, item, info, missing)
        if fact:
            facts[node] = fact
            line = short_line(item["stdout"], r"^\s*Internet Address .*\bArea\s+\d+(?:\.\d+){3}\s*$")
            area_evidence.append(cite(node, item, line, f"互联接口 {info['link_interface']} 的 Area 为 {fact[0]}"))
    if len(facts) == 2:
        first, second = node_names
        if facts[first][1].network != facts[second][1].network or facts[first][1].ip == facts[second][1].ip:
            missing.append("两端接口地址未能证明属于同一条互联子网")
    if missing:
        return result(
            "insufficient_evidence", "两端互联接口区域证据不足或不一致，无法确认根因。",
            area_evidence, missing,
            ["只读核对两端 `show ip ospf interface <接口>` 的 Internet Address 和 Area 行。",
             "核对两端接口地址与 `show running-config` 是否一致。"],
            "暂不建议修改配置。", collected_at,
        )
    symptoms: list[dict[str, Any]] = []
    all_healthy = True
    for node in node_names:
        peer = node_names[1] if node == node_names[0] else node_names[0]
        peer_id = discovered[peer].get("router_id")
        peer_prefix = discovered[peer].get("loopback_prefix")
        if not isinstance(peer_id, str) or not isinstance(peer_prefix, str):
            missing.append(f"{peer}: 缺少远端 Router ID 或 Loopback 前缀")
            continue
        neighbor = mapped[node]["ospf_neighbors"]
        route = mapped[node]["ospf_routes"]
        link_ping = mapped[node]["ping_peer_link"]
        loop_ping = mapped[node]["ping_remote_loopback"]
        neighbor_ok = has_full_neighbor(str(neighbor.get("stdout") or ""), peer_id)
        route_ok = has_ospf_route(str(route.get("stdout") or ""), peer_prefix)
        link_ok = link_ping["exit_code"] == 0
        loop_ok = loop_ping["exit_code"] == 0
        all_healthy = all_healthy and neighbor_ok and route_ok and link_ok and loop_ok
        symptoms.extend([
            cite(node, neighbor,
                 short_line(str(neighbor.get("stdout") or ""), r"^" + re.escape(peer_id) + r".*$"),
                 f"远端邻居 FULL={neighbor_ok}"),
            cite(node, route,
                 short_line(str(route.get("stdout") or ""), r"^O.*" + re.escape(peer_prefix) + r".*$"),
                 f"远端 /32 路由存在={route_ok}"),
            cite(node, link_ping, short_line(str(link_ping.get("stdout") or ""), r"^.*packets transmitted.*$"),
                 f"互联地址 ping 成功={link_ok}"),
            cite(node, loop_ping,
                 short_line(str(loop_ping.get("stdout") or "") + "\n" + str(loop_ping.get("stderr") or ""),
                            r"^.*(?:packets transmitted|Network unreachable).*$"),
                 f"远端 Loopback ping 成功={loop_ok}"),
        ])
    if missing:
        return result("insufficient_evidence", "远端身份或路由目标缺失，无法确认根因。",
                      area_evidence, missing,
                      ["重新采集两端运行配置、邻居和 OSPF 路由。"], "暂不建议修改配置。", collected_at)
    areas = {node: facts[node][0] for node in node_names}
    if len(set(areas.values())) == 2:
        return result(
            "ospf_area_mismatch", "证据支持两端互联接口 OSPF 区域不一致。",
            area_evidence + symptoms, [],
            ["只读复核两端互联接口的 `show ip ospf interface <接口>` Area 行。",
             "核对 `show running-config` 中互联子网的区域语句与预期设计。"],
            "确认设计区域后，将一侧互联接口的 OSPF 区域调整为与对端一致；本程序不执行修改。",
            collected_at,
        )
    if all_healthy:
        return result(
            "normal", "当前观测正常：区域一致、邻居 FULL、远端路由和双向可达性均成立。",
            area_evidence + symptoms, [],
            ["需要持续观察时，定期只读采集两端 OSPF 邻居、路由和 ping。"],
            "无需修复。", collected_at,
        )
    return result(
        "insufficient_evidence", "区域一致，但存在邻居、路由或可达性异常；现有证据不能确认根因。",
        area_evidence + symptoms,
        ["缺少能区分接口、链路、计时器及其他 OSPF 故障原因的证据"],
        ["只读检查两端 `show ip ospf neighbor detail` 和接口状态。",
         "核对两端 `show ip ospf interface <接口>` 的计时器及网络类型。"],
        "暂不按区域不一致修改配置。", collected_at,
    )


def render_chinese_report(diagnosis: dict[str, Any]) -> str:
    lines = ["# 离线 OSPF 诊断报告", "", f"状态：`{diagnosis['status']}`", "",
             f"结论：{diagnosis['conclusion']}", "", "## 支持证据", ""]
    if diagnosis["supporting_evidence"]:
        for item in diagnosis["supporting_evidence"]:
            command = " ".join(item["command"]) if isinstance(item.get("command"), list) else str(item.get("command"))
            lines.append(f"- `{item['node']}` / `{item['command_id']}`：{item['finding']}；命令：`{command}`；输出：`{item['output_excerpt']}`")
    else:
        lines.append("- 无可用于确认根因的完整证据。")
    lines += ["", "## 缺失信息", ""]
    lines += [f"- {item}" for item in diagnosis["missing_information"]] or ["- 无。"]
    lines += ["", "## 下一步只读检查", ""]
    lines += [f"- {item}" for item in diagnosis["next_read_only_checks"]]
    lines += ["", "## 修复建议", "", diagnosis["repair_suggestion"], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="one observations.json file")
    parser.add_argument("--output-dir", type=Path, required=True, help="new diagnosis output directory")
    args = parser.parse_args()
    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        diagnosis = collection_failure([f"无法读取观测 JSON：{type(exc).__name__}: {exc}"], [], None)
    else:
        diagnosis = diagnose(document)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "diagnosis.json").write_text(
        json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "report.zh-CN.md").write_text(render_chinese_report(diagnosis), encoding="utf-8")
    print(f"status={diagnosis['status']}")
    print(args.output_dir / "report.zh-CN.md")
    return 0 if diagnosis["status"] in ("normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive") else 1


if __name__ == "__main__":
    raise SystemExit(main())
