"""One v0.4 retry-state defect, covered by two offline regression assertions.

Usage: python3 test_review_recheck_v04.py [path/to/netsecops-agent]
No model API, shell command, router operation, or signal is used.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent / "source"
LAB = ROOT / "labs/ospf-baseline"
sys.path.insert(0, str(LAB))

from agent_loop import validate_final
from agent_tools import AgentTools, ReplayBackend
from evidence_checks import critical_history


def scenario():
    fixtures = LAB / "tests/fixtures/observations"
    # The interface is administratively down for the whole task. Only the first
    # OSPF command attempt has a transient execution error; no topology changes.
    backend = ReplayBackend(fixtures / "stage5-case-02.json")
    tools = AgentTools(backend)
    topology = tools.call("query_topology", {})
    failed_document = json.loads((fixtures / "failed-command.json").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
