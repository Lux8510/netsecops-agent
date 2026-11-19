"""Semantic evidence checks: citations must actually support the claim."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from evidence_semantics import collect_facts, semantic_support  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / "traces" / name).read_text(encoding="utf-8"))


class SemanticTest(unittest.TestCase):
    def test_area_mismatch_is_supported(self):
        result = semantic_support(load("stage5-case-01.json"), "ospf_area_mismatch")
        self.assertTrue(result["supported"])
        self.assertEqual(result["facts"]["areas"], ["0.0.0.0", "0.0.0.1"])
        self.assertFalse(result["ambiguous_faults"])

    def test_normal_is_supported(self):
        result = semantic_support(load("normal.json"), "normal")
        self.assertTrue(result["supported"])
        self.assertEqual(result["contradictions"], [])

    def test_admin_down_is_supported(self):
        result = semantic_support(load("stage5-case-02.json"), "interface_admin_down")
        self.assertTrue(result["supported"])
        self.assertTrue(result["facts"]["admin_down"])

    def test_passive_is_supported(self):
        result = semantic_support(load("stage5-case-03.json"), "ospf_passive")
        self.assertTrue(result["supported"])
        self.assertIn("r2", result["facts"]["passive"])

    def test_wrong_claim_is_contradicted(self):
        result = semantic_support(load("normal.json"), "ospf_area_mismatch")
        self.assertFalse(result["supported"])
        self.assertIn("areas are not different", result["contradictions"])

    def test_area_claim_rejects_coexisting_admin_fault(self):
        result = semantic_support(load("stage5-case-02.json"), "ospf_area_mismatch")
        self.assertFalse(result["supported"])
        self.assertTrue(any("direct interface fault" in item for item in result["contradictions"]))

    def test_facts_ignore_missing_observations(self):
        facts = collect_facts({"evidence": {}})
        self.assertEqual(facts, {})

    def test_uncited_or_failed_commands_do_not_support_normal(self):
        trace = {
            "initial_topology": {"link": [{"node": "r1", "interface": "eth1"},
                                          {"node": "r2", "interface": "eth1"}]},
            "final": {"status": "normal", "evidence_ids": []},
            "evidence": {
                "E1": {"tool": "query_ospf_interface", "status": "ok",
                       "arguments": {"node": "r1", "interface": "eth1"},
                       "observations": [{"exit_code": 1, "stdout": "Area 0.0.0.0"}]},
                "E2": {"tool": "query_ospf_interface", "status": "ok",
                       "arguments": {"node": "r2", "interface": "eth1"},
                       "observations": [{"exit_code": 1, "stdout": "Area 0.0.0.0"}]},
            },
        }
        result = semantic_support(trace, "normal")
        self.assertFalse(result["supported"])
        self.assertIn("area evidence missing or incomplete", result["contradictions"])

    def test_citations_without_neighbor_or_ping_are_not_enough(self):
        trace = {
            "initial_topology": {"link": [{"node": "r1", "interface": "eth1"},
                                          {"node": "r2", "interface": "eth1"}]},
            "final": {"status": "normal", "evidence_ids": ["E1", "E2"]},
            "evidence": {
                "E1": {"tool": "query_ospf_interface", "status": "ok",
                       "arguments": {"node": "r1", "interface": "eth1"},
                       "observations": [{"exit_code": 0, "stdout": "Area 0.0.0.0"}]},
                "E2": {"tool": "query_ospf_interface", "status": "ok",
                       "arguments": {"node": "r2", "interface": "eth1"},
                       "observations": [{"exit_code": 0, "stdout": "Area 0.0.0.0"}]},
            },
        }
        result = semantic_support(trace, "normal")
        self.assertFalse(result["supported"])
        self.assertTrue(any("neighbor" in item for item in result["contradictions"]))

    def test_ping_loss_is_parsed_as_a_number(self):
        def ping_trace(text: str) -> dict:
            return {
                "initial_topology": {"link": [{"node": "r1", "interface": "eth1"},
                                              {"node": "r2", "interface": "eth1"}]},
                "final": {"status": "normal", "evidence_ids": ["P1", "P2"]},
                "evidence": {
                    "P1": {"tool": "ping_address", "status": "ok",
                           "arguments": {"node": "r1", "target": "10.255.0.2"},
                           "observations": [{"exit_code": 0, "stdout": text}]},
                    "P2": {"tool": "ping_address", "status": "ok",
                           "arguments": {"node": "r2", "target": "10.255.0.1"},
                           "observations": [{"exit_code": 0, "stdout": text}]},
                },
            }
        self.assertTrue(collect_facts(ping_trace("0% packet loss"))["r1"]["ping_results"] == [True])
        self.assertTrue(collect_facts(ping_trace("10% packet loss"))["r1"]["ping_results"] == [False])
        self.assertTrue(collect_facts(ping_trace("100% packet loss"))["r1"]["ping_results"] == [False])


if __name__ == "__main__":
    unittest.main()
