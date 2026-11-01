# -*- coding: utf-8 -*-
"""FastEngine: a zero-dependency C++ analysis engine for CppAssistant.

The engine is the "phase one" base layer described in the project blueprint:
a pure-Python, incremental lexer plus a lightweight structural parser.  It
does not need tree-sitter, libclang, clangd, or a compiler, so it works on a
fresh Sublime Text install.

What it provides
----------------
* A tokenizer with content/line based caching and incremental re-lexing.
* Scope-aware symbols: namespaces, classes, functions, parameters, locals,
  types, aliases, enumerators and macros.
* Completion candidates for locals/parameters/members, merged with the
  existing STL/snippet database by the host plugin.
* Same-file go-to-definition with declaration/definition ranking.
* Millisecond syntax diagnostics: bracket balance, unterminated literals,
  comments and raw strings, full-width punctuation, malformed includes.

The parser is intentionally tolerant.  It is a structural indexer, not a
compiler: it aims to give useful answers for competitive-programming style
single-file code, never to reject valid C++.

The source keeps Python 3.3 compatible syntax because older Sublime Text 4
builds embed Python 3.3.
"""

import bisect
import re
import time


# ---------------------------------------------------------------------------
# Public data objects
# ---------------------------------------------------------------------------

class Token(object):
    """A lexical token.  ``line`` is 1-based, ``col`` is 0-based."""

    __slots__ = ("kind", "start", "end", "line", "col")

    def __init__(self, kind, start, end, line, col):
        self.kind = kind
        self.start = start
        self.end = end
        self.line = line
        self.col = col

    def __repr__(self):
        return "Token(%r, %d, %d, %d, %d)" % (
            self.kind, self.start, self.end, self.line, self.col)


class Diagnostic(object):
    """A syntax problem found by the fast lexer/parser."""

    __slots__ = ("offset", "line", "col", "severity", "en", "zh", "code")

    def __init__(self, offset, line, col, severity, en, zh, code):
        self.offset = offset
        self.line = line
        self.col = col
        self.severity = severity
        self.en = en
        self.zh = zh
        self.code = code

    def as_tuple(self):
        return (self.line, self.col, self.severity, self.en, self.zh)


class Scope(object):
    """A brace-delimited (or global) region and the symbols declared in it."""

    __slots__ = ("kind", "name", "start", "end", "parent", "children",
                 "symbols", "by_name", "open_index", "close_index",
                 "base_types")

    def __init__(self, kind, name, start, end, parent):
        self.kind = kind
        self.name = name
        self.start = start
        self.end = end
        self.parent = parent
        self.children = []
        self.symbols = []
        self.by_name = {}
        self.open_index = -1
        self.close_index = -1
        self.base_types = []

    def add(self, symbol):
        self.symbols.append(symbol)
        self.by_name.setdefault(symbol.name, []).append(symbol)

    def __repr__(self):
        return "Scope(%r, %r, %d..%d)" % (
            self.kind, self.name, self.start, self.end)


class Symbol(object):
    """A named declaration extracted from the token stream."""

    __slots__ = ("name", "kind", "type", "signature", "offset", "name_offset",
                 "line", "col", "scope", "is_definition", "params",
                 "target_scope", "end_offset")

    def __init__(self, name, kind, type_text, signature, offset, name_offset,
                 line, col, scope, is_definition=False, params=None,
                 target_scope=None, end_offset=None):
        self.name = name
        self.kind = kind
        self.type = type_text
        self.signature = signature
        self.offset = offset
        self.name_offset = name_offset
        self.line = line
        self.col = col
        self.scope = scope
        self.is_definition = is_definition
        self.params = params or []
        self.target_scope = target_scope
        self.end_offset = end_offset

    def __repr__(self):
        return "Symbol(%r, %r, %r)" % (self.name, self.kind, self.type)


class Analysis(object):
    """Cached result of lexing + parsing one revision of a buffer."""

    __slots__ = (
        "text", "cache_key", "version", "tokens", "token_starts",
        "token_scope", "scopes", "global_scope", "symbols", "by_name",
        "type_scopes", "includes", "using_std", "diags", "lex_diags",
        "comment_ranges", "line_starts", "line_states", "lex_ms", "parse_ms",
        "total_ms", "completion_cache", "text_len", "incremental",
        "reused_tokens",
    )

    def __init__(self):
        self.text = ""
        self.cache_key = None
        self.version = None
        self.tokens = []
        self.token_starts = []
        self.token_scope = []
        self.scopes = []
        self.global_scope = None
        self.symbols = []
        self.by_name = {}
        self.type_scopes = {}
        self.includes = []
        self.using_std = False
        self.diags = []
        self.lex_diags = []
        self.comment_ranges = []
        self.line_starts = [0]
        self.line_states = []
        self.lex_ms = 0.0
        self.parse_ms = 0.0
        self.total_ms = 0.0
        self.completion_cache = {}
        self.text_len = 0
        self.incremental = False
        self.reused_tokens = 0

    def scope_at(self, offset):
        """Innermost scope containing ``offset`` (best effort)."""
        if not self.tokens:
            return self.global_scope
        idx = bisect.bisect_right(self.token_starts, offset) - 1
        if idx < 0:
            return self.global_scope
        if idx >= len(self.token_scope):
            idx = len(self.token_scope) - 1
        return self.token_scope[idx] or self.global_scope

    def symbols_named(self, name):
        return self.by_name.get(name, ())

    def token_at(self, offset):
        """Token containing ``offset`` or the nearest token before it."""
        if not self.tokens:
            return None
        idx = bisect.bisect_right(self.token_starts, offset) - 1
        if idx < 0:
            return None
        return self.tokens[idx]

    def in_comment(self, offset):
        for (a, b) in self.comment_ranges:
            if a <= offset < b:
                return True
        return False


# ---------------------------------------------------------------------------
# Language tables
# ---------------------------------------------------------------------------

KEYWORDS = frozenset("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char
char8_t char16_t char32_t class compl concept const consteval constexpr
constinit const_cast continue co_await co_return co_yield decltype default
delete do double dynamic_cast else enum explicit export extern false float
for friend goto if inline int long mutable namespace new noexcept not not_eq
nullptr operator or or_eq private protected public register reinterpret_cast
requires return short signed sizeof static static_assert static_cast struct
switch template this thread_local throw true try typedef typeid typename union
unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())

# Tokens that may appear before a type name in a declaration.
DECL_SPECIFIERS = frozenset("""
const volatile static constexpr consteval constinit inline virtual explicit
friend extern mutable register thread_local typename struct class enum union
typedef
""".split())

TYPE_KEYWORDS = frozenset("""
auto bool char char8_t char16_t char32_t double float int long short signed
unsigned void wchar_t
""".split())

CONTROL_KEYWORDS = frozenset("""
if for while switch catch return sizeof decltype alignas alignof typeid
static_assert noexcept requires
""".split())

# Common standard types used to recognise a declaration without a symbol
# table entry.  The list is deliberately conservative: a false positive here
# only affects completion ranking, never diagnostics.
KNOWN_TYPES = frozenset("""
array atomic bitset byte complex deque exception forward_list function
ifstream istream istringstream iterator const_iterator list map multimap
multiset mutex optional ostream ofstream ostringstream pair priority_queue
queue set shared_ptr span stack string string_view stringstream thread tuple
unordered_map unordered_multimap unordered_multiset unordered_set unique_ptr
variant vector weak_ptr wstring wstring_view
size_t ssize_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t uint16_t
uint32_t uint64_t intmax_t uintmax_t FILE
""".split())

_STRING_PREFIXES = (
    ("u8R", True), ("uR", True), ("UR", True), ("LR", True), ("R", True),
    ("u8", False), ("u", False), ("U", False), ("L", False),
)

_FULLWIDTH = u"，；：（）｛｝【】“”‘’！？"
_HALFWIDTH = {
    u"，": ",", u"；": ";", u"：": ":", u"（": "(", u"）": ")",
    u"｛": "{", u"｝": "}", u"【": "[", u"】": "]",
    u"“": '"', u"”": '"', u"‘": "'", u"’": "'",
    u"！": "!", u"？": "?",
}

_OPERATORS = (
    ">>=", "<<=", "<=>", "->*", "...", "::", "->", "++", "--", "<<", ">>",
    "<=", ">=", "==", "!=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=",
    "|=", "^=", "##", ".*", "[[", "]]",
)

# Group multi-character operators by their first character so the lexer does
# not try every operator for every punctuation token.  Longest first keeps
# greedy matching correct (e.g. <<= before << and <=).
_OP_BY_FIRST = {}
for _op in _OPERATORS:
    _OP_BY_FIRST.setdefault(_op[0], []).append(_op)
for _ops in _OP_BY_FIRST.values():
    _ops.sort(key=len, reverse=True)

_ID_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_ID_CONT = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789")
_STRING_PREFIX_START = frozenset("RuUL")

_NORMAL_STATE = (0, None, 0, 0)  # mode, raw terminator, start line, start col
_COMMENT_STATE = 1
_RAW_STATE = 2

_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")


def _line_starts(text):
    """Offsets of the beginning of every line (1-based line -> index-1)."""
    starts = [0]
    i = text.find("\n")
    while i != -1:
        starts.append(i + 1)
        i = text.find("\n", i + 1)
    return starts


def _is_id_start(ch):
    return ch in _ID_START


def _is_id_cont(ch):
    return ch in _ID_CONT


