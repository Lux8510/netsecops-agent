"""Stage 5 synthetic direct-evidence and injection safety checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

from agent_loop import parse_final, run_agent, validate_final  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402
from agent_config import load_settings  # noqa: E402
from diagnose_offline import diagnose  # noqa: E402
from run_fault_scenario import configure_setting, fault_stable  # noqa: E402
import run_fault_scenario  # noqa: E402
from collect_evidence import read_layout  # noqa: E402


BASE = Path(__file__).resolve().parent / "fixtures/observations/normal.json"


def command(document: dict, node: str, key: str) -> dict:
    return next(item for item in document["nodes"][node]["commands"] if item["id"] == key)


def fixture(fault: str) -> dict:
    doc = copy.deepcopy(json.loads(BASE.read_text(encoding="utf-8")))
    config = command(doc, "r1", "running_config")
    if fault == "admin_down":
        config["stdout"] = config["stdout"].replace("interface eth1\n", "interface eth1\n shutdown\n", 1)
        frr = command(doc, "r1", "frr_interface")
        frr["stdout"] = (frr["stdout"].replace("Interface eth1 is up, line protocol is up",
                                                 "Interface eth1 is down, line protocol is down", 1)
                         .replace("Shutdown config is Not specified by CLI", "Shutdown config is on", 1))
        command(doc, "r1", "ospf_interface")["stdout"] = "eth1 is down\n"
    elif fault == "ospf_passive":
        config["stdout"] = config["stdout"].replace("interface eth1\n", "interface eth1\n ip ospf passive\n", 1)
        command(doc, "r1", "ospf_interface")["stdout"] += "  Passive interface\n"
    elif fault != "symptoms_only":
        raise ValueError(fault)
    for node in ("r1", "r2"):
        command(doc, node, "ospf_neighbors")["stdout"] = "Neighbor ID     Pri State\n"
        command(doc, node, "ospf_routes")["stdout"] = ""
        ping = command(doc, node, "ping_remote_loopback")
        ping["exit_code"] = 1
        ping["stdout"] = "2 packets transmitted, 0 received, 100% packet loss\n"
    return doc


class Stage5Test(unittest.TestCase):
    def test_rule_admin_requires_effective_admin_state(self):
        doc = fixture("admin_down")
        result = diagnose(doc)
        self.assertEqual(result["status"], "interface_admin_down")
        self.assertEqual({item["command_id"] for item in result["supporting_evidence"]},
                         {"running_config", "frr_interface"})
        command(doc, "r1", "frr_interface")["stdout"] = "Interface eth1 is down\nShutdown config is Not specified by CLI\n"
        self.assertEqual(diagnose(doc)["status"], "insufficient_evidence")

    def test_rule_passive_requires_scoped_config(self):
        doc = fixture("ospf_passive")
        self.assertEqual(diagnose(doc)["status"], "ospf_passive")
        config = command(doc, "r1", "running_config")
        config["stdout"] = config["stdout"].replace(" ip ospf passive\n", "", 1)
        self.assertEqual(diagnose(doc)["status"], "insufficient_evidence")

    def test_symptoms_alone_do_not_prove_three_causes(self):
        self.assertEqual(diagnose(fixture("symptoms_only"))["status"], "insufficient_evidence")

    def test_agent_guard_requires_cited_direct_evidence(self):
        for fault, tool, expected in (("admin_down", "query_interface", "interface_admin_down"),
                                      ("ospf_passive", "query_interface_config", "ospf_passive")):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "observations.json"
                path.write_text(json.dumps(fixture(fault)), encoding="utf-8")
                tools = AgentTools(ReplayBackend(path))
                args = {"node": "r1", "interface": "eth1"}
                evidence = tools.call(tool, args)
                self.assertEqual(evidence["status"], "ok")
                raw = {"status": expected, "conclusion": expected, "evidence_ids": [evidence["evidence_id"]],
                       "missing_information": [], "repair_suggestion": "read only"}
                self.assertEqual(validate_final(raw, tools)["status"], expected)
                raw["evidence_ids"] = []
                self.assertEqual(validate_final(raw, tools)["status"], "insufficient_evidence")

    def test_injector_rejects_unlisted_commands_before_execution(self):
        layout = read_layout(LAB)
        with self.assertRaisesRegex(Exception, "fixed fault/restore allowlist"):
            configure_setting(layout, "r1", "eth1", "write memory")
        with self.assertRaisesRegex(Exception, "interconnect"):
            configure_setting(layout, "r1", "eth2", "shutdown")

    def test_fault_poll_does_not_require_admin_direct_link_ping_failure(self):
        value = {node: {"neighbor_full": False, "remote_loopback_route": False,
                        "peer_link_ping": True, "remote_loopback_ping": False} for node in ("r1", "r2")}
        self.assertTrue(fault_stable(value, "admin_down"))
        self.assertTrue(fault_stable(value, "ospf_passive"))

    def test_trailing_json_after_prose_is_parsed_without_guessing(self):
        value = parse_final("Explanation.\n{\"status\": \"interface_admin_down\", \"evidence_ids\": [\"E001\"]}")
        self.assertEqual(value["status"], "interface_admin_down")
        self.assertIsNone(parse_final("Explanation only, no JSON conclusion"))

    def test_budget_exhausted_asks_for_final_without_extra_tool(self):
        from dataclasses import replace

        class Model:
            calls = 0
            def complete(self, messages, tools, *, deadline):
                self.calls += 1
                if self.calls == 1:
                    self_test.assertEqual(len(tools) > 0, True)
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "c1", "type": "function", "function": {"name": "query_interface",
                        "arguments": '{"node":"r1","interface":"eth1"}'}}]}
                else:
                    self_test.assertEqual(tools, [])
                    message = {"role": "assistant", "content": '{"status":"interface_admin_down",'
                               '"conclusion":"admin down","evidence_ids":["E001"],'
                               '"missing_information":[],"repair_suggestion":"manual only"}'}
                return {"message": message, "finish_reason": "tool_calls" if self.calls == 1 else "stop",
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                        "model": "mock", "request_id": f"m{self.calls}"}

        self_test = self
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps(fixture("admin_down")), encoding="utf-8")
            tools = AgentTools(ReplayBackend(path))
            trace = run_agent("diagnose", Model(), tools, replace(load_settings(), max_tool_calls=1), mode="replay")
        self.assertEqual(trace["tool_calls_executed"], 1)
        self.assertEqual(trace["final"]["status"], "interface_admin_down")

    def test_partial_admin_injection_exception_still_restores(self):
        original = (Path(__file__).resolve().parent / "fixtures/configs/r1-startup-frr.conf").read_text(encoding="utf-8")
        layout = read_layout(LAB)
        base = json.loads(BASE.read_text(encoding="utf-8"))
        details = base["discovered"]
        good = {node: {"neighbor_full": True, "remote_loopback_route": True,
                       "peer_link_ping": True, "remote_loopback_ping": True,
                       "link_area": "0.0.0.0"} for node in layout.nodes}

        def fake_collect(_lab, path):
            path.mkdir(parents=True, exist_ok=False)
            return {"discovered": details}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.conf"
            deployed = root / "deployed.conf"
            source.write_text(original, encoding="utf-8")
            deployed.write_text(original, encoding="utf-8")
            with (mock.patch.object(run_fault_scenario, "ensure_wsl2_and_sudo"),
                  mock.patch.object(run_fault_scenario, "preflight", return_value={}),
                  mock.patch.object(run_fault_scenario.subprocess, "run",
                                    return_value=mock.Mock(returncode=0, stdout="FRRouting 10.7.1_git (r1)\n", stderr="")),
                  mock.patch.object(run_fault_scenario, "collect_evidence", side_effect=fake_collect),
                  mock.patch.object(run_fault_scenario, "metrics_from_evidence", return_value=good),
                  mock.patch.object(run_fault_scenario, "by_id", return_value={"stdout": original}),
                  mock.patch.object(run_fault_scenario, "startup_files",
                                    return_value={"source": source, "deployed": deployed}),
                  mock.patch.object(run_fault_scenario, "configure_setting",
                                    side_effect=[RuntimeError("partial injection"), {"exit_code": 0}]) as configure,
                  mock.patch.object(run_fault_scenario, "running_config", return_value=original),
                  mock.patch.object(run_fault_scenario, "poll", return_value=(True, good))):
                _, record = run_fault_scenario.run_experiment("admin_down", "r1", root)
            self.assertEqual(record["status"], "FAIL")
            self.assertTrue(record["phases"]["recovered"]["passed"])
            self.assertEqual(configure.call_args_list[0].args[-1], "shutdown")
            self.assertEqual(configure.call_args_list[1].args[-1], "no shutdown")


if __name__ == "__main__":
    unittest.main()
