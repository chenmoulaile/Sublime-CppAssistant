# -*- coding: utf-8 -*-
"""安全替换全角标点。

中文输入法会在写代码时留下 ``，；：（）`` 这类全角标点，编译器只会报
``stray '\\357' in program``，竞赛现场很难一眼看出。即时检查已经能报
出来，这个模块负责把报告变成可以一键应用的编辑。

只改**代码区**：注释和字符串/字符字面量里的中文标点是用户本来就想
要的（``printf("答案：%d", x)``），绝不能动。原始字符串 ``R"(...)"``
同样原样保留。

纯 Python、不依赖 sublime，方便单元测试。
"""

import bisect
import collections

# 一处可替换的全角标点。用 namedtuple 而不是 dict：mypy 能把字段当
# Any，跨模块做 offset 算术时不会报 object 不可运算。
FullwidthFix = collections.namedtuple(
    "FullwidthFix", "offset old new line col")

# 全角 -> 半角。只收录在 C/C++ 代码里确实会出现的符号：
# 中文句号/顿号一起映射到 "." / ","，因为全角句号出现在表达式里基本
# 就是把 "." 或 "," 打错了。
FULLWIDTH_MAP = {
    u"，": u",",
    u"。": u".",
    u"、": u",",
    u"；": u";",
    u"：": u":",
    u"？": u"?",
    u"！": u"!",
    u"（": u"(",
    u"）": u")",
    u"［": u"[",
    u"］": u"]",
    u"｛": u"{",
    u"｝": u"}",
    u"【": u"[",
    u"】": u"]",
    u"“": u"\"",
    u"”": u"\"",
    u"‘": u"'",
    u"’": u"'",
    u"《": u"<",
    u"》": u">",
    u"＜": u"<",
    u"＞": u">",
    u"＝": u"=",
    u"＋": u"+",
    u"－": u"-",
    u"＊": u"*",
    u"／": u"/",
    u"＼": u"\\",
    u"％": u"%",
    u"＆": u"&",
    u"｜": u"|",
    u"＾": u"^",
    u"～": u"~",
    u"＃": u"#",
    u"＠": u"@",
    u"＄": u"$",
    u"＿": u"_",
    u"｀": u"`",
    u"＂": u"\"",
    u"＇": u"'",
    u"．": u".",
    u"　": u" ",  # 全角空格
}

# ``u8R"delim(...)delim"`` 之类的原始字符串前缀
_RAW_PREFIXES = ("u8R\"", "uR\"", "UR\"", "LR\"", "R\"")


def _raw_string_end(text, start):
    """``text[start:]`` 以 ``R"`` 开头时返回原始字符串结束位置。"""
    quote = text.find("\"", start)
    if quote == -1:
        return None
    open_paren = text.find("(", quote + 1, quote + 18)
    if open_paren == -1:
        return None
    delim = text[quote + 1:open_paren]
    if "\n" in delim or "\r" in delim:
        return None
    close = ")" + delim + "\""
    end = text.find(close, open_paren + 1)
    if end == -1:
        return len(text)
    return end + len(close)


def _normal_string_end(text, start):
    """返回普通字符串/字符字面量（含转义）的结束位置。"""
    quote = text[start]
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        if ch == "\n":  # 未闭合：到行尾为止，后面的代码继续按代码处理
            return i
        i += 1
    return n


def code_spans(text):
    """产出 ``(start, end)``：属于代码（非注释/字面量）的区间。"""
    n = len(text)
    i = 0
    span_start = 0
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                if span_start < i:
                    yield span_start, i
                nl = text.find("\n", i)
                i = n if nl == -1 else nl
                span_start = i
                continue
            if nxt == "*":
                if span_start < i:
                    yield span_start, i
                end = text.find("*/", i + 2)
                i = n if end == -1 else end + 2
                span_start = i
                continue
        raw = None
        for prefix in _RAW_PREFIXES:
            if text.startswith(prefix, i):
                raw = _raw_string_end(text, i)
                break
        if raw is not None:
            if span_start < i:
                yield span_start, i
            i = raw
            span_start = i
            continue
        if ch in "\"'":
            # 带前缀的普通字面量：u8"..." L'x' 等，前缀本身仍是代码，
            # 但无所谓——前缀里不会有全角字符。
            if span_start < i:
                yield span_start, i
            i = _normal_string_end(text, i)
            span_start = i
            continue
        i += 1
    if span_start < n:
        yield span_start, n


def _line_starts(text):
    starts = [0]
    i = 0
    while True:
        i = text.find("\n", i)
        if i == -1:
            break
        i += 1
        starts.append(i)
    return starts


def _line_col(starts, offset):
    idx = bisect.bisect_right(starts, offset) - 1
    if idx < 0:
        return 1, 1
    return idx + 1, offset - starts[idx] + 1


def find_fullwidth(text):
    """找出代码区里的全角标点。

    返回 ``[FullwidthFix(offset, old, new, line, col)]``（line/col 从 1 开始）。
    """
    out = []
    if not text:
        return out
    starts = None
    for start, end in code_spans(text):
        chunk = text[start:end]
        if not chunk:
            continue
        for i, ch in enumerate(chunk):
            rep = FULLWIDTH_MAP.get(ch)
            if rep is None:
                continue
            if starts is None:
                starts = _line_starts(text)
            line, col = _line_col(starts, start + i)
            out.append(FullwidthFix(start + i, ch, rep, line, col))
    return out


def apply_fixes(text, fixes):
    """把 ``find_fullwidth`` 的结果应用到文本上，返回新文本。"""
    if not fixes:
        return text
    parts = []
    last = 0
    for fix in sorted(fixes, key=lambda f: f.offset):
        off = fix.offset
        if off < last or off >= len(text):
            continue
        parts.append(text[last:off])
        parts.append(fix.new)
        last = off + 1
    parts.append(text[last:])
    return u"".join(parts)


def describe(fixes, limit=5):
    """给确认对话框用的一句话摘要。"""
    if not fixes:
        return u""
    shown = fixes[:limit]
    bits = [u"line %d: '%s' -> '%s'" % (f.line, f.old, f.new)
            for f in shown]
    more = u""
    if len(fixes) > len(shown):
        more = u", ... (%d more)" % (len(fixes) - len(shown))
    return u"; ".join(bits) + more
