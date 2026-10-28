"""HTTP/SSE tests using only the fixed observation fixture and a mock model."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from threading import Event
import unittest

from fastapi.testclient import TestClient


LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from progress_api import ProgressService, create_app  # noqa: E402


class ProgressAPITest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
        self.capabilities = self.client.get("/api/capabilities").json()
        self.session = {"X-NetSecOps-Session": self.capabilities["session_token"]}

    def test_mock_run_streams_real_agent_events_and_final_trace(self):
        self.assertTrue(self.capabilities["demo_available"])
        self.assertFalse(self.capabilities["live_available"])
        self.assertFalse(self.capabilities["model_api_called_by_demo"])
        started = self.client.post("/api/runs", json={"mode": "demo"}, headers=self.session)
        self.assertEqual(started.status_code, 202)
        run_id = started.json()["run_id"]
        streamed = self.client.get(f"/api/runs/{run_id}/events")
        self.assertEqual(streamed.status_code, 200)
        self.assertTrue(streamed.headers["content-type"].startswith("text/event-stream"))
        events = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith("data: ")]
        kinds = [item["kind"] for item in events]
        self.assertEqual(kinds[0], "started")
        self.assertEqual(kinds[-1], "completed")
        selected = [call for item in events if item["kind"] == "model_response"
                    for call in item["tool_calls"] or []]
        results = [item for item in events if item["kind"] == "tool_result"]
        self.assertEqual(len(selected), len(results))
        self.assertEqual(len(results), 9)
        self.assertEqual([call["id"] for call in selected],
                         [item["tool_call_id"] for item in results])
        self.assertEqual([item["result"]["evidence_id"] for item in results],
                         [f"E{index:03d}" for index in range(1, 10)])
        trace = self.client.get(f"/api/runs/{run_id}/trace").json()
        self.assertEqual(trace, events[-1]["trace"])
        self.assertEqual(trace["final"]["status"], "normal")
        self.assertEqual(trace["model_reported"], "mock-offline")
        self.assertEqual(trace["usage_total"]["requests_with_usage"], 0)
        self.assertNotIn("DEEPSEEK_API_KEY", streamed.text)

    def test_explicit_session_and_local_origin_required(self):
        self.assertEqual(self.client.post("/api/runs", json={"mode": "demo"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"mode": "demo"},
                                          headers={**self.session, "Origin": "https://foreign.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"mode": "live"},
                                          headers=self.session).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"mode": "other"},
                                          headers=self.session).status_code, 422)
        self.assertEqual(self.client.post("/api/runs", json={"mode": "demo", "path": "/etc/passwd"},
                                          headers=self.session).status_code, 422)
        self.assertEqual(self.client.get("/api/runs/not-a-run/events").status_code, 404)

    def test_second_run_is_rejected_while_first_is_active(self):
        gate = Event()
        active = Event()

        def blocked(emit):
            active.set()
            if not gate.wait(2):
                raise RuntimeError("test worker timed out")
            emit({"schema_version": 1, "kind": "completed", "trace": {"final": {"status": "normal"}}})
            return {"final": {"status": "normal"}}

        client = TestClient(create_app(ProgressService(demo_runner=blocked)),
                            base_url="http://127.0.0.1:8000")
        token = client.get("/api/capabilities").json()["session_token"]
        headers = {"X-NetSecOps-Session": token}
        try:
            first = client.post("/api/runs", json={"mode": "demo"}, headers=headers)
            self.assertEqual(first.status_code, 202)
            self.assertTrue(active.wait(1))
            self.assertEqual(client.post("/api/runs", json={"mode": "demo"},
                                         headers=headers).status_code, 409)
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main()