def _string_prefix(text, i):
    """Return (prefix_length, is_raw) when a string literal starts at i."""
    if text[i] not in _STRING_PREFIX_START:
        return 0, False
    for prefix, raw in _STRING_PREFIXES:
        j = i + len(prefix)
        if text.startswith(prefix, i) and j < len(text) and \
                text[j] == '"':
            if i > 0 and _is_id_cont(text[i - 1]):
                continue
            return len(prefix), raw
    return 0, False


def _count_newlines(s):
    return s.count("\n")


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

def _diag(diags, offset, line, col, severity, en, zh, code):
    diags.append(Diagnostic(offset, line, col, severity, en, zh, code))


def _skip_quoted(text, quote_start, line, col, quote):
    """Scan a normal string/char literal.

    Returns ``(end, line, col, closed)``.  A newline before the closing quote
    ends the literal (the newline itself is not consumed).
    """
    n = len(text)
    i = quote_start + 1
    ln = line
    cl = col + 1
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                ln += 1
                cl = 0
                i += 2
                continue
            i += 2
            cl += 2
            continue
        if ch == quote:
            return i + 1, ln, cl + 1, True
        if ch == "\n":
            break
        i += 1
        cl += 1
    return i, ln, cl, False


def _scan_raw_string(text, start, prefix_len, line, col):
    """Scan a raw string.  Returns (end, line, col, terminator, closed)."""
    n = len(text)
    q = start + prefix_len  # points at '"'
    open_paren = text.find("(", q + 1)
    if open_paren == -1 or open_paren - q > 18:
        return n, line, col, None, False
    delim = text[q + 1:open_paren]
    if any(ch in ' ()\\\t\r\n' for ch in delim):
        return n, line, col, None, False
    terminator = ")" + delim + '"'
    close = text.find(terminator, open_paren + 1)
    if close == -1:
        return n, line, col, terminator, False
    end = close + len(terminator)
    segment = text[start:end]
    nl = segment.count("\n")
    if nl:
        line += nl
        col = len(segment) - segment.rfind("\n") - 1
    else:
        col += len(segment)
    return end, line, col, terminator, True


def _lex(text, offset, line, col, state, states, comment_ranges=None):
    """Lex ``text`` from ``offset``.

    ``states`` is a list indexed by line number holding the lexer state at the
    beginning of that line.  The function fills it for every line it touches.
    ``state`` must be normal when called.
    """
    tokens = []
    diags = []
    if comment_ranges is None:
        comment_ranges = []
    n = len(text)
    i = offset
    ln = line
    cl = col
    at_line_start = (cl == 0)
    states[ln] = _NORMAL_STATE
    pending_raw = None  # (start_offset, start_line, start_col, terminator)

    while i < n:
        ch = text[i]

        if ch == "\n":
            ln += 1
            cl = 0
            i += 1
            states[ln] = _NORMAL_STATE
            at_line_start = True
            continue

        if ch in " \t\r\f\v":
            i += 1
            cl += 1
            continue

        # Comments -----------------------------------------------------
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                j = text.find("\n", i)
                if j == -1:
                    comment_ranges.append((i, n + 1))
                    j = n
                else:
                    # Include the newline so a caret at the end of the
                    # comment still counts as being inside it.
                    comment_ranges.append((i, j + 1))
                cl += j - i
                i = j
                at_line_start = False
                continue
            if nxt == "*":
                start_line, start_col = ln, cl
                j = i + 2
                cl += 2
                closed = False
                while j < n:
                    cj = text[j]
                    if cj == "\n":
                        ln += 1
                        cl = 0
                        j += 1
                        states[ln] = (_COMMENT_STATE, None,
                                      start_line, start_col)
                        continue
                    if cj == "*" and j + 1 < n and text[j + 1] == "/":
                        j += 2
                        cl += 2
                        closed = True
                        break
                    j += 1
                    cl += 1
                comment_ranges.append((i, n + 1 if not closed else j))
                if not closed:
                    _diag(diags, i, start_line + 1, start_col, "warning",
                          u"Unterminated block comment: missing closing */",
                          u"块注释 /* 未闭合", "unterminated-comment")
                i = j
                at_line_start = False
                continue

        # Preprocessor directive ---------------------------------------
        if ch == "#" and at_line_start:
            start = i
            start_line, start_col = ln, cl
            j = i
            l2 = ln
            while j < n:
                if text[j] == "\n":
                    k = j - 1
                    while k >= start and text[k] in " \t\r":
                        k -= 1
                    if k >= start and text[k] == "\\":
                        l2 += 1
                        states[l2] = _NORMAL_STATE
                        j += 1
                        continue
                    break
                j += 1
            segment = text[start:j]
            nl = segment.count("\n")
            if nl:
                ln += nl
                cl = len(segment) - segment.rfind("\n") - 1
            else:
                cl += len(segment)
            tokens.append(Token("pp", start, j, start_line + 1, start_col))
            i = j
            at_line_start = False
            continue

        # String / char literals ---------------------------------------
        if ch == '"' or ch == "'":
            quote = ch
            start_line, start_col = ln, cl
            end, ln2, cl2, closed = _skip_quoted(text, i, ln, cl, quote)
            if not closed:
                if quote == '"':
                    _diag(diags, i, start_line + 1, start_col, "warning",
                          u'Unterminated string: missing closing "',
                          u"字符串缺少结尾双引号", "unterminated-string")
                else:
                    _diag(diags, i, start_line + 1, start_col, "warning",
                          u"Unterminated character literal: missing closing '",
                          u"字符常量缺少结尾单引号", "unterminated-char")
            tokens.append(Token("str" if quote == '"' else "chr",
                                i, end, start_line + 1, start_col))
            ln, cl = ln2, cl2
            i = end
            at_line_start = False
            continue

        # Identifier (possibly a string prefix) ------------------------
        if _is_id_start(ch):
            prefix_len, is_raw = 0, False
            if ch in _STRING_PREFIX_START:
                prefix_len, is_raw = _string_prefix(text, i)
            if prefix_len:
                start_line, start_col = ln, cl
                if is_raw:
                    end, ln2, cl2, term, closed = _scan_raw_string(
                        text, i, prefix_len, ln, cl)
                    if not closed:
                        _diag(diags, i, start_line + 1, start_col, "warning",
                              u"Unterminated raw string literal",
                              u"原始字符串未闭合", "unterminated-raw")
                    # Record raw-string state for every line it covers.
                    if end > i and "\n" in text[i:end]:
                        nl = text.count("\n", i, end)
                        for k in range(1, nl + 1):
                            states[ln + k] = (_RAW_STATE, term,
                                              start_line, start_col)
                    tokens.append(Token("str", i, end, start_line + 1,
                                        start_col))
                    ln, cl = ln2, cl2
                    i = end
                else:
                    q = i + prefix_len
                    end, ln2, cl2, closed = _skip_quoted(text, q, ln, cl, '"')
                    if not closed:
                        _diag(diags, i, start_line + 1, start_col, "warning",
                              u'Unterminated string: missing closing "',
                              u"字符串缺少结尾双引号", "unterminated-string")
                    tokens.append(Token("str", i, end, start_line + 1,
                                        start_col))
                    ln, cl = ln2, cl2
                    i = end
                at_line_start = False
                continue

            j = i + 1
            while j < n and _is_id_cont(text[j]):
                j += 1
            word = text[i:j]
            tokens.append(Token(
                "kw" if word in KEYWORDS else "id",
                i, j, ln + 1, cl))
            cl += j - i
            i = j
            at_line_start = False
            continue

        # Numbers ------------------------------------------------------
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i + 1
            while j < n:
                cj = text[j]
                if cj.isalnum() or cj in "_'.":
                    j += 1
                    continue
                if cj in "+-" and j > i and text[j - 1] in "eEpP":
                    j += 1
                    continue
                break
            tokens.append(Token("num", i, j, ln + 1, cl))
            cl += j - i
            i = j
            at_line_start = False
            continue

        # Full-width punctuation --------------------------------------
        if ch in _FULLWIDTH:
            _diag(diags, i, ln + 1, cl, "warning",
                  u"Full-width character '%s'; use the ASCII '%s'"
                  % (ch, _HALFWIDTH.get(ch, "?")),
                  u"疑似全角标点 '%s'，应改为半角" % ch, "fullwidth")
            tokens.append(Token("op", i, i + 1, ln + 1, cl))
            i += 1
            cl += 1
            at_line_start = False
            continue

        # Operators ----------------------------------------------------
        matched = None
        candidates = _OP_BY_FIRST.get(ch)
        if candidates is not None:
            for op in candidates:
                if text.startswith(op, i):
                    matched = op
                    break
        if matched is None:
            matched = ch
        tokens.append(Token("op", i, i + len(matched), ln + 1, cl))
        i += len(matched)
        cl += len(matched)
        at_line_start = False

    return tokens, diags, comment_ranges


# ---------------------------------------------------------------------------
# Bracket diagnostics (operates on the final token list)
# ---------------------------------------------------------------------------

