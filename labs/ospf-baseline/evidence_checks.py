"""Pure, read-only checks for evidence needed by final diagnoses.

Only records actually returned by this Agent run are inspected. Dictionary
insertion order is the tool-call order: a successful recheck can supersede an
earlier failed attempt, but neither record is removed from the trace.
"""

from __future__ import annotations

from typing import Any
import re

from collect_evidence import Layout, observation_mismatch, ospf_address_area, parse_node_config, sections


CRITICAL_TOOLS = frozenset(("query_ospf_interface", "query_interface", "query_interface_config"))
TOOL_KEYS = {"query_ospf_interface": ("ospf_interface",),
             "query_interface": ("interfaces_ipv4", "interconnect_link", "frr_interface"),
             "query_interface_config": ("running_config",)}


def ospf_inactive_output(output: str, interface: str) -> bool:
    """Recognize the FRR interface-status response without treating it as Area evidence."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return (len(lines) == 3 and lines[0] in (f"{interface} is down", f"{interface} is up") and
            bool(re.fullmatch(r"ifindex\s+[1-9]\d*,\s+MTU\s+[1-9]\d* bytes,\s+"
                              r"BW\s+[1-9]\d* Mbit\s+<[A-Z,]+>", lines[1])) and
            lines[2] == "OSPF not enabled on this interface")


def validated_topology(layout: Layout, evidence: dict[str, dict[str, Any]]) -> tuple[str, dict] | None:
    """Corroborate the newest topology response against both raw running configs."""
    attempts = [(identifier, item) for identifier, item in evidence.items()
                if item.get("tool") == "query_topology"]
    if not attempts:
        return None
    identifier, item = attempts[-1]
    if item.get("status") != "ok" or item.get("arguments") != {}:
        return None
    data = item.get("data") or {}
    if not isinstance(data, dict):
        return None
    displayed = data.get("nodes")
    records = item.get("observations") or []
    if (not isinstance(records, list) or any(not isinstance(record, dict) for record in records) or
            data.get("lab") != layout.lab_name or not isinstance(displayed, dict) or
            set(displayed) != set(layout.nodes) or len(records) != len(layout.nodes) or
            {record.get("node") for record in records} != set(layout.nodes)):
        return None
    details = {}
    try:
        for record in records:
            node = record["node"]
            if (record.get("id") != "running_config" or record.get("exit_code") != 0 or
                    record.get("error") or record.get("validation_error") or
                    observation_mismatch(layout, node, "running_config", record)):
                return None
            details[node] = parse_node_config(str(record.get("stdout") or ""), layout.interfaces[node])
            shown = displayed[node]
            if (not isinstance(shown, dict) or
                    shown.get("interconnect_interface") != layout.interfaces[node] or
                    shown.get("interconnect_address") != details[node]["link_address"] or
                    shown.get("loopback_address") != details[node]["loopback_address"] or
                    shown.get("peer") != layout.peer(node)):
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return identifier, details


def latest_matching(evidence: dict[str, dict[str, Any]], tool: str, arguments: dict[str, str]) -> tuple[str, dict] | None:
    matches = [(identifier, item) for identifier, item in evidence.items()
               if item.get("tool") == tool and item.get("arguments") == arguments]
    return matches[-1] if matches else None


def normal_proofs(layout: Layout, evidence: dict[str, dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Select only the latest valid record for each normal-network requirement."""
    proofs: dict[str, str] = {}
    missing: list[str] = []
    topology = validated_topology(layout, evidence)
    if topology is None:
        return proofs, ["缺少可由两端原始运行配置核实的拓扑和地址证据。"]
    proofs["topology"] = topology[0]
    details = topology[1]
    areas: dict[str, str] = {}
    for node in layout.nodes:
        peer = layout.peer(node)
        interface = layout.interfaces[node]
        checks = (
            ("area", "query_ospf_interface", {"node": node, "interface": interface}, "ospf_interface"),
            ("neighbor", "query_neighbors", {"node": node}, "ospf_neighbors"),
            ("route", "query_routes", {"node": node, "scope": "ospf"}, "ospf_routes"),
            ("ping", "ping_address", {"node": node, "target": details[peer]["loopback_address"]},
             "ping_remote_loopback"),
        )
        for requirement, tool, arguments, key in checks:
            latest = latest_matching(evidence, tool, arguments)
            if latest is None:
                missing.append(f"{node} 缺少 {requirement} 查询。")
                continue
            identifier, item = latest
            records = item.get("observations") or []
            if item.get("status") != "ok" or not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                missing.append(f"{node} 的 {requirement} 最新查询不可用。")
                continue
            record = records[0]
            if (record.get("exit_code") != 0 or record.get("error") or record.get("validation_error") or
                    observation_mismatch(layout, node, key, record,
                                         details if key in ("ospf_interface", "ping_remote_loopback") else None)):
                missing.append(f"{node} 的 {requirement} 原始观测无效。")
                continue
            output = str(record.get("stdout") or "")
            valid = False
            if requirement == "area":
                fact = ospf_address_area(output)
                if fact:
                    areas[node] = fact[1]
                    valid = True
            elif requirement == "neighbor":
                remote_id = details[peer]["router_id"]
                valid = any(len(parts := line.split()) >= 3 and parts[0] == remote_id and
                            parts[2].startswith("Full") for line in output.splitlines())
            elif requirement == "route":
                prefix = details[peer]["loopback_prefix"]
                valid = bool(re.search(r"(?m)^O[>* ]*\s+" + re.escape(prefix) + r"\b", output))
            elif requirement == "ping":
                data = item.get("data") or {}
                valid = (isinstance(data, dict) and data.get("target") == details[peer]["loopback_address"] and
                         data.get("reachable") is True)
            if valid:
                proofs[f"{requirement}:{node}"] = identifier
            else:
                missing.append(f"{node} 的 {requirement} 输出不支持正常结论。")
    if len(areas) != len(layout.nodes) or len(set(areas.values())) != 1:
        missing.append("两端互联接口没有经核实的一致 OSPF Area。")
    return proofs, missing


