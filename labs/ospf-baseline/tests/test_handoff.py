"""Handoff bundle: stable hash, config snapshot, allowlisted commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from handoff import HandoffError, build_bundle, derive_commands, verify_bundle  # noqa: E402


def load(name: str) -> dict:
    document = json.loads((FIXTURES / "traces" / name).read_text(encoding="utf-8"))
    return document.get("trace") if isinstance(document.get("trace"), dict) else document


class HandoffTest(unittest.TestCase):
    def test_area_bundle_verifies_and_has_snapshot(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r2", target="r2")
        verify_bundle(bundle)
        self.assertEqual(bundle["diagnosis"]["status"], "ospf_area_mismatch")
        self.assertIn("Current configuration", bundle["config_snapshot"]["text"])
        self.assertEqual(len(bundle["bundle_sha256"]), 64)
        self.assertEqual(bundle["proposed_commands"],
                         ["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"])

    def test_snapshot_is_bound_to_the_requested_node(self):
        trace = load("stage5-case-01.json")
        bundle = build_bundle(trace, "r2", target="r2")
        snapshot = bundle["config_snapshot"]
        self.assertEqual(snapshot["node"], "r2")
        self.assertEqual(snapshot["evidence_id"], "E007")
        self.assertIn("hostname r2", snapshot["text"])
        peer = build_bundle(trace, "r1", target="r1")
        self.assertEqual(peer["config_snapshot"]["node"], "r1")
        self.assertIn("hostname r1", peer["config_snapshot"]["text"])

    def test_tampering_is_rejected(self):
        bundle = build_bundle(load("stage5-case-03.json"), "r2", target="r2")
        bundle["diagnosis"]["status"] = "normal"
        with self.assertRaises(HandoffError):
            verify_bundle(bundle)

    def test_config_tampering_is_rejected(self):
        bundle = build_bundle(load("stage5-case-03.json"), "r2", target="r2")
        bundle["config_snapshot"]["text"] += "\n!"
        with self.assertRaises(HandoffError):
            verify_bundle(bundle)

    def test_arbitrary_commands_are_rejected(self):
        with self.assertRaises(HandoffError):
            build_bundle(load("stage5-case-02.json"), "r1", commands=["write memory"])

    def test_only_plannable_statuses_get_commands(self):
        self.assertEqual(derive_commands(load("normal.json"), "normal", "r1"), [])
        self.assertEqual(derive_commands(load("normal.json"), "insufficient_evidence", "r1"), [])

    def test_admin_and_passive_plans(self):
        # The admin-down fixture only contains the peer's config; refusing is the safe behavior.
        with self.assertRaises(HandoffError):
            build_bundle(load("stage5-case-02.json"), "r1", target="r1")
        passive = build_bundle(load("stage5-case-03.json"), "r2", target="r2")
        self.assertEqual(passive["proposed_commands"], ["no ip ospf passive"])

    def test_missing_target_snapshot_does_not_fall_back_to_peer(self):
        trace = load("stage5-case-01.json")
        trace["evidence"] = {key: item for key, item in trace["evidence"].items()
                             if not (item["tool"] == "query_interface_config"
                                     and item["arguments"].get("node") == "r2")}
        with self.assertRaises(HandoffError):
            build_bundle(trace, "r2", target="r2", commands=[])

    def test_device_name_must_match_snapshot_node(self):
        with self.assertRaises(HandoffError):
            build_bundle(load("stage5-case-01.json"), "r1", target="r2", commands=[])

    def test_bundle_carries_citations_not_paths(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r2", target="r2")
        self.assertTrue(bundle["citations"])
        serialized = json.dumps(bundle)
        self.assertNotIn(str(LAB_DIR), serialized)


if __name__ == "__main__":
    unittest.main()


class TraceIdentityTest(unittest.TestCase):
    def test_trace_id_is_stable_across_reexports(self):
        from handoff import trace_identity
        trace = load("stage5-case-01.json")
        first = trace_identity(trace)
        second = trace_identity(load("stage5-case-01.json"))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_snapshot_carries_observation_identity(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r2", target="r2")
        snapshot = bundle["config_snapshot"]
        self.assertEqual(snapshot["node"], "r2")
        self.assertEqual(snapshot["target"], "r2")
        self.assertEqual(snapshot["exit_code"], 0)
        self.assertTrue(any("show running-config" in part for part in snapshot["command"]))
        self.assertTrue(snapshot["evidence_id"])

    def test_citations_carry_digests(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r2", target="r2")
        for item in bundle["citations"]:
            self.assertIn("content_sha256", item)
            self.assertTrue(item["observations"])
            self.assertIn("stdout", item["observations"][0])
            self.assertIn("exit_code", item["observations"][0])


class SnapshotCitationOrderingTest(unittest.TestCase):
    def test_snapshot_citation_uses_the_selected_observation_not_the_first(self):
        import copy
        trace = copy.deepcopy(load("stage5-case-01.json"))
        # Find the r2 running-config record and reorder so a different observation is first.
        for evidence_id, record in trace["evidence"].items():
            if record.get("tool") == "query_interface_config" and record.get("arguments", {}).get("node") == "r2":
                running = record["observations"][0]
                other = dict(running)
                other["command"] = ["vtysh", "-c", "show interface eth1"]
                other["exit_code"] = 0
                other["stdout"] = "eth1 is up\n"
                record["observations"] = [other, running]
        bundle = build_bundle(trace, "r2", target="r2")
        snapshot = bundle["config_snapshot"]
        snapshot_citation = next(item for item in bundle["citations"]
                                 if item["evidence_id"] == snapshot["evidence_id"])
        observation = snapshot_citation["observations"][0]
        self.assertTrue(any("show running-config" in str(p) for p in observation["command"]))
        self.assertEqual(observation["exit_code"], 0)
        self.assertEqual(hashlib.sha256(observation["stdout"].encode()).hexdigest(), snapshot["sha256"])


class PeerSnapshotTest(unittest.TestCase):
    def test_area_trace_exports_peer_snapshot(self):
        bundle = build_bundle(load("stage5-case-01.json"), "r2", target="r2")
        peer = bundle["peer_snapshot"]
        self.assertIsNotNone(peer)
        self.assertEqual(peer["node"], "r1")
        self.assertEqual(peer["interface"], "eth1")
        self.assertIn("hostname r1", peer["text"])
        self.assertEqual(hashlib.sha256(peer["text"].encode()).hexdigest(), peer["sha256"])

    def test_missing_peer_snapshot_is_null_not_invented(self):
        trace = load("stage5-case-01.json")
        trace["evidence"] = {key: item for key, item in trace["evidence"].items()
                             if not (item["tool"] == "query_interface_config"
                                     and item["arguments"].get("node") == "r1")}
        bundle = build_bundle(trace, "r2", target="r2")
        self.assertIsNone(bundle["peer_snapshot"])


class TruncationTest(unittest.TestCase):
    def test_truncated_snapshot_is_rejected(self):
        trace = load("stage5-case-01.json")
        for record in trace["evidence"].values():
            if record.get("tool") == "query_interface_config" and record.get("arguments", {}).get("node") == "r2":
                record["observations"][0]["stdout_truncated"] = True
                record["observations"][0]["stdout_bytes"] = len(record["observations"][0]["stdout"].encode()) + 100
        with self.assertRaises(HandoffError):
            build_bundle(trace, "r2", target="r2")

    def test_upstream_truncation_is_preserved_in_other_citations(self):
        trace = load("stage5-case-01.json")
        for record in trace["evidence"].values():
            if record.get("tool") == "query_neighbors":
                record["observations"][0]["stdout_truncated"] = True
                record["observations"][0]["stdout_bytes"] = 12345
                break
        bundle = build_bundle(trace, "r2", target="r2")
        citation = next(item for item in bundle["citations"] if item["tool"] == "query_neighbors")
        observation = citation["observations"][0]
        self.assertTrue(observation["stdout_truncated"])
        self.assertEqual(observation["stdout_bytes"], 12345)


class NormalSavedTraceTest(unittest.TestCase):
    def test_normal_saved_trace_exports_with_target_snapshot(self):
        document = json.loads((FIXTURES / "v04-failures/normal-trace.json").read_text(encoding="utf-8"))
        trace = document.get("trace") if isinstance(document.get("trace"), dict) else document
        bundle = build_bundle(trace, "r1", target="r1")
        verify_bundle(bundle)
        self.assertEqual(bundle["diagnosis"]["status"], "normal")
        self.assertEqual(bundle["config_snapshot"]["node"], "r1")
        self.assertEqual(bundle["proposed_commands"], [])
