#!/usr/bin/env python3
"""Semantic checks on cited evidence: does the output actually support the claim?

Structural coverage (tool + node) is necessary but not sufficient: a trace can
cite the right command and still misread its output. These checks parse the
saved observations and verify the facts the conclusion depends on.
"""

from __future__ import annotations

import re
from typing import Any

AREA = re.compile(r"Internet Address\s+\d{1,3}(?:\.\d{1,3}){3}/\d{1,2},.*?\bArea\s+(\d{1,3}(?:\.\d{1,3}){3})")
NEIGHBORS = re.compile(r"Neighbor Count is\s+(\d+)")
ADMIN_DOWN = re.compile(r"^Interface\s+\S+\s+is\s+down\b", re.MULTILINE)
SHUTDOWN_CONFIG = re.compile(r"^\s*Shutdown config is\s+(?!Not specified by CLI|off|disabled).+",
                             re.MULTILINE | re.IGNORECASE)
PING_OK = re.compile(r"(?:0(?:\.0+)?% packet loss|, 0% packet loss)", re.IGNORECASE)


def _observations(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in evidence.get("observations") or [] if isinstance(item, dict)]


def _text(evidence: dict[str, Any]) -> str:
    return "\n".join(str(item.get("stdout") or "") for item in _observations(evidence))


def _config_section(text: str, interface: str) -> str:
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == f"interface {interface}")
    except StopIteration:
        return ""
    result = []
    for line in lines[start + 1:]:
        if line and not line[0].isspace():
            break
        result.append(line)
    return "\n".join(result)


def _node(evidence: dict[str, Any]) -> str | None:
    value = (evidence.get("arguments") or {}).get("node")
    return value if isinstance(value, str) else None


def collect_facts(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for evidence in (trace.get("evidence") or {}).values():
        node = _node(evidence)
        tool = evidence.get("tool")
        if not node or tool not in ("query_ospf_interface", "query_neighbors", "query_interface",
                                    "query_interface_config", "ping_address"):
            continue
        fact = facts.setdefault(node, {})
        text = _text(evidence)
        if tool == "query_ospf_interface":
            area = AREA.search(text)
            if area:
                fact["area"] = area.group(1)
            count = NEIGHBORS.search(text)
            if count:
                fact["neighbors"] = int(count.group(1))
            if ADMIN_DOWN.search(text) or (SHUTDOWN_CONFIG.search(text) and "is down" in text):
                fact["admin_down"] = True
            else:
                fact.setdefault("admin_down", False)
        elif tool == "query_neighbors":
            rows = [line for line in text.splitlines()
                    if line.strip() and not line.lower().startswith(("neighbor id", "interface", "---"))]
            fact["neighbor_rows"] = len(rows)
            if "FULL" in text.upper():
                fact["neighbor_full"] = True
            elif rows == 0:
                fact["neighbor_full"] = False
        elif tool == "query_interface":
            if ADMIN_DOWN.search(text) or SHUTDOWN_CONFIG.search(text):
                fact["admin_down"] = True
        elif tool == "query_interface_config":
            interface = (evidence.get("arguments") or {}).get("interface")
            section = _config_section(text, str(interface))
            fact.setdefault("passive_interfaces", set())
            if re.search(r"(?m)^\s*ip ospf passive\s*$", section):
                fact["passive_interfaces"].add(str(interface))
        elif tool == "ping_address":
            if PING_OK.search(text):
                fact["ping_ok"] = True
            elif re.search(r"(?:100% packet loss|Network unreachable)", text, re.IGNORECASE):
                fact["ping_ok"] = False
    return facts


def _facts_for(facts: dict[str, dict[str, Any]], nodes: tuple[str, ...] = ("r1", "r2")) -> dict[str, Any]:
    areas = [facts.get(node, {}).get("area") for node in nodes]
    areas = [area for area in areas if isinstance(area, str)]
    admin = [node for node in nodes if facts.get(node, {}).get("admin_down")]
    passive = [node for node in nodes
               if facts.get(node, {}).get("passive_interfaces")]
    return {"areas": areas, "admin_down": admin, "passive": passive,
            "neighbors": {node: facts.get(node, {}).get("neighbors") for node in nodes}}


def semantic_support(trace: dict[str, Any], expected: str) -> dict[str, Any]:
    facts = _facts_for(collect_facts(trace))
    areas = facts["areas"]
    admin = facts["admin_down"]
    passive = facts["passive"]
    neighbor_values = [value for value in facts["neighbors"].values() if isinstance(value, int)]
    checks: list[dict[str, Any]] = []
    contradictions: list[str] = []
    ambiguous = len(set(admin) | set(passive)) + (1 if len(set(areas)) == 2 else 0) >= 2

    if expected == "normal":
        if len(areas) == 2 and len(set(areas)) == 1:
            checks.append({"check": "areas_equal", "ok": True})
        elif len(areas) == 2:
            checks.append({"check": "areas_equal", "ok": False})
            contradictions.append("areas differ")
        else:
            contradictions.append("area evidence missing")
        if neighbor_values and all(value > 0 for value in neighbor_values):
            checks.append({"check": "neighbors_present", "ok": True})
        elif neighbor_values:
            checks.append({"check": "neighbors_present", "ok": False})
            contradictions.append("neighbor count is zero")
        if admin:
            contradictions.append("admin down on " + ",".join(admin))
        if passive:
            contradictions.append("passive on " + ",".join(passive))
    elif expected == "ospf_area_mismatch":
        if len(set(areas)) == 2:
            checks.append({"check": "areas_differ", "ok": True})
        else:
            contradictions.append("expected two different areas")
        if admin or passive:
            contradictions.append("a direct interface fault exists as well")
    elif expected == "interface_admin_down":
        if admin:
            checks.append({"check": "admin_down_present", "ok": True})
        else:
            contradictions.append("no admin down evidence")
        if len(set(areas)) == 2:
            contradictions.append("areas also differ (ambiguous)")
    elif expected == "ospf_passive":
        if passive:
            checks.append({"check": "passive_present", "ok": True})
        else:
            contradictions.append("no passive evidence")
        if len(set(areas)) == 2:
            contradictions.append("areas also differ (ambiguous)")
    elif expected == "insufficient_evidence":
        checks.append({"check": "abstention", "ok": True})
    elif expected == "collection_failure":
        checks.append({"check": "collection_failure", "ok": True})

    supported = not contradictions and all(check["ok"] for check in checks)
    return {"supported": supported, "checks": checks, "contradictions": contradictions,
            "ambiguous_faults": ambiguous, "facts": {
                "areas": sorted(set(areas)),
                "admin_down": sorted(admin),
                "passive": sorted(passive),
            }}
