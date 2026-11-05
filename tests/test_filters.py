# -*- coding: utf-8 -*-
"""Tests for diagnostic filter normalisation."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_filters


class FilterTests(unittest.TestCase):
    def test_normalize_accepts_common_spellings(self):
        for value in ("-Wunused-variable", "Wno-unused-variable",
                      "-Wno-unused-variable", "Wunused-variable",
                      "unused-variable", "  -WUNUSED-VARIABLE  "):
            self.assertEqual(ca_filters.normalize_diagnostic_filter(value),
                             "unused-variable", value)

    def test_normalize_rejects_non_code_values(self):
        for value in ("", None, 0, "-W", "not a flag", "-Wfoo bar", "a;b"):
            self.assertEqual(ca_filters.normalize_diagnostic_filter(value), "")

    def test_codes_are_deduplicated_and_ordered(self):
        values = ["-Wunused-variable", "unused-variable",
                  "sign-compare", "-Wunused-variable"]
        self.assertEqual(ca_filters.diagnostic_filter_codes(values),
                         ["unused-variable", "sign-compare"])

    def test_flags_include_unused_warnings_by_default(self):
        flags = ca_filters.diagnostic_filter_flags([])
        self.assertIn("-Wno-unused-variable", flags)
        self.assertIn("-Wno-unused-but-set-variable", flags)

    def test_flags_can_turn_off_unused_warnings(self):
        flags = ca_filters.diagnostic_filter_flags(
            [], suppress_unused_warnings=False)
        self.assertEqual(flags, [])

    def test_flags_deduplicate(self):
        flags = ca_filters.diagnostic_filter_flags(
            ["-Wunused-variable"], suppress_unused_warnings=True)
        self.assertEqual(flags.count("-Wno-unused-variable"), 1)


if __name__ == "__main__":
    unittest.main()
