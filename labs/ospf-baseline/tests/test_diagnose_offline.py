"""Offline regression and conservative-failure tests for the rule baseline."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))
from diagnose_offline import diagnose  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures/observations"


def load(stage: str) -> dict:
    name = {"normal": "normal.json", "fault/snapshot": "area-fault.json",
            "recovered/snapshot": "recovered.json"}[stage]
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def command(document: dict, node: str, key: str) -> dict:
    return next(item for item in document["nodes"][node]["commands"] if item["id"] == key)


class OfflineDiagnosisTest(unittest.TestCase):
    def test_previous_normal(self) -> None:
        self.assertEqual(diagnose(load("normal"))["status"], "normal")

    def test_previous_fault_requires_both_area_citations(self) -> None:
        value = diagnose(load("fault/snapshot"))
        self.assertEqual(value["status"], "ospf_area_mismatch")
        area_items = [item for item in value["supporting_evidence"] if item["command_id"] == "ospf_interface"]
        self.assertEqual({item["node"] for item in area_items}, {"r1", "r2"})
        self.assertTrue(any("Area 0.0.0.1" in item["output_excerpt"] for item in area_items))
        self.assertTrue(any("Area 0.0.0.0" in item["output_excerpt"] for item in area_items))
        self.assertTrue(all(item["command"] for item in area_items))

    def test_previous_recovered(self) -> None:
        self.assertEqual(diagnose(load("recovered/snapshot"))["status"], "normal")

    def test_missing_one_area_is_insufficient(self) -> None:
        observation = load("fault/snapshot")
        item = command(observation, "r2", "ospf_interface")
        item["stdout"] = item["stdout"].replace(", Area 0.0.0.0", "")
        value = diagnose(observation)
        self.assertEqual(value["status"], "insufficient_evidence")
        self.assertNotEqual(value["status"], "ospf_area_mismatch")

    def test_failed_key_command_is_collection_failure(self) -> None:
        observation = load("fault/snapshot")
        item = command(observation, "r2", "ospf_interface")
        item["exit_code"] = 1
        item["stderr"] = "collector failed"
        value = diagnose(observation)
        self.assertEqual(value["status"], "collection_failure")
        self.assertIn("r2/ospf_interface", " ".join(value["missing_information"]))

    def test_symptoms_alone_never_prove_mismatch(self) -> None:
        observation = copy.deepcopy(load("normal"))
        for node in ("r1", "r2"):
            command(observation, node, "ospf_neighbors")["stdout"] = ""
            command(observation, node, "ospf_routes")["stdout"] = ""
            command(observation, node, "ping_remote_loopback")["exit_code"] = 1
            command(observation, node, "ping_remote_loopback")["stderr"] = "Network unreachable"
        self.assertEqual(diagnose(observation)["status"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
