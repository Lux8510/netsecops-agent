"""Offline tests for the fault-injection matrix and evidence metrics."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
ROOT = LAB_DIR.parents[1]
sys.path.insert(0, str(LAB_DIR))

from experiment_matrix import (  # noqa: E402
    MatrixError, SCENARIOS, aggregate, coverage, evaluate_manifest,
    extract_trace, render_markdown, trace_metrics,
)


FIXTURES = LAB_DIR / "tests" / "fixtures"
CASES = {
    "normal": FIXTURES / "traces/normal.json",
    "area-mismatch": FIXTURES / "traces/stage5-case-01.json",
    "admin-down": FIXTURES / "traces/stage5-case-02.json",
    "passive": FIXTURES / "traces/stage5-case-03.json",
    "abstain": FIXTURES / "v04-failures/failed-collection-trace.json",
}


def load_case(name: str) -> dict:
    document = json.loads(CASES[name].read_text(encoding="utf-8"))
    return document if "trace" in document else {"trace": document}


class MetricsTest(unittest.TestCase):
    def test_area_mismatch_trace_is_accurate_and_fully_covered(self):
        trace = load_case("area-mismatch")["trace"]
        metrics = trace_metrics(trace, "ospf_area_mismatch")
        self.assertTrue(metrics["root_cause_match"])
        self.assertEqual(metrics["evidence_coverage"], 1.0)
        self.assertFalse(metrics["false_confidence"])
        self.assertFalse(metrics["wrong_confident"])
        self.assertFalse(metrics["abstained"])

    def test_missing_proof_lowers_coverage_and_marks_false_confidence(self):
        trace = deepcopy(load_case("area-mismatch")["trace"])
        trace["final"]["evidence_ids"] = ["E002", "E003"]  # both OSPF areas, no neighbors
        metrics = trace_metrics(trace, "ospf_area_mismatch")
        self.assertLess(metrics["evidence_coverage"], 1.0)
        self.assertIn("query_neighbors@r1", metrics["missing_proofs"])
        self.assertTrue(metrics["false_confidence"])

    def test_wrong_but_confident_is_counted_separately(self):
        trace = deepcopy(load_case("normal")["trace"])
        trace["final"]["status"] = "ospf_area_mismatch"
        metrics = trace_metrics(trace, "normal")
        self.assertTrue(metrics["wrong_confident"])
        # A wrong claim is also unsupported by these observations.
        self.assertTrue(metrics["false_confidence"])
        self.assertFalse(metrics["root_cause_match"])

    def test_abstention_is_not_false_confidence(self):
        trace = load_case("abstain")["trace"]
        metrics = trace_metrics(trace, "insufficient_evidence")
        self.assertTrue(metrics["abstained"])
        self.assertFalse(metrics["false_confidence"])
        self.assertEqual(metrics["evidence_coverage"], 1.0)  # no required proofs for abstention

    def test_unknown_citations_are_reported(self):
        trace = deepcopy(load_case("normal")["trace"])
        trace["final"]["evidence_ids"].append("E999")
        self.assertEqual(trace_metrics(trace, "normal")["unknown_citations"], ["E999"])

    def test_coverage_requires_peer_and_ping_targets(self):
        trace = load_case("normal")["trace"]
        ratio, missing = coverage(trace, "normal")
        self.assertEqual(ratio, 1.0)
        self.assertEqual(missing, [])

    def test_aggregate_groups_scenarios_and_rates(self):
        rows = [
            dict(trace_metrics(load_case("area-mismatch")["trace"], "ospf_area_mismatch"), scenario="area_mismatch"),
            dict(trace_metrics(load_case("abstain")["trace"], "insufficient_evidence"), scenario="missing_area"),
        ]
        summary = aggregate(rows)
        self.assertEqual(summary["runs"], 2)
        self.assertEqual(summary["root_cause_accuracy"], 1.0)
        self.assertEqual(set(summary["by_scenario"]), {"area_mismatch", "missing_area"})
        self.assertIn("area_mismatch", render_markdown(summary))

    def test_empty_aggregate_is_rejected(self):
        with self.assertRaises(MatrixError):
            aggregate([])

    def test_metrics_include_semantic_support_and_ci(self):
        metrics = trace_metrics(load_case("area-mismatch")["trace"], "ospf_area_mismatch")
        self.assertTrue(metrics["semantic_supported"])
        summary = aggregate([dict(metrics, scenario="area_mismatch")])
        self.assertEqual(summary["semantic_support_rate"], 1.0)
        self.assertEqual(summary["root_cause_accuracy_ci95"]["total"], 1)
        self.assertTrue(summary["underpowered"])
        self.assertEqual(summary["failures"], [])

    def test_failures_list_records_mismatch_and_contradiction(self):
        trace = deepcopy(load_case("normal")["trace"])
        trace["final"]["status"] = "ospf_area_mismatch"
        summary = aggregate([dict(trace_metrics(trace, "normal"), scenario="normal")])
        self.assertEqual(len(summary["failures"]), 1)
        self.assertEqual(summary["failures"][0]["status"], "ospf_area_mismatch")

    def test_dropped_proof_marks_false_confidence(self):
        trace = deepcopy(load_case("area-mismatch")["trace"])
        trace["final"]["evidence_ids"] = [key for key, item in trace["evidence"].items()
                                         if item["tool"] == "query_ospf_interface"]
        metrics = trace_metrics(trace, "ospf_area_mismatch")
        self.assertLess(metrics["evidence_coverage"], 1.0)
        self.assertTrue(metrics["false_confidence"])

    def test_extract_trace_accepts_wrapper_and_raw_trace(self):
        case = load_case("admin-down")
        self.assertIs(extract_trace(case), case["trace"])
        self.assertIs(extract_trace(case["trace"]), case["trace"])
        with self.assertRaises(MatrixError):
            extract_trace({"not": "a trace"})


class ManifestTest(unittest.TestCase):
    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trace = load_case("abstain")["trace"]
            (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
            manifest = {"runs": [{"trace": "trace.json", "scenario": "normal",
                                  "expected": "insufficient_evidence"}]}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            summary = evaluate_manifest(root / "manifest.json")
            self.assertEqual(summary["runs"], 1)
            self.assertEqual(summary["abstention_rate"], 1.0)

    def test_unknown_scenario_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text(json.dumps(
                {"runs": [{"trace": "x.json", "scenario": "bogus"}]}), encoding="utf-8")
            with self.assertRaises(MatrixError):
                evaluate_manifest(root / "manifest.json")


class CatalogTest(unittest.TestCase):
    def test_catalog_is_unique_and_expected_statuses_are_known(self):
        self.assertEqual(len({item.scenario_id for item in SCENARIOS.values()}), len(SCENARIOS))
        from experiment_matrix import EXPECTED
        for scenario in SCENARIOS.values():
            self.assertIn(scenario.expected_status, EXPECTED)
            if scenario.injectable:
                self.assertIn(scenario.fault, ("area_mismatch", "admin_down", "ospf_passive"))
                self.assertIn(scenario.target, ("r1", "r2"))


if __name__ == "__main__":
    unittest.main()
