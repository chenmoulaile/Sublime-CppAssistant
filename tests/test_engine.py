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

    def test_offset_from_position_handles_utf16(self):
        text = u"a\U0001F600b\nc"
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 0, "character": 1}), 1)
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 0, "character": 3}), 2)
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 0, "character": 4}), 3)
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 1, "character": 1}), 5)

    def test_offset_from_position_clamps_and_ignores_cr(self):
        text = u"a\r\nb"
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 0, "character": 2}), 1)
        self.assertEqual(
            ca_clangd.offset_from_position(
                text, {"line": 9, "character": 0}), len(text))

    def test_apply_text_edits_uses_descending_offsets(self):
        text = u"one two three"
        edits = [
            {"range": {"start": {"line": 0, "character": 0},
                       "end": {"line": 0, "character": 3}},
             "newText": u"1"},
            {"range": {"start": {"line": 0, "character": 8},
                       "end": {"line": 0, "character": 13}},
             "newText": u"3"},
        ]
        self.assertEqual(
            ca_clangd.apply_text_edits(text, edits), u"1 two 3")

    def test_parse_workspace_edit_supports_document_changes(self):
        edit = {
            "documentChanges": [
                {
                    "textDocument": {"uri": "file:///a.cpp", "version": 1},
                    "edits": [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "newText": u"x",
                    }],
                },
                {"kind": "create", "uri": "file:///b.cpp"},
            ]
        }
        parsed = ca_clangd.parse_workspace_edit(edit)
        self.assertEqual(len(parsed["files"]), 1)
        self.assertEqual(parsed["files"][0][0], "file:///a.cpp")
        self.assertEqual(len(parsed["files"][0][1]), 1)
        self.assertEqual(len(parsed["resource_ops"]), 1)

    def test_parse_code_actions_reports_disabled_and_preferred(self):
        actions = ca_clangd.parse_code_actions([
            {"title": "Add include", "kind": "quickfix",
             "isPreferred": True, "edit": {"changes": {}}},
            {"title": "Unavailable",
             "disabled": {"reason": "no symbol"}},
        ])
        self.assertEqual(len(actions), 2)
        self.assertTrue(actions[0]["preferred"])
        self.assertEqual(actions[0]["kind"], "quickfix")
        self.assertEqual(actions[1]["disabled"], "no symbol")

    def test_parse_publish_diagnostics_uses_top_level_uri(self):
        uri, items = ca_clangd.parse_publish_diagnostics({
            "uri": "file:///a.cpp",
            "diagnostics": [{
                "range": {"start": {"line": 2, "character": 3},
                          "end": {"line": 2, "character": 5}},
                "severity": 2,
                "message": "unused variable",
            }],
        })
        self.assertEqual(uri, "file:///a.cpp")
        self.assertEqual(items, [(2, 3, 2, "unused variable")])

    def test_parse_locations_supports_location_links(self):
        locations = ca_clangd.parse_locations([
            {"uri": "file:///a.cpp",
             "range": {"start": {"line": 1, "character": 2},
                       "end": {"line": 1, "character": 5}}},
            {"targetUri": "file:///b.cpp",
             "targetRange": {"start": {"line": 3, "character": 0},
                             "end": {"line": 3, "character": 4}},
             "targetSelectionRange": {
                 "start": {"line": 3, "character": 1},
                 "end": {"line": 3, "character": 3}}},
        ])
        self.assertEqual(len(locations), 2)
        self.assertEqual(locations[0]["uri"], "file:///a.cpp")
        self.assertEqual(locations[1]["uri"], "file:///b.cpp")
        self.assertEqual(
            locations[1]["range"]["start"]["character"], 1)


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

    def test_format_code_range_only_changes_selected_lines(self):
        src = (u"int main() {\n"
               u"int x=1;\n"
               u"if(x){x=2;}\n"
               u"return 0;\n"
               u"}\n")
        start = src.index(u"int x=1;")
        end = src.index(u"\n", src.index(u"if(x){x=2;}"))
        out = ca_engine.format_code_range(src, start, end, 4)
        self.assertTrue(out.startswith(u"int main() {\n"))
        self.assertIn(u"    int x = 1;", out)
        self.assertIn(u"    if(x) {x = 2; }", out)
        self.assertTrue(out.endswith(u"return 0;\n}\n"))

    def test_format_code_range_keeps_text_outside_range(self):
        src = (u"void f(){\n"
               u"int  a=1;\n"
               u"}\n"
               u"void g(){\n"
               u"int  b=2;\n"
               u"}\n")
        start = src.index(u"int  b=2;")
        end = src.index(u"\n", start)
        out = ca_engine.format_code_range(src, start, end, 4)
        self.assertIn(u"int  a=1;", out)
        self.assertIn(u"    int b = 2;", out)


if __name__ == "__main__":
    unittest.main()
