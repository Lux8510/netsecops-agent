"""A/B comparison over saved traces, with token and latency budgets."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
FIXTURES = LAB_DIR / "tests" / "fixtures"
sys.path.insert(0, str(LAB_DIR))

from ab_compare import compare, render_markdown, variant_summary  # noqa: E402


def write_variant(root: Path, name: str, trace_path: Path, tokens: int | None = None,
                  seconds: float | None = None, status: str | None = None) -> Path:
    directory = root / name
    directory.mkdir()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if tokens is not None:
        trace["usage_total"] = {"total_tokens": tokens}
    if seconds is not None:
        trace["elapsed_seconds"] = seconds
    if status is not None:
        trace["final"]["status"] = status
    (directory / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    manifest = {"runs": [{"trace": "trace.json", "scenario": "normal", "expected": "normal"}]}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory / "manifest.json"


class CompareTest(unittest.TestCase):
    def test_compares_accuracy_tokens_and_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = write_variant(root, "chat", FIXTURES / "traces/normal.json",
                                 tokens=10_000, seconds=4.0)
            cheap = write_variant(root, "cheap", FIXTURES / "traces/normal.json",
                                  tokens=100, seconds=1.0)
            summary = compare({"chat": base, "cheap": cheap}, max_tokens=500)
            by_name = {item["variant"]: item for item in summary["variants"]}
            self.assertEqual(by_name["chat"]["over_budget_rate"], 1.0)
            self.assertEqual(by_name["cheap"]["over_budget_rate"], 0.0)
            self.assertEqual(by_name["chat"]["mean_tokens"], 10_000)
            self.assertIn("chat", render_markdown(summary))

    def test_ci_is_reported_for_small_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = write_variant(Path(temp), "one", FIXTURES / "traces/normal.json")
            summary = variant_summary("one", manifest)
            self.assertEqual(summary["runs"], 1)
            self.assertTrue(summary["root_cause_accuracy_ci95"]["underpowered"])

    def test_missing_telemetry_is_not_counted_as_within_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trace = json.loads((FIXTURES / "traces/normal.json").read_text(encoding="utf-8"))
            trace.pop("usage_total", None)
            directory = root / "no-usage"
            directory.mkdir()
            (directory / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
            (directory / "manifest.json").write_text(json.dumps(
                {"runs": [{"trace": "trace.json", "scenario": "normal"}]}), encoding="utf-8")
            summary = variant_summary("no-usage", directory / "manifest.json", max_tokens=10)
            self.assertIsNone(summary["over_budget_rate"])
            self.assertEqual(summary["missing_telemetry_rate"], 1.0)

    def test_same_labels_different_samples_are_not_paired(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = write_variant(root, "first", FIXTURES / "traces/normal.json")
            second = write_variant(root, "second", FIXTURES / "traces/normal.json")
            # Same labels, different trace bytes: mutate only a non-evidence field.
            trace_path = root / "second/trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            first_key = next(iter(trace["evidence"]))
            trace["evidence"][first_key]["data"]["lab"] = "different-sample"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            from experiment_matrix import MatrixError
            with self.assertRaises(MatrixError):
                compare({"first": first, "second": second})

    def test_pairing_ignores_manifest_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = write_variant(root, "first", FIXTURES / "traces/normal.json")
            second = write_variant(root, "second", FIXTURES / "traces/normal.json")
            for manifest in (first, second):
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["runs"] = list(reversed(data["runs"]))
                manifest.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(compare({"first": first, "second": second})["paired"])

    def test_unpaired_variants_are_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one = write_variant(root, "one", FIXTURES / "traces/normal.json")
            directory = root / "two"
            directory.mkdir()
            (directory / "trace.json").write_text(
                (FIXTURES / "traces/stage5-case-01.json").read_text(encoding="utf-8"), encoding="utf-8")
            (directory / "manifest.json").write_text(json.dumps(
                {"runs": [{"trace": "trace.json", "scenario": "area_mismatch"}]}), encoding="utf-8")
            from experiment_matrix import MatrixError
            with self.assertRaises(MatrixError):
                compare({"one": one, "two": directory / "manifest.json"})
            summary = compare({"one": one, "two": directory / "manifest.json"}, allow_unpaired=True)
            self.assertFalse(summary["paired"])

    def test_wrong_variant_shows_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = write_variant(root, "good", FIXTURES / "traces/normal.json")
            bad = write_variant(root, "bad", FIXTURES / "traces/normal.json", status="ospf_area_mismatch")
            summary = compare({"good": good, "bad": bad})
            by_name = {item["variant"]: item for item in summary["variants"]}
            self.assertEqual(by_name["good"]["root_cause_accuracy"], 1.0)
            self.assertEqual(by_name["bad"]["root_cause_accuracy"], 0.0)
            self.assertTrue(by_name["bad"]["failures"])


if __name__ == "__main__":
    unittest.main()
