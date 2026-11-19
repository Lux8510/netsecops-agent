#!/usr/bin/env python3
"""Semantic checks on cited evidence: does the output actually support the claim?

Facts are taken only from cited, successful observations that match the lab's
interconnect interface. Missing facts stay unknown: they are never treated as
support. A claim is checked against the claim itself; accuracy is checked
separately against the scenario's expected status.
"""

from __future__ import annotations

import re
from typing import Any

AREA = re.compile(r"Internet Address\s+\d{1,3}(?:\.\d{1,3}){3}/\d{1,2},.*?\bArea\s+(\d{1,3}(?:\.\d{1,3}){3})")
NEIGHBORS = re.compile(r"Neighbor Count is\s+(\d+)")
ADMIN_DOWN = re.compile(r"^Interface\s+\S+\s+is\s+down\b", re.MULTILINE)
SHUTDOWN_CONFIG = re.compile(r"^\s*Shutdown config is\s+(?!Not specified by CLI|off|disabled).+",
                             re.MULTILINE | re.IGNORECASE)
LOSS = re.compile(r"(\d+(?:\.\d+)?)%\s*packet loss", re.IGNORECASE)
DEFINITE = ("normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive")


def _interconnects(trace: dict[str, Any]) -> dict[str, str]:
    result = {}
    for item in (trace.get("initial_topology") or {}).get("link") or []:
        if isinstance(item, dict) and isinstance(item.get("node"), str) and isinstance(item.get("interface"), str):
            result[item["node"]] = item["interface"]
    return result


def _observations(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in evidence.get("observations") or [] if isinstance(item, dict)]


def _text(evidence: dict[str, Any]) -> str:
    return "\n".join(str(item.get("stdout") or "") for item in _observations(evidence))


def _successful(evidence: dict[str, Any]) -> bool:
    if evidence.get("status") != "ok":
        return False
    observations = _observations(evidence)
    if not observations:
        return False
    return all(item.get("exit_code") == 0 and not item.get("error") for item in observations)


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


def _interface(evidence: dict[str, Any]) -> str | None:
    value = (evidence.get("arguments") or {}).get("interface")
    return value if isinstance(value, str) else None


def collect_facts(trace: dict[str, Any], *, cited_only: bool = True) -> dict[str, dict[str, Any]]:
    cited = set((trace.get("final") or {}).get("evidence_ids") or []) if cited_only else None
    links = _interconnects(trace)
    facts: dict[str, dict[str, Any]] = {}
    for evidence_id, evidence in (trace.get("evidence") or {}).items():
        if cited is not None and evidence_id not in cited:
            continue
        if not _successful(evidence):
            continue
        node = _node(evidence)
        tool = evidence.get("tool")
        if not node or tool not in ("query_ospf_interface", "query_neighbors", "query_interface",
                                    "query_interface_config", "ping_address"):
            continue
        fact = facts.setdefault(node, {"neighbor_counts": [], "ping_results": [],
                                       "passive_interfaces": set()})
        text = _text(evidence)
        interconnect = links.get(node)
        if tool == "query_ospf_interface":
            if interconnect and _interface(evidence) != interconnect:
                continue
            area = AREA.search(text)
            if area:
                fact["area"] = area.group(1)
            count = NEIGHBORS.search(text)
            if count:
                fact["neighbor_counts"].append(int(count.group(1)))
            if ADMIN_DOWN.search(text) or (SHUTDOWN_CONFIG.search(text) and "is down" in text):
                fact["admin_down"] = True
            else:
                fact.setdefault("admin_down", False)
        elif tool == "query_neighbors":
            rows = [line for line in text.splitlines()
                    if line.strip() and not line.lower().startswith(("neighbor id", "interface", "---"))]
            if "FULL" in text.upper():
                fact["neighbor_counts"].append(1)
            elif rows == 0:
                fact["neighbor_counts"].append(0)
        elif tool == "query_interface":
            if interconnect and _interface(evidence) != interconnect:
                continue
            if ADMIN_DOWN.search(text) or SHUTDOWN_CONFIG.search(text):
                fact["admin_down"] = True
        elif tool == "query_interface_config":
            interface = _interface(evidence)
            if not interface:
                continue
            section = _config_section(text, interface)
            if re.search(r"(?m)^\s*ip ospf passive\s*$", section):
                fact["passive_interfaces"].add(interface)
                if interface == interconnect:
                    fact["passive"] = True
        elif tool == "ping_address":
            loss = LOSS.search(text)
            if loss:
                fact["ping_results"].append(float(loss.group(1)) == 0.0)
            elif re.search(r"Network unreachable", text, re.IGNORECASE):
                fact["ping_results"].append(False)
    return facts