def critical_attempt_kind(layout: Layout, item: dict[str, Any], details: dict | None) -> str:
    """Classify a relevant tool attempt as success, real command failure, or invalid."""
    tool = item.get("tool")
    args = item.get("arguments") or {}
    if not isinstance(args, dict):
        return "invalid"
    node = args.get("node")
    if (tool not in CRITICAL_TOOLS or node not in layout.nodes or
            args.get("interface") != layout.interfaces[node]):
        return "invalid"
    expected = TOOL_KEYS[tool]
    records = item.get("observations") or []
    if not isinstance(records, list) or not records:
        return "invalid"
    records_by_key = {record.get("id"): record for record in records if isinstance(record, dict)}
    if len(records_by_key) != len(records) or any(key not in expected for key in records_by_key):
        return "invalid"
    for key, record in records_by_key.items():
        if record.get("validation_error") or observation_mismatch(
                layout, node, key, record, details if key == "ospf_interface" else None):
            return "invalid"
    if item.get("status") in ("collection_failed", "unavailable"):
        if any(record.get("error") or record.get("exit_code") not in (0, None)
               for record in records_by_key.values()):
            return "failure"
        return "invalid"
    if item.get("status") != "ok" or set(records_by_key) != set(expected):
        return "invalid"
    if any(record.get("exit_code") != 0 or record.get("error") for record in records_by_key.values()):
        return "invalid"
    if tool == "query_ospf_interface":
        output = str(records_by_key["ospf_interface"].get("stdout") or "")
        if details is None or (ospf_address_area(output) is None and
                               not ospf_inactive_output(output, layout.interfaces[node])):
            return "invalid"
    if tool == "query_interface_config":
        parts = sections(str(records_by_key["running_config"].get("stdout") or ""))
        if f"interface {layout.interfaces[node]}" not in parts:
            return "invalid"
    return "success"


def critical_history(layout: Layout, evidence: dict[str, dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Return active failures and failure→success rechecks, without deleting history."""
    topology = validated_topology(layout, evidence)
    details = topology[1] if topology else None
    latest: dict[tuple[str, str, str], tuple[str, str]] = {}
    superseded: list[dict] = []
    for identifier, item in evidence.items():
        kind = critical_attempt_kind(layout, item, details)
        if kind == "invalid":
            continue
        args = item["arguments"]
        key = (item["tool"], args["node"], args["interface"])
        previous = latest.get(key)
        if kind == "success" and previous and previous[0] == "failure":
            superseded.append({"failed_evidence_id": previous[1],
                               "successful_evidence_id": identifier,
                               "tool": key[0], "node": key[1], "interface": key[2]})
        latest[key] = kind, identifier
    active = [{"evidence_id": identifier, "tool": key[0], "node": key[1], "interface": key[2]}
              for key, (kind, identifier) in latest.items() if kind == "failure"]
    return active, superseded
