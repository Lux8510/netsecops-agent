"""Wilson interval and small-sample flags."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from stats import rate, wilson  # noqa: E402


class WilsonTest(unittest.TestCase):
    def test_bounds_are_clamped(self):
        low, high = wilson(0, 5)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)
        low, high = wilson(5, 5)
        self.assertLess(low, 1.0)
        self.assertEqual(high, 1.0)

    def test_zero_and_all_success_are_not_degenerate(self):
        low, high = wilson(0, 1)
        self.assertEqual(low, 0.0)
        self.assertLess(high, 0.8)
        low, high = wilson(1, 1)
        self.assertGreater(low, 0.2)

    def test_more_samples_narrow_the_interval(self):
        wide = wilson(8, 10)
        narrow = wilson(80, 100)
        self.assertGreater(narrow[0] - wide[0], 0)
        self.assertGreater(wide[1] - narrow[1], 0)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            wilson(0, 0)
        with self.assertRaises(ValueError):
            wilson(2, 1)

    def test_empty_sample_has_a_null_rate(self):
        result = rate(0, 0)
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["rate"])
        self.assertIsNone(result["ci95_low"])
        self.assertTrue(result["underpowered"])

    def test_rate_marks_underpowered(self):
        self.assertTrue(rate(3, 3, minimum=5)["underpowered"])
        self.assertFalse(rate(50, 50, minimum=5)["underpowered"])


if __name__ == "__main__":
    unittest.main()
