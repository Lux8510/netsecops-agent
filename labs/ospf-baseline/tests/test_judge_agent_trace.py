"""No-API regression for the independent acceptance judge."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))
from judge_agent_trace import verdict  # noqa: E402


class TraceJudgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal = json.loads((Path(__file__).resolve().parent / "fixtures/traces/normal.json").read_text(encoding="utf-8"))

    def test_real_normal_trace_passes(self):
        self.assertEqual(verdict(self.normal, "normal")["verdict"], "PASS")

    def test_tool_execution_must_match_model_call(self):
        trace = deepcopy(self.normal)
        trace["rounds"][0]["tool_results"][0]["arguments"] = {"node": "r1"}
        self.assertEqual(verdict(trace, "normal")["verdict"], "FAIL")

    def test_missing_citation_fails(self):
        trace = deepcopy(self.normal)
        trace["final"]["evidence_ids"].append("E999")
        self.assertEqual(verdict(trace, "normal")["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
