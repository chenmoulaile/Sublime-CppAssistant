# -*- coding: utf-8 -*-
"""Unit tests for the pure-Python engine and clangd protocol helpers.

Run from the repository root:

    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_clangd
from cppassistant import ca_engine


class ClangdHelperTests(unittest.TestCase):
    def test_short_label_keeps_operator_names(self):
        self.assertEqual(ca_clangd._short_label("operator<=>"), "operator<=>")
        self.assertEqual(ca_clangd._short_label("operator<<"), "operator<<")
        self.assertEqual(
            ca_clangd._short_label("push_back(const value_type &x)"),
            "push_back")

    def test_extract_includes_handles_multiple_lines(self):
        item = {
            "additionalTextEdits": [{
                "newText": "#include <vector>\n#include <utility>\n",
            }]
        }
        self.assertEqual(
            ca_clangd._extract_includes(item), ["vector", "utility"])

    def test_extract_includes_deduplicates(self):
        item = {
            "additionalTextEdits": [
                {"newText": "#include <vector>\n"},
                {"newText": "#include <vector>\n"},
            ]
        }
        self.assertEqual(ca_clangd._extract_includes(item), ["vector"])

    def test_clamp_insert_uses_text_edit_format(self):
        item = {
            "label": "f",
            "textEdit": {"newText": "f(${1:x})", "insertTextFormat": 2},
        }
        text, is_snippet = ca_clangd._clamp_insert(item)
        self.assertEqual(text, "f(${1:x})")
        self.assertTrue(is_snippet)

    def test_signature_help_skips_empty_signature(self):
        result = {
            "signatures": [
                {"label": ""},
                {"label": "void f(int x)"},
            ],
            "activeSignature": 1,
            "activeParameter": 0,
        }
        parsed = ca_clangd.parse_signature_help(result)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["active"], "void f(int x)")
        self.assertEqual(parsed["lines"], [("void f(int x)", True)])

    def test_norm_uri_is_case_sensitive_off_windows(self):
        if os.name == "nt":
            self.skipTest("Windows paths are case-insensitive")
        self.assertNotEqual(
            ca_clangd._norm_uri("file:///tmp/Foo.cpp"),
            ca_clangd._norm_uri("file:///tmp/foo.cpp"))


class EngineTests(unittest.TestCase):
    def test_user_symbols_keep_both_languages(self):
        syms = ca_engine.user_symbols(
            "struct Foo {};\nvoid bar() {}\n#define M 1\n")
        self.assertEqual(syms["Foo"], ("type", u"结构体/类"))
        self.assertEqual(syms["bar"], ("function", u"自定义函数"))
        self.assertEqual(syms["M"], ("macro", u"宏定义"))

    def test_completion_details_are_bilingual(self):
        results = ca_engine.analyze("std::vec", 8)
        by_trigger = dict((r["trigger"], r) for r in results)
        entry = by_trigger["vector<${1:int}>"]
        self.assertEqual(entry["detail_en"], entry["detail"])

    def test_snippet_completion_has_english_detail(self):
        results = ca_engine.analyze("us", 2)
        by_trigger = dict((r["trigger"], r) for r in results)
        self.assertIn("us", by_trigger)
        self.assertTrue(by_trigger["us"]["detail_en"])

    def test_compiler_notes_attach_to_previous_diagnostic(self):
        output = (
            "a.cpp:3:5: warning: use of undeclared identifier 'x'\n"
            "a.cpp:3:5: note: did you mean 'y'?\n"
            "a.cpp:4:1: error: expected ';' before 'return'\n"
        )
        entries = ca_engine.parse_compiler_output(output)
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(entries[0]["notes"]), 1)
        self.assertEqual(entries[0]["notes"][0]["msg"], "did you mean 'y'?")
        self.assertEqual(entries[1]["sev_en"], "error")

    def test_standalone_note_is_ignored(self):
        entries = ca_engine.parse_compiler_output(
            "a.cpp:1:1: note: unrelated note\n")
        self.assertEqual(entries, [])

    def test_find_definitions_in_include_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = os.path.join(tmp, "foo.h")
            with open(header, "w", encoding="utf-8") as f:
                f.write("struct Foo {\n    int value;\n};\n")
            hits = ca_engine.find_definitions_in_files("Foo", [tmp])
            self.assertTrue(hits)
            self.assertEqual(os.path.normcase(hits[0][0]),
                             os.path.normcase(header))

    def test_find_system_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = os.path.join(tmp, "vector")
            with open(header, "w", encoding="utf-8") as f:
                f.write("// stub\n")
            self.assertEqual(
                ca_engine.find_system_header("vector", [tmp]), header)

    def test_std_fallback_can_use_system_include_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = os.path.join(tmp, "vector")
            with open(header, "w", encoding="utf-8") as f:
                f.write("// stub\n")
            hits = ca_engine.find_definition_std_fallback("vector", [tmp])
            self.assertTrue(hits)
            self.assertEqual(hits[0]["path"], header)


if __name__ == "__main__":
    unittest.main()
