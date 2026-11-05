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

    def test_parse_call_hierarchy_items(self):
        result = [{
            "name": "solve",
            "detail": "void ()",
            "uri": "file:///tmp/a.cpp",
            "selectionRange": {"start": {"line": 4, "character": 5}},
        }]
        parsed = ca_clangd.parse_call_hierarchy_items(result)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "solve")
        self.assertEqual(parsed[0]["line"], 5)
        self.assertEqual(parsed[0]["character"], 6)
        self.assertIs(parsed[0]["raw"], result[0])

    def test_parse_call_hierarchy_skips_incomplete_items(self):
        result = [{"name": "no_uri"}, {"uri": "file:///a"}, "junk", None]
        self.assertEqual(ca_clangd.parse_call_hierarchy_items(result), [])

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
        self.assertEqual(items, [(2, 3, 2, "unused variable", "")])

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
        # Test that keyword completions have English detail
        results = ca_engine.analyze("for", 3)
        by_trigger = dict((r["trigger"], r) for r in results)
        self.assertIn("for", by_trigger)
        self.assertTrue(by_trigger["for"]["detail_en"])

    def test_local_variable_details_and_ranking(self):
        src = (u"int add(int x, int y) {\n"
               u"    int total = x + y;\n"
               u"    tot\n"
               u"}\n")
        offset = src.index(u"tot") + len(u"tot")
        items = ca_engine.analyze(src, offset)
        by_trigger = dict((r["trigger"], r) for r in items)
        self.assertIn("total", by_trigger)
        entry = by_trigger["total"]
        self.assertEqual(entry["detail"], u"int")
        self.assertEqual(entry["detail_en"], u"int")
        self.assertEqual(entry["kind"], "v")
        self.assertEqual(entry["annotation"], u"局部变量")
        self.assertEqual(entry["annotation_en"], u"local variable")
        self.assertEqual(items[0]["trigger"], "total")

    def test_parameter_details(self):
        src = (u"int add(int left, int right) {\n"
               u"    le\n"
               u"}\n")
        offset = src.index(u"le") + len(u"le")
        items = ca_engine.analyze(src, offset)
        by_trigger = dict((r["trigger"], r) for r in items)
        self.assertIn("left", by_trigger)
        entry = by_trigger["left"]
        self.assertEqual(entry["detail"], u"int")
        self.assertEqual(entry["kind"], "v")
        self.assertEqual(entry["annotation"], u"参数")
        self.assertEqual(entry["annotation_en"], u"parameter")

    def test_user_function_signature_detail(self):
        src = (u"void solve(int n) {\n"
               u"    sol\n"
               u"}\n")
        offset = src.index(u"sol") + len(u"sol")
        items = ca_engine.analyze(src, offset)
        by_trigger = dict((r["trigger"], r) for r in items)
        self.assertIn("solve", by_trigger)
        entry = by_trigger["solve"]
        self.assertEqual(entry["detail"], u"void solve(int n)")
        self.assertEqual(entry["kind"], "f")
        self.assertEqual(entry["annotation"], u"自定义函数")
        self.assertEqual(entry["annotation_en"], u"function")

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


class MemberSignatureTests(unittest.TestCase):
    def _detail(self, src, trigger):
        items = ca_engine.analyze(src, len(src))
        for item in items:
            if item["trigger"] == trigger:
                return item["detail"]
        self.fail("no completion for %r" % trigger)

    def test_sequence_push_back(self):
        self.assertEqual(
            self._detail(u"vector<int> v; v.", "push_back"),
            u"push_back(int) · O(1)")
        self.assertEqual(
            self._detail(u"vector<pair<int,int>> v; v.", "push_back"),
            u"push_back(pair<int,int>) · O(1)")
        self.assertEqual(
            self._detail(u"vector<vector<int>> v; v.", "push_back"),
            u"push_back(vector<int>) · O(1)")

    def test_map_signatures(self):
        self.assertEqual(
            self._detail(u"map<string,int> m; m.", "at"),
            u"at(const string&) · O(log n)")
        self.assertEqual(
            self._detail(u"map<string,int> m; m.", "find"),
            u"find(const string&) · O(log n)")
        self.assertEqual(
            self._detail(u"unordered_map<long long, vector<int>> m; m.",
                         "emplace"),
            u"emplace(long long, vector<int>) · O(1) avg")

    def test_set_find_uses_key_type(self):
        self.assertEqual(
            self._detail(u"set<long long> s; s.", "find"),
            u"find(const long long&) · O(log n)")

    def test_string_and_adapter_signatures(self):
        self.assertEqual(
            self._detail(u"string s; s.", "push_back"),
            u"push_back(char) · O(1)")
        self.assertEqual(
            self._detail(u"queue<int> q; q.", "push"),
            u"push(int) · O(1)")

    def test_array_fill_signature(self):
        self.assertEqual(
            self._detail(u"array<int, 3> a; a.", "fill"),
            "fill(const int&)")

    def test_pbds_tree_signatures(self):
        src = (u"__gnu_pbds::tree<int, null_type, less<int>, rb_tree_tag, "
               u"tree_order_statistics_node_update> s; s.")
        self.assertEqual(self._detail(src, "order_of_key"),
                        u"order_of_key(const int&) · O(log n)")
        self.assertEqual(self._detail(src, "find_by_order"),
                        u"find_by_order(size_t) · O(log n)")
        self.assertEqual(self._detail(src, "find"),
                        u"find(const int&) · O(log n)")

    def test_pbds_hash_map_signatures(self):
        src = u"gp_hash_table<long long, string> h; h."
        self.assertEqual(self._detail(src, "operator[]"),
                         u"operator[](const long long&) · O(1) avg")
        self.assertEqual(self._detail(src, "insert"),
                         u"insert({const long long&, string}) · O(1) avg")

    def test_split_args_keeps_nested_templates(self):
        args = ca_engine._split_args(
            u"string, vector<pair<int,int>>")
        self.assertEqual(args, [u"string", u"vector<pair<int,int>>"])