def _bracket_diags(text, tokens):
    diags = []
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    closer_name = {")": "')'", "]": "']'", "}": "'}'"}
    opener_name = {"(": "')'", "[": "']'", "{": "'}'"}
    for idx, tok in enumerate(tokens):
        if tok.kind != "op":
            continue
        s = text[tok.start:tok.end]
        if s in "([{":
            stack.append((s, tok))
        elif s in ")]}":
            if stack and stack[-1][0] == pairs[s]:
                stack.pop()
                continue
            matched = False
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == pairs[s]:
                    matched = True
                    break
            if matched:
                while stack and stack[-1][0] != pairs[s]:
                    oc, ot = stack.pop()
                    _diag(diags, ot.start, ot.line, ot.col, "error",
                          u"Unclosed bracket: missing matching %s"
                          % opener_name[oc],
                          u"括号未闭合：缺少对应的 %s" % opener_name[oc],
                          "unclosed-bracket")
                stack.pop()
            else:
                _diag(diags, tok.start, tok.line, tok.col, "error",
                      u"Extra closing bracket %s: no matching opener"
                      % closer_name[s],
                      u"多余的右括号 %s（没有与之匹配的左括号）"
                      % closer_name[s], "extra-bracket")
    for oc, ot in stack:
        _diag(diags, ot.start, ot.line, ot.col, "error",
              u"Unclosed bracket: missing matching %s" % opener_name[oc],
              u"括号未闭合：缺少对应的 %s" % opener_name[oc],
              "unclosed-bracket")
    return diags


