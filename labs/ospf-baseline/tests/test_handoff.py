"""Handoff bundle: stable hash, config snapshot, allowlisted commands."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from handoff import HandoffError, build_bundle, derive_commands, verify_bundle  # noqa: E402


def load(name: str) -> dict:
    document = json.loads((FIXTURES / "traces" / name).read_text(encoding="utf-8"))
    return document.get("trace") if isinstance(document.get("trace"), dict) else document


class HandoffTest(unittest.TestCase):
    def test_area_bundle_verifies_and_has_snapshot(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r1", target="r2")
        verify_bundle(bundle)
        self.assertEqual(bundle["diagnosis"]["status"], "ospf_area_mismatch")
        self.assertIn("Current configuration", bundle["config_snapshot"]["text"])
        self.assertEqual(len(bundle["bundle_sha256"]), 64)
        self.assertEqual(bundle["proposed_commands"],
                         ["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"])

    def test_tampering_is_rejected(self):
        bundle = build_bundle(load("stage5-case-02.json"), "r1")
        bundle["diagnosis"]["status"] = "normal"
        with self.assertRaises(HandoffError):
            verify_bundle(bundle)

    def test_config_tampering_is_rejected(self):
        bundle = build_bundle(load("stage5-case-02.json"), "r1")
        bundle["config_snapshot"]["text"] += "\n!"
        with self.assertRaises(HandoffError):
            verify_bundle(bundle)

    def test_arbitrary_commands_are_rejected(self):
        with self.assertRaises(HandoffError):
            build_bundle(load("stage5-case-02.json"), "r1", commands=["write memory"])

    def test_only_plannable_statuses_get_commands(self):
        self.assertEqual(derive_commands(load("normal.json"), "normal", "r1"), [])
        self.assertEqual(derive_commands(load("normal.json"), "insufficient_evidence", "r1"), [])

    def test_admin_and_passive_plans(self):
        admin = build_bundle(load("stage5-case-02.json"), "r1", target="r1")
        self.assertEqual(admin["proposed_commands"], ["no shutdown"])
        passive = build_bundle(load("stage5-case-03.json"), "r2", target="r2")
        self.assertEqual(passive["proposed_commands"], ["no ip ospf passive"])

    def test_bundle_carries_citations_not_paths(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r1", target="r2")
        self.assertTrue(bundle["citations"])
        serialized = json.dumps(bundle)
        self.assertNotIn(str(LAB_DIR), serialized)


if __name__ == "__main__":
    unittest.main()
