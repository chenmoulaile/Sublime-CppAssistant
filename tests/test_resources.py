# -*- coding: utf-8 -*-
"""Packaging sanity checks: metadata files stay valid and in sync."""

import ast
import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as f:
        return f.read()


def _load_sublime_settings(name):
    text = _read(name)
    # The shipped settings file only uses full-line // comments.
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return json.loads(text)


class ResourceTests(unittest.TestCase):
    def test_settings_parse_and_have_expected_keys(self):
        data = _load_sublime_settings("CppAssistant.sublime-settings")
        for key in ("enable_completions", "enable_clangd_engine",
                    "enable_fast_engine", "enable_libclang_engine",
                    "libclang_path", "libclang_args", "libclang_pch",
                    "libclang_completion", "libclang_goto",
                    "display_language", "lint_engine",
                    "cxx_standard", "include_paths", "clang_format_style"):
            self.assertIn(key, data)
        self.assertIn(data["lint_engine"], ("compiler", "clangd", "libclang"))

    def test_commands_parse_and_have_unique_captions(self):
        data = json.loads(_read("CppAssistant.sublime-commands"))
        captions = [entry["caption"] for entry in data]
        self.assertEqual(len(captions), len(set(captions)))
        commands = set(entry["command"] for entry in data)
        self.assertIn("ca_format_document", commands)
        self.assertIn("ca_goto_definition", commands)
        lint_engines = set(
            entry.get("args", {}).get("engine")
            for entry in data if entry["command"] == "ca_set_lint_engine")
        self.assertIn("libclang", lint_engines)

    def test_menu_has_no_duplicate_settings_entry(self):
        data = json.loads(_read("Main.sublime-menu"))
        package = data[0]["children"][0]["children"][0]
        captions = [child.get("caption") for child in package["children"]]
        self.assertEqual(captions.count("Settings"), 1)
        self.assertNotIn("Settings – User", captions)
        text = _read("Main.sublime-menu")
        self.assertIn('"engine": "libclang"', text)

    def test_readme_has_no_changelog_section(self):
        readme = _read("README.md")
        self.assertNotIn("## 更新日志", readme)
        self.assertNotIn("## Changelog", readme)
        self.assertIn("CHANGELOG.md", readme)

    def test_readme_mentions_current_python_host(self):
        readme = _read("README.md")
        self.assertIn("3.14", readme)
        self.assertIn("3.3", readme)

    def test_python33_syntax_compatibility(self):
        """Older Sublime Text 4 builds embed Python 3.3."""
        if sys.version_info < (3, 8):
            self.skipTest("ast.parse(feature_version=...) needs Python 3.8+")
        files = [os.path.join(ROOT, "CppAssistant.py")]
        files.extend(glob.glob(os.path.join(ROOT, "cppassistant", "*.py")))
        for path in files:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            ast.parse(source, filename=path, feature_version=(3, 3))


if __name__ == "__main__":
    unittest.main()