def _summary(facts: dict[str, dict[str, Any]], nodes: tuple[str, ...]) -> dict[str, Any]:
    areas = {node: facts.get(node, {}).get("area") for node in nodes}
    known_areas = [value for value in areas.values() if isinstance(value, str)]
    neighbors = {node: facts.get(node, {}).get("neighbor_counts") or [] for node in nodes}
    pings = {node: facts.get(node, {}).get("ping_results") or [] for node in nodes}
    return {
        "areas": known_areas,
        "area_by_node": areas,
        "neighbors": neighbors,
        "pings": pings,
        "admin_down": [node for node in nodes if facts.get(node, {}).get("admin_down")],
        "passive": [node for node in nodes if facts.get(node, {}).get("passive")],
    }


def semantic_support(trace: dict[str, Any], claim: str) -> dict[str, Any]:
    if claim not in DEFINITE + ("insufficient_evidence", "collection_failure"):
        raise ValueError(f"Unsupported claim: {claim!r}")
    facts = _summary(collect_facts(trace), ("r1", "r2"))
    areas = facts["areas"]
    admin = facts["admin_down"]
    passive = facts["passive"]
    checks: list[dict[str, Any]] = []
    contradictions: list[str] = []
    ambiguous = len(set(admin) | set(passive)) + (1 if len(set(areas)) == 2 else 0) >= 2

    def check(name: str, ok: bool, reason: str) -> None:
        checks.append({"check": name, "ok": ok})
        if not ok:
            contradictions.append(reason)

    if claim == "normal":
        check("areas_known", len(areas) == 2, "area evidence missing or incomplete")
        check("areas_equal", len(areas) == 2 and len(set(areas)) == 1, "areas differ")
        known_neighbors = {node: values for node, values in facts["neighbors"].items() if values}
        check("neighbors_known", len(known_neighbors) == 2, "neighbor evidence missing")
        check("neighbors_present", len(known_neighbors) == 2
              and all(any(value > 0 for value in values) for values in known_neighbors.values()),
              "neighbor count is zero")
        ping_values = [value for values in facts["pings"].values() for value in values]
        check("ping_evidence", len(ping_values) >= 2 and all(ping_values), "ping evidence missing or failing")
        check("no_admin_down", not admin, "admin down on " + ",".join(admin))
        check("no_passive", not passive, "passive on " + ",".join(passive))
    elif claim == "ospf_area_mismatch":
        check("areas_known", len(areas) == 2, "need two known interconnect areas")
        check("areas_differ", len(set(areas)) == 2, "areas are not different")
        check("no_direct_fault", not admin and not passive, "a direct interface fault also exists")
    elif claim == "interface_admin_down":
        check("admin_down_present", bool(admin), "no admin down evidence")
        check("areas_equal", len(set(areas)) <= 1, "areas also differ (ambiguous)")
    elif claim == "ospf_passive":
        check("passive_present", bool(passive), "no passive evidence on the interconnect")
        check("areas_equal", len(set(areas)) <= 1, "areas also differ (ambiguous)")
    else:
        checks.append({"check": "abstention", "ok": True})

    return {"supported": not contradictions and all(check["ok"] for check in checks),
            "checks": checks, "contradictions": contradictions, "ambiguous_faults": ambiguous,
            "facts": {"areas": sorted(set(areas)),
                      "admin_down": sorted(admin), "passive": sorted(passive),
                      "neighbor_counts": facts["neighbors"], "pings": facts["pings"]}}
