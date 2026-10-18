"""Offline tests for restricted tools, guardrails, and Chat Completions loop."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))
from agent_config import load_settings  # noqa: E402
from agent_loop import run_agent, validate_final  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402
from deepseek_client import DeepSeekAPIError, DeepSeekChatClient  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures/observations"


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def complete(self, messages, tools, *, deadline):
        self.calls += 1
        return next(self.responses)


def response(*, calls=None, final=None):
    message = {"role": "assistant", "content": json.dumps(final, ensure_ascii=False) if final else None}
    if calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function", "function": {"name": name,
             "arguments": json.dumps(arguments)}} for i, (name, arguments) in enumerate(calls, 1)
        ]
    return {"message": message, "finish_reason": "tool_calls" if calls else "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "mock", "request_id": "mock-request"}


class AgentRuntimeTest(unittest.TestCase):
    def tools(self, phase="normal"):
        return AgentTools(ReplayBackend(FIXTURES / ("area-fault.json" if phase == "fault" else "normal.json")))

    def test_invalid_parameters_cannot_execute(self):
        tools = self.tools()
        for name, args in (("query_interface", {"node": "other", "interface": "eth1"}),
                           ("query_ospf_interface", {"node": "r1", "interface": "eth2"}),
                           ("ping_address", {"node": "r1", "target": "8.8.8.8"}),
                           ("shell", {"command": "id"})):
            self.assertEqual(tools.call(name, args)["status"], "invalid_argument")

    def test_interconnect_interface_with_linux_peer_suffix(self):
        tools = self.tools()
        result = tools.call("query_interface", {"node": "r1", "interface": "eth1"})
        self.assertEqual(result["status"], "ok")
        self.assertIn("10.0.12.1/30", " ".join(result["data"]["address_lines"]))

    def test_normal_and_mismatch_require_cited_area_evidence(self):
        for phase, area_status in (("normal", "normal"), ("fault", "ospf_area_mismatch")):
            tools = self.tools(phase)
            left = tools.call("query_ospf_interface", {"node": "r1", "interface": "eth1"})
            right = tools.call("query_ospf_interface", {"node": "r2", "interface": "eth1"})
            requested = {"status": "ospf_area_mismatch", "conclusion": "mismatch", "evidence_ids":
                         [left["evidence_id"], right["evidence_id"]], "missing_information": [], "repair_suggestion": "read only"}
            result = validate_final(requested, tools)
            self.assertEqual(result["status"], "insufficient_evidence" if phase == "normal" else area_status)
            if phase == "fault":
                requested["evidence_ids"] = [left["evidence_id"]]
                self.assertEqual(validate_final(requested, tools)["status"], "insufficient_evidence")

    def test_collection_failure_not_network_mismatch(self):
        tools = self.tools()
        failed = tools.call("query_ospf_interface", {"node": "r1", "interface": "eth1"})
        # A real command failure retains the expected command identity and
        # records a failed exit; changing only the tool status is not enough.
        failed["status"] = "collection_failed"
        failed["observations"][0]["exit_code"] = 1
        failed["observations"][0]["error"] = "simulated command execution failure"
        tools.evidence[failed["evidence_id"]] = failed
        raw = {"status": "ospf_area_mismatch", "conclusion": "x", "evidence_ids": [failed["evidence_id"]],
               "missing_information": [], "repair_suggestion": "x"}
        self.assertEqual(validate_final(raw, tools)["status"], "collection_failure")

    def test_missing_area_and_failed_command_replays(self):
        for case, expected in (("missing-one-area", "ok"), ("failed-key-command", "collection_failed")):
            tools = AgentTools(ReplayBackend(FIXTURES / ("failed-command.json" if case == "failed-key-command"
                                                      else "missing-one-area.json")))
            first = tools.call("query_ospf_interface", {"node": "r1", "interface": "eth1"})
            second = tools.call("query_ospf_interface", {"node": "r2", "interface": "eth1"})
            self.assertEqual(second["status"], expected)
            claim = {"status": "ospf_area_mismatch", "conclusion": "x",
                     "evidence_ids": [first["evidence_id"], second["evidence_id"]],
                     "missing_information": [], "repair_suggestion": "x"}
            self.assertEqual(validate_final(claim, tools)["status"],
                             "collection_failure" if case == "failed-key-command" else "insufficient_evidence")

    def test_simulated_model_cannot_promote_missing_or_failed_area(self):
        for case in ("missing-one-area", "failed-key-command"):
            tools = AgentTools(ReplayBackend(FIXTURES / ("failed-command.json" if case == "failed-key-command"
                                                      else "missing-one-area.json")))
            model = ScriptedModel([
                response(calls=[("query_ospf_interface", {"node": "r1", "interface": "eth1"}),
                                ("query_ospf_interface", {"node": "r2", "interface": "eth1"})]),
                response(final={"status": "ospf_area_mismatch", "conclusion": "claim mismatch",
                                "evidence_ids": ["E001", "E002"], "missing_information": [],
                                "repair_suggestion": "do nothing"}),
            ])
            trace = run_agent("check", model, tools, load_settings(), mode="replay")
            self.assertEqual(trace["final"]["status"],
                             "collection_failure" if case == "failed-key-command" else "insufficient_evidence")

    def test_agent_tool_loop_and_token_total(self):
        tools = self.tools()
        model = ScriptedModel([
            response(calls=[("query_ospf_interface", {"node": "r1", "interface": "eth1"})]),
            response(final={"status": "insufficient_evidence", "conclusion": "Only one end checked",
                            "evidence_ids": ["E001"], "missing_information": ["r2 area"],
                            "repair_suggestion": "read r2"}),
        ])
        trace = run_agent("check", model, tools, load_settings(), mode="replay")
        self.assertEqual(trace["tool_calls_executed"], 1)
        self.assertEqual(trace["usage_total"]["total_tokens"], 30)
        self.assertEqual(trace["final"]["status"], "insufficient_evidence")
        self.assertEqual(trace["rounds"][0]["tool_results"][0]["result"]["evidence_id"], "E001")

    def test_call_limit_stops_model(self):
        tools = self.tools()
        model = ScriptedModel([response(calls=[("query_topology", {}), ("query_neighbors", {"node": "r1"})])])
        trace = run_agent("check", model, tools, replace(load_settings(), max_tool_calls=1), mode="replay")
        self.assertEqual(trace["final"]["status"], "incomplete")
        self.assertEqual(trace["tool_calls_executed"], 1)
        self.assertEqual(model.calls, 1)

    def test_authentication_error_is_not_retried_or_leaked(self):
        client = DeepSeekChatClient(load_settings(), "secret-test-value")
        with patch.object(client, "_send_once", side_effect=DeepSeekAPIError("authentication", "invalid key", 401)) as send:
            with self.assertRaises(DeepSeekAPIError):
                client.complete([{"role": "user", "content": "check"}], [], deadline=time.monotonic() + 10)
            self.assertEqual(send.call_count, 1)

    def test_balance_error_is_not_retried(self):
        client = DeepSeekChatClient(load_settings(), "secret-test-value")
        with patch.object(client, "_send_once", side_effect=DeepSeekAPIError("balance", "insufficient balance", 402)) as send:
            with self.assertRaises(DeepSeekAPIError):
                client.complete([{"role": "user", "content": "check"}], [], deadline=time.monotonic() + 10)
            self.assertEqual(send.call_count, 1)

    def test_persistent_server_error_stops_at_retry_limit(self):
        client = DeepSeekChatClient(load_settings(), "secret-test-value")
        with patch.object(client, "_send_once", side_effect=DeepSeekAPIError("server", "HTTP 500", 500)) as send, \
             patch("deepseek_client.time.sleep"):
            with self.assertRaises(DeepSeekAPIError):
                client.complete([{"role": "user", "content": "check"}], [], deadline=time.monotonic() + 30)
            self.assertEqual(send.call_count, client.settings.max_retries + 1)


if __name__ == "__main__":
    unittest.main()
