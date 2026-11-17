# -*- coding: utf-8 -*-
"""Template compile smoke tests.

The bundled ``templates/**/*.sublime-snippet`` files used to be validated
only as XML (see ``test_snippets.py``): nobody ever fed their C++ bodies to
a compiler.  That let several "expands-but-does-not-compile / always-WA"
math templates slip through (REVIEW.md blind spot (1)).

This module closes that gap:

* extract the C++ body from every snippet's ``<![CDATA[...]]>`` block;
* turn Sublime placeholders (``${1:default}``, ``${N}``, ``$N``, ``$0``)
  into valid C++ while keeping literal ``$`` characters intact;
* run ``g++ -std=c++14 -fsyntax-only`` over every resulting translation
  unit (skipping cleanly when no compiler is installed);
* compile the ``geometry`` pack in "whole pack" mode: the dependent
  snippets reference ``Point`` / ``eps`` / ``PI`` from ``point`` and must
  be compiled together with it;
* optionally execute small programs that pin the runtime fixes made in
  this round (kmp / mincostflow / aho-corasick / manacher), using
  ``-D_GLIBCXX_DEBUG`` plus a timeout to catch out-of-bounds access and
  infinite loops.

Run on its own with::

    python -m unittest tests.test_template_compile -v
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEMPLATES_DIR = os.path.join(ROOT, "templates")

# 编译用的最小前奏：模板正文里普遍直接使用 vector/string/sort/... 而不带 std::。
PREFIX = "#include <bits/stdc++.h>\nusing namespace std;\n"

GXX = shutil.which("g++")

# geometry 里这些模板依赖 point 提供的 Point / eps / PI，单独编译必然失败，
# 必须与 point 正文拼在一起（整包模式）编译。
GEOMETRY_DEPENDENTS = frozenset(
    ("circle", "line", "polygon", "convexhull", "intersection"))
GEOMETRY_POINT = "point"

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


# --------------------------------------------------------------------------
# Snippet 解析 / 占位符替换
# --------------------------------------------------------------------------
def _all_snippets():
    """按路径排序返回所有 ``*.sublime-snippet`` 的绝对路径。"""
    return sorted(glob.glob(os.path.join(TEMPLATES_DIR, "**", "*.sublime-snippet"),
                            recursive=True))


def _snippet_key(path):
    """返回 ``(pack, trigger)``，例如 ``("geometry", "point")``。"""
    pack = os.path.basename(os.path.dirname(path))
    trigger = os.path.basename(path)[:-len(".sublime-snippet")]
    return pack, trigger


def _raw_body(path):
    """抽取 CDATA 块的 C++ 正文（多段 CDATA 用换行拼接）。"""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    parts = _CDATA_RE.findall(text)
    return "\n".join(parts)


def _substitute_placeholders(text):
    """把 Sublime 片段占位符替换成合法 C++。

    * ``${1:默认文本}`` -> ``默认文本``（递归解析其中嵌套的占位符）；
    * ``${数字}`` / ``$数字`` / ``$0`` -> 空串；
    * 其它字面 ``$``（如字符串/字符里的内容）保持原样。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "$" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "{":
                # 统计花括号深度来找与 "${" 配对的 "}"，这样嵌套的
                # ${2:...} 以及默认文本里成对的 {} 都能正确处理。
                depth = 1
                j = i + 2
                while j < n and depth:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                if depth == 0:
                    inner = text[i + 2:j - 1]
                    if ":" in inner:
                        _name, default = inner.split(":", 1)
                        out.append(_substitute_placeholders(default))
                    # 形如 ${N}（无冒号）-> 空串
                    i = j
                    continue
                # 没有配对的 "}"：按字面 "$" 处理
                out.append(ch)
                i += 1
                continue
            if nxt.isdigit():
                # $N / $0：吞掉整段数字，替换为空串
                j = i + 1
                while j < n and text[j].isdigit():
                    j += 1
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _body(path):
    """返回某个片段占位符替换后的 C++ 正文。"""
    return _substitute_placeholders(_raw_body(path))


