"""Three offline counterexamples supplied by the v0.2 review, kept in-project."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from agent_config import load_settings  # noqa: E402
from agent_loop import run_agent, validate_final  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402
from judge_agent_trace import verdict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIELDS = ("status", "conclusion", "evidence_ids", "missing_information", "repair_suggestion",
          "root_cause", "observed_facts", "inferred_impacts")


def reviewed_case(number: int) -> tuple[Path, dict, AgentTools]:
    observation = FIXTURES / f"observations/stage5-case-{number:02d}.json"
    trace = json.loads((FIXTURES / f"traces/stage5-case-{number:02d}.json").read_text(encoding="utf-8"))
    tools = AgentTools(ReplayBackend(observation))
    tools.evidence = deepcopy(trace["evidence"])
    trace["schema_version"] = 2
    return observation, trace, tools


def update_final(trace: dict, tools: AgentTools) -> None:
    raw = trace["model_raw_final"]
    trace["final"] = validate_final(raw, tools)
    update_change_log(trace)


def update_change_log(trace: dict) -> None:
    raw, final = trace["model_raw_final"], trace["final"]
    trace["guardrail_changes"] = [
        {"field": key, "model_value": raw.get(key), "final_value": final.get(key),
         "reason": "offline review counterexample"}
        for key in FIELDS if raw.get(key) != final.get(key)
    ]


class ReviewV02Regressions(unittest.TestCase):
    def test_unreachable_ping_is_valid_cited_symptom(self):
        path = FIXTURES / "observations/stage5-case-01.json"
        tools = AgentTools(ReplayBackend(path))

        class ScriptedModel:
            calls = 0

            def complete(self, _messages, _schemas, *, deadline):
                self.calls += 1
                if self.calls == 1:
                    requests = [("query_topology", {}),
                                ("query_ospf_interface", {"node": "r1", "interface": "eth1"}),
                                ("query_ospf_interface", {"node": "r2", "interface": "eth1"}),
                                ("ping_address", {"node": "r1", "target": "10.255.0.2"})]
                    message = {"role": "assistant", "content": None, "tool_calls": [
                        {"id": f"mock-{number}", "type": "function", "function": {
                            "name": name, "arguments": json.dumps(arguments)}}
                        for number, (name, arguments) in enumerate(requests)]}
                else:
                    message = {"role": "assistant", "content": json.dumps({
                        "status": "ospf_area_mismatch", "conclusion": "两端区域不同；ping 不可达是症状。",
                        "evidence_ids": ["E001", "E002", "E003", "E004"],
                        "missing_information": [], "repair_suggestion": "仅人工核对设计区域。"},
                        ensure_ascii=False)}
                return {"message": message, "finish_reason": "tool_calls" if self.calls == 1 else "stop",
                        "model": "deepseek-flash", "request_id": f"mock-offline-{self.calls}",
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

        trace = run_agent("离线核对", ScriptedModel(), tools, load_settings(), mode="mock")
        ping = trace["evidence"]["E004"]
        self.assertEqual((ping["status"], ping["data"]["reachable"],
                          ping["observations"][0]["exit_code"]), ("ok", False, 1))
        self.assertEqual(trace["final"]["status"], "ospf_area_mismatch")
        self.assertEqual(verdict(trace, "ospf_area_mismatch")["verdict"], "PASS")

    def test_ospf_address_conflicting_with_config_cannot_prove_area(self):
        _path, trace, tools = reviewed_case(1)
        record = tools.evidence["E003"]
        observation = record["observations"][0]
        self.assertIn("10.0.12.2/30", observation["stdout"])
        observation["stdout"] = observation["stdout"].replace("10.0.12.2/30", "10.9.9.2/30")
        record["data"]["output"] = observation["stdout"]
        trace["evidence"] = tools.evidence
        for turn in trace["rounds"]:
            for returned in turn["tool_results"]:
                if returned["result"].get("evidence_id") == "E003":
                    returned["result"] = deepcopy(record)
        update_final(trace, tools)
        self.assertNotEqual(trace["final"]["status"], "ospf_area_mismatch")
        self.assertEqual(verdict(trace, "ospf_area_mismatch")["verdict"], "FAIL")

    def test_repair_text_cannot_hide_wrong_action_behind_correct_node_mention(self):
        _path, trace, tools = reviewed_case(2)
        update_final(trace, tools)
        self.assertEqual(verdict(trace, "interface_admin_down")["verdict"], "PASS")
        trace["final"]["repair_suggestion"] = (
            "r2 eth1 是故障点，请保持不动；在 r1 eth1 执行 no shutdown。")
        update_change_log(trace)
        self.assertEqual(verdict(trace, "interface_admin_down")["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
