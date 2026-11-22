"""Collection -> trace -> handoff path, without a model key."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from build_handoff_trace import build_trace  # noqa: E402
from handoff import HandoffError, build_bundle, require_peer_snapshot  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / "observations" / name).read_text(encoding="utf-8"))


class TraceBuildTest(unittest.TestCase):
    def test_normal_trace_contains_both_configs(self):
        trace = build_trace(load("normal.json"), "r1")
        self.assertEqual(trace["final"]["status"], "normal")
        self.assertEqual(len(trace["evidence"]), 2)
        bundle = build_bundle(trace, "r1", target="r1")
        self.assertIn("hostname r1", bundle["config_snapshot"]["text"])
        self.assertIn("hostname r2", bundle["peer_snapshot"]["text"])

    def test_area_trace_exports_both_sides(self):
        trace = build_trace(load("stage5-case-01.json"), "r2")
        self.assertEqual(trace["final"]["status"], "ospf_area_mismatch")
        bundle = build_bundle(trace, "r2", target="r2")
        require_peer_snapshot(bundle)
        self.assertEqual(bundle["config_snapshot"]["node"], "r2")
        self.assertEqual(bundle["peer_snapshot"]["node"], "r1")

    def test_missing_peer_config_refuses_require_peer(self):
        trace = build_trace(load("stage5-case-01.json"), "r2")
        trace["evidence"] = {key: value for key, value in trace["evidence"].items()
                             if (value.get("arguments") or {}).get("node") != "r1"}
        bundle = build_bundle(trace, "r2", target="r2", commands=[])
        self.assertIsNone(bundle["peer_snapshot"])
        with self.assertRaises(HandoffError):
            require_peer_snapshot(bundle)

    def test_missing_target_config_refuses_build(self):
        document = copy.deepcopy(load("normal.json"))
        document["nodes"]["r1"]["commands"] = [
            item for item in document["nodes"]["r1"]["commands"] if item["id"] != "running_config"]
        trace = build_trace(document, "r1")
        with self.assertRaises(HandoffError):
            build_bundle(trace, "r1", target="r1")


if __name__ == "__main__":
    unittest.main()


class SnapshotIntegrityTest(unittest.TestCase):
    def _mutation(self, node: str, **changes) -> dict:
        document = copy.deepcopy(load("stage5-case-01.json"))
        for item in document["nodes"][node]["commands"]:
            if item["id"] == "running_config":
                item.update(changes)
                return document
        raise AssertionError("running_config missing")

    def test_truncated_target_is_rejected(self):
        document = self._mutation("r2", stdout_truncated=True)
        trace = build_trace(document, "r2")
        with self.assertRaises(HandoffError):
            build_bundle(trace, "r2", target="r2")

    def test_truncated_peer_cannot_enter_double_snapshot_path(self):
        document = self._mutation("r1", stdout_truncated=True)
        trace = build_trace(document, "r2")
        bundle = build_bundle(trace, "r2", target="r2")
        self.assertIsNone(bundle["peer_snapshot"])
        with self.assertRaises(HandoffError):
            require_peer_snapshot(bundle, expected_status="ospf_area_mismatch")

    def test_peer_stdout_bytes_mismatch_is_rejected(self):
        text = next(item["stdout"] for item in load("stage5-case-01.json")["nodes"]["r1"]["commands"]
                    if item["id"] == "running_config")
        document = self._mutation("r1", stdout_bytes=len(text.encode("utf-8")) + 1)
        trace = build_trace(document, "r2")
        self.assertIsNone(build_bundle(trace, "r2", target="r2")["peer_snapshot"])

    def test_missing_peer_config_degrades_status_and_stops_area_handoff(self):
        document = copy.deepcopy(load("stage5-case-01.json"))
        document["nodes"]["r1"]["commands"] = [
            item for item in document["nodes"]["r1"]["commands"] if item["id"] != "running_config"]
        trace = build_trace(document, "r2")
        self.assertEqual(trace["final"]["status"], "insufficient_evidence")
        bundle = build_bundle(trace, "r2", target="r2", commands=[])
        with self.assertRaises(HandoffError):
            require_peer_snapshot(bundle, expected_status="ospf_area_mismatch")
