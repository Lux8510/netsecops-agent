"""Static consistency checks; not a substitute for clab deploy or FRR runtime checks."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
import json
from pathlib import Path
import re
import unittest

from v1.topology import Topology


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "v1/lab"
FIXTURES = ROOT / "v1/tests/fixtures"


def interface_blocks(config: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(
        r"(?ms)^interface ([A-Za-z][A-Za-z0-9_.-]*)\n(.*?)(?=^!\n)", config
    )}


class LabAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.topology = Topology.load(LAB / "topology.json")
        cls.clab = (LAB / "topology.clab.yml").read_text(encoding="utf-8")

    def test_frr_config_matches_every_declared_router_interface(self) -> None:
        for name, node in self.topology.nodes.items():
            if node.role != "router":
                continue
            with self.subTest(node=name):
                config = (LAB / "configs" / name / "frr.conf").read_text(encoding="utf-8")
                blocks = interface_blocks(config)
                self.assertEqual(set(blocks), set(node.interfaces))
                self.assertIn(f"hostname {name}\n", config)
                self.assertIn(f"ospf router-id {node.router_id}\n", config)
                self.assertNotIn("ip ospf area ", config)  # avoid mixing area and network activation
                for iface, ip in node.interfaces.items():
                    self.assertIn(f"ip address {ip}\n", blocks[iface])
                    self.assertIn(f"network {ip.network} area 0.0.0.0\n", config)
                self.assertIn("passive-interface lo\n", config)

    def test_ospf_intent_costs_and_access_passive_are_in_configs(self) -> None:
        for link in self.topology.links:
            if link.ospf_cost is None:
                router_end = next(e for e in link.ends if self.topology.nodes[e.node].role == "router")
                config = (LAB / "configs" / router_end.node / "frr.conf").read_text(encoding="utf-8")
                self.assertIn(f"passive-interface {router_end.interface}\n", config)
                continue
            for end in link.ends:
                config = (LAB / "configs" / end.node / "frr.conf").read_text(encoding="utf-8")
                block = interface_blocks(config)[end.interface]
                self.assertIn(f"ip ospf cost {link.ospf_cost}\n", block)
                self.assertIn("ip ospf network point-to-point\n", block)

    def test_containerlab_links_match_validated_manifest(self) -> None:
        listed = [tuple(x) for x in re.findall(
            r'^    - endpoints: \["([^\"]+)", "([^\"]+)"\]$', self.clab, re.M
        )]
        expected = [(f"{link.ends[0].node}:{link.ends[0].interface}",
                     f"{link.ends[1].node}:{link.ends[1].interface}") for link in self.topology.links]
        self.assertEqual(listed, expected)
        for node in self.topology.nodes:
            self.assertRegex(self.clab, rf"(?m)^    {re.escape(node)}:$")

    def test_management_plan_and_image_tags_are_explicit(self) -> None:
        self.assertIn("name: netsecops-v1-http\n", self.clab)
        self.assertIn("network: netsecops-v1-mgmt\n", self.clab)
        subnet = IPv4Network("172.31.87.0/24")
        addresses = re.findall(r"mgmt-ipv4: ([0-9.]+)", self.clab)
        self.assertEqual(len(addresses), 6)
        self.assertEqual(len(set(addresses)), 6)
        self.assertTrue(all(IPv4Address(address) in subnet for address in addresses))
        self.assertIn("quay.io/frrouting/frr:containerlab-10.7.1", self.clab)
        self.assertIn("python:3.12.15-alpine3.24", self.clab)
        self.assertNotIn(":latest", self.clab)

    def test_host_return_routes_and_http_service_are_declared(self) -> None:
        self.assertIn("ip route replace 10.104.0.0/30 via 10.101.0.1 dev eth1", self.clab)
        self.assertIn("ip route replace 10.101.0.0/30 via 10.104.0.1 dev eth1", self.clab)
        self.assertIn("services:/srv/http:ro", self.clab)
        self.assertIn("python3 -m http.server 8080", self.clab)
        self.assertEqual((LAB / "services/health").read_text(encoding="utf-8").strip(), "ok")

    def test_samples_are_explicitly_mock_and_labels_are_separate(self) -> None:
        observations = sorted((FIXTURES / "observations").glob("*.json"))
        self.assertEqual(len(observations), 4)
        labels = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(set(labels["labels_not_for_agent_input"]), {x.name for x in observations})
        for file in observations:
            with self.subTest(sample=file.name):
                sample = json.loads(file.read_text(encoding="utf-8"))
                self.assertEqual(sample["schema_version"], 1)
                self.assertEqual(sample["provenance"], "synthetic/mock")
                self.assertEqual(sample["topology_ref"], "v1/lab/topology.json")
                self.assertNotIn("scenario", sample)
                self.assertTrue(sample["measurements"])

    def test_mock_samples_cover_planned_contrasts(self) -> None:
        def values(name: str) -> dict[tuple[str, str], str]:
            sample = json.loads((FIXTURES / "observations" / name).read_text(encoding="utf-8"))
            return {(item["kind"], item.get("link", item.get("target", item.get("prefix")))):
                    item["mock_value"] for item in sample["measurements"]}

        normal = values("sample-01.json")
        backup = values("sample-02.json")
        missing = values("sample-03.json")
        stopped = values("sample-04.json")
        self.assertIn("HTTP 200", normal[("http", "http-health")])
        self.assertEqual(backup[("link_state", "r1-r2")], "down")
        self.assertIn("10.13.0.2", backup[("route", "10.104.0.0/30")])
        self.assertIn("absent", missing[("route", "10.104.0.0/30")])
        self.assertIn("connection refused", stopped[("http", "http-health")])


if __name__ == "__main__":
    unittest.main()
