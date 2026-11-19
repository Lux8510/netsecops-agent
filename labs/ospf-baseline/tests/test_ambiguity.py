"""Two independent faults must produce an abstention, not a single guess."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from diagnose_offline import diagnose  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / "observations" / name).read_text(encoding="utf-8"))


def break_peer_area(document: dict, node: str) -> dict:
    document = deepcopy(document)
    for command in document["nodes"][node]["commands"]:
        if command["id"] == "running_config":
            command["stdout"] = command["stdout"].replace(
                "interface eth1\n description r2-to-r1\n ip address 10.0.12.2/30\n ip ospf area 0.0.0.0",
                "interface eth1\n description r2-to-r1\n ip address 10.0.12.2/30\n ip ospf area 0.0.0.1")
        if command["id"] == "ospf_interface":
            command["stdout"] = command["stdout"].replace("Area 0.0.0.0", "Area 0.0.0.1", 1)
    document["discovered"][node]["link_area"] = "0.0.0.1"
    return document


class AmbiguityTest(unittest.TestCase):
    def test_admin_down_plus_area_mismatch_abstains(self):
        document = break_peer_area(load("stage5-case-02.json"), "r2")
        result = diagnose(document)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("区域不一致", result["conclusion"])

    def test_passive_plus_area_mismatch_abstains(self):
        document = break_peer_area(load("stage5-case-03.json"), "r2")
        result = diagnose(document)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("区域不一致", result["conclusion"])

    def test_single_fault_is_still_reported(self):
        self.assertEqual(diagnose(load("stage5-case-02.json"))["status"], "interface_admin_down")
        self.assertEqual(diagnose(load("stage5-case-03.json"))["status"], "ospf_passive")


if __name__ == "__main__":
    unittest.main()
