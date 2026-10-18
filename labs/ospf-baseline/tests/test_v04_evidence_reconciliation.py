"""Real v0.3 failure regressions; no model API, Docker, or history directory."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


LAB = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(LAB))
from agent_loop import validate_final  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402
from judge_agent_trace import verdict  # noqa: E402


def sample(name: str) -> dict:
    return json.loads((FIXTURES / "v04-failures" / name).read_text(encoding="utf-8"))


def replay_tools(trace: dict, observation: str) -> AgentTools:
    tools = AgentTools(ReplayBackend(FIXTURES / "observations" / observation))
    tools.evidence = deepcopy(trace["evidence"])
    return tools


def with_revalidated_final(trace: dict, tools: AgentTools) -> dict:
    result = deepcopy(trace)
    result["evidence"] = tools.evidence
    raw = result["model_raw_final"]
    result["final"] = validate_final(raw, tools)
    fields = ("status", "conclusion", "evidence_ids", "missing_information",
              "repair_suggestion", "root_cause", "observed_facts", "inferred_impacts")
    result["guardrail_changes"] = [
        {"field": key, "model_value": raw.get(key), "final_value": result["final"].get(key),
         "reason": "证据引用和直接根因条件校验"}
        for key in fields if raw.get(key) != result["final"].get(key)
    ]
    return result


class RealFailureEvidenceRegression(unittest.TestCase):
    def test_real_normal_missing_topology_citation_is_proven_and_added(self):
        trace = sample("normal-trace.json")
        self.assertEqual(sample("normal-acceptance.json")["normal_judge"]["verdict"], "FAIL")
        raw_ids = deepcopy(trace["model_raw_final"]["evidence_ids"])
        tools = replay_tools(trace, "normal.json")
        repaired = with_revalidated_final(trace, tools)
        self.assertEqual(repaired["final"]["status"], "normal")
        self.assertEqual(trace["model_raw_final"]["evidence_ids"], raw_ids)
        self.assertEqual(set(repaired["final"]["evidence_ids"]) - set(raw_ids), {"E001"})
        self.assertEqual([item["evidence_id"] for item in repaired["final"]["evidence_additions"]], ["E001"])
        self.assertEqual(verdict(repaired, "normal")["verdict"], "PASS")

    def test_real_failed_command_is_classified_without_model_citation(self):
        trace = sample("failed-collection-trace.json")
        self.assertEqual(sample("failed-collection-judge.json")["verdict"], "FAIL")
        raw_ids = deepcopy(trace["model_raw_final"]["evidence_ids"])
        tools = replay_tools(trace, "failed-command.json")
        repaired = with_revalidated_final(trace, tools)
        self.assertEqual(repaired["final"]["status"], "collection_failure")
        self.assertEqual(trace["model_raw_final"]["evidence_ids"], raw_ids)
        self.assertEqual(set(repaired["final"]["evidence_ids"]) - set(raw_ids), {"E003"})
        self.assertEqual([item["evidence_id"] for item in repaired["final"]["evidence_additions"]], ["E003"])
        self.assertEqual(verdict(repaired, "collection_failure")["verdict"], "PASS")

    def test_normal_fails_if_topology_was_never_collected(self):
        trace = sample("normal-trace.json")
        tools = replay_tools(trace, "normal.json")
        tools.evidence.pop("E001")
        result = validate_final(trace["model_raw_final"], tools)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertFalse(result["evidence_additions"])

    def test_normal_cannot_add_an_address_conflicting_topology(self):
        trace = sample("normal-trace.json")
        tools = replay_tools(trace, "normal.json")
        tools.evidence["E001"]["data"]["nodes"]["r2"]["loopback_address"] = "10.255.99.2"
        result = validate_final(trace["model_raw_final"], tools)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("E001", result["evidence_ids"])
        self.assertFalse(result["evidence_additions"])

    def test_normal_cannot_infer_a_never_collected_remote_route(self):
        trace = sample("normal-trace.json")
        tools = replay_tools(trace, "normal.json")
        tools.evidence.pop("E008")
        result = validate_final(trace["model_raw_final"], tools)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_missing_record_is_not_an_executed_command_failure(self):
        trace = sample("failed-collection-trace.json")
        tools = replay_tools(trace, "failed-command.json")
        missing = tools.evidence["E003"]
        missing["status"] = "collection_failed"
        missing["observations"] = [{"node": "r2", "id": "ospf_interface", "command": [],
                                    "exit_code": None, "error": "observation missing or duplicated"}]
        claim = {"status": "insufficient_evidence", "evidence_ids": ["E003"],
                 "missing_information": [], "conclusion": "no observation", "repair_suggestion": "read only"}
        result = validate_final(claim, tools)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_identity_conflict_is_not_an_executed_command_failure(self):
        trace = sample("failed-collection-trace.json")
        tools = replay_tools(trace, "failed-command.json")
        conflict = tools.evidence["E003"]
        conflict["status"] = "unavailable"
        conflict["observations"][0]["validation_error"] = "OSPF address contradicts config"
        claim = {"status": "insufficient_evidence", "evidence_ids": ["E003"],
                 "missing_information": [], "conclusion": "conflict", "repair_suggestion": "read only"}
        result = validate_final(claim, tools)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_successful_recheck_supersedes_but_keeps_earlier_failure(self):
        trace = sample("failed-collection-trace.json")
        tools = replay_tools(trace, "failed-command.json")
        successful = deepcopy(sample("normal-trace.json")["evidence"]["E003"])
        successful["evidence_id"] = "E011"
        successful["collected_at_utc"] = "2026-10-14T08:10:00+00:00"
        tools.evidence["E011"] = successful
        claim = {"status": "collection_failure", "evidence_ids": ["E003"],
                 "missing_information": [], "conclusion": "earlier failure", "repair_suggestion": "read only"}
        result = validate_final(claim, tools)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("E003", tools.evidence)  # historical failed attempt is not erased
        self.assertIn("E011", result["evidence_ids"])
        self.assertTrue(any("重采" in text or "后续" in text for text in result["missing_information"]))

    def test_independent_judge_rejects_superseded_failure(self):
        trace = sample("failed-collection-trace.json")
        successful = deepcopy(sample("normal-trace.json")["evidence"]["E003"])
        successful["evidence_id"] = "E011"
        successful["collected_at_utc"] = "2026-10-14T08:10:00+00:00"
        trace["evidence"]["E011"] = successful
        trace["final"] = {"status": "collection_failure", "conclusion": "命令采集失败。",
                          "evidence_ids": ["E003"], "missing_information": ["采集失败"],
                          "repair_suggestion": "只读重试。", "root_cause": None,
                          "observed_facts": [], "inferred_impacts": []}
        trace["model_raw_final"] = deepcopy(trace["final"])
        trace["guardrail_changes"] = []
        judgment = verdict(trace, "collection_failure")
        self.assertEqual(judgment["verdict"], "FAIL")
        self.assertTrue(any("supersed" in problem.lower() or "重采" in problem
                            for problem in judgment["problems"]))


if __name__ == "__main__":
    unittest.main()
