"""Offline topology and authorization tests; no containers or network access."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from v1.topology import Topology, TopologyError


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "v1/lab/topology.json"


def source() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = source()

    def invalid(self, edit) -> None:
        data = deepcopy(self.data)
        edit(data)
        with self.assertRaises(TopologyError):
            Topology.from_dict(data)

    def test_manifest_loads_and_roles_are_distinct(self) -> None:
        topology = Topology.load(MANIFEST)
        self.assertEqual((len(topology.nodes), len(topology.links)), (6, 6))
        self.assertEqual({name for name, n in topology.nodes.items() if n.role == "router"},
                         {"r1", "r2", "r3", "r4"})
        self.assertEqual(topology.nodes["client"].router_id, None)
        self.assertEqual(topology.nodes["server"].role, "server")

    def test_unknown_schema_is_rejected(self) -> None:
        self.invalid(lambda d: d.update(schema_version=2))

    def test_invalid_node_name_is_rejected(self) -> None:
        self.invalid(lambda d: d["nodes"].update({"r1; sh": d["nodes"].pop("r1")}))

    def test_invalid_role_is_rejected(self) -> None:
        self.invalid(lambda d: d["nodes"]["r1"].update(role="server; router"))

    def test_router_id_is_required_and_unique(self) -> None:
        self.invalid(lambda d: d["nodes"]["r2"].update(router_id=d["nodes"]["r1"]["router_id"]))
        self.invalid(lambda d: d["nodes"]["client"].update(router_id="10.0.0.9"))
        self.invalid(lambda d: d["nodes"]["r2"].update(router_id=1234))

    def test_invalid_interface_name_and_address_are_rejected(self) -> None:
        self.invalid(lambda d: d["nodes"]["r1"]["interfaces"].update({"eth1;ls": "10.10.1.1/30"}))
        self.invalid(lambda d: d["nodes"]["r1"]["interfaces"].update(eth1="999.1.1.1/30"))
        self.invalid(lambda d: d["nodes"]["r1"]["interfaces"].update(eth1="10.101.0.1"))

    def test_duplicate_address_is_rejected(self) -> None:
        self.invalid(lambda d: d["nodes"]["r2"]["interfaces"].update(eth1="10.12.0.1/30"))

    def test_wrong_interface_or_owner_is_rejected(self) -> None:
        self.invalid(lambda d: d["links"][1]["endpoints"][1].update(interface="eth9"))
        self.invalid(lambda d: d["links"][1]["endpoints"][1].update(node="client"))

    def test_reused_endpoint_or_wrong_subnet_is_rejected(self) -> None:
        self.invalid(lambda d: d["links"][2]["endpoints"][0].update(interface="eth1"))
        self.invalid(lambda d: d["nodes"]["r2"]["interfaces"].update(eth2="10.99.0.1/30"))

    def test_self_link_and_overlapping_link_are_rejected(self) -> None:
        self.invalid(lambda d: d["links"][2]["endpoints"][1].update(node="r2", interface="eth2"))
        def overlap(d):
            d["nodes"]["r1"]["interfaces"]["eth3"] = "10.12.0.5/29"
            d["nodes"]["r3"]["interfaces"]["eth1"] = "10.12.0.6/29"
        self.invalid(overlap)

    def test_router_link_requires_valid_cost_and_access_link_cannot_claim_ospf_cost(self) -> None:
        self.invalid(lambda d: d["links"][1].update(ospf_cost=0))
        self.invalid(lambda d: d["links"][1].pop("ospf_cost"))
        self.invalid(lambda d: d["links"][0].update(ospf_cost=10))

    def test_business_target_requires_declared_server_address_and_client(self) -> None:
        self.invalid(lambda d: d["business_targets"][0].update(address="10.104.0.99"))
        self.invalid(lambda d: d["business_targets"][0].update(address=1234))
        self.invalid(lambda d: d["business_targets"][0].update(node="r4"))
        self.invalid(lambda d: d["business_targets"][0].update(source_nodes=["r1"]))

    def test_authorization_is_derived_from_validated_manifest(self) -> None:
        scope = Topology.load(MANIFEST).tool_scope()
        scope.require_node("client")
        scope.require_interface("r4", "eth3")
        scope.require_router("r2")
        scope.require_ospf_interface("r2", "eth1")
        scope.require_route_query("r4")
        scope.require_ping("client", "10.104.0.2")
        scope.require_http("client", "10.104.0.2", 8080, "/health")
        for operation in (
            lambda: scope.require_node("evil"),
            lambda: scope.require_interface("r1", "eth9"),
            lambda: scope.require_router("server"),
            lambda: scope.require_ospf_interface("client", "eth1"),
            lambda: scope.require_ospf_interface("r2", "eth9"),
            lambda: scope.require_ping("client", "8.8.8.8"),
            lambda: scope.require_ping("client", 1234),
            lambda: scope.require_http("r1", "10.104.0.2", 8080, "/health"),
            lambda: scope.require_http("client", "10.104.0.2", 9090, "/health"),
        ):
            with self.assertRaises(TopologyError):
                operation()

    def test_candidate_paths_are_structural_and_sorted_by_configured_cost(self) -> None:
        lab = Topology.load(MANIFEST)
        paths = lab.candidate_paths("r1", "r4")
        self.assertEqual([(p.nodes, p.configured_cost) for p in paths],
                         [(('r1', 'r2', 'r4'), 20), (('r1', 'r3', 'r4'), 100)])
        self.assertEqual(lab.candidate_paths("r4", "r1")[0].configured_cost, 20)
        with self.assertRaises(TopologyError):
            lab.candidate_paths("client", "r4")

    def test_candidate_path_remains_when_one_primary_link_is_absent(self) -> None:
        data = deepcopy(self.data)
        data["links"] = [x for x in data["links"] if x["name"] != "r1-r2"]
        lab = Topology.from_dict(data)
        self.assertEqual([p.nodes for p in lab.candidate_paths("r1", "r4")],
                         [('r1', 'r3', 'r4')])

    def test_dynamic_multinode_topology_does_not_assume_one_peer(self) -> None:
        lab = Topology.load(MANIFEST)
        self.assertEqual(sum("r1" in (x.ends[0].node, x.ends[1].node) for x in lab.links), 3)
        self.assertGreater(len(lab.nodes["r1"].interfaces), 2)
        self.assertEqual({peer.node for peer in lab.neighbors("r1")}, {"client", "r2", "r3"})


if __name__ == "__main__":
    unittest.main()
