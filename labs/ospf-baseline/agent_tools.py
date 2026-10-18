"""Read-only, topology-constrained live and replay tools for one OSPF lab."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from collect_evidence import (EXPECTED_IMAGE, LAB_DIR, Layout, checked_ipv4,
                              execute, observation_mismatch, parse_node_config,
                              ping_unreachable_observed,
                              read_layout, sections, utc_now)


TOOL_ARGUMENTS = {
    "query_topology": (),
    "query_interface": ("node", "interface"),
    "query_interface_config": ("node", "interface"),
    "query_ospf_interface": ("node", "interface"),
    "query_neighbors": ("node",),
    "query_routes": ("node", "scope"),
    "ping_address": ("node", "target"),
}

TOOL_DESCRIPTIONS = {
    "query_topology": "查询这一个实验的节点、互联接口及互联/Loopback 地址；不读取故障记录。已返回的地址无需另查 lo 接口。",
    "query_interface": "查询指定节点的 Linux 链路、地址和 FRR 管理状态；可直接证明互联接口管理关闭。interface 只能是拓扑互联接口或 lo。",
    "query_interface_config": "只读查询指定互联接口的运行配置及 OSPF passive 配置；在怀疑 passive 或配置冲突时使用，不必无故查询两端。",
    "query_ospf_interface": "查询指定节点互联接口的 OSPF 状态与 Area；不能指定任意接口。",
    "query_neighbors": "查询指定节点的 OSPF 邻居。",
    "query_routes": "查询指定节点的路由表，scope 只能为 all 或 ospf。",
    "ping_address": "从指定节点测试对端互联地址或 Loopback 地址；不能探测拓扑以外目标。",
}


class ToolInputError(ValueError):
    pass


class ToolDeadlineExceeded(TimeoutError):
    pass


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise ToolDeadlineExceeded("total diagnosis deadline reached before next command")


def tool_schemas() -> list[dict[str, Any]]:
    properties = {
        "node": {"type": "string", "description": "节点名称，例如 r1 或 r2"},
        "interface": {"type": "string", "description": "已知接口名称，例如 eth1 或 lo"},
        "scope": {"type": "string", "enum": ["all", "ospf"]},
        "target": {"type": "string", "description": "对端互联或 Loopback 的 IPv4 地址"},
    }
    return [{"type": "function", "function": {
        "name": name, "description": TOOL_DESCRIPTIONS[name],
        "parameters": {"type": "object", "properties": {key: properties[key] for key in keys},
                       "required": list(keys), "additionalProperties": False},
    }} for name, keys in TOOL_ARGUMENTS.items()]


class ObservationBackend:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self._details: dict[str, dict[str, str]] | None = None
        self._config_records: dict[str, dict[str, Any]] = {}

    def get(self, node: str, key: str, *, deadline: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def details(self, *, deadline: float | None = None) -> dict[str, dict[str, str]]:
        if self._details is None:
            found: dict[str, dict[str, str]] = {}
            for node in self.layout.nodes:
                check_deadline(deadline)
                record = self.get(node, "running_config", deadline=deadline)
                self._config_records[node] = record
                if record.get("exit_code") != 0 or record.get("error"):
                    raise ValueError(f"{node}: running configuration unavailable")
                found[node] = parse_node_config(str(record.get("stdout") or ""), self.layout.interfaces[node])
            self._details = found
        return self._details


class ReplayBackend(ObservationBackend):
    """Receives a single observations.json, never an experiment directory or record."""

    def __init__(self, observations_path: Path, layout: Layout | None = None) -> None:
        super().__init__(layout or read_layout(LAB_DIR))
        document = json.loads(observations_path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1 or document.get("lab") != self.layout.lab_name:
            raise ValueError("Incompatible collector observation schema or lab")
        if set(document.get("nodes", {})) != set(self.layout.nodes):
            raise ValueError("Observation nodes do not match the lab")
        self._document = document

    def get(self, node: str, key: str, *, deadline: float | None = None) -> dict[str, Any]:
        check_deadline(deadline)
        if node not in self.layout.nodes:
            raise ValueError("Unknown topology node")
        matches = [item for item in self._document["nodes"][node].get("commands", [])
                   if isinstance(item, dict) and item.get("id") == key]
        if len(matches) != 1:
            return {"node": node, "id": key, "command": [], "started_at_utc": None,
                    "ended_at_utc": None, "exit_code": None,
                    "error": "observation missing or duplicated", "stdout": "", "stderr": ""}
        item = deepcopy(matches[0])
        details = self.details(deadline=deadline) if key.startswith("ping_") or key == "ospf_interface" else None
        mismatch = observation_mismatch(self.layout, node, key, item, details)
        if mismatch:
            item["validation_error"] = mismatch
            item["error"] = mismatch
        return item


class LiveBackend(ObservationBackend):
    def __init__(self, command_timeout_seconds: int, layout: Layout | None = None) -> None:
        super().__init__(layout or read_layout(LAB_DIR))
        if sys.platform != "linux" or "microsoft-standard-WSL2" not in os.uname().release:
            raise RuntimeError("Live tools require the existing Ubuntu/WSL2 lab")
        containers = [self.layout.container(node) for node in self.layout.nodes]
        # Never prompt for sudo while the fault is active; preflight happens before injection.
        inspected = subprocess.run(["sudo", "-n", "docker", "inspect", *containers],
                                   capture_output=True, text=True, timeout=command_timeout_seconds, check=True)
        metadata = json.loads(inspected.stdout)
        if len(metadata) != len(containers):
            raise ValueError("Unexpected number of lab containers")
        for container, item in zip(containers, metadata):
            if (item.get("Name") != "/" + container or item.get("Config", {}).get("Image") != EXPECTED_IMAGE
                    or item.get("State", {}).get("Running") is not True):
                raise ValueError("Lab container identity, pinned image, or running state does not match")
        self.command_timeout_seconds = command_timeout_seconds

    def get(self, node: str, key: str, *, deadline: float | None = None) -> dict[str, Any]:
        check_deadline(deadline)
        details = None if key == "running_config" else self.details(deadline=deadline)
        item = execute(self.layout, node, key, details, timeout_seconds=self.command_timeout_seconds,
                       deadline=deadline)
        mismatch = observation_mismatch(self.layout, node, key, item, details)
        if mismatch:
            item["validation_error"] = mismatch
            item["error"] = mismatch
        return item


def command_failed(item: dict[str, Any], *, ping: bool = False) -> bool:
    if item.get("error"):
        return True
    code = item.get("exit_code")
    if ping and code == 1:
        return not ping_unreachable_observed(item)
    return code != 0


class AgentTools:
    def __init__(self, backend: ObservationBackend) -> None:
        self.backend = backend
        self.layout = backend.layout
        self.evidence: dict[str, dict[str, Any]] = {}

    def _validated_args(self, name: str, arguments: Any) -> dict[str, str]:
        if name not in TOOL_ARGUMENTS:
            raise ToolInputError("Unknown tool")
        expected = set(TOOL_ARGUMENTS[name])
        if not isinstance(arguments, dict) or set(arguments) != expected:
            raise ToolInputError("Tool arguments do not match the declared schema")
        if any(not isinstance(value, str) for value in arguments.values()):
            raise ToolInputError("Tool arguments must be strings")
        if "node" in arguments and arguments["node"] not in self.layout.nodes:
            raise ToolInputError("Node is outside this topology")
        if "interface" in arguments:
            node = arguments["node"]
            allowed = ({self.layout.interfaces[node], "lo"} if name == "query_interface"
                       else {self.layout.interfaces[node]})
            if arguments["interface"] not in allowed:
                raise ToolInputError("Interface is outside this tool's fixed allowlist")
        if "scope" in arguments and arguments["scope"] not in ("all", "ospf"):
            raise ToolInputError("Route scope must be all or ospf")
        if "target" in arguments:
            try:
                canonical = checked_ipv4(arguments["target"])
            except ValueError:
                raise ToolInputError("Target must be a valid IPv4 address") from None
            if canonical != arguments["target"]:
                raise ToolInputError("Target must be a canonical IPv4 address")
        return arguments

    def call(self, name: str, arguments: Any, *, deadline: float | None = None) -> dict[str, Any]:
        evidence_id = f"E{len(self.evidence) + 1:03d}"
        record: dict[str, Any] = {"evidence_id": evidence_id, "collected_at_utc": utc_now(),
                                  "tool": name, "arguments": arguments, "status": "ok",
                                  "data": None, "observations": [], "error": None}
        try:
            args = self._validated_args(name, arguments)
            check_deadline(deadline)
            self._dispatch(name, args, record, deadline=deadline)
        except ToolDeadlineExceeded as exc:
            record["status"] = "limit_exceeded"
            record["error"] = str(exc)
        except ToolInputError as exc:
            record["status"] = "invalid_argument"
            record["error"] = str(exc)
        except ValueError as exc:
            record["status"] = "unavailable"
            record["error"] = str(exc)
        except (OSError, KeyError, TypeError) as exc:
            record["status"] = "unavailable"
            record["error"] = f"{type(exc).__name__}: tool observation unavailable"
        self.evidence[evidence_id] = record
        return record

    def _add(self, record: dict[str, Any], node: str, key: str, *, ping: bool = False,
             deadline: float | None = None) -> dict[str, Any]:
        check_deadline(deadline)
        item = self.backend.get(node, key, deadline=deadline)
        record["observations"].append(item)
        if item.get("validation_error"):
            record["status"] = "unavailable"
            record["error"] = f"{node}/{key}: observation identity or content is inconsistent"
        elif command_failed(item, ping=ping):
            record["status"] = "collection_failed"
            record["error"] = f"{node}/{key}: command failed or output unavailable"
        return item

    def _dispatch(self, name: str, args: dict[str, str], record: dict[str, Any],
                  *, deadline: float | None = None) -> None:
        if name == "query_topology":
            details = self.backend.details(deadline=deadline)
            for node in self.layout.nodes:
                record["observations"].append(deepcopy(self.backend._config_records[node]))
            record["data"] = {"lab": self.layout.lab_name, "nodes": {
                node: {"interconnect_interface": self.layout.interfaces[node],
                       "interconnect_address": details[node]["link_address"],
                       "loopback_address": details[node]["loopback_address"],
                       "peer": self.layout.peer(node)} for node in self.layout.nodes}}
        elif name == "query_interface":
            node, interface = args["node"], args["interface"]
            address = self._add(record, node, "interfaces_ipv4", deadline=deadline)
            if interface == self.layout.interfaces[node]:
                link = self._add(record, node, "interconnect_link", deadline=deadline)
                frr = self._add(record, node, "frr_interface", deadline=deadline)
            else:
                link = None
                frr = None
            lines = [line for line in str(address.get("stdout") or "").splitlines()
                     if line.split() and line.split()[0].split("@", 1)[0] == interface]
            record["data"] = {"node": node, "interface": interface, "address_lines": lines,
                              "link_output": link.get("stdout") if link else None,
                              "frr_output": frr.get("stdout") if frr else None}
            if not lines and record["status"] == "ok":
                record["status"] = "unavailable"
                record["error"] = "Interface not present in address output"
        elif name == "query_interface_config":
            node, interface = args["node"], args["interface"]
            item = self._add(record, node, "running_config", deadline=deadline)
            if record["status"] != "ok":
                return
            parts = sections(str(item.get("stdout") or ""))
            lines = parts.get(f"interface {interface}")
            if lines is None:
                record["status"] = "unavailable"
                record["error"] = "Requested interface is absent from running configuration"
                return
            passive_lines = [line for line in parts.get("router ospf", [])
                             if line.startswith("passive-interface ") or line.startswith("no passive-interface ")]
            record["data"] = {"node": node, "interface": interface,
                              "interface_config": "\n".join([f"interface {interface}", *lines]),
                              "ospf_passive_config": "\n".join(["router ospf", *passive_lines])}
        elif name == "query_ospf_interface":
            item = self._add(record, args["node"], "ospf_interface", deadline=deadline)
            record["data"] = {"node": args["node"], "interface": args["interface"],
                              "output": item.get("stdout")}
        elif name == "query_neighbors":
            item = self._add(record, args["node"], "ospf_neighbors", deadline=deadline)
            record["data"] = {"node": args["node"], "output": item.get("stdout")}
        elif name == "query_routes":
            key = "routes" if args["scope"] == "all" else "ospf_routes"
            item = self._add(record, args["node"], key, deadline=deadline)
            record["data"] = {"node": args["node"], "scope": args["scope"], "output": item.get("stdout")}
        elif name == "ping_address":
            peer = self.layout.peer(args["node"])
            details = self.backend.details(deadline=deadline)
            target = args["target"]
            if target == details[peer]["link_address"]:
                key = "ping_peer_link"
            elif target == details[peer]["loopback_address"]:
                key = "ping_remote_loopback"
            else:
                raise ToolInputError("Target is outside the allowed peer link/Loopback addresses")
            item = self._add(record, args["node"], key, ping=True, deadline=deadline)
            record["data"] = {"node": args["node"], "target": target,
                              "reachable": item.get("exit_code") == 0 if record["status"] == "ok" else None,
                              "stdout": item.get("stdout"), "stderr": item.get("stderr")}

