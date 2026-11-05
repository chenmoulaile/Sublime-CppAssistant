# -*- coding: utf-8 -*-
"""Tests for settings validation (invalid values are silently ignored by ST)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_config


class SettingsValidationTests(unittest.TestCase):
    def _keys(self, data):
        return [p["key"] for p in ca_config.validate(data)]

    def test_shipped_defaults_are_valid(self):
        data = {
            "cxx_standard": "c++14",
            "lint_engine": "compiler",
            "display_language": "en",
            "menu_language": "en",
            "lint_timeout": 12,
            "lint_debounce": 0.1,
            "indent_width": 4,
            "clangd_completion_wait_ms": 0,
            "compiler_extra_args": [],
            "include_paths": [],
            "clangd_args": [],
            "diagnostic_filters": [],
            "libclang_args": [],
            "clangd_extra_fallback_flags": [],
            "clang_format_style": "{ BasedOnStyle: Google }",
        }
        self.assertEqual(ca_config.validate(data), [])

    def test_unknown_standard_is_reported(self):
        self.assertIn("cxx_standard",
                      self._keys({"cxx_standard": "c++2x"}))

    def test_valid_standard_variants(self):
        for std in ("c++11", "c++17", "c++20", "c++23", "gnu++17", "c++2b"):
            self.assertEqual(
                self._keys({"cxx_standard": std}), [], std)

    def test_unknown_lint_engine_is_reported(self):
        self.assertIn("lint_engine", self._keys({"lint_engine": "gcc"}))

    def test_display_language_values(self):
        for lang in ("en", "zh", "both"):
            self.assertEqual(self._keys({"display_language": lang}), [], lang)
        self.assertIn("display_language",
                      self._keys({"display_language": "cn"}))

    def test_menu_language_values(self):
        self.assertIn("menu_language",
                      self._keys({"menu_language": "auto"}))

    def test_timeout_out_of_range(self):
        self.assertIn("lint_timeout", self._keys({"lint_timeout": 0}))
        self.assertIn("lint_timeout", self._keys({"lint_timeout": 999}))
        self.assertIn("lint_timeout", self._keys({"lint_timeout": "12"}))

    def test_wait_must_be_int(self):
        self.assertIn("clangd_completion_wait_ms",
                      self._keys({"clangd_completion_wait_ms": -1}))
        self.assertIn("clangd_completion_wait_ms",
                      self._keys({"clangd_completion_wait_ms": 1.5}))

    def test_list_settings_must_be_lists(self):
        self.assertIn("include_paths",
                      self._keys({"include_paths": "D:/inc"}))
        self.assertIn("compiler_extra_args",
                      self._keys({"compiler_extra_args": [1]}))

    def test_style_accepts_object(self):
        self.assertEqual(
            self._keys({"clang_format_style": {"BasedOnStyle": "Google"}}),
            [])

    def test_non_dict_root(self):
        problems = ca_config.validate([])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["key"], "(root)")

    def test_summary_and_report_text(self):
        problems = ca_config.validate({"lint_engine": "gcc"})
        self.assertTrue(ca_config.summary(problems))
        report = ca_config.report_text(problems)
        self.assertIn("lint_engine", report)
        self.assertIn("zh", report)
        self.assertEqual(ca_config.summary([]), u"")


if __name__ == "__main__":
    unittest.main()
