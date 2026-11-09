"""Validated, observation-free topology and tool authorization for the v1 lab.

This module describes intended wiring and addresses. It never infers live
adjacencies, installed routes, service health, or the faulty end of a link.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import json
from pathlib import Path
import re
from typing import Any


NODE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
IFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")
ROLES = frozenset({"router", "client", "server"})


class TopologyError(ValueError):
    """The topology manifest is inconsistent or outside the supported schema."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TopologyError(f"{label} must be an object")
    return value


def _keys(obj: dict[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    missing = required - obj.keys()
    unknown = obj.keys() - allowed
    if missing or unknown:
        raise TopologyError(f"{label}: missing {sorted(missing)}, unknown {sorted(unknown)}")


def _name(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TopologyError(f"invalid {label}: {value!r}")
    return value


def _address(value: Any, label: str) -> IPv4Interface:
    if not isinstance(value, str) or "/" not in value:
        raise TopologyError(f"{label} must be an IPv4 interface address")
    try:
        parsed = IPv4Interface(value)
    except ValueError as exc:
        raise TopologyError(f"invalid {label}: {value!r}") from exc
    if parsed.ip.is_unspecified or parsed.ip.is_multicast or parsed.ip.is_loopback:
        raise TopologyError(f"{label} is not a usable lab address")
    if parsed.network.prefixlen <= 30 and parsed.ip in (parsed.network.network_address, parsed.network.broadcast_address):
        raise TopologyError(f"{label} uses a network or broadcast address")
    return parsed


@dataclass(frozen=True)
class Node:
    name: str
    role: str
    interfaces: dict[str, IPv4Interface]
    router_id: IPv4Address | None


@dataclass(frozen=True)
class Endpoint:
    node: str
    interface: str


@dataclass(frozen=True)
class Link:
    name: str
    ends: tuple[Endpoint, Endpoint]
    ospf_cost: int | None


@dataclass(frozen=True)
class BusinessTarget:
    name: str
    node: str
    interface: str
    address: IPv4Address
    port: int
    path: str
    source_nodes: tuple[str, ...]


@dataclass(frozen=True)
class CandidatePath:
    nodes: tuple[str, ...]
    links: tuple[str, ...]
    configured_cost: int


@dataclass(frozen=True)
class ToolScope:
    """Exact read-only query targets derived from an already validated topology."""

    nodes: frozenset[str]
    router_nodes: frozenset[str]
    interfaces: dict[str, frozenset[str]]
    data_addresses: frozenset[IPv4Address]
    http_targets: frozenset[tuple[str, IPv4Address, int, str]]

    def require_node(self, node: str) -> None:
        if not isinstance(node, str) or node not in self.nodes:
            raise TopologyError(f"node is outside the topology: {node!r}")

    def require_interface(self, node: str, interface: str) -> None:
        self.require_node(node)
        if not isinstance(interface, str) or interface not in self.interfaces[node]:
            raise TopologyError(f"interface is outside {node}: {interface!r}")

    def require_router(self, node: str) -> None:
        self.require_node(node)
        if node not in self.router_nodes:
            raise TopologyError(f"OSPF/route query requires a router: {node!r}")

    def require_ospf_interface(self, node: str, interface: str) -> None:
        self.require_router(node)
        self.require_interface(node, interface)

    def require_route_query(self, node: str) -> None:
        self.require_router(node)

    def require_ping(self, source: str, destination: str) -> None:
        self.require_node(source)
        if not isinstance(destination, str):
            raise TopologyError("ping target must be a declared IPv4 address string")
        try:
            address = IPv4Address(destination)
        except (ValueError, TypeError) as exc:
            raise TopologyError("ping target must be a declared IPv4 address") from exc
        if address not in self.data_addresses:
            raise TopologyError(f"ping target is outside declared data addresses: {destination!r}")

    def require_http(self, source: str, destination: str, port: int, path: str) -> None:
        self.require_node(source)
        if not isinstance(destination, str):
            raise TopologyError("HTTP target must be a declared IPv4 address string")
        try:
            address = IPv4Address(destination)
        except (ValueError, TypeError) as exc:
            raise TopologyError("HTTP target must be a declared IPv4 address") from exc
        if (source, address, port, path) not in self.http_targets:
            raise TopologyError("HTTP target/source is outside the business target allowlist")


@dataclass(frozen=True)
class Topology:
    name: str
    nodes: dict[str, Node]
    links: tuple[Link, ...]
    business_targets: tuple[BusinessTarget, ...]

    @classmethod
    def from_dict(cls, data: Any) -> Topology:
        top = _object(data, "topology")
        _keys(top, {"schema_version", "name", "nodes", "links", "business_targets"},
              {"schema_version", "name", "nodes", "links", "business_targets"}, "topology")
        if type(top["schema_version"]) is not int or top["schema_version"] != 1:
            raise TopologyError("unsupported topology schema_version")
        name = _name(top["name"], NODE_RE, "topology name")
        raw_nodes = _object(top["nodes"], "nodes")
        if not raw_nodes:
            raise TopologyError("topology needs at least one node")
        nodes: dict[str, Node] = {}
        used_ips: dict[IPv4Address, str] = {}
        router_ids: set[IPv4Address] = set()
        for raw_name, value in raw_nodes.items():
            node_name = _name(raw_name, NODE_RE, "node name")
            raw_node = _object(value, f"node {node_name}")
            _keys(raw_node, {"role", "interfaces"}, {"role", "interfaces", "router_id"}, f"node {node_name}")
            role = raw_node["role"]
            if not isinstance(role, str) or role not in ROLES:
                raise TopologyError(f"invalid role for {node_name}: {role!r}")
            raw_ifaces = _object(raw_node["interfaces"], f"interfaces of {node_name}")
            if not raw_ifaces:
                raise TopologyError(f"{node_name} needs at least one interface")
            ifaces: dict[str, IPv4Interface] = {}
            for raw_iface, raw_ip in raw_ifaces.items():
                iface = _name(raw_iface, IFACE_RE, "interface name")
                ip = _address(raw_ip, f"{node_name}:{iface}")
                if ip.ip in used_ips:
                    raise TopologyError(f"duplicate address {ip.ip}: {used_ips[ip.ip]} and {node_name}:{iface}")
                used_ips[ip.ip] = f"{node_name}:{iface}"
                ifaces[iface] = ip
            rid_raw = raw_node.get("router_id")
            if role == "router":
                if not isinstance(rid_raw, str):
                    raise TopologyError(f"router {node_name} needs an IPv4 router_id string")
                try:
                    rid = IPv4Address(rid_raw)
                except (ValueError, TypeError) as exc:
                    raise TopologyError(f"router {node_name} needs an IPv4 router_id") from exc
                if rid.is_unspecified or rid.is_multicast or rid in router_ids:
                    raise TopologyError(f"invalid or duplicate router_id: {rid}")
                router_ids.add(rid)
            else:
                if rid_raw is not None:
                    raise TopologyError(f"non-router {node_name} cannot have a router_id")
                rid = None
            nodes[node_name] = Node(node_name, role, ifaces, rid)

        raw_links = top["links"]
        if not isinstance(raw_links, list):
            raise TopologyError("links must be an array")
        links: list[Link] = []
        link_names: set[str] = set()
        linked_ifaces: set[tuple[str, str]] = set()
        link_networks: list[IPv4Network] = []
        for item in raw_links:
            raw_link = _object(item, "link")
            _keys(raw_link, {"name", "endpoints"}, {"name", "endpoints", "ospf_cost"}, "link")
            link_name = _name(raw_link["name"], NODE_RE, "link name")
            if link_name in link_names:
                raise TopologyError(f"duplicate link name: {link_name}")
            link_names.add(link_name)
            ends_raw = raw_link["endpoints"]
            if not isinstance(ends_raw, list) or len(ends_raw) != 2:
                raise TopologyError(f"link {link_name} requires exactly two endpoints")
            ends: list[Endpoint] = []
            for raw_end in ends_raw:
                end = _object(raw_end, f"endpoint of {link_name}")
                _keys(end, {"node", "interface"}, {"node", "interface"}, "endpoint")
                node, iface = end["node"], end["interface"]
                if not isinstance(node, str) or node not in nodes or not isinstance(iface, str) or iface not in nodes[node].interfaces:
                    raise TopologyError(f"invalid endpoint {node}:{iface} on {link_name}")
                if (node, iface) in linked_ifaces:
                    raise TopologyError(f"interface {node}:{iface} belongs to multiple links")
                linked_ifaces.add((node, iface))
                ends.append(Endpoint(node, iface))
            if ends[0].node == ends[1].node:
                raise TopologyError(f"link {link_name} connects a node to itself")
            networks = [nodes[e.node].interfaces[e.interface].network for e in ends]
            if networks[0] != networks[1]:
                raise TopologyError(f"link {link_name} endpoint subnets disagree")
            if any(networks[0].overlaps(existing) for existing in link_networks):
                raise TopologyError(f"link {link_name} subnet overlaps another link")
            link_networks.append(networks[0])
            cost = raw_link.get("ospf_cost")
            both_routers = all(nodes[e.node].role == "router" for e in ends)
            if cost is not None and (not both_routers or type(cost) is not int or not 1 <= cost <= 65535):
                raise TopologyError(f"link {link_name} has invalid OSPF cost")
            if both_routers and cost is None:
                raise TopologyError(f"router link {link_name} needs intended ospf_cost")
            links.append(Link(link_name, (ends[0], ends[1]), cost))

        raw_targets = top["business_targets"]
        if not isinstance(raw_targets, list):
            raise TopologyError("business_targets must be an array")
        targets: list[BusinessTarget] = []
        target_names: set[str] = set()
        for item in raw_targets:
            raw = _object(item, "business target")
            _keys(raw, {"name", "node", "interface", "address", "port", "path", "source_nodes"},
                  {"name", "node", "interface", "address", "port", "path", "source_nodes"}, "business target")
            target_name = _name(raw["name"], NODE_RE, "business target name")
            if target_name in target_names:
                raise TopologyError(f"duplicate business target: {target_name}")
            target_names.add(target_name)
            node, iface = raw["node"], raw["interface"]
            if not isinstance(node, str) or node not in nodes or nodes[node].role != "server" or not isinstance(iface, str) or iface not in nodes[node].interfaces:
                raise TopologyError(f"business target must use a declared server interface: {node}:{iface}")
            if not isinstance(raw["address"], str):
                raise TopologyError("business target address must be an IPv4 string")
            try:
                address = IPv4Address(raw["address"])
            except (ValueError, TypeError) as exc:
                raise TopologyError("business target has invalid address") from exc
            if address != nodes[node].interfaces[iface].ip:
                raise TopologyError(f"business target address does not match {node}:{iface}")
            port, path, sources = raw["port"], raw["path"], raw["source_nodes"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise TopologyError("business target port must be 1..65535")
            if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9/_-]{0,127}", path):
                raise TopologyError("business target path is invalid")
            if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources) or len(sources) != len(set(sources)):
                raise TopologyError("business target needs unique source nodes")
            if any(source not in nodes or nodes[source].role != "client" for source in sources):
                raise TopologyError("business target source must be a declared client")
            targets.append(BusinessTarget(target_name, node, iface, address, port, path, tuple(sources)))
        return cls(name, nodes, tuple(links), tuple(targets))

    @classmethod
    def load(cls, path: str | Path) -> Topology:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TopologyError(f"cannot read topology manifest: {exc}") from exc
        return cls.from_dict(data)

    def tool_scope(self) -> ToolScope:
        return ToolScope(
            nodes=frozenset(self.nodes),
            router_nodes=frozenset(name for name, n in self.nodes.items() if n.role == "router"),
            interfaces={name: frozenset(n.interfaces) for name, n in self.nodes.items()},
            data_addresses=frozenset(ip.ip for n in self.nodes.values() for ip in n.interfaces.values()),
            http_targets=frozenset((src, t.address, t.port, t.path)
                                   for t in self.business_targets for src in t.source_nodes),
        )

    def neighbors(self, node: str) -> tuple[Endpoint, ...]:
        """Return every declared physical peer; never collapse to one peer."""
        self.tool_scope().require_node(node)
        return tuple(link.ends[1] if link.ends[0].node == node else link.ends[0]
                     for link in self.links if node in (link.ends[0].node, link.ends[1].node))

    def candidate_paths(self, source: str, destination: str) -> tuple[CandidatePath, ...]:
        """List simple router-only structural paths, ordered by intended link costs.

        This is not a prediction of installed routes or live business health.
        """
        scope = self.tool_scope()
        scope.require_router(source)
        scope.require_router(destination)
        if source == destination:
            return (CandidatePath((source,), (), 0),)
        graph: dict[str, list[tuple[str, Link]]] = {name: [] for name in scope.router_nodes}
        for link in self.links:
            if link.ospf_cost is not None:
                left, right = link.ends[0].node, link.ends[1].node
                graph[left].append((right, link))
                graph[right].append((left, link))
        found: list[CandidatePath] = []

        def visit(current: str, nodes: tuple[str, ...], links: tuple[str, ...], cost: int) -> None:
            if current == destination:
                found.append(CandidatePath(nodes, links, cost))
                return
            for neighbor, link in graph[current]:
                if neighbor not in nodes:
                    visit(neighbor, nodes + (neighbor,), links + (link.name,), cost + link.ospf_cost)

        visit(source, (source,), (), 0)
        return tuple(sorted(found, key=lambda p: (p.configured_cost, p.nodes)))


if __name__ == "__main__":
    lab = Topology.load(Path(__file__).with_name("lab") / "topology.json")
    print(f"validated: {lab.name}; {len(lab.nodes)} nodes; {len(lab.links)} links")
    for path in lab.candidate_paths("r1", "r4"):
        print(f"structural candidate: {' -> '.join(path.nodes)}; intended OSPF cost {path.configured_cost}")
