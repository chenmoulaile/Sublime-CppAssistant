# -*- coding: utf-8 -*-
"""Packaging sanity checks: metadata files stay valid and in sync."""

import json
import os
import re
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
                    "display_language", "lint_engine", "cxx_standard",
                    "include_paths", "clang_format_style"):
            self.assertIn(key, data)

    def test_commands_parse_and_have_unique_captions(self):
        data = json.loads(_read("CppAssistant.sublime-commands"))
        captions = [entry["caption"] for entry in data]
        self.assertEqual(len(captions), len(set(captions)))
        commands = set(entry["command"] for entry in data)
        self.assertIn("ca_format_document", commands)
        self.assertIn("ca_goto_definition", commands)

    def test_menu_has_no_duplicate_settings_entry(self):
        data = json.loads(_read("Main.sublime-menu"))
        package = data[0]["children"][0]["children"][0]
        captions = [child.get("caption") for child in package["children"]]
        self.assertEqual(captions.count("Settings"), 1)
        self.assertNotIn("Settings – User", captions)

    def test_readme_has_no_changelog_section(self):
        readme = _read("README.md")
        self.assertNotIn("## 更新日志", readme)
        self.assertNotIn("## Changelog", readme)
        self.assertIn("CHANGELOG.md", readme)

    def test_readme_mentions_current_python_host(self):
        readme = _read("README.md")
        self.assertIn("3.14", readme)
        self.assertIn("3.3", readme)


if __name__ == "__main__":
    unittest.main()
