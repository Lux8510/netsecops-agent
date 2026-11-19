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
        self.assertIn("expected two different areas", result["contradictions"])

    def test_area_claim_rejects_coexisting_admin_fault(self):
        result = semantic_support(load("stage5-case-02.json"), "ospf_area_mismatch")
        self.assertFalse(result["supported"])
        self.assertTrue(any("direct interface fault" in item for item in result["contradictions"]))

    def test_facts_ignore_missing_observations(self):
        facts = collect_facts({"evidence": {}})
        self.assertEqual(facts, {})


if __name__ == "__main__":
    unittest.main()