class CpDatabaseTests(unittest.TestCase):
    """PBDS namespace, ranges/concepts and include details."""

    def _items(self, src):
        """``|`` 标记光标位置；没有标记时用文本末尾。"""
        if u"|" in src:
            offset = src.index(u"|")
            src = src[:offset] + src[offset + 1:]
        else:
            offset = len(src)
        items = ca_engine.analyze(src, offset)
        return dict((r["trigger"], r) for r in items)

    def test_pbds_namespace_lists_short_triggers(self):
        by_trigger = self._items(u"int main() {\n__gnu_pbds::|\n}\n")
        for name in ("tree", "gp_hash_table", "cc_hash_table",
                     "rb_tree_tag", "splay_tree_tag", "ov_tree_tag",
                     "tree_order_statistics_node_update"):
            self.assertIn(name, by_trigger)
        self.assertTrue(by_trigger["tree"]["insert"].startswith("tree<"))
        self.assertEqual(by_trigger["tree"]["kind"], "t")
        # __gnu_pbds is not std: no "std::" prefix may be added
        self.assertFalse(by_trigger["tree"]["insert"].startswith("std::"))

    def test_pbds_namespace_works_with_pb_ds_alias(self):
        by_trigger = self._items(u"int main() {\npb_ds::|\n}\n")
        self.assertIn("tree", by_trigger)

    def test_ranges_and_views_entries_exist(self):
        by_trigger = self._items(u"ranges::s")
        self.assertIn("sort", by_trigger)
        entry = by_trigger["sort"]
        self.assertEqual(entry["insert"], "sort(${1:v})")
        self.assertIn("C++20", entry["annotation"])
        # later-standard entries must carry an English annotation too
        self.assertTrue(entry["annotation_en"])

    def test_views_scope_lists_only_views(self):
        by_trigger = self._items(u"views::t")
        self.assertIn("take", by_trigger)
        self.assertEqual(by_trigger["take"]["insert"], "take(${1:v}, ${2:n})")
        self.assertNotIn("sort", by_trigger)

    def test_concepts_have_explicit_triggers(self):
        by_trigger = self._items(u"std::integr")
        self.assertIn("integral", by_trigger)
        self.assertTrue(
            by_trigger["integral"]["insert"].startswith("integral"))

    def test_include_completion_has_human_details(self):
        by_trigger = self._items(u"#include <ext/pb_ds/assoc")
        entry = by_trigger["ext/pb_ds/assoc_container.hpp"]
        self.assertIn("PBDS", entry["detail"])
        self.assertNotEqual(entry["detail"], "#include "
                            "ext/pb_ds/assoc_container.hpp")

    def test_bits_header_detail_explains_pbds_gap(self):
        by_trigger = self._items(u"#include <bits/std")
        self.assertIn("PBDS", by_trigger["bits/stdc++.h"]["detail"])

    def test_macro_has_its_own_kind(self):
        src = (u"#define MAXN 100\n"
               u"int main() { int x = MAX; }\n")
        offset = src.index(u"MAX;") + 3
        by_trigger = dict((r["trigger"], r) for r in
                          ca_engine.analyze(src, offset))
        self.assertEqual(by_trigger["MAXN"]["kind"], "d")

    def test_auto_iterator_binding_gets_iterator_type(self):
        src = (u"vector<int> v;\n"
               u"int main() {\n"
               u"    auto it = v.find(1);\n"
               u"}\n")
        env = ca_engine.build_env(src)
        self.assertEqual(env["var_types"]["it"], "vector<int>::iterator")

    def test_inline_auto_binding_gets_iterator_type(self):
        src = (u"vector<int> v;\n"
               u"int main() { auto it = v.begin(); }\n")
        env = ca_engine.build_env(src)
        self.assertEqual(env["var_types"]["it"], "vector<int>::iterator")

    def test_param_types_are_exposed(self):
        src = u"void solve(int n, vector<int>& a) { }\n"
        env = ca_engine.build_env(src)
        self.assertEqual(env["param_types"]["n"], u"int")
        self.assertEqual(env["param_types"]["a"], u"vector<int>&")


if __name__ == "__main__":
    unittest.main()
