# -*- coding: utf-8 -*-
"""Unit tests for the zero-dependency FastEngine (cppassistant/ca_fast.py).

Run from the repository root:

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_fast


SAMPLE = u"""\
#include <bits/stdc++.h>
using namespace std;

#define rep(i,a,b) for (int i=(a);i<=(b);++i)
using ll = long long;
typedef pair<int, int> pii;

namespace ns {
int K = 7;

struct Base {
    int b;
};

class Box : public Base {
public:
    T value;
    Box(T v) : value(v) {}
    T get() { return value; }
    int count;
};

template <typename T>
T twice(T x) { return x * 2; }
}

enum class Color { Red, Green, Blue };

int main() {
    ns::Box<int> box;
    ll x = 1;
    Color c = Color::Red;
    for (int i = 0; i < 10; ++i) {
        x += i;
    }
    return 0;
}
"""


class LexerTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def test_token_kinds_and_positions(self):
        a = self.engine.analyze(u"int main() {\n    return 0;\n}\n")
        kinds = [t.kind for t in a.tokens]
        self.assertEqual(kinds[0], "kw")
        self.assertEqual(a.tokens[0].line, 1)
        self.assertEqual(a.tokens[0].col, 0)
        ret = [t for t in a.tokens if t.kind == "kw"
               and a.text[t.start:t.end] == "return"][0]
        self.assertEqual(ret.line, 2)
        self.assertEqual(ret.col, 4)
        num = [t for t in a.tokens if t.kind == "num"][0]
        self.assertEqual(a.text[num.start:num.end], "0")

    def test_comments_and_literals_are_skipped(self):
        src = u'// int hidden;\nchar *s = "int not_a_token";\n/* int x; */\n'
        a = self.engine.analyze(src)
        words = [a.text[t.start:t.end] for t in a.tokens]
        self.assertNotIn("hidden", words)
        self.assertNotIn("not_a_token", words)
        self.assertEqual(len(a.comment_ranges), 2)
        self.assertTrue(a.in_comment(src.index("hidden")))
        self.assertFalse(a.in_comment(src.index("char")))

    def test_raw_string_spans_lines(self):
        src = u'auto s = R"tag(line1\nline2)tag";\nint x;\n'
        a = self.engine.analyze(src)
        x = [t for t in a.tokens if a.text[t.start:t.end] == "x"][0]
        self.assertEqual(x.line, 3)
        self.assertFalse([d for d in a.diags if d.code == "unterminated-raw"])

    def test_crlf_line_numbers(self):
        src = u"int a;\r\nint b;\r\nint c;\r\n"
        a = self.engine.analyze(src)
        c = [t for t in a.tokens if a.text[t.start:t.end] == "c"][0]
        self.assertEqual(c.line, 3)

    def test_preprocessor_is_one_token(self):
        src = u"#define F(x) \\\n  ((x) + 1)\nint y;\n"
        a = self.engine.analyze(src)
        pp = [t for t in a.tokens if t.kind == "pp"]
        self.assertEqual(len(pp), 1)
        self.assertIn("F(x)", a.text[pp[0].start:pp[0].end])
        y = [t for t in a.tokens if a.text[t.start:t.end] == "y"][0]
        self.assertEqual(y.line, 3)

    def test_numbers_include_suffixes_and_exponents(self):
        src = u"auto a = 1e-9; auto b = 0xFFu; auto c = 1'000;"
        a = self.engine.analyze(src)
        nums = [a.text[t.start:t.end] for t in a.tokens if t.kind == "num"]
        self.assertEqual(nums, [u"1e-9", u"0xFFu", u"1'000"])


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()
        self.a = self.engine.analyze(SAMPLE)

    def _kind(self, name):
        return [s.kind for s in self.a.by_name.get(name, [])]

    def test_namespace_and_includes(self):
        self.assertEqual(self._kind("ns"), ["namespace"])
        self.assertIn("bits/stdc++.h", self.a.includes)
        self.assertTrue(self.a.using_std)

    def test_types_and_members(self):
        self.assertIn("type", self._kind("Box"))
        self.assertIn("type", self._kind("Base"))
        self.assertIn("member variable", self._kind("value"))
        self.assertIn("member variable", self._kind("b"))
        self.assertIn("method", self._kind("get"))
        self.assertIn("Box", self.a.type_scopes)
        self.assertIn("Base", self.a.type_scopes["Box"].base_types)

    def test_function_parameters_and_locals(self):
        self.assertIn("function", self._kind("main"))
        self.assertIn("parameter", self._kind("x"))
        self.assertIn("variable", self._kind("box"))
        self.assertIn("variable", self._kind("x"))
        self.assertIn("variable", self._kind("i"))
        twice = [s for s in self.a.by_name["twice"]
                 if s.kind == "function"][0]
        self.assertEqual([p.name for p in twice.params], ["x"])
        self.assertTrue(twice.is_definition)

    def test_aliases_macros_and_templates(self):
        self.assertIn("alias", self._kind("ll"))
        self.assertIn("alias", self._kind("pii"))
        self.assertIn("macro", self._kind("rep"))
        self.assertIn("template type", self._kind("T"))

    def test_enum_class_enumerators(self):
        self.assertIn("enum", self._kind("Color"))
        for name in ("Red", "Green", "Blue"):
            self.assertIn("enumerator", self._kind(name))
        self.assertIn("Color", self.a.type_scopes)

    def test_qualified_type_recorded(self):
        box = [s for s in self.a.by_name["box"]
               if s.kind == "variable"][0]
        self.assertIn("Box", box.type)
        self.assertIn("int", box.type)

    def test_constructor_initializer_list(self):
        ctor = [s for s in self.a.by_name["Box"]
                if s.kind == "method"][0]
        self.assertTrue(ctor.is_definition)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def _triggers(self, text, offset, prefix=None):
        items = self.engine.complete(text, offset, prefix=prefix)
        return [d["trigger"] for d in items]

    def test_locals_and_parameters(self):
        src = (u"int add(int left, int right) {\n"
               u"    int total = left + right;\n"
               u"    \n"
               u"}\n")
        offset = src.index(u"    \n", src.index(u"int total")) + 4
        triggers = self._triggers(src, offset)
        self.assertIn("total", triggers)
        self.assertIn("left", triggers)
        self.assertIn("right", triggers)

    def test_member_completion(self):
        src = (u"struct Point { int x; int y; void norm(); };\n"
               u"int main() { Point p; p. }\n")
        offset = src.index(u"p. ") + 2
        triggers = self._triggers(src, offset)
        for name in ("x", "y", "norm"):
            self.assertIn(name, triggers)

    def test_arrow_completion(self):
        src = (u"struct Node { int value; };\n"
               u"int main() { Node *n; n-> }\n")
        offset = src.index(u"n-> ") + 3
        triggers = self._triggers(src, offset)
        self.assertIn("value", triggers)

    def test_scope_completion(self):
        src = (u"namespace geo {\n"
               u"struct Vec { int x; };\n"
               u"int origin;\n"
               u"}\n"
               u"void f() { geo:: }\n")
        offset = src.index(u"geo:: ") + 5
        triggers = self._triggers(src, offset)
        self.assertIn("Vec", triggers)
        self.assertIn("origin", triggers)

    def test_enum_scope_completion(self):
        src = (u"enum class Color { Red, Green, Blue };\n"
               u"Color c = Color::\n")
        offset = len(src) - 1
        triggers = self._triggers(src, offset)
        for name in ("Red", "Green", "Blue"):
            self.assertIn(name, triggers)

    def test_prefix_filter(self):
        src = (u"int apple = 1; int apricot = 2; int banana = 3;\n"
               u"ap\n")
        offset = len(src) - 1
        triggers = self._triggers(src, offset)
        self.assertIn("apple", triggers)
        self.assertIn("apricot", triggers)
        self.assertNotIn("banana", triggers)

    def test_no_completion_inside_comment_or_string(self):
        src = u"int alpha;\n// al\nconst char *s = \"al\";\n"
        comment_off = src.index(u"// al") + 5
        string_off = src.index(u"\"al\"") + 3
        self.assertEqual(self._triggers(src, comment_off), [])
        self.assertEqual(self._triggers(src, string_off), [])

    def test_include_completion(self):
        src = u"#include <vec\n"
        offset = len(src) - 1
        triggers = self._triggers(src, offset)
        self.assertIn("vector", triggers)

    def test_completion_items_have_plugin_shape(self):
        src = u"struct S { int member; };\nvoid f() { S s; s.mem }\n"
        offset = src.index(u"s.mem") + 5
        items = self.engine.complete(src, offset)
        self.assertTrue(items)
        for d in items:
            for key in ("trigger", "insert", "annotation", "annotation_en",
                        "kind", "detail", "detail_en"):
                self.assertIn(key, d)

    def test_auto_iterator_type_is_inferred(self):
        src = (u"vector<int> v;\n"
               u"int main() {\n"
               u"    auto it = v.find(1);\n"
               u"    it\n"
               u"}\n")
        offset = src.rindex(u"it") + 2
        items = self.engine.complete(src, offset)
        by_trigger = dict((d["trigger"], d) for d in items)
        self.assertEqual(by_trigger["it"]["detail"],
                         "vector<int>::iterator")

    def test_auto_value_type_from_at(self):
        src = (u"map<string, int> m;\n"
               u"void f() { auto value = m.at(\"k\"); value; }\n")
        offset = src.rindex(u"value") + 5
        items = self.engine.complete(src, offset)
        by_trigger = dict((d["trigger"], d) for d in items)
        self.assertEqual(by_trigger["value"]["detail"], "int")

    def test_macro_kind_is_macro(self):
        src = u"#define MAXN 100\nint a[MAXN];\nint main() { MAX }\n"
        offset = src.rindex(u"MAX") + 3
        items = self.engine.complete(src, offset)
        by_trigger = dict((d["trigger"], d) for d in items)
        self.assertEqual(by_trigger["MAXN"]["kind"], "d")

    def test_pbds_namespace_completion(self):
        src = u"int main() {\n    __gnu_pbds::\n}\n"
        offset = src.index(u"__gnu_pbds::") + len(u"__gnu_pbds::")
        triggers = self._triggers(src, offset)
        for name in ("tree", "gp_hash_table", "rb_tree_tag"):
            self.assertIn(name, triggers)

    def test_include_completion_details(self):
        src = u"#include <ext/pb_ds/assoc\n"
        offset = len(src) - 1
        items = self.engine.complete(src, offset)
        by_trigger = dict((d["trigger"], d) for d in items)
        entry = by_trigger["ext/pb_ds/assoc_container.hpp"]
        self.assertIn("PBDS", entry["detail"])


class CallInfoTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def test_signature_and_argument_index(self):
        src = (u"void solve(int n, vector<int>& a) {\n}\n"
               u"int main() {\n"
               u"    solve(n, a);\n"
               u"}\n")
        offset = src.index(u"a);") + 1
        info = self.engine.call_info(src, offset)
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "solve")
        self.assertEqual(info.arg_index, 1)
        self.assertEqual(info.arg_count, 2)
        self.assertEqual(info.param, "vector<int>& a")
        self.assertEqual(info.signature,
                         "void solve(int n, vector<int>& a)")

    def test_first_argument(self):
        src = (u"void solve(int n, vector<int>& a) {\n}\n"
               u"int main() { solve(|); }\n")
        offset = src.index(u"|")
        info = self.engine.call_info(src, offset)
        self.assertEqual(info.arg_index, 0)
        self.assertEqual(info.param, "int n")

    def test_unknown_function_still_reports_name(self):
        src = u"int main() { helper(1, 2); }\n"
        offset = src.index(u"2")
        info = self.engine.call_info(src, offset)
        self.assertEqual(info.name, "helper")
        self.assertEqual(info.arg_index, 1)
        self.assertEqual(info.signature, "")

    def test_outside_call_returns_none(self):
        src = u"int main() { int x = 1; }\n"
        offset = src.index(u"1;")
        self.assertIsNone(self.engine.call_info(src, offset))

    def test_nested_call_counts_only_outer_commas(self):
        src = (u"void f(int a, int b, int c) {}\n"
               u"int main() { f(g(1, 2), 3, 4); }\n")
        offset = src.index(u"3,") + 1
        info = self.engine.call_info(src, offset)
        self.assertEqual(info.name, "f")
        self.assertEqual(info.arg_index, 1)

    def test_template_call_name(self):
        src = (u"template <class T> void go(int a, int b) {}\n"
               u"int main() { go<int>(1, 2); }\n")
        offset = src.index(u"2)")
        info = self.engine.call_info(src, offset)
        self.assertEqual(info.name, "go")
        self.assertEqual(info.arg_index, 1)


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def test_find_function_definition(self):
        src = (u"int helper(int x);\n"
               u"int helper(int x) { return x; }\n"
               u"int main() { return helper(1); }\n")
        hits = self.engine.find_definitions(
            src, "helper", src.index(u"helper(1)"))
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 2)
        self.assertEqual(hits[0]["kind"], "function")

    def test_find_type_and_member(self):
        src = (u"struct Widget { int size; };\n"
               u"int main() { Widget w; return w.size; }\n")
        hits = self.engine.find_definitions(
            src, "Widget", src.index(u"Widget w"))
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 1)
        hits = self.engine.find_definitions(
            src, "size", src.index(u"w.size"))
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 1)

    def test_find_enum_enumerator(self):
        src = (u"enum class Mode { Fast, Slow };\n"
               u"Mode m = Mode::Fast;\n")
        hits = self.engine.find_definitions(
            src, "Fast", src.index(u"Mode::Fast") + 6)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 1)

    def test_missing_symbol_returns_empty(self):
        hits = self.engine.find_definitions(
            u"int main() { return 0; }\n", "does_not_exist")
        self.assertEqual(hits, [])

    def test_definition_offsets_point_at_names(self):
        src = u"int counter = 0;\nint main() { return counter; }\n"
        hits = self.engine.find_definitions(
            src, "counter", src.index(u"return counter"))
        self.assertTrue(hits)
        off = hits[0]["offset"]
        self.assertEqual(src[off:off + len("counter")], "counter")
        self.assertEqual(hits[0]["line"], 1)


class ArrayBoundsTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def _bounds(self, src):
        return [d for d in self.engine.diagnostics(src)
                if d["code"] == "array-bounds"]

    def test_declaration_is_not_an_access(self):
        self.assertEqual(self._bounds(u"int a[10];\n"), [])

    def test_in_bounds_and_out_of_bounds(self):
        self.assertEqual(
            self._bounds(u"int a[10];\nint main() { a[9]; }\n"), [])
        diags = self._bounds(u"int a[10];\nint main() { a[10]; }\n")
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["line"], 2)

    def test_negative_suffix_hex_and_octal_indices(self):
        self.assertEqual(
            len(self._bounds(u"int a[10];\nint main() { a[-1]; }\n")), 1)
        self.assertEqual(
            len(self._bounds(u"int a[10];\nint main() { a[10u]; }\n")), 1)
        self.assertEqual(
            len(self._bounds(u"int a[10];\nint main() { a[0xA]; }\n")), 1)
        # C-style octal: 010 == 8 (valid), 077 == 63 (out of bounds).
        self.assertEqual(
            self._bounds(u"int a[10];\nint main() { a[010]; }\n"), [])
        self.assertEqual(
            len(self._bounds(u"int a[10];\nint main() { a[077]; }\n")), 1)

    def test_multidimensional_chain(self):
        self.assertEqual(
            len(self._bounds(
                u"int a[2][3];\nint main() { a[2][0]; }\n")), 1)
        self.assertEqual(
            len(self._bounds(
                u"int a[2][3];\nint main() { a[0][3]; }\n")), 1)
        self.assertEqual(
            self._bounds(u"int a[2][3];\nint main() { a[1][2]; }\n"), [])
        self.assertEqual(
            len(self._bounds(
                u"int a[2][3];\nint main() { a[2][3]; }\n")), 2)

    def test_shadowing_non_array_stops_the_search(self):
        src = (u"int a[10];\n"
               u"void f() { int *a = nullptr; a[10]; }\n")
        self.assertEqual(self._bounds(src), [])

    def test_inner_array_shadows_outer_size(self):
        src = (u"void f() { int a[2]; a[1]; }\n"
               u"void g() { int a[10]; a[9]; }\n")
        self.assertEqual(self._bounds(src), [])
        src = u"void f() { int a[2]; a[2]; }\n"
        self.assertEqual(len(self._bounds(src)), 1)

    def test_member_array_is_not_resolved_through_scope(self):
        src = (u"int a[10];\n"
               u"struct S { int a[2]; };\n"
               u"void f() { S s; s.a[10]; }\n")
        self.assertEqual(self._bounds(src), [])


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def _codes(self, src):
        return [d["code"] for d in self.engine.diagnostics(src)]

    def test_balanced_code_has_no_diagnostics(self):
        self.assertEqual(self._codes(u"int main() { return 0; }\n"), [])

    def test_unclosed_and_extra_brackets(self):
        self.assertIn("unclosed-bracket", self._codes(u"int main() {\n"))
        self.assertIn("extra-bracket", self._codes(u"int main() { return 0; }}\n"))

    def test_unterminated_string_and_comment(self):
        self.assertIn("unterminated-string", self._codes(u'const char *s = "x;\n'))
        self.assertIn("unterminated-comment", self._codes(u"/* never closed\n"))

    def test_fullwidth_punctuation(self):
        src = u"int main() ｛ return 0; ｝\n"
        diags = self.engine.diagnostics(src)
        codes = [d["code"] for d in diags]
        self.assertEqual(codes.count("fullwidth"), 2)
        self.assertEqual(diags[0]["line"], 1)

    def test_malformed_include(self):
        self.assertIn("malformed-include", self._codes(u"#include <vector\n"))

    def test_unterminated_if_and_extra_endif(self):
        self.assertIn("unterminated-if", self._codes(u"#if 1\nint x;\n"))
        self.assertIn("extra-endif", self._codes(u"#endif\n"))

    def test_basic_checks_tuple_shape(self):
        problems = self.engine.basic_checks(u"int main() {\n")
        self.assertTrue(problems)
        self.assertEqual(len(problems[0]), 5)
        line, col, severity, en, zh = problems[0]
        self.assertEqual(line, 1)
        self.assertIn(severity, ("error", "warning"))
        self.assertTrue(en)
        self.assertTrue(zh)

    def test_basic_checks_include_preprocessor_diagnostics(self):
        problems = self.engine.basic_checks(u"#include <vector\n#if 1\n")
        messages = [p[3] for p in problems]
        self.assertTrue(any("Malformed #include" in m for m in messages))
        self.assertTrue(any("#if" in m for m in messages))

    def test_basic_checks_cache_returns_same_result(self):
        src = u"int main() {\n"
        first = self.engine.basic_checks(src, cache_key=5, version=1)
        second = self.engine.basic_checks(src, cache_key=5, version=1)
        self.assertIs(first, second)
        third = self.engine.basic_checks(src, cache_key=5, version=2)
        self.assertIsNot(first, third)
        self.assertEqual(first, third)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.engine = ca_fast.FastEngine()

    def test_same_text_hits_cache(self):
        a1 = self.engine.analyze(SAMPLE, cache_key=1, version=1)
        a2 = self.engine.analyze(SAMPLE, cache_key=1, version=1)
        self.assertIs(a1, a2)
        self.assertEqual(self.engine.stats["incremental"], 0)

    def test_edit_one_line_keeps_prefix_tokens(self):
        src = u"int a;\nint b;\nint c;\n"
        a1 = self.engine.analyze(src, cache_key=7, version=1)
        src2 = u"int a;\nint b;\nint changed;\n"
        a2 = self.engine.analyze(src2, cache_key=7, version=2)
        self.assertTrue(a2.incremental)
        self.assertGreater(a2.reused_tokens, 0)
        self.assertLess(len(a2.tokens), len(a1.tokens) + 4)
        names = [t for t in a2.tokens
                 if a2.text[t.start:t.end] == "changed"]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0].line, 3)

    def test_edit_before_symbols_updates_line_numbers(self):
        src = u"int a;\nint target;\n"
        self.engine.analyze(src, cache_key=8, version=1)
        src2 = u"// new line\n// another\nint a;\nint target;\n"
        a2 = self.engine.analyze(src2, cache_key=8, version=2)
        hits = self.engine.find_definitions(
            src2, "target", src2.index(u"target"), cache_key=8, version=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 4)

    def test_edit_inside_multiline_comment_is_correct(self):
        src = u"/* one\ntwo\nthree */\nint value;\n"
        self.engine.analyze(src, cache_key=9, version=1)
        src2 = u"/* one\ntwo changed\nthree */\nint value;\n"
        a2 = self.engine.analyze(src2, cache_key=9, version=2)
        value = [t for t in a2.tokens
                 if a2.text[t.start:t.end] == "value"][0]
        self.assertEqual(value.line, 4)
        self.assertEqual(a2.diags, [])

    def test_cache_limit_evicts_old_entries(self):
        eng = ca_fast.FastEngine(cache_limit=2)
        eng.analyze(u"int a;\n", cache_key=1, version=1)
        eng.analyze(u"int b;\n", cache_key=2, version=1)
        eng.analyze(u"int c;\n", cache_key=3, version=1)
        self.assertNotIn(1, eng._cache)
        self.assertIn(3, eng._cache)

    def test_clear_drops_cache(self):
        self.engine.analyze(u"int a;\n", cache_key=1, version=1)
        self.engine.clear()
        self.assertEqual(self.engine._cache, {})


class ModuleApiTests(unittest.TestCase):
    def test_module_level_helpers(self):
        src = u"struct Foo { int n; };\nFoo f;\n"
        a = ca_fast.analyze(src)
        self.assertIn("Foo", a.by_name)
        items = ca_fast.complete(src, len(src), prefix=u"F")
        self.assertIn("Foo", [d["trigger"] for d in items])
        hits = ca_fast.find_definitions(src, "Foo")
        self.assertTrue(hits)
        self.assertEqual(ca_fast.basic_checks(u"int x;\n"), [])

    def test_engine_singleton_is_stable(self):
        self.assertIs(ca_fast.engine(), ca_fast.engine())


if __name__ == "__main__":
    unittest.main()