def _translation_unit(path):
    """返回用于编译的单个翻译单元源码。

    geometry 的依赖项会把 point 正文拼在前面（整包模式）。
    """
    pack, trigger = _snippet_key(path)
    body = _body(path)
    if pack == "geometry" and trigger in GEOMETRY_DEPENDENTS:
        point_path = os.path.join(TEMPLATES_DIR, "geometry",
                                  GEOMETRY_POINT + ".sublime-snippet")
        body = _body(point_path) + "\n" + body
    return PREFIX + body


# --------------------------------------------------------------------------
# g++ 帮助函数
# --------------------------------------------------------------------------
def _run_gxx(source, output=None, extra_flags=()):
    cmd = [GXX, "-std=c++14"] + list(extra_flags)
    if output is None:
        cmd += ["-fsyntax-only", source]
    else:
        cmd += [source, "-o", output]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace", timeout=60)


def _syntax_check(source, label):
    """返回 None 表示编译通过，否则返回可读的失败信息。"""
    tmpdir = tempfile.mkdtemp(prefix="ca_tpl_syntax_")
    src = os.path.join(tmpdir, "probe.cpp")
    try:
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(source)
        try:
            proc = _run_gxx(src)
        except subprocess.TimeoutExpired:
            return "%s: g++ timed out" % label
        if proc.returncode != 0:
            return "%s: g++ -fsyntax-only failed\n%s" % (
                label, (proc.stderr or proc.stdout).strip())
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _compile_and_run(body, main_code, extra_flags=(), timeout=10):
    """把正文 + main 编成可执行程序并运行，返回 (stdout, 说明)。

    失败时 (None, 原因)；成功时 (stdout 字符串, None)。
    """
    tmpdir = tempfile.mkdtemp(prefix="ca_tpl_compile_")
    src = os.path.join(tmpdir, "probe.cpp")
    exe = os.path.join(tmpdir, "probe.exe" if os.name == "nt" else "probe")
    try:
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(PREFIX + body + "\n" + main_code)
        try:
            comp = _run_gxx(src, output=exe, extra_flags=extra_flags)
        except subprocess.TimeoutExpired:
            return None, "g++ 编译超时"
        if comp.returncode != 0:
            return None, "g++ 编译失败:\n%s" % (
                (comp.stderr or comp.stdout).strip(),)
        try:
            proc = subprocess.run([exe], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, encoding="utf-8",
                                  errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "程序运行超时（疑似死循环）"
        if proc.returncode != 0:
            return None, "程序运行失败 (rc=%d):\n%s" % (
                proc.returncode, (proc.stderr or proc.stdout).strip())
        return proc.stdout.strip(), None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


class _GxxTestCase(unittest.TestCase):
    def setUp(self):
        if not GXX:
            self.skipTest("系统未安装 g++，跳过需要编译的模板测试")


# --------------------------------------------------------------------------
# 纯 Python 单测：不依赖编译器
# --------------------------------------------------------------------------
class PlaceholderSubstitutionTests(unittest.TestCase):
    def test_placeholders_become_valid_cpp(self):
        self.assertEqual(_substitute_placeholders("int ${1:x} = 0;"),
                         "int x = 0;")
        self.assertEqual(_substitute_placeholders("f($1, $2, $0);"),
                         "f(, , );")
        self.assertEqual(_substitute_placeholders("f(${1}, ${22});"),
                         "f(, );")

    def test_literal_dollar_is_preserved(self):
        # manacher 旧哨兵写法里的 '$' 不应被当成占位符抹掉。
        self.assertEqual(_substitute_placeholders("char c = '$'; t += '$';"),
                         "char c = '$'; t += '$';")

    def test_nested_default_is_resolved_recursively(self):
        self.assertEqual(
            _substitute_placeholders("x = ${1:foo${2:bar}baz};"),
            "x = foobarbaz;")

    def test_snippet_discovery_finds_all_packs(self):
        snippets = _all_snippets()
        self.assertGreater(len(snippets), 0, "未发现任何 .sublime-snippet")
        packs = {_snippet_key(p)[0] for p in snippets}
        self.assertTrue({"geometry", "graph", "ds", "strings", "math"} <= packs)
        for path in snippets:
            self.assertTrue(_raw_body(path).strip(),
                            "%s 缺少 CDATA 正文" % path)


# --------------------------------------------------------------------------
# 编译冒烟测试
# --------------------------------------------------------------------------
class TemplateCompileTests(_GxxTestCase):
    def test_every_snippet_compiles(self):
        """逐个片段做 -fsyntax-only；geometry 依赖项自动进入整包模式。"""
        snippets = _all_snippets()
        self.assertTrue(snippets)
        for path in snippets:
            pack, trigger = _snippet_key(path)
            with self.subTest(snippet="%s/%s" % (pack, trigger)):
                failure = _syntax_check(_translation_unit(path),
                                        "%s/%s" % (pack, trigger))
                self.assertIsNone(failure, failure or "")

    def test_geometry_pack_compiles_as_whole(self):
        """整包模式：point 在其它几何模板之前，全体一起编译应成功。"""
        base = os.path.join(TEMPLATES_DIR, "geometry")
        order = [GEOMETRY_POINT] + sorted(GEOMETRY_DEPENDENTS)
        bodies = []
        for trigger in order:
            path = os.path.join(base, trigger + ".sublime-snippet")
            self.assertTrue(os.path.exists(path), "缺少 %s" % path)
            bodies.append(_body(path))
        failure = _syntax_check(PREFIX + "\n".join(bodies),
                                "geometry(整包)")
        self.assertIsNone(failure, failure or "")


# --------------------------------------------------------------------------
# 运行时行为回归（可选，g++ 可用时执行）
# --------------------------------------------------------------------------
class RuntimeBehaviorTests(_GxxTestCase):
    def _body_of(self, pack, trigger):
        return _body(os.path.join(TEMPLATES_DIR, pack,
                                  trigger + ".sublime-snippet"))

    def _assert_output(self, pack, trigger, main_code, expected, **kwargs):
        actual, err = _compile_and_run(self._body_of(pack, trigger),
                                       main_code, **kwargs)
        self.assertIsNone(err, "%s/%s: %s" % (pack, trigger, err or ""))
        self.assertEqual(actual, expected)

    def test_kmp_empty_pattern_does_not_read_out_of_bounds(self):
        # 修复前 pat[0] 越界；_GLIBCXX_DEBUG 下会直接 abort。
        main = """
int main() {
    vector<int> e = kmp_search("abcabc", "");
    vector<int> r = kmp_search("abababa", "aba");
    cout << e.size();
    for (int x : r) cout << " " << x;
    cout << "\\n";
}
"""
        self._assert_output("strings", "kmp", main, "0 0 2 4",
                            extra_flags=("-D_GLIBCXX_DEBUG",))

    def test_mincostflow_run_same_source_sink_terminates(self):
        # 修复前 run(s,s) 的 SPFA 死循环；靠短超时捕获。
        main = """
int main() {
    MCMF m(3);
    m.add_edge(0, 1, 1, 1);
    m.add_edge(1, 2, 1, 1);
    pair<long long,long long> same = m.run(0, 0);
    pair<long long,long long> ok = m.run(0, 2);
    cout << same.first << " " << same.second << " "
         << ok.first << " " << ok.second << "\\n";
}
"""
        self._assert_output("graph", "mincostflow", main, "0 0 1 2",
                            extra_flags=("-D_GLIBCXX_DEBUG",), timeout=5)

    def test_aho_corasick_uppercase_input_does_not_index_negatively(self):
        # 修复前大写字母 'A'-'a' = -32 导致数组越界；应安全跳过。
        main = """
int main() {
    AhoCorasick ac;
    ac.insert("abc");
    ac.insert("he");
    ac.build();
    cout << ac.count("ABCabc") << " " << ac.count("HEhe") << "\\n";
}
"""
        self._assert_output("strings", "aho-corasick", main, "1 1",
                            extra_flags=("-D_GLIBCXX_DEBUG",))

    def test_manacher_handles_literal_dollar_in_input(self):
        # 旧实现用 '$' 当哨兵，输入含 '$' 时结果错误；改用控制字符后
        # 含 '$' 的输入应返回正确的最长回文子串。
        main = """
int main() {
    cout << manacher("a$b$a") << "\\n";
    cout << manacher("abba") << "\\n";
}
"""
        self._assert_output("strings", "manacher", main, "a$b$a\nabba",
                            extra_flags=("-D_GLIBCXX_DEBUG",))


if __name__ == "__main__":
    unittest.main()
