# -*- coding: utf-8 -*-
"""Tests for the full-width punctuation fixer."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_fixes


class FullwidthFixTests(unittest.TestCase):
    def test_statement_semicolon_is_fixed(self):
        fixes = ca_fixes.find_fullwidth(u"int a；\n")
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].old, u"；")
        self.assertEqual(fixes[0].new, u";")
        self.assertEqual(fixes[0].line, 1)
        self.assertEqual(fixes[0].col, 6)
        self.assertEqual(ca_fixes.apply_fixes(u"int a；\n", fixes),
                         u"int a;\n")

    def test_comma_in_call_is_fixed(self):
        fixes = ca_fixes.find_fullwidth(u"if (a，b) { }\n")
        self.assertEqual([f.new for f in fixes], [u","])

    def test_line_comment_is_untouched(self):
        text = u"// 注释里的，标点不要动\nint x;\n"
        self.assertEqual(ca_fixes.find_fullwidth(text), [])
        self.assertEqual(ca_fixes.apply_fixes(text, []), text)

    def test_block_comment_is_untouched(self):
        text = u"/* 块注释（含括号）：不要改 */\nint x;\n"
        self.assertEqual(ca_fixes.find_fullwidth(text), [])

    def test_string_literal_is_untouched(self):
        text = u"printf(\"答案：%d，完成\", x);\n"
        self.assertEqual(ca_fixes.find_fullwidth(text), [])

    def test_char_literal_is_untouched(self):
        text = u"char c = '，';\n"
        self.assertEqual(ca_fixes.find_fullwidth(text), [])

    def test_raw_string_is_untouched(self):
        text = u"auto s = R\"(a，b)\";\nint y;\n"
        self.assertEqual(ca_fixes.find_fullwidth(text), [])

    def test_code_after_string_is_still_fixed(self):
        text = u"printf(\"全角，标点\")；\n"
        fixes = ca_fixes.find_fullwidth(text)
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].new, u";")

    def test_escaped_quote_does_not_end_string(self):
        text = u"string s = \"quote\\\" ，\"; int z；\n"
        fixes = ca_fixes.find_fullwidth(text)
        self.assertEqual([f.new for f in fixes], [u";"])

    def test_unterminated_string_only_affects_its_line(self):
        text = u"string s = \"oops\nint z；\n"
        fixes = ca_fixes.find_fullwidth(text)
        self.assertEqual([f.new for f in fixes], [u";"])

    def test_fullwidth_space_is_fixed(self):
        fixes = ca_fixes.find_fullwidth(u"int\u3000x;\n")
        self.assertEqual([f.new for f in fixes], [u" "])

    def test_multiple_fixes_keep_offsets(self):
        text = u"int a；int b；\n"
        fixes = ca_fixes.find_fullwidth(text)
        self.assertEqual(len(fixes), 2)
        self.assertEqual(ca_fixes.apply_fixes(text, fixes),
                         u"int a;int b;\n")

    def test_describe_lists_first_fixes(self):
        fixes = ca_fixes.find_fullwidth(u"int a；int b；\n")
        text = ca_fixes.describe(fixes, limit=1)
        self.assertIn("line 1", text)
        self.assertIn("more", text)

    def test_empty_text(self):
        self.assertEqual(ca_fixes.find_fullwidth(u""), [])
        self.assertEqual(ca_fixes.apply_fixes(u"", []), u"")

    def test_crlf_preserved(self):
        text = u"int a；\r\nint b;\r\n"
        fixed = ca_fixes.apply_fixes(text, ca_fixes.find_fullwidth(text))
        self.assertEqual(fixed, u"int a;\r\nint b;\r\n")


if __name__ == "__main__":
    unittest.main()