def _pp_diags(text, tokens):
    """Cheap preprocessor diagnostics for the instant-check path.

    The structural parser collects the same problems, but basic checks must
    stay fast on large files, so this scans only the ``pp`` tokens.
    """
    diags = []
    if_stack = []
    for tok in tokens:
        if tok.kind != "pp":
            continue
        raw = text[tok.start:tok.end]
        body = raw.lstrip()
        if body.startswith("#"):
            body = body[1:].lstrip()
        m = re.match(r"include\s*([<\"])([^>\"\r\n]*)([>\"]?)", body)
        if m:
            opener, closer = m.group(1), m.group(3)
            want = ">" if opener == "<" else '"'
            if closer != want:
                _diag(diags, tok.start, tok.line, tok.col, "error",
                      u"Malformed #include: missing closing '%s'" % want,
                      u"#include 缺少结尾的 '%s'" % want,
                      "malformed-include")
            continue
        m = re.match(r"(if|ifdef|ifndef)\b", body)
        if m:
            if_stack.append(tok.line)
            continue
        m = re.match(r"endif\b", body)
        if m:
            if if_stack:
                if_stack.pop()
            else:
                _diag(diags, tok.start, tok.line, tok.col, "warning",
                      u"#endif without a matching #if",
                      u"多余的 #endif（没有对应的 #if）", "extra-endif")
    for line in if_stack:
        _diag(diags, 0, line, 0, "warning",
              u"Unterminated #if block: missing #endif",
              u"#if 块未闭合：缺少 #endif", "unterminated-if")
    return diags


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Parser(object):
    """Tolerant structural parser over the FastEngine token stream."""

    def __init__(self, text, tokens, line_starts):
        self.text = text
        self.tokens = tokens
        self.n = len(tokens)
        self.line_starts = line_starts
        self.match = {}
        self.token_scope = [None] * self.n
        self.scopes = []
        self.global_scope = Scope("global", "", 0, len(text), None)
        self.scopes.append(self.global_scope)
        self.symbols = []
        self.by_name = {}
        self.type_scopes = {}
        self.type_names = set(KNOWN_TYPES)
        self.includes = []
        self.using_std = False
        self.diags = []
        self._pp_if_stack = []
        self._compute_matches()

    # -- small helpers -----------------------------------------------------

    def _text(self, idx):
        tok = self.tokens[idx]
        return self.text[tok.start:tok.end]

    def _tok(self, idx):
        return self.tokens[idx]

    def _active_scope(self, scope):
        while scope is not None and scope.kind == "initializer" and \
                scope.parent is not None:
            scope = scope.parent
        return scope or self.global_scope

    def _compute_matches(self):
        stack = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for i, tok in enumerate(self.tokens):
            if tok.kind != "op":
                continue
            s = self.text[tok.start:tok.end]
            if s in "([{":
                stack.append((s, i))
            elif s in ")]}":
                if stack and stack[-1][0] == pairs[s]:
                    _, j = stack.pop()
                    self.match[i] = j
                    self.match[j] = i

    def _split_top_level(self, start, end, separators):
        """Split token range on separators at bracket/template depth zero."""
        parts = []
        part_start = start
        paren = bracket = brace = angle = 0
        for i in range(start, end):
            s = self._text(i)
            if s == "(":
                paren += 1
            elif s == ")":
                paren = max(0, paren - 1)
            elif s == "[":
                bracket += 1
            elif s == "]":
                bracket = max(0, bracket - 1)
            elif s == "{":
                brace += 1
            elif s == "}":
                brace = max(0, brace - 1)
            elif s == "<":
                angle += 1
            elif s == ">":
                angle = max(0, angle - 1)
            elif s == ">>":
                angle = max(0, angle - 2)
            if paren == 0 and bracket == 0 and brace == 0 and angle == 0 and \
                    s in separators:
                parts.append((part_start, i))
                part_start = i + 1
        parts.append((part_start, end))
        return parts

    def _find_angle_close(self, open_idx, end):
        depth = 0
        for i in range(open_idx, end):
            s = self._text(i)
            if s == "<":
                depth += 1
            elif s == ">":
                depth -= 1
            elif s == ">>":
                depth -= 2
            if depth <= 0:
                return i
        return -1

    def _name_after_keyword(self, keyword_idx, end):
        i = keyword_idx + 1
        if self._text(keyword_idx) == "enum" and i < end and \
                self._text(i) == "class":
            i += 1
        while i < end:
            s = self._text(i)
            tok = self.tokens[i]
            if tok.kind == "id" and s not in ("final", "class", "struct"):
                return i
            if s in ("{", ":", ";", "("):
                return -1
            i += 1
        return -1

    # -- symbol creation ---------------------------------------------------

    def _add_symbol(self, scope, name, kind, type_text, signature, offset,
                    name_offset, line, col, is_definition=False, params=None,
                    target_scope=None, end_offset=None):
        if not name:
            return None
        sym = Symbol(name, kind, type_text, signature, offset, name_offset,
                     line, col, scope, is_definition, params, target_scope,
                     end_offset)
        scope.add(sym)
        self.symbols.append(sym)
        self.by_name.setdefault(name, []).append(sym)
        if kind in ("type", "enum") and target_scope is not None:
            self.type_scopes[name] = target_scope
        return sym

    # -- main build --------------------------------------------------------

    def build(self):
        stack = [self.global_scope]
        stmt_start = 0
        paren_depth = 0
        i = 0
        while i < self.n:
            tok = self.tokens[i]
            s = self.text[tok.start:tok.end]
            scope = stack[-1]
            active = self._active_scope(scope)

            if tok.kind == "pp":
                self.token_scope[i] = active
                self._parse_pp(tok, active)
                stmt_start = i + 1
                i += 1
                continue

            if s == "(":
                paren_depth += 1
            elif s == ")":
                paren_depth = max(0, paren_depth - 1)

            if s == "{":
                child = self._open_brace(i, stmt_start, scope)
                if child.kind == "initializer":
                    self.token_scope[i] = active
                    stack.append(child)
                else:
                    self.token_scope[i] = child
                    stack.append(child)
                    stmt_start = i + 1
                i += 1
                continue

            if s == "}":
                if len(stack) > 1:
                    closed = stack.pop()
                    closed.end = tok.end
                    closed.close_index = i
                    if closed.kind == "initializer":
                        self.token_scope[i] = self._active_scope(stack[-1])
                    else:
                        self.token_scope[i] = stack[-1]
                        stmt_start = i + 1
                else:
                    self.token_scope[i] = self.global_scope
                i += 1
                continue

            if s == ":" and paren_depth == 0:
                # Access specifiers, case labels and goto labels are not
                # declarations; start a fresh statement after them.
                reset = False
                if i > 0 and self._text(i - 1) in ("public", "private",
                                                    "protected"):
                    reset = True
                elif any(self._text(k) in ("case", "default")
                         for k in range(stmt_start, i)):
                    reset = True
                elif i == stmt_start + 1 and \
                        self.tokens[stmt_start].kind == "id":
                    reset = True
                if reset:
                    stmt_start = i + 1
                self.token_scope[i] = active
                i += 1
                continue

            if s == ";" and paren_depth == 0:
                if i > stmt_start:
                    self._parse_statement(stmt_start, i, active)
                stmt_start = i + 1
                self.token_scope[i] = active
                i += 1
                continue

            if s in ("for", "if", "while", "switch") and \
                    i + 1 < self.n and self._text(i + 1) == "(":
                self._parse_control(i, active)

            self.token_scope[i] = active
            i += 1

        for sc in stack[1:]:
            sc.end = len(self.text)
        for sym in self.symbols:
            if sym.end_offset is None:
                sym.end_offset = sym.scope.end if sym.scope else len(self.text)
        if self._pp_if_stack:
            line = self._pp_if_stack[-1]
            _diag(self.diags, 0, line, 0, "warning",
                  u"Unterminated #if block: missing #endif",
                  u"#if 块未闭合：缺少 #endif", "unterminated-if")

    # -- braces and headers ------------------------------------------------

    def _open_brace(self, i, stmt_start, parent):
        parent = self._active_scope(parent)
        header_start = stmt_start
        # Skip a leading template<...> clause and make its parameters visible.
        if header_start + 1 < i and self._text(header_start) == "template" and \
                self._text(header_start + 1) == "<":
            gt = self._find_angle_close(header_start + 1, i)
            if gt > 0:
                self._parse_template_params(header_start + 2, gt, parent)
                header_start = gt + 1
        header = list(range(header_start, i))

        # namespace / extern "C" ---------------------------------------
        ns_idx = -1
        for j in header:
            if self._text(j) == "namespace":
                ns_idx = j
        if ns_idx >= 0:
            name_idx = self._name_after_keyword(ns_idx, i)
            name = self._text(name_idx) if name_idx >= 0 else ""
            scope = Scope("namespace", name, self.tokens[i].start,
                          self.tokens[i].end, parent)
            parent.children.append(scope)
            self.scopes.append(scope)
            if name:
                tok = self.tokens[name_idx]
                self._add_symbol(parent, name, "namespace", "namespace",
                                 "namespace " + name, tok.start, tok.start,
                                 tok.line, tok.col, True, target_scope=scope)
            return scope

        if header and self._text(header[0]) == "extern" and \
                any(self.tokens[j].kind == "str" for j in header):
            scope = Scope("namespace", 'extern "C"',
                          self.tokens[i].start, self.tokens[i].end, parent)
            parent.children.append(scope)
            self.scopes.append(scope)
            return scope

        # class / struct / union / enum --------------------------------
        type_idx = -1
        type_kw = None
        for j in header:
            s = self._text(j)
            if s == "enum":
                type_idx = j
                type_kw = s
                break
            if s in ("class", "struct", "union"):
                type_idx = j
                type_kw = s
        if type_idx >= 0:
            name_idx = self._name_after_keyword(type_idx, i)
            name = self._text(name_idx) if name_idx >= 0 else ""
            # Reject "struct Foo f() {" (a return type, not a definition).
            if name_idx >= 0 and name_idx + 1 < i:
                after = self._text(name_idx + 1)
                if after not in (":", "final", "{", ";"):
                    type_idx = -1
            if type_idx >= 0:
                kind = "enum" if type_kw == "enum" else "type"
                scope = Scope(kind, name, self.tokens[i].start,
                              self.tokens[i].end, parent)
                parent.children.append(scope)
                self.scopes.append(scope)
                if name:
                    self.type_names.add(name)
                    tok = self.tokens[name_idx]
                    self._add_symbol(
                        parent, name, kind, type_kw,
                        "%s %s" % (type_kw, name), tok.start, tok.start,
                        tok.line, tok.col, True, target_scope=scope)
                    # base classes
                    colon = -1
                    for j in range(name_idx + 1, i):
                        if self._text(j) == ":":
                            colon = j
                            break
                    if colon >= 0:
                        for (a, b) in self._split_top_level(colon + 1, i, (",")):
                            ids = [self._text(k) for k in range(a, b)
                                   if self.tokens[k].kind == "id"]
                            if ids:
                                scope.base_types.append(ids[-1])
                if kind == "enum":
                    self._parse_enum_body(i, scope)
                return scope

        # function / lambda --------------------------------------------
        info = self._find_function_header(header_start, i)
        if info is not None:
            name = info["name"]
            name_idx = info["name_idx"]
            is_ctor = info.get("is_ctor", False)
            ret = info.get("return_type", "")
            params = self._parse_params(info["open"], info["close"], None)
            param_text = ", ".join(p.signature for p in params)
            if is_ctor:
                sig = "%s(%s)" % (name, param_text)
                ret = name.lstrip("~")
            else:
                sig = ("%s %s(%s)" % (ret, name, param_text)).strip()
            tok = self.tokens[name_idx]
            kind = "method" if parent.kind == "type" else "function"
            scope = Scope("function", name, self.tokens[i].start,
                          self.tokens[i].end, parent)
            parent.children.append(scope)
            self.scopes.append(scope)
            if not name.startswith("<"):
                self._add_symbol(parent, name, kind, ret, sig, tok.start,
                                 tok.start, tok.line, tok.col, True,
                                 params=params, target_scope=scope)
            for p in params:
                p.scope = scope
                scope.add(p)
                self.symbols.append(p)
                self.by_name.setdefault(p.name, []).append(p)
            return scope

        # brace initialisation: keep the enclosing statement alive ------
        prev = self._text(i - 1) if i > 0 else ""
        initializer = prev in ("=", ",", "(", "[", "return", ":")
        if not initializer and header:
            # "Foo x{1, 2}" and nested braced initialisers.
            if all(self._text(j) not in (";", "}", ")") for j in header) \
                    and not any(self._text(j) in
                                ("if", "for", "while", "switch", "else",
                                 "do", "try", "catch")
                                for j in header):
                if self._text(header[-1]) not in ("do", "else", "try"):
                    initializer = True
        if initializer:
            scope = Scope("initializer", "", self.tokens[i].start,
                          self.tokens[i].end, parent)
            parent.children.append(scope)
            return scope

        scope = Scope("block", "", self.tokens[i].start,
                      self.tokens[i].end, parent)
        parent.children.append(scope)
        self.scopes.append(scope)
        return scope

    def _find_function_header(self, start, brace):
        # A constructor initializer list starts after a top-level ':'.  The
        # real parameter list is before it, so ignore later parentheses.
        limit = brace
        for k in range(start, brace):
            if self._text(k) == ":" and self._top_level_at(start, k):
                limit = k
                break
        brace = limit
        if brace <= start:
            return None

        # lambda: [capture](params) { ... }
        if brace - 1 >= start and self._text(brace - 1) == "]":
            for j in range(brace - 2, start - 1, -1):
                if self._text(j) == ")" and j in self.match and \
                        self.match[j] >= start:
                    return {"name": "<lambda>", "name_idx": j,
                            "open": self.match[j], "close": j,
                            "return_type": "", "is_ctor": False}
            return {"name": "<lambda>", "name_idx": brace - 1,
                    "open": brace, "close": brace,
                    "return_type": "", "is_ctor": False}

        for j in range(brace - 1, start - 1, -1):
            if self._text(j) != ")" or j not in self.match:
                continue
            open_idx = self.match[j]
            if open_idx < start:
                continue
            prev = open_idx - 1
            if prev < start:
                continue
            prev_s = self._text(prev)
            if prev_s in CONTROL_KEYWORDS or prev_s in (
                    "if", "for", "while", "switch", "catch", "return"):
                continue
            # operator() / operator[] name: "operator ( ) (params)"
            name = None
            name_idx = -1
            if prev_s == ")" and prev - 2 >= start and \
                    self._text(prev - 1) == "(" and \
                    self._text(prev - 2) == "operator":
                name = "operator()"
                name_idx = prev - 2
            elif prev_s == "]" and prev - 2 >= start and \
                    self._text(prev - 1) == "[" and \
                    self._text(prev - 2) == "operator":
                name = "operator[]"
                name_idx = prev - 2
            elif prev_s == "operator":
                name = "operator"
                name_idx = prev
            elif self.tokens[prev].kind == "op" and prev_s in (
                    "+", "-", "*", "/", "%", "^", "&", "|", "~", "!", "=",
                    "<", ">", "+=", "-=", "*=", "/=", "%=", "^=", "&=",
                    "|=", "<<", ">>", "==", "!=", "<=", ">=", "&&", "||",
                    "++", "--", ",", "->*", "->"):
                # operator + ( ... )
                k = prev
                parts = []
                while k >= start and self.tokens[k].kind == "op" and \
                        self._text(k) in (
                            "+", "-", "*", "/", "%", "^", "&", "|", "~",
                            "!", "=", "<", ">", "+=", "-=", "*=", "/=",
                            "%=", "^=", "&=", "|=", "<<", ">>", "==", "!=",
                            "<=", ">=", "&&", "||", "++", "--", ",", "->*",
                            "->"):
                    parts.append(self._text(k))
                    k -= 1
                if k >= start and self._text(k) == "operator":
                    name = "operator" + "".join(reversed(parts))
                    name_idx = k
            if name is None:
                if prev_s == ">":
                    depth = 0
                    k2 = prev
                    while k2 >= start:
                        s2 = self._text(k2)
                        if s2 == ">":
                            depth += 1
                        elif s2 == ">>":
                            depth += 2
                        elif s2 == "<":
                            depth -= 1
                            if depth <= 0:
                                break
                        k2 -= 1
                    if k2 <= start:
                        continue
                    name_idx = k2 - 1
                    name = self._text(name_idx)
                elif prev_s == "~" and prev - 1 >= start:
                    name_idx = prev - 1
                    name = "~" + self._text(name_idx)
                elif self.tokens[prev].kind == "id":
                    name_idx = prev
                    name = prev_s
                    if prev - 1 >= start and \
                            self._text(prev - 1) == "operator":
                        name = "operator " + name
                        name_idx = prev - 1
                else:
                    continue

            # A semicolon between ')' and '{' means this brace is not a body.
            bad = False
            for k in range(j + 1, brace):
                if self._text(k) == ";":
                    bad = True
                    break
            if bad:
                continue

            # Return type: everything before the name, without specifiers.
            ret_parts = []
            for k in range(start, name_idx):
                s = self._text(k)
                if s in ("template", "inline", "static", "constexpr",
                         "consteval", "constinit", "virtual", "explicit",
                         "friend", "extern"):
                    continue
                ret_parts.append(s)
            ret = " ".join(ret_parts).strip()
            is_ctor = False
            parent = self._active_scope(self._scope_of(start))
            if not ret and parent is not None and parent.kind == "type":
                is_ctor = True
            if name.startswith("~"):
                is_ctor = True
            return {"name": name, "name_idx": name_idx, "open": open_idx,
                    "close": j, "return_type": ret, "is_ctor": is_ctor}
        return None

    def _scope_of(self, token_idx):
        if token_idx <= 0:
            return self.global_scope
        return self.token_scope[token_idx - 1] or self.global_scope

    def _parse_params(self, open_idx, close_idx, func_scope):
        params = []
        if open_idx >= close_idx:
            return params
        for (a, b) in self._split_top_level(open_idx + 1, close_idx, (",")):
            sym = self._parse_param(a, b, func_scope)
            if sym is not None:
                params.append(sym)
        return params

    def _parse_param(self, start, end, scope):
        # Strip attributes and a default value.
        if start >= end:
            return None
        while start + 1 < end and self._text(start) == "[" and \
                self._text(start + 1) == "[":
            depth = 0
            k = start
            while k < end:
                if self._text(k) == "[" and k + 1 < end and \
                        self._text(k + 1) == "[":
                    depth += 1
                    k += 2
                    continue
                if self._text(k) == "]" and k + 1 < end and \
                        self._text(k + 1) == "]":
                    depth -= 1
                    k += 2
                    if depth == 0:
                        break
                    continue
                k += 1
            start = k
        original_end = end
        for (a, b) in self._split_top_level(start, end, ("=",)):
            if b < original_end:
                end = b
                break
        if start >= end:
            return None
        # Find the last identifier that is not a type name.
        name_idx = -1
        for k in range(end - 1, start - 1, -1):
            tok = self.tokens[k]
            if tok.kind != "id":
                continue
            word = self._text(k)
            if word in self.type_names or word in KNOWN_TYPES:
                continue
            name_idx = k
            break
        if name_idx < 0:
            return None
        name = self._text(name_idx)
        if name in KEYWORDS or name in self.type_names:
            return None
        type_parts = [self._text(k) for k in range(start, name_idx)]
        type_text = " ".join(type_parts).strip()
        tok = self.tokens[name_idx]
        return Symbol(name, "parameter", type_text,
                      (type_text + " " + name).strip(), tok.start, tok.start,
                      tok.line, tok.col, scope, False)

    # -- statements --------------------------------------------------------

    def _parse_statement(self, start, end, scope):
        if start >= end:
            return
        scope = self._active_scope(scope)

        # Attributes: [[...]]
        while start + 1 < end and self._text(start) == "[" and \
                self._text(start + 1) == "[":
            k = start + 2
            depth = 1
            while k < end and depth > 0:
                if self._text(k) == "[" and k + 1 < end and \
                        self._text(k + 1) == "[":
                    depth += 1
                    k += 2
                elif self._text(k) == "]" and k + 1 < end and \
                        self._text(k + 1) == "]":
                    depth -= 1
                    k += 2
                else:
                    k += 1
            start = k
        if start >= end:
            return

        first = self._text(start)

        # template <...> prefix
        if first == "template" and start + 1 < end and \
                self._text(start + 1) == "<":
            gt = self._find_angle_close(start + 1, end)
            if gt > 0:
                self._parse_template_params(start + 2, gt, scope)
                start = gt + 1
                if start >= end:
                    return
                first = self._text(start)

        if first in ("public", "private", "protected") and \
                (start + 1 >= end or self._text(start + 1) == ":"):
            return
        if first in ("static_assert", "asm", "__attribute__", "friend"):
            if first == "friend":
                self._parse_statement(start + 1, end, scope)
            return
        if first == "using":
            self._parse_using(start, end, scope)
            return
        if first == "typedef":
            self._parse_typedef(start, end, scope)
            return
        if first == "namespace":
            self._parse_namespace_alias(start, end, scope)
            return
        if first in ("class", "struct", "union", "enum"):
            name_idx = self._name_after_keyword(start, end)
            if name_idx >= 0:
                name = self._text(name_idx)
                tok = self.tokens[name_idx]
                self.type_names.add(name)
                self._add_symbol(scope, name, "type", first,
                                 "%s %s" % (first, name), tok.start, tok.start,
                                 tok.line, tok.col, False)
            return

        if self._parse_function_prototype(start, end, scope):
            return
        self._parse_variable(start, end, scope)

    def _parse_using(self, start, end, scope):
        texts = [self._text(k) for k in range(start, end)]
        if len(texts) >= 3 and texts[1] == "namespace":
            if texts[2] == "std":
                self.using_std = True
            name = texts[2]
            self._add_symbol(scope, name, "namespace", "namespace",
                             "namespace " + name, self.tokens[start + 2].start,
                             self.tokens[start + 2].start,
                             self.tokens[start + 2].line,
                             self.tokens[start + 2].col, False)
            return
        if "=" in texts:
            eq = texts.index("=")
            if eq >= 2:
                name = texts[1]
                type_text = " ".join(texts[eq + 1:])
                tok = self.tokens[start + 1]
                self.type_names.add(name)
                self._add_symbol(scope, name, "alias", type_text,
                                 "using %s = %s" % (name, type_text),
                                 tok.start, tok.start, tok.line, tok.col,
                                 False)
            return
        # using std::vector; -> expose the final name
        if texts:
            name = texts[-1]
            tok = self.tokens[end - 1]
            self._add_symbol(scope, name, "type", "", "using " + name,
                             tok.start, tok.start, tok.line, tok.col, False)

    def _parse_typedef(self, start, end, scope):
        name_idx = -1
        for k in range(end - 1, start, -1):
            tok = self.tokens[k]
            if tok.kind == "id":
                name_idx = k
                break
        if name_idx < 0:
            return
        name = self._text(name_idx)
        type_text = " ".join(self._text(k) for k in range(start + 1, name_idx))
        tok = self.tokens[name_idx]
        self.type_names.add(name)
        self._add_symbol(scope, name, "alias", type_text,
                         "typedef %s %s" % (type_text, name), tok.start,
                         tok.start, tok.line, tok.col, False)

    def _parse_namespace_alias(self, start, end, scope):
        texts = [self._text(k) for k in range(start, end)]
        if len(texts) >= 4 and texts[2] == "=":
            name = texts[1]
            type_text = " ".join(texts[3:])
            tok = self.tokens[start + 1]
            self._add_symbol(scope, name, "namespace", type_text,
                             "namespace %s = %s" % (name, type_text),
                             tok.start, tok.start, tok.line, tok.col, False)

    def _parse_function_prototype(self, start, end, scope):
        open_idx = -1
        for k in range(end - 1, start - 1, -1):
            if self._text(k) == "(" and k in self.match and \
                    self.match[k] < end:
                open_idx = k
                break
        if open_idx < 0:
            return False
        close_idx = self.match[open_idx]
        prev = open_idx - 1
        if prev < start:
            return False
        prev_s = self._text(prev)
        if prev_s in CONTROL_KEYWORDS or prev_s in (
                "if", "for", "while", "switch", "catch", "return", "new",
                "delete"):
            return False
        name_idx = prev
        name = prev_s
        if prev_s == ">":
            depth = 0
            k = prev
            while k >= start:
                s = self._text(k)
                if s == ">":
                    depth += 1
                elif s == ">>":
                    depth += 2
                elif s == "<":
                    depth -= 1
                    if depth <= 0:
                        break
                k -= 1
            if k <= start:
                return False
            name_idx = k - 1
            name = self._text(name_idx)
        if self.tokens[name_idx].kind not in ("id",):
            return False
        if name in KEYWORDS or name in CONTROL_KEYWORDS:
            return False
        ret_parts = []
        for k in range(start, name_idx):
            s = self._text(k)
            if s in ("inline", "static", "constexpr", "consteval",
                     "constinit", "virtual", "explicit", "friend", "extern",
                     "template"):
                continue
            ret_parts.append(s)
        ret = " ".join(ret_parts).strip()
        is_ctor = (scope.kind == "type" and
                   (name == scope.name or name == "~" + scope.name))
        if not ret and not is_ctor:
            return False
        # Distinguish "vector<int> v(10);" from a real prototype.
        if not is_ctor and ret:
            args = [self._text(k) for k in range(open_idx + 1, close_idx)]
            looks_like_params = bool(args) and any(
                a in TYPE_KEYWORDS or a in DECL_SPECIFIERS or
                a in self.type_names for a in args)
            if scope.kind in ("function", "block") and not looks_like_params:
                return False
            if not args and ret in self.type_names and \
                    name[:1].islower():
                return False
        params = self._parse_params(open_idx, close_idx, None)
        param_text = ", ".join(p.signature for p in params)
        sig = ("%s %s(%s)" % (ret, name, param_text)).strip()
        if is_ctor:
            sig = "%s(%s)" % (name, param_text)
        tok = self.tokens[name_idx]
        self._add_symbol(scope, name,
                         "method" if scope.kind == "type" else "function",
                         ret or name, sig, tok.start, tok.start, tok.line,
                         tok.col, False, params=params)
        return True

    def _parse_variable(self, start, end, scope):
        if start >= end:
            return
        texts = [self._text(k) for k in range(start, end)]
        # Strip leading declaration specifiers to find the base type.
        type_start = start
        while type_start < end and texts[type_start - start] in DECL_SPECIFIERS:
            type_start += 1
        if type_start >= end:
            return
        first = self._text(type_start)
        first_tok = self.tokens[type_start]
        qualified = (type_start + 1 < end and
                     self._text(type_start + 1) == "::")
        if first == "std" or qualified:
            pass
        elif first in TYPE_KEYWORDS or first in KNOWN_TYPES or \
                first in self.type_names or first in ("struct", "class",
                                                     "enum", "union"):
            pass
        elif first_tok.kind == "id" and first[:1].isupper():
            pass
        else:
            return
        # Obvious expression statements are not declarations.
        if type_start + 1 < end:
            second = self._text(type_start + 1)
            if first_tok.kind == "id" and second in ("=", "(", "[", ",", ";"):
                return

        base_type = " ".join(texts[:type_start - start + 1])

        # Structured binding: auto [a, b] = ...
        if first == "auto":
            for k in range(type_start + 1, end):
                if self._text(k) == "[" and k in self.match:
                    close = self.match[k]
                    for (a, b) in self._split_top_level(k + 1, close, (",")):
                        ids = [self._text(j) for j in range(a, b)
                               if self.tokens[j].kind == "id"]
                        if ids:
                            name = ids[0]
                            tok = self.tokens[a]
                            self._add_symbol(
                                scope, name,
                                "member variable" if scope.kind == "type"
                                else "variable", "auto",
                                "auto " + name, tok.start, tok.start,
                                tok.line, tok.col, False)
                    return

        for (a, b) in self._split_top_level(type_start, end, (",",)):
            seg_start = a
            # Cut an initializer.
            cut = b
            for (x, y) in self._split_top_level(seg_start, b, ("=", "{")):
                if y < b:
                    cut = y
                    break
            if cut <= seg_start:
                continue
            # Function pointer: type (*name)(...)
            name_idx = -1
            for k in range(seg_start + 1, cut - 1):
                if k + 2 >= cut:
                    break
                if self._text(k) == "(" and self._text(k + 1) == "*" and \
                        self.tokens[k + 2].kind == "id":
                    name_idx = k + 2
                    break
            if name_idx < 0:
                # "vector<int> v(10);" -> the declarator ends at '('.
                for k in range(seg_start, cut):
                    if self._text(k) == "(" and \
                            self._top_level_at(seg_start, k):
                        if not (k + 1 < cut and self._text(k + 1) == "*"):
                            cut = k
                            break
                for k in range(cut - 1, seg_start - 1, -1):
                    tok = self.tokens[k]
                    if tok.kind != "id":
                        continue
                    word = self._text(k)
                    if word in self.type_names or word in KNOWN_TYPES or \
                            word in KEYWORDS:
                        continue
                    name_idx = k
                    break
            if name_idx < 0:
                continue
            name = self._text(name_idx)
            if not name or name in KEYWORDS:
                continue
            type_parts = [self._text(k) for k in range(seg_start, name_idx)]
            if a > type_start:
                type_parts.insert(0, base_type)
            type_text = " ".join(type_parts).strip()
            tok = self.tokens[name_idx]
            self._add_symbol(
                scope, name,
                "member variable" if scope.kind == "type" else "variable",
                type_text, (type_text + " " + name).strip(), tok.start,
                tok.start, tok.line, tok.col, False)

    def _parse_control(self, i, scope):
        if i + 1 >= self.n or self._text(i + 1) != "(":
            return
        open_idx = i + 1
        close_idx = self.match.get(open_idx, -1)
        if close_idx < 0:
            return
        kw = self._text(i)
        if kw == "for":
            semis = [k for k in range(open_idx + 1, close_idx)
                     if self._text(k) == ";" and
                     self._top_level_at(open_idx + 1, k)]
            if semis:
                self._parse_variable(open_idx + 1, semis[0], scope)
            else:
                colons = [k for k in range(open_idx + 1, close_idx)
                          if self._text(k) == ":" and
                          self._top_level_at(open_idx + 1, k)]
                if colons:
                    self._parse_variable(open_idx + 1, colons[0], scope)
        elif kw in ("if", "while", "switch"):
            for k in range(open_idx + 1, close_idx):
                if self._text(k) == ";" and self._top_level_at(open_idx + 1, k):
                    self._parse_variable(open_idx + 1, k, scope)
                    return

    def _top_level_at(self, start, target):
        # Angle brackets are intentionally ignored here: in a control header
        # "i < n" is a comparison, not a template argument list.
        paren = bracket = brace = 0
        for k in range(start, target):
            s = self._text(k)
            if s == "(":
                paren += 1
            elif s == ")":
                paren = max(0, paren - 1)
            elif s == "[":
                bracket += 1
            elif s == "]":
                bracket = max(0, bracket - 1)
            elif s == "{":
                brace += 1
            elif s == "}":
                brace = max(0, brace - 1)
        return paren == 0 and bracket == 0 and brace == 0

    def _parse_template_params(self, start, end, scope):
        for (a, b) in self._split_top_level(start, end, (",")):
            ids = [(k, self._text(k)) for k in range(a, b)
                   if self.tokens[k].kind == "id"]
            if not ids:
                continue
            is_type = any(self._text(k) in ("typename", "class")
                          for k in range(a, b))
            name_idx, name = ids[-1]
            tok = self.tokens[name_idx]
            self._add_symbol(scope, name,
                             "template type" if is_type else "constant",
                             "template parameter",
                             "template parameter " + name, tok.start, tok.start,
                             tok.line, tok.col, False)
            if is_type:
                self.type_names.add(name)

    def _parse_enum_body(self, open_idx, scope):
        close_idx = self.match.get(open_idx, -1)
        if close_idx < 0:
            return
        for (a, b) in self._split_top_level(open_idx + 1, close_idx, (",")):
            if a >= b:
                continue
            tok = self.tokens[a]
            if tok.kind != "id":
                continue
            name = self._text(a)
            self._add_symbol(scope, name, "enumerator", scope.name,
                             scope.name + "::" + name, tok.start, tok.start,
                             tok.line, tok.col, True)

    # -- preprocessor ------------------------------------------------------

    def _parse_pp(self, tok, scope):
        raw = self.text[tok.start:tok.end]
        body = raw.lstrip()
        if body.startswith("#"):
            body = body[1:].lstrip()
        m = re.match(r"include\s*([<\"])([^>\"\r\n]*)([>\"]?)", body)
        if m:
            opener, header, closer = m.group(1), m.group(2), m.group(3)
            want = ">" if opener == "<" else '"'
            if closer != want:
                _diag(self.diags, tok.start, tok.line, tok.col, "error",
                      u"Malformed #include: missing closing '%s'" % want,
                      u"#include 缺少结尾的 '%s'" % want, "malformed-include")
            elif header:
                self.includes.append(header)
            return
        m = re.match(r"define\s+([A-Za-z_]\w*)", body)
        if m:
            name = m.group(1)
            value = body[m.end():].strip()
            self._add_symbol(self.global_scope, name, "macro",
                             value or "macro",
                             "#define " + name + (" " + value if value else ""),
                             tok.start, tok.start, tok.line, tok.col, True)
            return
        m = re.match(r"(if|ifdef|ifndef)\b", body)
        if m:
            self._pp_if_stack.append(tok.line)
            return
        m = re.match(r"endif\b", body)
        if m:
            if self._pp_if_stack:
                self._pp_if_stack.pop()
            else:
                _diag(self.diags, tok.start, tok.line, tok.col, "warning",
                      u"#endif without a matching #if",
                      u"多余的 #endif（没有对应的 #if）", "extra-endif")

    # -- public result -----------------------------------------------------

    def finish(self):
        return {
            "scopes": self.scopes,
            "global_scope": self.global_scope,
            "symbols": self.symbols,
            "by_name": self.by_name,
            "type_scopes": self.type_scopes,
            "includes": self.includes,
            "using_std": self.using_std,
            "diags": self.diags,
            "token_scope": self.token_scope,
        }


