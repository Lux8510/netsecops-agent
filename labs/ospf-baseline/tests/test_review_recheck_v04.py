"""P2 recheck regression: command success is not the same as Area evidence."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


LAB = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "observations"
sys.path.insert(0, str(LAB))

from agent_loop import validate_final  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402
from evidence_checks import critical_history  # noqa: E402


def scenario():
    # r2 remains administratively down throughout; only the first execution fails.
    backend = ReplayBackend(FIXTURES / "stage5-case-02.json")
    tools = AgentTools(backend)
    topology = tools.call("query_topology", {})
    failed_document = json.loads((FIXTURES / "failed-command.json").read_text(encoding="utf-8"))
    failed_observation = deepcopy(next(item for item in failed_document["nodes"]["r2"]["commands"]
                                       if item["id"] == "ospf_interface"))
    original_get = backend.get

    def transient_failure(node, key, *, deadline=None):
        if node == "r2" and key == "ospf_interface":
            return deepcopy(failed_observation)
        return original_get(node, key, deadline=deadline)

    with patch.object(backend, "get", side_effect=transient_failure):
        failed = tools.call("query_ospf_interface", {"node": "r2", "interface": "eth1"})
    succeeded = tools.call("query_ospf_interface", {"node": "r2", "interface": "eth1"})
    admin = tools.call("query_interface", {"node": "r2", "interface": "eth1"})
    raw = {"status": "interface_admin_down", "conclusion": "r2 eth1 管理关闭。",
           "evidence_ids": [topology["evidence_id"], succeeded["evidence_id"], admin["evidence_id"]],
           "missing_information": [], "repair_suggestion": "人工核对管理状态。"}
    return tools, failed, succeeded, validate_final(raw, tools)


class RecheckRegression(unittest.TestCase):
    def test_valid_down_output_clears_earlier_command_failure(self):
        tools, failed, succeeded, _ = scenario()
        self.assertEqual(failed["status"], "collection_failed")
        self.assertEqual(succeeded["status"], "ok")
        self.assertEqual(succeeded["observations"][0]["exit_code"], 0)
        self.assertIn("eth1 is down", succeeded["observations"][0]["stdout"])
        active, superseded = critical_history(tools.layout, tools.evidence)
        self.assertEqual(active, [], "A successful read of a down interface is not an ongoing command failure")
        self.assertTrue(any(item["failed_evidence_id"] == failed["evidence_id"] and
                            item["successful_evidence_id"] == succeeded["evidence_id"]
                            for item in superseded))

    def test_direct_admin_cause_is_not_overridden_by_resolved_failure(self):
        _, _, _, final = scenario()
        self.assertEqual(final["status"], "interface_admin_down",
                         "Successful OSPF output without Area must not keep an earlier execution failure active")
        self.assertEqual((final["root_cause"]["node"], final["root_cause"]["interface"]), ("r2", "eth1"))

    def test_down_output_without_area_proves_neither_normal_nor_mismatch(self):
        tools, _, succeeded, _ = scenario()
        r1 = tools.call("query_ospf_interface", {"node": "r1", "interface": "eth1"})
        for status in ("normal", "ospf_area_mismatch"):
            raw = {"status": status, "evidence_ids": [r1["evidence_id"], succeeded["evidence_id"]],
                   "missing_information": [], "conclusion": status, "repair_suggestion": "read only"}
            with self.subTest(status=status):
                self.assertEqual(validate_final(raw, tools)["status"], "insufficient_evidence")

    def test_truncated_down_output_does_not_clear_execution_failure(self):
        tools, failed, succeeded, _ = scenario()
        succeeded["observations"][0]["stdout"] = "eth1 is down\n"
        active, superseded = critical_history(tools.layout, tools.evidence)
        self.assertEqual([item["evidence_id"] for item in active], [failed["evidence_id"]])
        self.assertEqual(superseded, [])

    def test_wrong_interface_output_does_not_clear_execution_failure(self):
        tools, failed, succeeded, _ = scenario()
        succeeded["observations"][0]["stdout"] = succeeded["observations"][0]["stdout"].replace(
            "eth1 is down", "eth9 is down", 1)
        active, _ = critical_history(tools.layout, tools.evidence)
        self.assertEqual([item["evidence_id"] for item in active], [failed["evidence_id"]])

    def test_wrong_area_address_does_not_clear_execution_failure(self):
        tools, failed, succeeded, _ = scenario()
        normal = json.loads((FIXTURES / "normal.json").read_text(encoding="utf-8"))
        output = next(item["stdout"] for item in normal["nodes"]["r2"]["commands"]
                      if item["id"] == "ospf_interface")
        succeeded["observations"][0]["stdout"] = output.replace("10.0.12.2/30", "10.0.12.9/30")
        active, _ = critical_history(tools.layout, tools.evidence)
        self.assertEqual([item["evidence_id"] for item in active], [failed["evidence_id"]])


if __name__ == "__main__":
    unittest.main()
