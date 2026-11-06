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


class PackageIntegrityTests(unittest.TestCase):
    """Everything referenced by commands/menu/keymap must actually exist."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read("CppAssistant.py")
        cls.class_names = set(re.findall(r"^class\s+(\w+)",
                                         cls.source, re.M))

    def _class_for(self, command):
        # ca_fix_fullwidth_punctuation -> CaFixFullwidthPunctuationCommand
        name = command[3:] if command.startswith("ca_") else command
        parts = [p for p in name.split("_") if p]
        return "Ca" + "".join(p[:1].upper() + p[1:] for p in parts) + \
            "Command"

    def test_commands_exist_as_classes(self):
        data = json.loads(_read("CppAssistant.sublime-commands"))
        for entry in data:
            command = entry["command"]
            if not command.startswith("ca_"):
                continue
            cls = self._class_for(command)
            self.assertIn(cls, self.class_names, command)

    def test_menu_commands_exist(self):
        data = json.loads(_read("Main.sublime-menu"))
        stack = [data]
        checked = 0
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            command = node.get("command")
            if isinstance(command, str) and command.startswith("ca_"):
                checked += 1
                self.assertIn(self._class_for(command), self.class_names,
                              command)
            for value in node.values():
                if isinstance(value, (list, dict)):
                    stack.append(value)
        self.assertGreater(checked, 5)

    def test_menu_ids_are_unique(self):
        data = json.loads(_read("Main.sublime-menu"))
        ids = re.findall(r'"id"\s*:\s*"([^"]+)"', _read("Main.sublime-menu"))
        self.assertEqual(len(ids), len(set(ids)))
        # the parsed tree must be a list of top-level menus
        self.assertIsInstance(data, list)

    def test_keymap_commands_exist(self):
        text = _read("Example.sublime-keymap")
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        data = json.loads(text)
        for entry in data:
            command = entry.get("command") or ""
            if command.startswith("ca_"):
                self.assertIn(self._class_for(command), self.class_names,
                              command)

    def test_new_settings_have_defaults(self):
        data = _load_sublime_settings("CppAssistant.sublime-settings")
        self.assertIs(data["cpp_auto_insert_pbds_includes"], True)
        self.assertIs(data["show_status_hint"], True)

    def test_messages_json_is_valid(self):
        """Package Control joins each value onto the package dir and opens it.

        A previous release stored the whole message text as the value, so
        ``open()`` received a paragraph with newlines and colons and raised
        ``OSError: [Errno 22] Invalid argument`` on Windows, aborting the
        install. Keep the values as real in-package file paths.
        """
        data = json.loads(_read("messages.json"))
        self.assertIn("install", data)
        root = os.path.realpath(ROOT)
        for key, value in data.items():
            self.assertIsInstance(key, str)
            self.assertTrue(key == "install" or
                            re.match(r"^\d+\.\d+\.\d+$", key), key)
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip(), key)
            self.assertNotIn("\n", value, key)
            self.assertNotIn("\r", value, key)
            self.assertFalse(os.path.isabs(value), key)
            self.assertNotIn("..", value.split("/"), key)
            for char in '<>:"|?*':
                self.assertNotIn(char, value, key)
            self.assertTrue(value.endswith((".txt", ".md")), key)
            path = os.path.join(ROOT, value)
            self.assertTrue(os.path.isfile(path), key)
            self.assertTrue(os.path.realpath(path).startswith(root), key)
            with open(path, "r", encoding="utf-8") as fobj:
                self.assertTrue(fobj.read().strip(), key)

    def test_package_control_can_read_every_message(self):
        """Reproduce package_control.package_manager.read_message()."""
        data = json.loads(_read("messages.json"))
        for version, rel_path in data.items():
            path = os.path.join(ROOT, rel_path)
            with open(path, "r", encoding="utf-8", errors="replace") as fobj:
                text = fobj.read()
            self.assertIn("CppAssistant", text, version)

    def test_build_does_not_exclude_message_files(self):
        workflow = _read(os.path.join(".github", "workflows", "tests.yml"))
        self.assertNotIn('-x "messages', workflow)

    def test_cli_commands_documented_in_commands_file(self):
        data = json.loads(_read("CppAssistant.sublime-commands"))
        commands = set(entry["command"] for entry in data)
        for command in ("ca_fix_fullwidth_punctuation",
                        "ca_browse_templates",
                        "ca_manage_user_snippets",
                        "ca_call_hierarchy",
                        "ca_validate_settings"):
            self.assertIn(command, commands)


class CompletionDatabaseIntegrityTests(unittest.TestCase):
    """Guard the built-in completion data against silent regressions."""

    @classmethod
    def setUpClass(cls):
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from cppassistant import ca_stdlib_data
        cls.data = ca_stdlib_data

    def test_no_duplicate_member_triggers(self):
        for container, members in self.data.MEMBERS_DB.items():
            triggers = [m[0].split("(")[0] for m in members]
            self.assertEqual(len(triggers), len(set(triggers)), container)

    def test_every_member_has_english_detail(self):
        missing = []
        for container, members in self.data.MEMBERS_DB.items():
            for comp, _ann, _kind in members:
                if not self.data.en_detail(comp, container) and \
                        not self.data.en_detail(comp):
                    missing.append((container, comp))
        self.assertEqual(missing, [])

    def test_std_triggers_are_unique(self):
        triggers = [item["trigger"] for item in self.data.STD_ITEMS_ALL]
        self.assertEqual(len(triggers), len(set(triggers)))

    def test_every_std_item_has_english_annotation(self):
        missing = [item["insert"] for item in self.data.STD_ITEMS_ALL
                   if not self.data.en_detail(item["insert"])]
        self.assertEqual(missing, [])

    def test_pbds_headers_are_complete(self):
        self.assertIn("ext/pb_ds/assoc_container.hpp",
                      self.data.PBDS_HEADERS)
        self.assertIn("ext/pb_ds/tree_policy.hpp",
                      self.data.PBDS_HEADERS)

    def test_header_details_cover_cp_headers(self):
        for header in ("bits/stdc++.h", "ext/pb_ds/assoc_container.hpp",
                       "ext/pb_ds/tree_policy.hpp", "ranges", "bit"):
            detail = self.data.header_detail(header)
            self.assertTrue(detail.startswith("#include <"), header)
            self.assertIn(header, detail)


if __name__ == "__main__":
    unittest.main()
