"""Offline tests for lab-only remediation simulation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from simulate_fix import (  # noqa: E402
    DockerExecutor, FakeExecutor, FixPlan, SimulationError, build_plan,
    require_lab_confirmation, simulate,
)


class PlanTest(unittest.TestCase):
    def test_area_plan_and_inverse(self):
        plan = build_plan("ospf_area_mismatch", "r2", "eth1",
                          current_area="0.0.0.1", peer_area="0.0.0.0")
        self.assertEqual(plan.commands, ("no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"))
        self.assertEqual(plan.inverse, ("no ip ospf area 0.0.0.0", "ip ospf area 0.0.0.1"))

    def test_admin_and_passive_plans(self):
        admin = build_plan("interface_admin_down", "r1", "eth1")
        self.assertEqual((admin.commands, admin.inverse), (("no shutdown",), ("shutdown",)))
        passive = build_plan("ospf_passive", "r2", "eth1")
        self.assertEqual((passive.commands, passive.inverse),
                         (("no ip ospf passive",), ("ip ospf passive",)))

    def test_unsafe_plans_are_refused(self):
        for status in ("normal", "insufficient_evidence", "collection_failure", "incomplete"):
            with self.assertRaises(SimulationError):
                build_plan(status, "r1", "eth1")
        with self.assertRaises(SimulationError):
            build_plan("ospf_area_mismatch", "r1", "eth1", current_area="0.0.0.0")  # missing peer
        with self.assertRaises(SimulationError):
            build_plan("ospf_area_mismatch", "r1", "eth1", current_area="not-an-area", peer_area="0.0.0.0")
        with self.assertRaises(SimulationError):
            build_plan("ospf_passive", "r3", "eth1")

    def test_allowlist_rejects_free_form_commands(self):
        from simulate_fix import _check
        with self.assertRaises(SimulationError):
            _check(["no ip ospf passive", "write memory"])
        with self.assertRaises(SimulationError):
            _check(["interface eth1"])


class SimulationTest(unittest.TestCase):
    def _run(self, plan, executor, statuses):
        queue = list(statuses)
        documents = []

        def collect():
            documents.append({})
            return {}

        def diagnose(_):
            return {"status": queue.pop(0)}

        return simulate(plan, executor, collect, diagnose), documents

    def test_success_applies_then_rolls_back(self):
        plan = build_plan("ospf_passive", "r2", "eth1")
        executor = FakeExecutor()
        report, documents = self._run(plan, executor, ["ospf_passive", "normal", "ospf_passive"])
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["restored"])
        self.assertEqual([call["commands"] for call in executor.calls],
                         [["no ip ospf passive"], ["ip ospf passive"]])
        self.assertEqual(len(documents), 3)

    def test_failed_verification_still_rolls_back_and_fails(self):
        plan = build_plan("interface_admin_down", "r1", "eth1")
        executor = FakeExecutor()
        report, _ = self._run(plan, executor, ["interface_admin_down", "interface_admin_down",
                                               "interface_admin_down"])
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["restored"])
        self.assertEqual(len(executor.calls), 2)

    def test_rollback_failure_is_reported(self):
        plan = build_plan("interface_admin_down", "r1", "eth1")
        executor = FakeExecutor(fail_on="shutdown")
        report, _ = self._run(plan, executor, ["interface_admin_down", "normal", "interface_admin_down"])
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["restored"])
        self.assertIn("rollback", report["rollback_error"].lower())

    def test_refuses_when_precheck_does_not_match_plan(self):
        plan = build_plan("ospf_passive", "r2", "eth1")
        executor = FakeExecutor()
        with self.assertRaises(SimulationError):
            self._run(plan, executor, ["normal"])
        self.assertEqual(executor.calls, [])

    def test_report_is_json_serialisable(self):
        plan = build_plan("ospf_passive", "r2", "eth1")
        report, _ = self._run(plan, FakeExecutor(), ["ospf_passive", "normal", "ospf_passive"])
        json.dumps(report)


class SafetyTest(unittest.TestCase):
    def test_requires_explicit_env_flag(self):
        with self.assertRaises(SimulationError):
            require_lab_confirmation({})
        require_lab_confirmation({"NETSECOPS_LAB_SIMULATION": "1"})

    def test_disabled_docker_executor_refuses(self):
        class Layout:
            nodes = ("r1", "r2")
            interfaces = {"r1": "eth1", "r2": "eth1"}
            lab_name = "netsecops-ospf"
            def container(self, node):
                return f"clab-{self.lab_name}-{node}"
        with self.assertRaises(SimulationError):
            DockerExecutor(Layout(), enabled=False)

    def test_docker_executor_checks_node_and_interface(self):
        class Layout:
            nodes = ("r1", "r2")
            interfaces = {"r1": "eth1", "r2": "eth1"}
            lab_name = "netsecops-ospf"
            def container(self, node):
                return f"clab-{self.lab_name}-{node}"
        executor = DockerExecutor(Layout(), enabled=True)
        with self.assertRaises(SimulationError):
            executor.run("r3", "eth1", ("no shutdown",))
        with self.assertRaises(SimulationError):
            executor.run("r1", "eth2", ("no shutdown",))
        with self.assertRaises(SimulationError):
            executor.run("r1", "eth1", ("write memory",))


if __name__ == "__main__":
    unittest.main()
