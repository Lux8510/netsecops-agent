"""Offline regression tests for the optional, observational Agent event hook."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from agent_config import load_settings  # noqa: E402
from agent_loop import run_agent  # noqa: E402
from agent_tools import AgentTools, ReplayBackend  # noqa: E402


OBSERVATIONS = Path(__file__).resolve().parent / "fixtures/observations/normal.json"


class TwoReplyModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools, *, deadline):
        self.calls += 1
        if self.calls == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "mock-call-1", "type": "function",
                "function": {"name": "query_ospf_interface",
                             "arguments": json.dumps({"node": "r1", "interface": "eth1"})},
            }]}
        else:
            message = {"role": "assistant", "content": json.dumps({
                "status": "insufficient_evidence", "conclusion": "Only one interface checked",
                "evidence_ids": ["E001"], "missing_information": ["r2 interface"],
                "repair_suggestion": "Read r2 without changing configuration",
            })}
        return {"message": message, "finish_reason": "tool_calls" if self.calls == 1 else "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "model": "mock", "request_id": "offline-mock"}


class AgentEventTest(unittest.TestCase):
    def tools(self):
        return AgentTools(ReplayBackend(OBSERVATIONS))

    def test_events_follow_actual_model_tool_results_and_final(self):
        events = []
        trace = run_agent("read-only check", TwoReplyModel(), self.tools(), load_settings(),
                          mode="replay", on_event=events.append)
        self.assertEqual([event["kind"] for event in events],
                         ["started", "model_response", "tool_result", "model_response", "completed"])
        selected = events[1]["tool_calls"][0]
        executed = events[2]
        self.assertEqual(selected["id"], executed["tool_call_id"])
        self.assertEqual(selected["function"]["name"], executed["name"])
        self.assertEqual(json.loads(selected["function"]["arguments"]), executed["arguments"])
        self.assertEqual(executed["result"]["evidence_id"], "E001")
        self.assertEqual(events[-1]["trace"]["final"], trace["final"])
        self.assertEqual(events[-1]["trace"]["evidence"], trace["evidence"])
        self.assertEqual(trace["usage_total"]["total_tokens"], 30)

    def test_callback_cannot_mutate_trace_or_break_diagnosis(self):
        events = []

        def hostile_callback(event):
            events.append(event["kind"])
            if event["kind"] == "tool_result":
                event["result"]["status"] = "collection_failed"
                raise RuntimeError("presentation unavailable")

        trace = run_agent("read-only check", TwoReplyModel(), self.tools(), load_settings(),
                          mode="replay", on_event=hostile_callback)
        self.assertEqual(trace["evidence"]["E001"]["status"], "ok")
        self.assertEqual(trace["final"]["status"], "insufficient_evidence")
        self.assertEqual(trace["event_delivery_errors"], 1)
        self.assertEqual(events[-1], "completed")


if __name__ == "__main__":
    unittest.main()
