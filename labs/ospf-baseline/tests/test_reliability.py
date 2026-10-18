"""Offline counterexamples for the v0.2 reliability repair (no API or live lab)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

from agent_config import load_settings  # noqa: E402
from agent_loop import run_agent, validate_final  # noqa: E402
from agent_tools import AgentTools, ObservationBackend, ReplayBackend  # noqa: E402
from judge_agent_trace import verdict  # noqa: E402
from collect_evidence import execute, read_layout  # noqa: E402
from diagnose_offline import diagnose  # noqa: E402
import run_fault_scenario  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NORMAL = FIXTURES / "observations/normal.json"


def case(number: int) -> tuple[Path, dict]:
    observation = FIXTURES / f"observations/stage5-case-{number:02d}.json"
    trace = FIXTURES / f"traces/stage5-case-{number:02d}.json"
    return observation, json.loads(trace.read_text(encoding="utf-8"))


class ReliabilityCounterexamples(unittest.TestCase):
    def test_wrong_fault_node_and_repair_are_rejected_by_guard_and_judge(self):
        observation, trace = case(2)
        tools = AgentTools(ReplayBackend(observation))
        tools.evidence = deepcopy(trace["evidence"])
        raw = deepcopy(trace["model_raw_final"])
        raw["conclusion"] = "确认 r1 eth1 被管理关闭；r2 没有故障。"
        raw["repair_suggestion"] = "在 r1 eth1 执行 no shutdown。"
        fixed = validate_final(raw, tools)
        self.assertEqual(fixed["status"], "interface_admin_down")
        self.assertEqual(fixed["root_cause"]["node"], "r2")
        self.assertIn("r2 eth1", fixed["conclusion"])
        self.assertIn("r2 eth1", fixed["repair_suggestion"])
        self.assertNotIn("r1 eth1", fixed["repair_suggestion"])
        trace["model_raw_final"] = raw
        trace["final"] = raw
        trace["rounds"][-1]["model_message"]["content"] = json.dumps(raw, ensure_ascii=False)
        self.assertEqual(verdict(trace, "interface_admin_down")["verdict"], "FAIL")

    def test_different_interface_command_and_output_cannot_prove_area(self):
        observation, trace = case(1)
        document = json.loads(observation.read_text(encoding="utf-8"))
        item = next(x for x in document["nodes"]["r2"]["commands"] if x["id"] == "ospf_interface")
        item["command"][-1] = "show ip ospf interface eth9"
        item["stdout"] = item["stdout"].replace("eth1", "eth9")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            tools = AgentTools(ReplayBackend(path))
            left = tools.call("query_ospf_interface", {"node": "r1", "interface": "eth1"})
            right = tools.call("query_ospf_interface", {"node": "r2", "interface": "eth1"})
            self.assertNotEqual(right["status"], "ok")
            raw = {"status": "ospf_area_mismatch", "conclusion": "mismatch",
                   "evidence_ids": [left["evidence_id"], right["evidence_id"]],
                   "missing_information": [], "repair_suggestion": "change area"}
            self.assertNotEqual(validate_final(raw, tools)["status"], "ospf_area_mismatch")
        saved = trace["evidence"]["E003"]
        saved["observations"][0]["command"][-1] = "show ip ospf interface eth9"
        saved["observations"][0]["stdout"] = saved["observations"][0]["stdout"].replace("eth1", "eth9")
        saved["data"]["output"] = saved["observations"][0]["stdout"]
        for turn in trace["rounds"]:
            for result in turn["tool_results"]:
                if result["result"].get("evidence_id") == "E003":
                    result["result"] = deepcopy(saved)
        self.assertEqual(verdict(trace, "ospf_area_mismatch")["verdict"], "FAIL")

    def test_missing_information_string_is_preserved(self):
        observation, trace = case(1)
        tools = AgentTools(ReplayBackend(observation))
        tools.evidence = deepcopy(trace["evidence"])
        raw = trace["model_raw_final"]
        result = validate_final(raw, tools)
        self.assertEqual(result["missing_information"], [raw["missing_information"]])

    def test_imported_experiment_installs_and_restores_signal_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        seen = []
        def fake_run(*_args):
            seen.append(signal.getsignal(signal.SIGTERM))
            return None
        with mock.patch.object(run_fault_scenario, "_run_experiment", side_effect=fake_run):
            run_fault_scenario.run_experiment("admin_down", "r1", Path("unused"))
        self.assertIs(seen[0], run_fault_scenario.interrupt_handler)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    @unittest.skipUnless(sys.platform == "linux", "POSIX SIGTERM delivery test runs in Linux/WSL")
    def test_sigterm_restores_only_its_own_mock_child(self):
        with tempfile.TemporaryDirectory(prefix="netsecops-mock-signal-") as temp:
            folder = Path(temp)
            child = subprocess.Popen([sys.executable, str(Path(__file__).parent / "mock_signal_child.py"), temp],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                until = time.monotonic() + 8
                while not (folder / "ready").exists() and child.poll() is None and time.monotonic() < until:
                    time.sleep(0.01)
                if not (folder / "ready").exists():
                    _, error = child.communicate(timeout=8)
                    self.fail("mock child did not reach fault wait: " + error.decode("utf-8", "replace"))
                child.terminate()  # Only this Popen child receives SIGTERM.
                _, error = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, error.decode("utf-8", "replace"))
                self.assertTrue((folder / "restored").exists())
                result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(result["recovered"])
            finally:
                if child.poll() is None:
                    child.kill()  # Still only the child created above.
                    child.wait(timeout=5)

    def test_command_timeout_is_clamped_to_total_deadline(self):
        layout = read_layout(LAB)
        with mock.patch("collect_evidence.time.monotonic", return_value=9.5), \
             mock.patch("collect_evidence.subprocess.run", return_value=mock.Mock(
                 returncode=0, stdout="", stderr="")) as command:
            execute(layout, "r1", "running_config", None, timeout_seconds=12, deadline=10.0)
        self.assertEqual(command.call_args.kwargs["timeout"], 0.5)
        with mock.patch("collect_evidence.time.monotonic", return_value=10.0), \
             mock.patch("collect_evidence.subprocess.run") as command:
            result = execute(layout, "r1", "running_config", None, deadline=10.0)
        command.assert_not_called()
        self.assertIn("deadline", result["error"])

    def test_replay_rejects_wrong_node_and_ping_address(self):
        document = json.loads(NORMAL.read_text(encoding="utf-8"))
        config = next(item for item in document["nodes"]["r2"]["commands"] if item["id"] == "running_config")
        config["node"] = "r1"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = AgentTools(ReplayBackend(path)).call("query_topology", {})
            self.assertNotEqual(result["status"], "ok")
        document = json.loads(NORMAL.read_text(encoding="utf-8"))
        ping = next(item for item in document["nodes"]["r1"]["commands"] if item["id"] == "ping_remote_loopback")
        ping["command"][-1] = "10.255.0.99"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = AgentTools(ReplayBackend(path)).call("ping_address", {"node": "r1", "target": "10.255.0.2"})
            self.assertNotEqual(result["status"], "ok")

    def test_rule_rejects_command_from_other_container(self):
        observation, _trace = case(1)
        document = json.loads(observation.read_text(encoding="utf-8"))
        item = next(x for x in document["nodes"]["r2"]["commands"] if x["id"] == "ospf_interface")
        item["command"][4] = "clab-netsecops-ospf-r1"
        self.assertNotEqual(diagnose(document)["status"], "ospf_area_mismatch")

    def test_structured_root_cause_binds_each_r2_fault(self):
        for number, status in ((1, "ospf_area_mismatch"), (2, "interface_admin_down"), (3, "ospf_passive")):
            observation, trace = case(number)
            tools = AgentTools(ReplayBackend(observation))
            tools.evidence = deepcopy(trace["evidence"])
            final = validate_final(trace["model_raw_final"], tools)
            self.assertEqual(final["status"], status)
            root = final["root_cause"]
            self.assertEqual(root["fault_type"], status)
            self.assertTrue(set(root["evidence_ids"]).issubset(final["evidence_ids"]))
            if number == 1:
                self.assertIsNone(root["node"])
                self.assertEqual({item["node"] for item in root["endpoints"]}, {"r1", "r2"})
            else:
                self.assertEqual((root["node"], root["interface"]), ("r2", "eth1"))

    def test_independent_judge_rejects_structured_location_and_repair_tampering(self):
        observation, trace = case(2)
        tools = AgentTools(ReplayBackend(observation))
        tools.evidence = deepcopy(trace["evidence"])
        final = validate_final(trace["model_raw_final"], tools)
        trace["schema_version"] = 2
        trace["final"] = final
        raw = trace["model_raw_final"]
        trace["guardrail_changes"] = [
            {"field": key, "model_value": raw.get(key), "final_value": final.get(key), "reason": "direct evidence"}
            for key in ("status", "conclusion", "evidence_ids", "missing_information", "repair_suggestion",
                        "root_cause", "observed_facts", "inferred_impacts") if raw.get(key) != final.get(key)]
        self.assertEqual(verdict(trace, "interface_admin_down")["verdict"], "PASS")
        wrong = deepcopy(trace)
        wrong["final"]["root_cause"]["node"] = "r1"
        self.assertEqual(verdict(wrong, "interface_admin_down")["verdict"], "FAIL")
        wrong = deepcopy(trace)
        wrong["final"]["repair_suggestion"] = "仅在 r1 eth1 执行 no shutdown。"
        self.assertEqual(verdict(wrong, "interface_admin_down")["verdict"], "FAIL")

    def test_total_deadline_stops_following_mock_commands(self):
        normal = json.loads(NORMAL.read_text(encoding="utf-8"))
        layout = ReplayBackend(NORMAL).layout
        clock = {"now": 0.0, "commands": []}

        class SlowBackend(ObservationBackend):
            def get(self, node, key, *, deadline=None):
                clock["commands"].append((node, key))
                clock["now"] += 6.0
                return deepcopy(next(item for item in normal["nodes"][node]["commands"] if item["id"] == key))

        class Model:
            def complete(self, *_args, deadline):
                return {"message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "mock-call", "type": "function", "function": {
                        "name": "query_interface", "arguments": '{"node":"r1","interface":"eth1"}'}}]},
                    "model": "mock", "request_id": "mock", "finish_reason": "tool_calls",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

        with mock.patch("agent_loop.time.monotonic", side_effect=lambda: clock["now"]):
            trace = run_agent("inspect", Model(), AgentTools(SlowBackend(layout)),
                              replace(load_settings(), total_timeout_seconds=10), mode="mock")
        self.assertLessEqual(len(clock["commands"]), 2)
        self.assertEqual(trace["final"]["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