# ---------------------------------------------------------------------------
# FastEngine
# ---------------------------------------------------------------------------

def _legacy_engine():
    """Return the bundled STL/snippet database module, if importable."""
    try:
        from . import ca_engine
        return ca_engine
    except Exception:
        try:
            import ca_engine
            return ca_engine
        except Exception:
            return None


def _score(name, prefix):
    if not prefix:
        return 0
    if name.startswith(prefix):
        return 0 if name == prefix else 1
    low, p = name.lower(), prefix.lower()
    if low.startswith(p):
        return 2
    if p in low:
        return 5
    it = iter(low)
    if all(ch in it for ch in p):
        return 8
    return 999


def _line_text(text, starts, idx):
    if idx < 0 or idx >= len(starts):
        return ""
    a = starts[idx]
    b = starts[idx + 1] if idx + 1 < len(starts) else len(text)
    return text[a:b]


def _strip_type(type_text):
    s = type_text or ""
    s = re.sub(r"<[^<>]*>", "", s)
    s = re.sub(r"<[^<>]*>", "", s)
    for word in ("const", "volatile", "static", "mutable", "typename",
                 "struct", "class", "enum", "register", "extern"):
        s = re.sub(r"\b" + word + r"\b", " ", s)
    s = s.replace("*", " ").replace("&", " ")
    s = s.replace("std::", "")
    s = s.strip()
    if "::" in s:
        s = s.split("::")[-1].strip()
    return s


