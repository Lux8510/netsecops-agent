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
