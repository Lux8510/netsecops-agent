#!/usr/bin/env python3
"""Collect read-only evidence from the two existing FRR lab nodes.

Only the command templates in COMMANDS are executable. All dynamic values come
from the local topology and the routers' running configurations and are checked
before being inserted into an argument vector. subprocess never uses a shell.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAB_DIR = Path(__file__).resolve().parent
EXPECTED_IMAGE = "quay.io/frrouting/frr:containerlab-10.7.1"
SAFE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
ENDPOINTS = re.compile(
    r'^\s*-\s*endpoints:\s*\[\s*"([^":]+):([^":]+)"\s*,\s*"([^":]+):([^":]+)"\s*\]\s*$'
)
NETWORK = re.compile(r"^network\s+(\S+)\s+area\s+(\S+)$")
ADDRESS = re.compile(r"^ip address\s+(\S+)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def checked_name(value: str) -> str:
    if not SAFE_NAME.fullmatch(value):
        raise ValueError(f"Unsafe lab, node, or interface name: {value!r}")
    return value


def checked_ipv4(value: str) -> str:
    return str(ipaddress.IPv4Address(value))


def checked_area(value: str) -> str:
    return str(ipaddress.IPv4Address(value))


def checked_prefix(value: str) -> str:
    return str(ipaddress.IPv4Network(value, strict=False))


@dataclass(frozen=True)
class Layout:
    lab_name: str
    nodes: tuple[str, str]
    interfaces: dict[str, str]

    def container(self, node: str) -> str:
        if node not in self.nodes:
            raise ValueError(f"Unexpected node: {node}")
        return f"clab-{self.lab_name}-{node}"

    def peer(self, node: str) -> str:
        return self.nodes[1] if node == self.nodes[0] else self.nodes[0]


def read_layout(lab_dir: Path = LAB_DIR) -> Layout:
    text = (lab_dir / "topology.clab.yml").read_text(encoding="utf-8")
    lab_match = re.search(r"(?m)^name:\s*([A-Za-z][A-Za-z0-9_.-]*)\s*$", text)
    image_match = re.search(r"(?m)^\s*image:\s*(\S+)\s*$", text)
    links = [ENDPOINTS.fullmatch(line) for line in text.splitlines()]
    links = [match for match in links if match]
    if not lab_match or not image_match or image_match.group(1) != EXPECTED_IMAGE:
        raise ValueError("Topology name or pinned FRR image does not match this experiment")
    if len(links) != 1:
        raise ValueError("Expected exactly one two-node link in the topology")
    node_a, iface_a, node_b, iface_b = links[0].groups()
    if node_a == node_b:
        raise ValueError("The link must connect two different nodes")
    for value in (lab_match.group(1), node_a, iface_a, node_b, iface_b):
        checked_name(value)
    return Layout(lab_match.group(1), (node_a, node_b), {node_a: iface_a, node_b: iface_b})


def sections(config: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current = "global"
    result[current] = []
    for line in config.splitlines():
        if line and not line[0].isspace() and line != "!":
            current = line.strip()
            result.setdefault(current, [])
        elif line.strip() != "!":
            result.setdefault(current, []).append(line.strip())
    return result


def parse_node_config(config: str, link_interface: str) -> dict[str, str]:
    """Identify the active link network/area and the unique non-link /32."""
    parts = sections(config)
    link_lines = parts.get(f"interface {link_interface}")
    ospf_lines = parts.get("router ospf")
    if link_lines is None or ospf_lines is None:
        raise ValueError(f"Missing interface {link_interface} or router ospf in running config")
    interface_areas = any(line.startswith("ip ospf area ") for lines in parts.values() for line in lines)
    link_addresses = [ipaddress.IPv4Interface(m.group(1)) for line in link_lines if (m := ADDRESS.fullmatch(line))]
    if len(link_addresses) != 1:
        raise ValueError(f"Expected one IPv4 address on {link_interface}")
    link_address = link_addresses[0]
    networks: list[tuple[ipaddress.IPv4Network, str]] = []
    for line in ospf_lines:
        match = NETWORK.fullmatch(line)
        if match:
            networks.append((ipaddress.IPv4Network(match.group(1), strict=False), checked_area(match.group(2))))
    if networks and interface_areas:
        raise ValueError("Mixed OSPF network and interface-level area configuration")
    if networks:
        matches = [(prefix, area) for prefix, area in networks if link_address.network.subnet_of(prefix)]
        if len(matches) != 1:
            raise ValueError(f"Expected one OSPF network covering {link_interface}, found {len(matches)}")
        link_prefix, area = matches[0]
        if link_prefix != link_address.network:
            raise ValueError("The link OSPF statement must exactly match its configured subnet")
        ospf_mode = "network"
    else:
        area_lines = [line.split()[3] for line in link_lines if line.startswith("ip ospf area ")]
        if len(area_lines) != 1:
            raise ValueError(f"Expected one OSPF area on {link_interface}")
        link_prefix, area = link_address.network, checked_area(area_lines[0])
        ospf_mode = "interface"
    loopbacks: list[ipaddress.IPv4Interface] = []
    for heading, lines in parts.items():
        if heading.startswith("interface ") and heading != f"interface {link_interface}":
            for line in lines:
                match = ADDRESS.fullmatch(line)
                if match:
                    addr = ipaddress.IPv4Interface(match.group(1))
                    advertised = (any(addr.ip in prefix for prefix, _ in networks)
                                  if ospf_mode == "network" else
                                  any(item.startswith("ip ospf area ") for item in lines))
                    if addr.network.prefixlen == 32 and advertised:
                        loopbacks.append(addr)
    if len(loopbacks) != 1:
        raise ValueError(f"Expected one OSPF-advertised non-link /32, found {len(loopbacks)}")
    router_ids = [line.split()[2] for line in ospf_lines if line.startswith("ospf router-id ")]
    if len(router_ids) != 1:
        raise ValueError("Expected exactly one OSPF router ID")
    return {
        "link_interface": checked_name(link_interface),
        "link_address": checked_ipv4(str(link_address.ip)),
        "link_prefix": checked_prefix(str(link_prefix)),
        "link_area": area,
        "ospf_mode": ospf_mode,
        "loopback_address": checked_ipv4(str(loopbacks[0].ip)),
        "loopback_prefix": f"{loopbacks[0].ip}/32",
        "router_id": checked_ipv4(router_ids[0]),
    }


def docker_prefix(layout: Layout, node: str) -> list[str]:
    return ["sudo", "-n", "docker", "exec", layout.container(node)]


def command_argv(layout: Layout, node: str, key: str, details: dict[str, dict[str, str]] | None) -> list[str]:
    """The sole read-only execution whitelist; key cannot supply free-form CLI."""
    if node not in layout.nodes:
        raise ValueError("Node is not in the topology")
    if key not in ("running_config", *COMMANDS):
        raise ValueError(f"Command is not whitelisted: {key}")
    if key == "running_config":
        inside = ["vtysh", "-c", "show running-config"]
    else:
        interface = checked_name(layout.interfaces[node])
        if key == "interfaces_ipv4":
            inside = ["ip", "-br", "-4", "addr"]
        elif key == "interconnect_link":
            inside = ["ip", "-s", "link", "show", "dev", interface]
        elif key == "frr_interface":
            inside = ["vtysh", "-c", f"show interface {interface}"]
        elif key == "ospf_interface":
            inside = ["vtysh", "-c", f"show ip ospf interface {interface}"]
        elif key == "ospf_neighbors":
            inside = ["vtysh", "-c", "show ip ospf neighbor"]
        elif key == "routes":
            inside = ["vtysh", "-c", "show ip route"]
        elif key == "ospf_routes":
            inside = ["vtysh", "-c", "show ip route ospf"]
        elif key == "ping_peer_link":
            if details is None:
                raise ValueError("Address discovery must finish before ping")
            remote = details[layout.peer(node)]
            inside = ["ping", "-c", "2", "-W", "1", checked_ipv4(remote["link_address"])]
        elif key == "ping_remote_loopback":
            if details is None:
                raise ValueError("Address discovery must finish before ping")
            remote = details[layout.peer(node)]
            inside = ["ping", "-c", "2", "-W", "1", checked_ipv4(remote["loopback_address"])]
        else:
            raise ValueError(f"Command is not whitelisted: {key}")
    return docker_prefix(layout, node) + inside


COMMANDS = (
    "interfaces_ipv4", "interconnect_link", "frr_interface", "ospf_interface",
    "ospf_neighbors", "routes", "ospf_routes", "ping_peer_link", "ping_remote_loopback",
)


OSPF_AREA = re.compile(r"\bArea\s+(\d{1,3}(?:\.\d{1,3}){3})\b")
OSPF_ADDRESS_LINE = re.compile(
    r"(?m)^\s*Internet Address\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}),\s*"
    r"Broadcast\s+\S+,\s*Area\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$")
PING_UNREACHABLE = re.compile(r"Network unreachable|[1-9]\d*% packet loss|0 packets received")


def ospf_address_area(output: str) -> tuple[ipaddress.IPv4Interface, str] | None:
    """Return one unambiguous OSPF address/Area line, never a loose Area token."""
    lines = OSPF_ADDRESS_LINE.findall(output)
    if len(lines) != 1 or len(OSPF_AREA.findall(output)) != 1:
        return None
    try:
        return ipaddress.IPv4Interface(lines[0][0]), checked_area(lines[0][1])
    except ValueError:
        return None


def ping_unreachable_observed(item: dict[str, Any]) -> bool:
    """Exit 1 is a measured network symptom only with a recognizable ping result."""
    if item.get("exit_code") != 1 or item.get("error"):
        return False
    output = str(item.get("stdout") or "") + "\n" + str(item.get("stderr") or "")
    return bool(PING_UNREACHABLE.search(output))


def observation_mismatch(layout: Layout, node: str, key: str, item: dict[str, Any],
                         details: dict[str, dict[str, str]] | None = None) -> str | None:
    """Return why a saved observation cannot represent this whitelisted command."""
    if item.get("node") != node or item.get("id") != key:
        return "observation node or command ID mismatch"
    try:
        expected = command_argv(layout, node, key, details)
    except (ValueError, KeyError, TypeError):
        return "expected command cannot be derived"
    if item.get("command") != expected:
        return "observation command does not match node, interface, or target address"
    if item.get("error"):
        return None  # Collector failure is handled separately; its output proves nothing.
    if key.startswith("ping_"):
        if item.get("exit_code") not in (0, 1):
            return None  # Execution failures are handled separately.
        output = str(item.get("stdout") or "")
        target = expected[-1]
        header = re.search(r"(?m)^PING\s+(\S+)", output)
        if header and not re.search(r"(?m)^PING\s+" + re.escape(target) + r"(?:\s|\()", output):
            return "ping output address does not match request"
        if item.get("exit_code") == 1 and not ping_unreachable_observed(item):
            return "ping exit 1 lacks a recognizable unreachable result"
        if item.get("exit_code") == 0 and not header:
            return "successful ping output lacks a target address"
        return None
    if item.get("exit_code") != 0:
        return None  # Other collector failures are handled separately.
    output = str(item.get("stdout") or "")
    interface = re.escape(layout.interfaces[node])
    if key == "running_config" and not re.search(r"(?m)^hostname\s+" + re.escape(node) + r"\s*$", output):
        return "running configuration hostname does not match node"
    if key == "ospf_interface":
        if not re.search(r"(?m)^" + interface + r"\s+is\s+", output):
            return "OSPF output interface does not match request"
        if OSPF_AREA.search(output):
            fact = ospf_address_area(output)
            if fact is None:
                return "OSPF output has no unique valid Internet Address and Area line"
            if details is not None:
                try:
                    configured = details[node]
                    prefixlen = ipaddress.IPv4Network(configured["link_prefix"]).prefixlen
                    address = ipaddress.IPv4Interface(f"{configured['link_address']}/{prefixlen}")
                except (KeyError, TypeError, ValueError):
                    return "configured interconnect address cannot be verified"
                if fact[0] != address:
                    return "OSPF Internet Address differs from this observation's running configuration"
    if key == "frr_interface" and not re.search(r"(?m)^Interface\s+" + interface + r"\s+is\s+", output):
        return "FRR output interface does not match request"
    if key == "interconnect_link" and not re.search(r"(?m)^\d+:\s+" + interface + r"(?:@|:)", output):
        return "link output interface does not match request"
    return None


def execute(layout: Layout, node: str, key: str, details: dict[str, dict[str, str]] | None,
            timeout_seconds: int = 12, *, deadline: float | None = None) -> dict[str, Any]:
    argv = command_argv(layout, node, key, details)
    started = utc_now()
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return {"node": node, "id": key, "command": argv, "started_at_utc": started,
                "ended_at_utc": utc_now(), "exit_code": None,
                "error": "total diagnosis deadline reached before command start", "stdout": "", "stderr": ""}
    effective_timeout = min(timeout_seconds, remaining) if remaining is not None else timeout_seconds
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=effective_timeout, check=False)
        stdout, stderr, exit_code, error = done.stdout, done.stderr, done.returncode, None
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        exit_code, error = None, f"command timed out after {effective_timeout} seconds"
    except OSError as exc:
        stdout, stderr, exit_code, error = "", "", None, f"{type(exc).__name__}: {exc}"
    return {
        "node": node, "id": key, "command": argv, "started_at_utc": started,
        "ended_at_utc": utc_now(), "exit_code": exit_code, "error": error,
        "stdout": stdout, "stderr": stderr,
    }


def record_command(result: dict[str, Any], output_dir: Path) -> None:
    stem = f"{result['node']}__{result['id']}"
    for stream in ("stdout", "stderr"):
        filename = f"{stem}.{stream}.txt"
        (output_dir / filename).write_text(result[stream], encoding="utf-8")
        result[f"{stream}_file"] = filename


def collect_evidence(lab_dir: Path, output_dir: Path) -> dict[str, Any]:
    layout = read_layout(lab_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {
        "schema_version": 1, "collected_at_utc": utc_now(),
        "lab": layout.lab_name, "nodes": {}, "discovery_error": None,
    }
    details: dict[str, dict[str, str]] = {}
    for node in layout.nodes:
        result = execute(layout, node, "running_config", None)
        record_command(result, output_dir)
        evidence["nodes"][node] = {"commands": [result]}
        if result["exit_code"] == 0 and not result["error"]:
            try:
                details[node] = parse_node_config(result["stdout"], layout.interfaces[node])
            except (ValueError, ipaddress.AddressValueError) as exc:
                evidence["discovery_error"] = f"{node}: {exc}"
        else:
            evidence["discovery_error"] = f"{node}: could not read running configuration"
    if len(details) == 2 and not evidence["discovery_error"]:
        evidence["discovered"] = details
        for node in layout.nodes:
            for key in COMMANDS:
                result = execute(layout, node, key, details)
                record_command(result, output_dir)
                evidence["nodes"][node]["commands"].append(result)
    (output_dir / "observations.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


def ensure_wsl2_and_sudo() -> None:
    if sys.platform != "linux" or "microsoft-standard-WSL2" not in os.uname().release:
        raise RuntimeError("Run this collector in the existing Ubuntu WSL2 distribution")
    subprocess.run(["sudo", "-v"], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    ensure_wsl2_and_sudo()
    evidence = collect_evidence(LAB_DIR, args.output.resolve())
    print(args.output.resolve() / "observations.json")
    if evidence["discovery_error"]:
        print(evidence["discovery_error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