class FastEngine(object):
    """Incremental, zero-dependency C++ indexer."""

    def __init__(self, cache_limit=6):
        self.cache_limit = cache_limit
        self._cache = {}
        self._order = []
        self._check_cache = {}
        self._check_order = []
        self.stats = {
            "analyses": 0,
            "incremental": 0,
            "completions": 0,
            "definitions": 0,
            "checks": 0,
            "last_ms": 0.0,
            "last_check_ms": 0.0,
            "reused_tokens": 0,
        }

    # -- cache -------------------------------------------------------------

    def clear(self):
        self._cache.clear()
        self._order = []
        self._check_cache.clear()
        self._check_order = []

    def cache_size(self):
        """Number of cached buffer revisions (for status output/tests)."""
        return len(self._cache)

    def _store(self, key, analysis):
        if key in self._cache:
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._cache[key] = analysis
        self._order.append(key)
        while len(self._order) > self.cache_limit:
            old = self._order.pop(0)
            self._cache.pop(old, None)

    # -- analysis ----------------------------------------------------------

    def analyze(self, text, cache_key=None, version=None):
        if cache_key is None:
            return self._full(text, None, version)
        old = self._cache.get(cache_key)
        if old is not None:
            if old.text is text:
                old.version = version
                return old
            if old.text_len == len(text) and old.text == text:
                old.version = version
                return old
            if old.version == version:
                # Same revision but a different string object: trust it.
                old.text = text
                old.text_len = len(text)
                return old
            return self._incremental(old, text, cache_key, version)
        analysis = self._full(text, cache_key, version)
        self._store(cache_key, analysis)
        return analysis

    def _full(self, text, key, version):
        t0 = time.perf_counter()
        starts = _line_starts(text)
        states = [_NORMAL_STATE] * (len(starts) + 1)
        comments = []
        tokens, lex_diags, comments = _lex(
            text, 0, 0, 0, _NORMAL_STATE, states, comments)
        t1 = time.perf_counter()
        bracket = _bracket_diags(text, tokens)
        parser = _Parser(text, tokens, starts)
        parser.build()
        t2 = time.perf_counter()
        a = Analysis()
        a.text = text
        a.cache_key = key
        a.version = version
        a.text_len = len(text)
        a.tokens = tokens
        a.token_starts = [t.start for t in tokens]
        a.line_starts = starts
        a.line_states = states
        a.comment_ranges = comments
        a.lex_diags = lex_diags
        a.diags = sorted(lex_diags + bracket + parser.diags,
                         key=lambda d: (d.offset, d.line, d.col))
        a.scopes = parser.scopes
        a.global_scope = parser.global_scope
        a.symbols = parser.symbols
        a.by_name = parser.by_name
        a.type_scopes = parser.type_scopes
        a.includes = parser.includes
        a.using_std = parser.using_std
        a.token_scope = parser.token_scope
        a.lex_ms = (t1 - t0) * 1000.0
        a.parse_ms = (t2 - t1) * 1000.0
        a.total_ms = (t2 - t0) * 1000.0
        self.stats["analyses"] += 1
        self.stats["last_ms"] = a.total_ms
        return a

    def _incremental(self, old, text, key, version):
        t0 = time.perf_counter()
        new_starts = _line_starts(text)
        old_starts = old.line_starts
        old_lines = len(old_starts)
        new_lines = len(new_starts)

        first = 0
        while first < old_lines and first < new_lines:
            if _line_text(old.text, old_starts, first) != \
                    _line_text(text, new_starts, first):
                break
            first += 1

        rewind = first
        while rewind < len(old.line_states) and \
                old.line_states[rewind][0] != 0:
            start_line = old.line_states[rewind][2]
            if start_line >= rewind:
                break
            rewind = start_line
        if rewind < 0:
            rewind = 0
        if rewind >= len(new_starts):
            rewind_offset = len(text)
        else:
            rewind_offset = new_starts[rewind]
        state = old.line_states[rewind] if \
            rewind < len(old.line_states) else _NORMAL_STATE
        if state[0] != 0:
            # Defensive fallback: a multi-line construct could not be rewound.
            return self._full(text, key, version)

        kept_tokens = [t for t in old.tokens if t.end <= rewind_offset]
        kept_lex = [d for d in old.lex_diags if d.offset < rewind_offset]
        kept_comments = [r for r in old.comment_ranges
                         if r[1] <= rewind_offset]
        states = [_NORMAL_STATE] * (len(new_starts) + 1)
        for k in range(0, min(rewind, len(old.line_states))):
            states[k] = old.line_states[k]

        new_comments = []
        new_tokens, new_diags, new_comments = _lex(
            text, rewind_offset, rewind, 0, state, states, new_comments)
        tokens = kept_tokens + new_tokens
        lex_diags = kept_lex + new_diags
        comments = kept_comments + new_comments
        bracket = _bracket_diags(text, tokens)
        parser = _Parser(text, tokens, new_starts)
        parser.build()
        t1 = time.perf_counter()

        a = Analysis()
        a.text = text
        a.cache_key = key
        a.version = version
        a.text_len = len(text)
        a.tokens = tokens
        a.token_starts = [t.start for t in tokens]
        a.line_starts = new_starts
        a.line_states = states
        a.comment_ranges = comments
        a.lex_diags = lex_diags
        a.diags = sorted(lex_diags + bracket + parser.diags,
                         key=lambda d: (d.offset, d.line, d.col))
        a.scopes = parser.scopes
        a.global_scope = parser.global_scope
        a.symbols = parser.symbols
        a.by_name = parser.by_name
        a.type_scopes = parser.type_scopes
        a.includes = parser.includes
        a.using_std = parser.using_std
        a.token_scope = parser.token_scope
        a.incremental = True
        a.reused_tokens = len(kept_tokens)
        a.lex_ms = (t1 - t0) * 1000.0
        a.parse_ms = 0.0
        a.total_ms = (t1 - t0) * 1000.0
        self.stats["analyses"] += 1
        self.stats["incremental"] += 1
        self.stats["last_ms"] = a.total_ms
        self.stats["reused_tokens"] = len(kept_tokens)
        self._store(key, a)
        return a

    # -- completion --------------------------------------------------------

    def complete(self, text, offset, prefix=None, cache_key=None,
                 version=None, clangd_style=True):
        a = self.analyze(text, cache_key, version)
        offset = max(0, min(offset, len(text)))
        if a.in_comment(offset):
            return []
        tok = a.token_at(offset)
        if tok is not None and tok.kind in ("str", "chr"):
            if tok.start < offset < tok.end:
                return []
        if tok is not None and tok.kind == "pp":
            if tok.start <= offset <= tok.end:
                return self._include_complete(text, offset, prefix)

        prefix_start = offset
        while prefix_start > 0 and _is_id_cont(text[prefix_start - 1]):
            prefix_start -= 1
        computed = text[prefix_start:offset]
        if prefix is None or computed != prefix:
            prefix = computed

        accessor, receiver = self._accessor(text, prefix_start)
        cache_key_c = (a.version, a.text_len, offset, prefix, accessor,
                       receiver, bool(clangd_style))
        cached = a.completion_cache.get(cache_key_c)
        if cached is not None:
            return cached

        if accessor:
            items = self._member_items(a, receiver, accessor, offset,
                                       prefix, clangd_style)
            if not items:
                items = self._legacy_complete(text, offset, prefix,
                                              cache_key, version,
                                              clangd_style)
        else:
            items = self._visible_items(a, offset, prefix, clangd_style)
            legacy = self._legacy_complete(text, offset, prefix, cache_key,
                                           version, clangd_style)
            seen = set(d.get("trigger") for d in items)
            for d in legacy:
                if d.get("trigger") not in seen:
                    items.append(d)
                    seen.add(d.get("trigger"))

        items = self._finish_items(items, prefix, clangd_style)
        a.completion_cache[cache_key_c] = items
        if len(a.completion_cache) > 64:
            for k in list(a.completion_cache.keys())[:16]:
                a.completion_cache.pop(k, None)
        self.stats["completions"] += 1
        return items

    def _legacy_complete(self, text, offset, prefix, cache_key, version,
                         clangd_style):
        legacy = _legacy_engine()
        if legacy is None:
            return []
        try:
            return legacy.analyze(text, offset, cache_key=cache_key,
                                  cache_version=version,
                                  clangd_style=clangd_style)
        except Exception:
            return []

    def _include_complete(self, text, offset, prefix):
        line_start = text.rfind("\n", 0, offset) + 1
        line = text[line_start:offset]
        m = re.search(r"#\s*include\s*[<\"]([^>\"\r\n]*)$", line)
        if not m:
            return []
        want = m.group(1)
        legacy = _legacy_engine()
        headers = getattr(legacy, "HEADERS", []) if legacy else []
        if not headers:
            headers = [
                "algorithm", "array", "bitset", "cassert", "cctype",
                "cfloat", "climits", "cmath", "cstdio", "cstdlib",
                "cstring", "deque", "functional", "iomanip", "iostream",
                "iterator", "limits", "list", "map", "numeric", "queue",
                "set", "sstream", "stack", "string", "tuple", "utility",
                "vector",
            ]
        out = []
        for h in headers:
            sc = _score(h, want)
            if sc >= 999:
                continue
            out.append({
                "trigger": h, "insert": h, "annotation": u"头文件",
                "annotation_en": "header", "kind": "t",
                "detail": "#include " + h, "detail_en": "#include " + h,
                "_score": sc,
            })
        out.sort(key=lambda d: (d["_score"], len(d["trigger"])))
        for d in out:
            d.pop("_score", None)
        return out[:60]

    def _accessor(self, text, prefix_start):
        j = prefix_start - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j < 0:
            return None, None
        if text[j] == ".":
            return "dot", self._receiver_before(text, j - 1)
        if text[j] == ":" and j - 1 >= 0 and text[j - 1] == ":":
            return "scope", self._receiver_before(text, j - 2)
        if text[j] == ">" and j - 1 >= 0 and text[j - 1] == "-":
            return "arrow", self._receiver_before(text, j - 2)
        return None, None

    def _receiver_before(self, text, end):
        j = end
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j >= 0 and text[j] == "]":
            depth = 1
            k = j - 1
            while k >= 0 and depth > 0:
                if text[k] == "]":
                    depth += 1
                elif text[k] == "[":
                    depth -= 1
                k -= 1
            j = k
        stop = j + 1
        while j >= 0 and (text[j].isalnum() or text[j] in "_:"):
            j -= 1
        return text[j + 1:stop]

    def _visible_items(self, a, offset, prefix, clangd_style):
        scope = a.scope_at(offset)
        seen = set()
        items = []
        while scope is not None:
            for sym in scope.symbols:
                if sym.name in seen or not sym.name:
                    continue
                seen.add(sym.name)
                items.append(self._candidate(sym, offset))
            scope = scope.parent
        return items

    def _member_items(self, a, receiver, accessor, offset, prefix,
                      clangd_style):
        if not receiver:
            return []
        target = None
        type_name = ""
        if receiver == "this":
            scope = a.scope_at(offset)
            while scope is not None and scope.kind != "type":
                scope = scope.parent
            target = scope
        else:
            sym = self._lookup(a, receiver, offset)
            if sym is not None and sym.target_scope is not None and \
                    sym.kind in ("namespace", "type", "enum"):
                target = sym.target_scope
            if sym is not None and target is None:
                if sym.kind == "alias":
                    type_name = _strip_type(sym.type)
                else:
                    type_name = _strip_type(sym.type)
            elif sym is None:
                type_name = receiver
            if target is None and type_name:
                target = a.type_scopes.get(type_name)
                if target is None:
                    # Follow one level of alias.
                    alias = self._lookup(a, type_name, offset)
                    if alias is not None and alias.kind == "alias":
                        target = a.type_scopes.get(_strip_type(alias.type))
        if target is None:
            return []
        seen = set()
        items = []
        self._collect_members(a, target, seen, items, offset)
        if not items:
            return []
        return items

    def _collect_members(self, a, scope, seen, items, offset, depth=0):
        if scope is None or depth > 6:
            return
        for sym in scope.symbols:
            if sym.name in seen or not sym.name:
                continue
            if sym.kind in ("type", "enum") and sym.name == scope.name:
                continue
            seen.add(sym.name)
            items.append(self._candidate(sym, offset))
        for base in scope.base_types:
            self._collect_members(a, a.type_scopes.get(base), seen, items,
                                  offset, depth + 1)

    def _lookup(self, a, name, offset):
        scope = a.scope_at(offset)
        while scope is not None:
            hits = scope.by_name.get(name)
            if hits:
                for sym in hits:
                    if sym.kind in ("variable", "parameter",
                                    "member variable"):
                        return sym
                return hits[0]
            scope = scope.parent
        hits = a.by_name.get(name)
        return hits[0] if hits else None

    def _candidate(self, sym, offset):
        name = sym.name
        kind = sym.kind
        if kind in ("function", "method"):
            code = "m" if kind == "method" else "f"
            ann, ann_en = u"函数", "function"
            detail = sym.signature or sym.type
        elif kind in ("variable", "member variable", "parameter"):
            code = "v"
            ann = u"参数" if kind == "parameter" else u"变量"
            ann_en = "parameter" if kind == "parameter" else "variable"
            detail = sym.type or kind
        elif kind in ("type", "alias", "template type"):
            code = "t"
            ann, ann_en = u"类型", "type"
            detail = sym.type or kind
        elif kind == "enum":
            code = "t"
            ann, ann_en = u"枚举", "enum"
            detail = sym.signature or sym.type
        elif kind == "enumerator":
            code = "c"
            ann, ann_en = u"枚举值", "enumerator"
            detail = sym.type or "enumerator"
        elif kind == "macro":
            code = "u"
            ann, ann_en = u"宏", "macro"
            detail = sym.signature or "macro"
        elif kind == "namespace":
            code = "u"
            ann, ann_en = u"命名空间", "namespace"
            detail = sym.type or "namespace"
        else:
            code = "u"
            ann, ann_en = u"符号", "symbol"
            detail = sym.type or kind
        return {
            "trigger": name,
            "insert": name,
            "annotation": ann,
            "annotation_en": ann_en,
            "kind": code,
            "detail": detail,
            "detail_en": detail,
        }

    def _finish_items(self, items, prefix, clangd_style):
        max_score = 999 if clangd_style else 5
        seen = set()
        out = []
        for d in items:
            trig = d.get("trigger")
            if not trig or trig in seen:
                continue
            sc = _score(trig, prefix)
            if sc >= max_score:
                continue
            seen.add(trig)
            d = dict(d)
            d["_score"] = sc
            out.append(d)
        out.sort(key=lambda d: (d["_score"], len(d["trigger"]),
                                d["trigger"]))
        if len(out) > 200:
            out = out[:200]
        for d in out:
            d.pop("_score", None)
        return out

    # -- definitions -------------------------------------------------------

    def find_definitions(self, text, symbol, offset=0, cache_key=None,
                         version=None):
        a = self.analyze(text, cache_key, version)
        hits = a.by_name.get(symbol, ())
        out = []
        for sym in hits:
            score = 0
            if not self._visible(a, sym, offset):
                score += 5
            if sym.offset > offset:
                score += 2
            if sym.kind in ("function", "method") and sym.is_definition:
                score -= 1
            if sym.kind in ("type", "enum"):
                score -= 1
            if sym.kind in ("variable", "parameter", "member variable"):
                score -= 1
            label = sym.signature or (sym.type + " " + sym.name).strip()
            out.append({
                "line": sym.line,
                "col": sym.col,
                "offset": sym.name_offset,
                "label": label,
                "preview": label,
                "kind": sym.kind,
                "score": score,
                "is_definition": sym.is_definition,
            })
        out.sort(key=lambda d: (d["score"], abs(d["offset"] - offset),
                                d["line"]))
        dedup = set()
        uniq = []
        for d in out:
            key = (d["line"], d["col"])
            if key in dedup:
                continue
            dedup.add(key)
            uniq.append(d)
        self.stats["definitions"] += 1
        return uniq

    def _visible(self, a, sym, offset):
        scope = a.scope_at(offset)
        while scope is not None:
            if scope is sym.scope:
                return True
            scope = scope.parent
        return sym.scope is a.global_scope

    # -- diagnostics -------------------------------------------------------

    def diagnostics(self, text, cache_key=None, version=None):
        a = self.analyze(text, cache_key, version)
        return [{
            "line": d.line,
            "col": d.col,
            "severity": d.severity,
            "en": d.en,
            "zh": d.zh,
            "code": d.code,
            "offset": d.offset,
        } for d in a.diags]

    def basic_checks(self, text, cache_key=None, version=None):
        """Lexer-only instant checks (no structural parse).

        Runs in a background thread on every edit, so it deliberately skips
        the parser and reports only problems the token stream can prove:
        brackets, literals/comments, full-width punctuation, and malformed
        preprocessor directives.
        """
        if cache_key is not None:
            hit = self._check_cache.get(cache_key)
            if hit is not None and hit[0] == version and \
                    hit[1] == len(text) and \
                    (hit[2] is text or hit[2] == text):
                return hit[3]
        t0 = time.perf_counter()
        starts = _line_starts(text)
        states = [_NORMAL_STATE] * (len(starts) + 1)
        tokens, lex_diags, _comments = _lex(
            text, 0, 0, 0, _NORMAL_STATE, states, [])
        diags = lex_diags + _bracket_diags(text, tokens) + \
            _pp_diags(text, tokens)
        diags.sort(key=lambda d: (d.offset, d.line, d.col))
        problems = [d.as_tuple() for d in diags]
        if cache_key is not None:
            if cache_key in self._check_cache:
                try:
                    self._check_order.remove(cache_key)
                except ValueError:
                    pass
            self._check_cache[cache_key] = (
                version, len(text), text, problems)
            self._check_order.append(cache_key)
            while len(self._check_order) > 8:
                old = self._check_order.pop(0)
                self._check_cache.pop(old, None)
        self.stats["checks"] += 1
        self.stats["last_check_ms"] = (time.perf_counter() - t0) * 1000.0
        return problems


_ENGINE = FastEngine()


def engine():
    return _ENGINE


def analyze(text, cache_key=None, version=None):
    return _ENGINE.analyze(text, cache_key, version)


def complete(text, offset, prefix=None, cache_key=None, version=None,
             clangd_style=True):
    return _ENGINE.complete(text, offset, prefix, cache_key, version,
                            clangd_style)


def find_definitions(text, symbol, offset=0, cache_key=None, version=None):
    return _ENGINE.find_definitions(text, symbol, offset, cache_key, version)


def diagnostics(text, cache_key=None, version=None):
    return _ENGINE.diagnostics(text, cache_key, version)


def basic_checks(text, cache_key=None, version=None):
    return _ENGINE.basic_checks(text, cache_key, version)
