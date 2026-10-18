#!/usr/bin/env python3
"""Independent, read-only acceptance judge for one saved agent trace.

The reference case is supplied only to this judge after the Agent has finished.
The judge never calls the model and never reads experiment injection records.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from collect_evidence import (Layout, observation_mismatch, ospf_address_area,
                              parse_node_config, ping_unreachable_observed)
from evidence_checks import critical_history, normal_proofs


EXPECTED = ("normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive",
            "insufficient_evidence", "collection_failure")


def observation_text(item: dict[str, Any]) -> str:
    return "\n".join(str(record.get("stdout") or "") for record in item.get("observations") or [])


def scoped_lines(config: str, heading: str) -> list[str]:
    lines = config.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line == heading)
    except StopIteration:
        return []
    result = []
    for line in lines[start + 1:]:
        if line and not line[0].isspace():
            break
        result.append(line.strip())
    return result


def verdict(trace: dict[str, Any], expected: str) -> dict[str, Any]:
    problems: list[str] = []
    if expected not in EXPECTED:
        raise ValueError("Unsupported expected status")
    rounds = trace.get("rounds") or []
    evidence = trace.get("evidence") or {}
    final = trace.get("final") or {}
    raw_status = (trace.get("model_raw_final") or {}).get("status")
    if rounds:
        content = (rounds[-1].get("model_message") or {}).get("content")
        if isinstance(content, str):
            fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.IGNORECASE | re.DOTALL)
            try:
                raw_status = json.loads(fenced[-1] if fenced else content.strip()).get("status")
            except (ValueError, AttributeError):
                pass
    citations = final.get("evidence_ids") or []
    if trace.get("error"):
        problems.append("Agent run has an API or local error")
    if trace.get("model_reported") != "deepseek-flash":
        problems.append("Response did not report deepseek-flash")
    if not rounds or any(not turn.get("request_id") for turn in rounds):
        problems.append("Missing real API response identifier")
    if final.get("status") != expected:
        problems.append(f"Final status {final.get('status')!r} differs from {expected!r}")
    if not isinstance(citations, list) or any(item not in evidence for item in citations):
        problems.append("Final citations contain missing evidence_id")
        citations = [item for item in citations if item in evidence]
    if len(citations) != len(set(citations)):
        problems.append("Final citations contain duplicate evidence_id")
    model_raw = trace.get("model_raw_final")
    if isinstance(model_raw, dict):
        checked_fields = ("status", "conclusion", "evidence_ids", "missing_information", "repair_suggestion")
        if trace.get("schema_version", 1) >= 2:
            checked_fields += ("root_cause", "observed_facts", "inferred_impacts")
        actual_changes = {key for key in checked_fields if model_raw.get(key) != final.get(key)}
        reported_changes = {item.get("field") for item in trace.get("guardrail_changes") or []
                            if isinstance(item, dict) and item.get("reason")}
        if actual_changes != reported_changes:
            problems.append("Guardrail change log does not match raw-versus-final differences")
    calls: list[dict[str, Any]] = []
    for turn in rounds:
        announced = (turn.get("model_message") or {}).get("tool_calls") or []
        returned = turn.get("tool_results") or []
        if len(announced) != len(returned):
            problems.append(f"Round {turn.get('round')}: model tool_calls and tool results differ in length")
        for model_call, result in zip(announced, returned):
            function = model_call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments", ""))
            except (TypeError, ValueError):
                arguments = None
            if (model_call.get("id") != result.get("tool_call_id") or
                    function.get("name") != result.get("name") or arguments != result.get("arguments")):
                problems.append(f"Round {turn.get('round')}: executed tool differs from API tool_calls")
            returned_item = result.get("result") or {}
            identifier = returned_item.get("evidence_id")
            saved = evidence.get(identifier)
            if not identifier or saved != returned_item:
                problems.append(f"Round {turn.get('round')}: returned evidence is absent or altered")
            calls.append({"round": turn.get("round"), "name": result.get("name"),
                          "arguments": result.get("arguments"), "evidence_id": identifier,
                          "status": returned_item.get("status")})
    if len(calls) != trace.get("tool_calls_executed"):
        problems.append("Tool-call count does not match the trace")
    if len(calls) > (trace.get("settings") or {}).get("max_tool_calls", 0):
        problems.append("Tool-call limit exceeded")
    returned_ids = {call["evidence_id"] for call in calls if call["evidence_id"]}
    if any(identifier not in returned_ids for identifier in citations):
        problems.append("Final citation was not returned by an actual model-selected tool call")
    if isinstance(model_raw, dict):
        original_ids = model_raw.get("evidence_ids")
        original_ids = original_ids if isinstance(original_ids, list) else []
        added_ids = [identifier for identifier in citations if identifier not in original_ids]
        additions = final.get("evidence_additions") or []
        if (not isinstance(additions, list) or
                [item.get("evidence_id") for item in additions if isinstance(item, dict)] != added_ids or
                len(additions) != len(added_ids)):
            problems.append("Evidence addition log does not match original-versus-final citations")
        else:
            for addition in additions:
                identifier = addition["evidence_id"]
                source = evidence.get(identifier) or {}
                if (identifier not in returned_ids or not isinstance(addition.get("reason"), str) or
                        not addition["reason"].strip() or
                        addition.get("source_tool") != source.get("tool") or
                        addition.get("node") != (source.get("arguments") or {}).get("node") or
                        addition.get("arguments") != source.get("arguments")):
                    problems.append(f"Added evidence {identifier} lacks a truthful tool-call source and reason")
    usage = trace.get("usage_per_request") or []
    total = trace.get("usage_total") or {}
    if len(usage) != len(rounds):
        problems.append("Per-request usage count differs from model response count")
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if any(not isinstance(item, dict) or not isinstance(item.get(key), int) for item in usage):
            problems.append(f"Missing API {key} usage")
        elif total.get(key) != sum(item[key] for item in usage):
            problems.append(f"Cumulative {key} does not equal API request sum")
    cited = {identifier: evidence[identifier] for identifier in citations if identifier in evidence}
    link = {item["node"]: item["interface"] for item in (trace.get("initial_topology") or {}).get("link") or []}
    nodes = list(link)
    layout = (Layout((trace.get("initial_topology") or {}).get("lab", "netsecops-ospf"),
                     tuple(nodes), link) if len(nodes) == 2 else None)
    active_failures, superseded = critical_history(layout, evidence) if layout is not None else ([], [])
    topology_items = [item for item in cited.values()
                      if item.get("tool") == "query_topology" and item.get("status") == "ok"]
    topology_data = topology_items[0].get("data", {}).get("nodes") if topology_items else None
    address_details = None
    if layout is not None and isinstance(topology_data, dict) and set(topology_data) == set(nodes):
        try:
            records = topology_items[0].get("observations") or []
            if len(records) != len(nodes) or {record.get("node") for record in records} != set(nodes):
                raise ValueError("topology configuration records missing or duplicated")
            parsed = {}
            for record in records:
                node = record["node"]
                if (record.get("id") != "running_config" or record.get("exit_code") != 0 or
                        record.get("error") or observation_mismatch(layout, node, "running_config", record)):
                    raise ValueError("topology configuration record does not match node")
                parsed[node] = parse_node_config(str(record.get("stdout") or ""), link[node])
                displayed = topology_data[node]
                if (not isinstance(displayed, dict) or
                        displayed.get("interconnect_interface") != link[node] or
                        displayed.get("interconnect_address") != parsed[node]["link_address"] or
                        displayed.get("loopback_address") != parsed[node]["loopback_address"]):
                    raise ValueError("topology addresses disagree with running configuration")
            address_details = parsed
        except (KeyError, TypeError, ValueError):
            problems.append("Cited topology addresses cannot be corroborated by running configuration")
    tool_keys = {"query_topology": {"running_config"}, "query_interface":
                 {"interfaces_ipv4", "interconnect_link", "frr_interface"},
                 "query_interface_config": {"running_config"}, "query_ospf_interface": {"ospf_interface"},
                 "query_neighbors": {"ospf_neighbors"}, "query_routes": {"routes", "ospf_routes"},
                 "ping_address": {"ping_peer_link", "ping_remote_loopback"}}
    for identifier, item in cited.items():
        if item.get("status") != "ok" or layout is None:
            continue
        requested = (item.get("arguments") or {}).get("node")
        for record in item.get("observations") or []:
            node = record.get("node")
            key = record.get("id")
            code = record.get("exit_code")
            measured_unreachable = key in ("ping_peer_link", "ping_remote_loopback") and ping_unreachable_observed(record)
            if (node not in link or key not in tool_keys.get(item.get("tool"), set()) or
                    (requested is not None and requested != node) or (code != 0 and not measured_unreachable) or
                    record.get("error") or observation_mismatch(
                        layout, node, key, record,
                        address_details if key.startswith("ping_") or key == "ospf_interface" else None)):
                problems.append(f"Cited observation {identifier} has node, command, interface, address, or output mismatch")
                break
    area_facts: dict[str, str] = {}
    area_evidence: dict[str, str] = {}
    area_attempts: set[str] = set()
    for identifier, item in evidence.items():
        if item.get("tool") != "query_ospf_interface":
            continue
        arguments = item.get("arguments") or {}
        node = arguments.get("node")
        if node in link and arguments.get("interface") == link[node]:
            area_attempts.add(node)
    for item in cited.values():
        if item.get("tool") != "query_ospf_interface" or item.get("status") != "ok":
            continue
        arguments = item.get("arguments") or {}
        node = arguments.get("node")
        if node in link and arguments.get("interface") == link[node]:
            records = item.get("observations") or []
            if (len(records) != 1 or records[0].get("exit_code") != 0 or records[0].get("error") or
                    layout is None or address_details is None or
                    observation_mismatch(layout, node, "ospf_interface", records[0], address_details)):
                continue
            fact = ospf_address_area(observation_text(item))
            if fact:
                area_facts[node] = fact[1]
                area_evidence[node] = next(identifier for identifier, saved in cited.items() if saved is item)
    if expected == "ospf_area_mismatch":
        if set(area_facts) != set(nodes) or len(set(area_facts.values())) != 2:
            problems.append("Cited raw OSPF interface outputs do not show two different Areas")
    admin_facts: list[str] = []
    passive_facts: list[str] = []
    admin_evidence: dict[str, str] = {}
    passive_evidence: dict[str, str] = {}
    for identifier, item in cited.items():
        args = item.get("arguments") or {}
        node = args.get("node")
        if node not in link or args.get("interface") != link[node] or item.get("status") != "ok":
            continue
        if item.get("tool") == "query_interface":
            records = {record.get("id"): record for record in item.get("observations") or []}
            frr = records.get("frr_interface") or {}
            output = str(frr.get("stdout") or "")
            if (frr.get("exit_code") == 0 and not frr.get("error") and layout is not None and
                    not observation_mismatch(layout, node, "frr_interface", frr) and
                    re.search(r"(?m)^Interface\s+" + re.escape(link[node]) + r"\s+is\s+down\b", output) and
                    re.search(r"(?im)^\s*Shutdown config is\s+(?!Not specified by CLI|off|disabled).+", output)):
                admin_facts.append(node)
                admin_evidence[node] = identifier
        if item.get("tool") == "query_interface_config":
            records = item.get("observations") or []
            if (len(records) != 1 or records[0].get("exit_code") != 0 or records[0].get("error") or
                    layout is None or observation_mismatch(layout, node, "running_config", records[0])):
                continue
            config = str(records[0].get("stdout") or "")
            interface_lines = scoped_lines(config, f"interface {link[node]}")
            process_lines = scoped_lines(config, "router ospf")
            if "ip ospf passive" in interface_lines or f"passive-interface {link[node]}" in process_lines:
                passive_facts.append(node)
                passive_evidence[node] = identifier
    if expected == "interface_admin_down" and not admin_facts:
        problems.append("No cited FRR output proves the interconnect administratively down")
    if expected == "ospf_passive" and not passive_facts:
        problems.append("No cited scoped running config proves interconnect OSPF passive")
    if expected in ("interface_admin_down", "ospf_passive"):
        direct_nodes = admin_facts if expected == "interface_admin_down" else passive_facts
        if len(set(direct_nodes)) != 1:
            problems.append("Fault node is not uniquely supported by cited direct evidence")
        else:
            node = direct_nodes[0]
            interface = link[node]
            repair = str(final.get("repair_suggestion") or "")
            expected_repair = (f"人工核对原配置后，仅在 {node} {interface} " +
                               ("恢复管理启用状态（no shutdown）。" if expected == "interface_admin_down"
                                else "恢复原有非 passive 状态（no ip ospf passive）。"))
            if repair != expected_repair:
                problems.append("Authoritative repair text differs from the evidenced fault action and target")
            if trace.get("schema_version", 1) >= 2:
                root = final.get("root_cause") or {}
                if (root.get("fault_type") != expected or root.get("node") != node or
                        root.get("interface") != interface or
                        root.get("evidence_ids") != [(admin_evidence if expected == "interface_admin_down"
                                                       else passive_evidence)[node]]):
                    problems.append("Structured root cause is not bound to cited fault node/interface evidence")
    if expected == "ospf_area_mismatch" and trace.get("schema_version", 1) >= 2:
        root = final.get("root_cause") or {}
        endpoints = root.get("endpoints") or []
        if (root.get("fault_type") != expected or root.get("node") is not None or
                root.get("interface") is not None or
                {item.get("node"): item.get("area") for item in endpoints} != area_facts or
                {item.get("node"): item.get("interface") for item in endpoints} != link or
                {item.get("node"): item.get("evidence_id") for item in endpoints} != area_evidence or
                set(root.get("evidence_ids") or []) != set(area_evidence.values())):
            problems.append("Structured Area root cause does not bind both cited endpoint facts")
        if final.get("repair_suggestion") != (
                "人工核对设计区域后，使两端互联接口 Area 一致；不要未经核实就指定某一端改配。"):
            problems.append("Authoritative Area repair text exceeds the verified two-endpoint fact")
    if expected == "normal":
        if active_failures:
            problems.append("Current critical collection failure is incompatible with a normal diagnosis")
        if layout is not None:
            latest_proofs, missing_requirements = normal_proofs(layout, evidence)
            if missing_requirements or any(identifier not in citations for identifier in latest_proofs.values()):
                problems.append("Normal conclusion lacks the latest verified topology, Areas, neighbors, routes, or bidirectional pings")
        if set(area_facts) != set(nodes) or len(set(area_facts.values())) != 1:
            problems.append("Cited raw OSPF interface outputs do not show equal Areas")
        topology = next((item.get("data", {}).get("nodes") for item in cited.values()
                         if item.get("tool") == "query_topology" and item.get("status") == "ok"), None)
        if not topology:
            problems.append("No cited topology addresses")
        else:
            for node in nodes:
                peer = next((other for other in nodes if other != node), None)
                remote = (topology.get(peer) or {}).get("loopback_address") if peer else None
                if not remote:
                    problems.append(f"No remote Loopback address for {node}")
                    continue
                if not any(item.get("tool") == "query_neighbors" and item.get("status") == "ok" and
                           item.get("arguments", {}).get("node") == node and
                           re.search(r"\bFull(?:/\S+)?\b", observation_text(item), re.IGNORECASE)
                           for item in cited.values()):
                    problems.append(f"No cited FULL neighbor for {node}")
                if not any(item.get("tool") == "query_routes" and item.get("status") == "ok" and
                           item.get("arguments", {}).get("node") == node and
                           item.get("arguments", {}).get("scope") == "ospf" and
                           re.search(r"(?m)^O[>* ]*\s+" + re.escape(remote + "/32") + r"\b", observation_text(item))
                           for item in cited.values()):
                    problems.append(f"No cited remote OSPF /32 route for {node}")
                if not any(item.get("tool") == "ping_address" and item.get("status") == "ok" and
                           item.get("arguments", {}).get("node") == node and
                           item.get("arguments", {}).get("target") == remote and
                           item.get("data", {}).get("reachable") is True and
                           all(record.get("exit_code") == 0 for record in item.get("observations") or [])
                           for item in cited.values()):
                    problems.append(f"No cited successful remote Loopback ping for {node}")
    if expected == "insufficient_evidence":
        if set(area_attempts) != set(nodes):
            problems.append("Both interface Areas were not attempted")
        if set(area_facts) == set(nodes) and len(set(area_facts.values())) == 2:
            problems.append("Two different valid Areas exist; this is not the missing-area case")
        suggestion = str(final.get("repair_suggestion") or "")
        if re.search(r"ip ospf area|区域统一|配置到相同|改为\s*Area", suggestion, re.IGNORECASE):
            problems.append("Repair suggestion assumes an unconfirmed area root cause")
    if expected == "collection_failure":
        if set(area_attempts) != set(nodes):
            problems.append("Both interface Areas were not attempted")
        current_failed_ids = {item["evidence_id"] for item in active_failures
                              if item["tool"] == "query_ospf_interface"}
        if not any(identifier in current_failed_ids for identifier in citations):
            problems.append("No cited failed OSPF interface collection")
        if any(item["failed_evidence_id"] in citations and
               item["failed_evidence_id"] not in current_failed_ids for item in superseded):
            problems.append("Cited collection failure was superseded by a valid successful recheck")
        narrative = str(final.get("conclusion") or "") + " " + " ".join(
            str(value) for value in final.get("missing_information") or [])
        if not re.search(r"采集|命令|不可用|失败", narrative):
            problems.append("Final text does not explain the failed collection")
    return {
        "verdict": "PASS" if not problems else "FAIL", "expected": expected,
        "actual": final.get("status"), "model_raw_status": raw_status,
        "post_validation_changed_status": raw_status is not None and raw_status != final.get("status"),
        "problems": problems,
        "model": trace.get("model_reported"), "model_requests": len(rounds),
        "tool_calls": len(calls), "elapsed_seconds": trace.get("elapsed_seconds"),
        "input_tokens": total.get("prompt_tokens"), "output_tokens": total.get("completion_tokens"),
        "total_tokens": total.get("total_tokens"), "area_facts_cited": area_facts,
        "tool_sequence": calls, "cited_evidence_ids": citations,
        "admin_facts_cited": admin_facts, "passive_facts_cited": passive_facts,
        "guardrail_changes": trace.get("guardrail_changes") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--expected", choices=EXPECTED, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verdict(json.loads(args.trace.read_text(encoding="utf-8")), args.expected)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
