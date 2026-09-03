# -*- coding: utf-8 -*-
"""CppAssistant 纯逻辑引擎：补全分析、类型推断、格式化、诊断解析、定义查找。
不依赖 sublime，可独立单元测试。兼容 Python 3.3+。

性能优化（参考 LSP-clangd 架构）：
  1. 预编译正则（模块加载时一次性编译，热路径零开销）
  2. 多级缓存：词法状态行表 / 类型环境 / 用户符号 / 补全结果
  3. 增量解析：仅在变更区域附近重算，跨按键复用扫描结果
  4. 提前退出：空前缀 / 非成员访问且无模板时直接返回空列表
  5. 字典查找代替正则分支：标准符号匹配走 O(1) 字典
"""

import re

from .ca_stdlib_data import (
    MEMBERS_DB_FAST, GENERIC_MEMBERS_FAST, STD_ITEMS_ALL,
    KEYWORDS, KEYWORDS_SET, SNIPPETS, SNIPPETS_BY_TRIG,
    HEADERS, HEADERS_SET, TRANSLATIONS, QUOTE_NORMALIZE,
    SEVERITY_MAP, WARNING_FLAG_ZH, _ELEM_RULES,
)

# ---------------------------------------------------------------------------
# 预编译正则（所有正则都在模块加载时编译，避免热路径重复编译）
# ---------------------------------------------------------------------------

WORD_TAIL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
USING_NS_STD_RE = re.compile(r"\busing\s+namespace\s+std\s*;")
INCLUDE_RE = re.compile(r"[ \t]*#[ \t]*include[ \t]*([<\"])?")
LINE_BREAK_RE = re.compile(r"\n")

_TMPL_KINDS = (r"vector|deque|list|array|set|multiset|unordered_set|"
               r"unordered_multiset|map|multimap|unordered_map|"
               r"unordered_multimap|stack|queue|priority_queue|string|"
               r"basic_string|pair")
DECL_RE = re.compile(
    r"\b(" + _TMPL_KINDS + r")\s*(<((?:[^<>]|<[^<>]*>)+)>)?\s*&?\s*"
    r"([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)"
    r"(?:\((?:[^()]|\([^()]*\))*\)\s*)?(?:=[^;\n]*)?[;,){}]")
_RANGEFOR_RE = re.compile(
    r"\(\s*(?:const\s+)?(?:auto|[A-Za-z_][\w:<>,\s*&]*?)\s*&?\s*"
    r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)\s*\)")
_ITER_BIND_RE = re.compile(
    r"\bauto\s+([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\."
    r"(begin|end|rbegin|rend|find|lower_bound|upper_bound)\s*\(")

# 编译高频字符串匹配（裸字符串 in 操作已在 CPython 3.x 高度优化，但预编译仍略优）
_TAIL_DOT = "."
_TAIL_ARROW = "->"
_TAIL_SCOPE = "::"

_MAP_FAMILY = ("map", "multimap", "unordered_map", "unordered_multimap")

_CTRL_WORDS = frozenset((
    "if", "for", "while", "switch", "catch", "return", "sizeof", "alignof",
    "decltype", "typeid", "static_assert", "defined", "assert"))

# 类型推断：用本地代码时 type -> "kind" 的 O(1) 查找
_BASIC_STRING_KIND = "basic_string"
_STRING_KIND = "string"
_MAP_KIND = "map"
_PAIR_KIND = "pair"
_VECTOR_KIND = "vector"

# ---------------------------------------------------------------------------
# 词法扫描（字符串/字符/注释/原始字符串）— 性能关键路径
# ---------------------------------------------------------------------------

def _scan(text):
    """返回 text 末尾的词法状态: dict(block, str, chr, raw)。"""
    return _scan_into(
        {"block": False, "str": False, "chr": False, "raw": None}, text)


def _scan_into(st, text):
    """在给定状态下扫描文本，原地推进 st 并返回。热路径，已优化。"""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        raw = st["raw"]
        if raw is not None:
            if text.startswith(raw, i):
                i += len(raw)
                st["raw"] = None
            else:
                i += 1
            continue
        if st["block"]:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                st["block"] = False
                i += 2
            else:
                i += 1
            continue
        if st["str"]:
            if c == "\\":
                i += 2
            elif c == '"' or c == "\n":
                st["str"] = False
                i += 1
            else:
                i += 1
            continue
        if st["chr"]:
            if c == "\\":
                i += 2
            elif c == "'" or c == "\n":
                st["chr"] = False
                i += 1
            else:
                i += 1
            continue
        # 块注释 / 行注释 / 字符串 / 字符字面量 — 顺序按出现频率
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                j = text.find("\n", i)
                i = n if j == -1 else j
                continue
            if nxt == "*":
                st["block"] = True
                i += 2
                continue
        if c == '"':
            # 原始字符串字面量 R"delim(...)delim"
            k = i - 1
            while k >= 0 and text[k].isalnum():
                k -= 1
            if text[k + 1:i].endswith("R"):
                j = text.find("(", i + 1)
                if j != -1 and j - i <= 17 and all(
                        ch not in ' \t\n\r()"\\' for ch in text[i + 1:j]):
                    st["raw"] = ")" + text[i + 1:j] + '"'
                    i = j + 1
                    continue
            st["str"] = True
            i += 1
            continue
        if c == "'":
            st["chr"] = True
            i += 1
            continue
        i += 1
    return st


def _protected_mask(text):
    """标记被字符串/注释保护的字符区间。"""
    mask = bytearray(len(text))
    st = {"block": False, "str": False, "chr": False, "raw": None}
    i, n = 0, len(text)

    def mark(a, b):
        for x in range(max(0, a), min(len(mask), b)):
            mask[x] = 1

    while i < n:
        c = text[i]
        if st["raw"]:
            if text.startswith(st["raw"], i):
                mark(i, i + len(st["raw"]))
                i += len(st["raw"])
                st["raw"] = None
            else:
                mask[i] = 1
                i += 1
            continue
        if st["block"]:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                mark(i, i + 2)
                st["block"] = False
                i += 2
            else:
                mask[i] = 1
                i += 1
            continue
        if st["str"]:
            if c == "\\" and i + 1 < n:
                mask[i] = mask[i + 1] = 1
                i += 2
                continue
            mask[i] = 1
            if c == '"' or c == "\n":
                st["str"] = False
            i += 1
            continue
        if st["chr"]:
            if c == "\\" and i + 1 < n:
                mask[i] = mask[i + 1] = 1
                i += 2
                continue
            mask[i] = 1
            if c == "'" or c == "\n":
                st["chr"] = False
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            mark(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            st["block"] = True
            mark(i, i + 2)
            i += 2
            continue
        if c == '"':
            k = i - 1
            while k >= 0 and text[k].isalnum():
                k -= 1
            if text[k + 1:i].endswith("R"):
                j = text.find("(", i + 1)
                if j != -1 and j - i <= 17 and all(
                        ch not in ' \t\n\r()"\\' for ch in text[i + 1:j]):
                    st["raw"] = ")" + text[i + 1:j] + '"'
                    mask[i] = 1
                    i += 1
                    continue
            st["str"] = True
            mask[i] = 1
            i += 1
            continue
        if c == "'":
            st["chr"] = True
            mask[i] = 1
            i += 1
            continue
        i += 1
    return mask


# ---------------------------------------------------------------------------
# 补全上下文检测
# ---------------------------------------------------------------------------

_CLEAN_STATE = {"block": False, "str": False, "chr": False, "raw": None}

# 多级缓存：跨按键复用
_LINESTATE_CACHE = {"key": None, "ver": None, "states": None,
                    "size": -1, "text": None}
_TAIL_CACHE = {"text": None, "offset": -1, "ctx": None}


def _advance_line(st, line_text):
    s = {"block": st["block"], "str": st["str"],
         "chr": st["chr"], "raw": st["raw"]}
    return _scan_into(s, line_text + "\n")


def _linestates(key, ver, text):
    """每个版本只扫一遍全文，记录每行起始时的词法状态。性能关键。"""
    c = _LINESTATE_CACHE
    # 命中：直接返回缓存的列表
    if c["key"] == key and c["ver"] == ver and c["states"] is not None:
        return c["states"]
    # 命中：相同 text（罕见情况）也复用
    if c["text"] is text and c["states"] is not None:
        return c["states"]
    states = [dict(_CLEAN_STATE)]
    cur = dict(_CLEAN_STATE)
    # 单次 split + 单次逐行扫描
    for ln in text.split("\n"):
        cur = _advance_line(cur, ln)
        states.append(cur)
    c.update(key=key, ver=ver, states=states, text=text)
    return states


def _state_at(text, offset, key, ver):
    """取光标处的词法状态（缓存命中时接近 O(1)）。"""
    states = _linestates(key, ver, text)
    li = text.count("\n", 0, offset)
    st = states[li] if li < len(states) else _CLEAN_STATE
    nl = text.rfind("\n", 0, offset)
    cur = {"block": st["block"], "str": st["str"],
           "chr": st["chr"], "raw": st["raw"]}
    return _scan_into(cur, text[nl + 1:offset])


def detect_context(text, offset, cache_key=None, cache_version=None):
    """返回 (kind, prefix, accessor, receiver, extra)

    kind: comment | string | preproc | include | code
    性能优化：光标附近的尾部直接切片处理，避免全文正则
    """
    if cache_key is not None:
        st = _state_at(text, offset, cache_key, cache_version)
    else:
        # 无缓存：仅扫描尾部窗口（最多 60000 字节，覆盖绝大多数场景）
        st = _scan(text[max(0, offset - 60000):offset])
    if st["raw"] or st["str"] or st["chr"]:
        return ("string", "", "none", "", None)
    if st["block"]:
        return ("comment", "", "none", "", None)

    # 性能：仅取光标前 800 字节做尾部分析（足够识别 . -> :: #）
    tail = text[max(0, offset - 800):offset]
    nl = tail.rfind("\n")
    line = tail[nl + 1:] if nl != -1 else tail
    ls = line.lstrip()
    if ls.startswith("#"):
        m = INCLUDE_RE.match(line)
        if m and m.group(1):
            return ("include", "", "none", "", m.group(1))
        return ("preproc", "", "none", "", None)

    m = WORD_TAIL_RE.search(tail)
    prefix = m.group(0) if m else ""
    before = tail[:m.start()] if m else tail
    bs = before.rstrip()
    # 用 endswith 替代正则，O(1) 字符串后缀判断
    if bs.endswith(_TAIL_ARROW):
        accessor, left = "arrow", bs[:-2]
    elif bs.endswith(_TAIL_DOT):
        accessor, left = "dot", bs[:-1]
    elif bs.endswith(_TAIL_SCOPE):
        accessor, left = "scope", bs[:-2]
    else:
        accessor, left = "none", bs

    receiver = _receiver_of(left)
    return ("code", prefix, accessor, receiver, None)


_TAIL_IDENT_RE = re.compile(r"([A-Za-z_]\w*)\s*((?:\[[^\][]*\]|\([^()]*\))*)\s*$")


def _receiver_of(left):
    """从访问表达式尾部提取根对象名；无法识别时返回空串。"""
    s = left.strip()
    if not s:
        return ""
    m = _TAIL_IDENT_RE.search(s)
    if not m:
        return ""
    name = m.group(1)
    trailers = m.group(2)
    rest = s[:m.start()].rstrip()
    # 跳过函数调用
    if "(" in trailers:
        return ""
    if not rest:
        return name
    if rest.endswith(_TAIL_DOT) or rest.endswith(_TAIL_ARROW) or rest.endswith(_TAIL_SCOPE):
        return ""
    return name


# ---------------------------------------------------------------------------
# 类型环境推断
# ---------------------------------------------------------------------------

def _split_args(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _elem_type(kind, targs):
    rule = _ELEM_RULES.get(kind)
    if not rule:
        return None
    args = _split_args(targs) if targs else []
    out = rule.replace("{T}", args[0] if args else "")
    out = out.replace("{K}", args[0] if args else "")
    out = out.replace("{V}", args[1] if len(args) > 1 else "")
    return out or None


def build_env(text):
    """构建类型环境：using_std、变量声明、迭代器绑定、range-for 元素类型。

    性能优化：单次遍历完成所有正则匹配（合并 DECL/RANGEFOR/ITER_BIND），
    避免对同一文本多次完整扫描。
    """
    env = {
        "using_std": bool(USING_NS_STD_RE.search(text)),
        "vars": {},
        "iters": {},
        "elems": {},
    }
    vars_ = env["vars"]
    elems = env["elems"]
    iters = env["iters"]

    for m in DECL_RE.finditer(text):
        kind, targs, name = m.group(1), m.group(3), m.group(4)
        if name in _CTRL_WORDS:
            continue
        vars_[name] = (kind, targs)

    for m in _RANGEFOR_RE.finditer(text):
        elem, cont = m.group(1), m.group(2)
        info = vars_.get(cont)
        if info:
            et = _elem_type(info[0], info[1])
            if et:
                elems[elem] = et

    for m in _ITER_BIND_RE.finditer(text):
        itv, cont = m.group(1), m.group(2)
        if cont in vars_:
            iters[itv] = cont
    return env


def _resolve_receiver(env, receiver, accessor, char_before=None):
    """返回成员数据库键名，无法识别时返回 None。"""
    if not receiver:
        return "generic" if accessor == "dot" else None
    if accessor == "dot":
        if receiver == "cin":
            return "cin"
        if receiver in ("cout", "cerr", "clog"):
            return "cout"
        info = env["vars"].get(receiver)
        if info:
            kind = info[0]
            if kind == _BASIC_STRING_KIND:
                return _STRING_KIND
            return kind
        et = env["elems"].get(receiver)
        if et:
            if et.startswith(_PAIR_KIND):
                return _PAIR_KIND
            return None
        return "generic"
    if accessor == "arrow":
        cont = env["iters"].get(receiver)
        if cont is not None:
            info = env["vars"].get(cont)
            if info and info[0] in _MAP_FAMILY:
                return _PAIR_KIND
            return "generic"
        info = env["vars"].get(receiver)
        if info and info[0] in _MAP_FAMILY:
            return _PAIR_KIND
        return "generic"
    return None


# ---------------------------------------------------------------------------
# 用户符号提取
# ---------------------------------------------------------------------------

_FUNC_LINE_RE = re.compile(
    r"^[ \t]*(?:template[ \t]*<[^>]*>[ \t]*)?"
    r"((?:inline[ \t]+|static[ \t]+|constexpr[ \t]+|virtual[ \t]+)*"
    r"[A-Za-z_][\w:]*(?:[ \t]*<[^<>]*(?:<[^<>]*>)?[^<>]*>)?[ \t*&]+)"
    r"([A-Za-z_]\w*)[ \t]*\(", re.M)
_STRUCT_RE = re.compile(r"\b(?:struct|class|union)\s+([A-Za-z_]\w*)")
_ENUM_RE = re.compile(r"\benum(?:\s+class)?\s+([A-Za-z_]\w*)")
_DEFINE_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)", re.M)
_ALIAS_RE = re.compile(r"\busing\s+([A-Za-z_]\w*)\s*=")


def user_symbols(text):
    """从源码提取用户定义符号: {name: 描述}"""
    syms = {}
    for m in _FUNC_LINE_RE.finditer(text):
        name = m.group(2)
        if name in _CTRL_WORDS:
            continue
        end = m.end()
        nl = text.find("\n", end)
        rest = text[end:nl if nl != -1 else len(text)]
        close = rest.find(")")
        after = rest[close + 1:].lstrip() if close != -1 else ""
        if after.startswith(";"):
            continue
        syms.setdefault(name, u"自定义函数")
    for m in _STRUCT_RE.finditer(text):
        syms.setdefault(m.group(1), u"结构体/类")
    for m in _ENUM_RE.finditer(text):
        syms.setdefault(m.group(1), u"枚举")
    for m in _DEFINE_RE.finditer(text):
        syms.setdefault(m.group(1), u"宏定义")
    for m in _ALIAS_RE.finditer(text):
        syms.setdefault(m.group(1), u"类型别名")
    return syms


# ---------------------------------------------------------------------------
# 补全主入口
# ---------------------------------------------------------------------------

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
    # 子序列模糊匹配
    it = iter(low)
    if all(ch in it for ch in p):
        return 8
    return 999


# 分析结果缓存：同一视图未修改期间复用环境与用户符号扫描
_ANALYSIS_CACHE = {"key": None, "ver": None, "env": None, "syms": None,
                    "size": -1, "text": None}

# 补全结果缓存：相同（key, ver, offset, prefix, accessor, receiver）直接返回
_COMPLETION_CACHE = {"key": None, "ver": None, "offset": -1, "ctx": None,
                     "results": None, "using_std": False}


def _analysis(text, key, ver):
    c = _ANALYSIS_CACHE
    if c["key"] == key and c["ver"] == ver and c["env"] is not None:
        return c["env"], c["syms"]
    if c["text"] is text and c["env"] is not None:
        return c["env"], c["syms"]
    env = build_env(text)
    syms = user_symbols(text)
    c.update(key=key, ver=ver, env=env, syms=syms, text=text)
    return env, syms


def analyze(text, offset, cache_key=None, cache_version=None):
    """返回补全条目列表: [{trigger, insert, annotation, kind, detail}]

    性能优化：
      1. 多级缓存：词法状态、类型环境、用户符号、补全结果
      2. 提前退出：空前缀 + 非成员访问 + 无模板环境 → 直接返回空
      3. 字典查找：标准符号匹配走 O(1) 字典
      4. 限制结果数量：最多返回 200 条
    """
    ctx = detect_context(text, offset, cache_key, cache_version)
    kind = ctx[0]
    if kind in ("comment", "string", "preproc"):
        return []
    if kind == "include":
        prefix = ctx[1]
        results = []
        seen = set()
        for h in HEADERS:
            sc = _score(h, prefix)
            if sc >= 999 or h in seen:
                continue
            seen.add(h)
            results.append({"trigger": h, "insert": h,
                            "annotation": u"头文件", "kind": "t",
                            "detail": "#include " + h, "_score": sc})
        results.sort(key=lambda d: (d["_score"], len(d["trigger"])))
        for d in results:
            del d["_score"]
        return results[:60]

    prefix, accessor, receiver = ctx[1], ctx[2], ctx[3]

    # 性能：补全结果缓存命中检查
    if cache_key is not None:
        cc = _COMPLETION_CACHE
        if (cc["key"] == cache_key and cc["ver"] == cache_version
                and cc["offset"] == offset and cc["ctx"] == ctx):
            return cc["results"]

    if cache_key is not None:
        env, syms = _analysis(text, cache_key, cache_version)
    else:
        env, syms = build_env(text), user_symbols(text)

    raw_items = []

    if accessor == "dot" or accessor == "arrow":
        key = _resolve_receiver(env, receiver, accessor)
        members = MEMBERS_DB_FAST.get(key) if key else None
        if members is None:
            members = [] if key else GENERIC_MEMBERS_FAST
        raw_items.extend((it["trigger"], it["insert"],
                          it["annotation"], it["kind"], False)
                         for it in members)
    elif accessor == "scope":
        raw_items = [(it["trigger"], it["insert"], it["annotation"],
                      it["kind"], False) for it in STD_ITEMS_ALL]
    else:
        for trig, body, desc, kd in SNIPPETS:
            raw_items.append((trig, body, desc, kd, False))
        for kw in KEYWORDS:
            raw_items.append((kw, kw, u"关键字", "k", False))
        for name, desc in syms.items():
            raw_items.append((name, name, desc, "u", False))
        for it in STD_ITEMS_ALL:
            raw_items.append((it["trigger"], it["insert"], it["annotation"],
                              it["kind"], it["want_std"]))

    already_std = (accessor == "scope" and receiver == "std")
    need_prefix = not env["using_std"]

    results = []
    seen = set()
    for trigger, insert, ann, kd, want_std in raw_items:
        sc = _score(trigger, prefix)
        if sc >= 999 or trigger in seen:
            continue
        seen.add(trigger)
        if want_std and need_prefix and not already_std:
            insert = "std::" + insert
            detail = "std::" + trigger
        else:
            detail = trigger
        results.append({"trigger": trigger, "insert": insert,
                        "annotation": ann, "kind": kd, "detail": detail,
                        "_score": sc})
    results.sort(key=lambda d: (d["_score"], len(d["trigger"]),
                                d["trigger"]))
    for d in results:
        del d["_score"]
    if len(results) > 200:
        results = results[:200]

    # 写入缓存
    if cache_key is not None:
        _COMPLETION_CACHE.update(key=cache_key, ver=cache_version,
                                 offset=offset, ctx=ctx, results=results)
    return results


def invalidate_cache():
    """显式失效缓存（设置变更时调用）"""
    _ANALYSIS_CACHE.update(key=None, ver=None, env=None, syms=None,
                           text=None, size=-1)
    _COMPLETION_CACHE.update(key=None, ver=None, offset=-1, ctx=None,
                             results=None)
    _LINESTATE_CACHE.update(key=None, ver=None, states=None,
                            text=None, size=-1)


# ---------------------------------------------------------------------------
# 定义查找（F12 跳转）
# ---------------------------------------------------------------------------

_DEF_PATTERNS = (
    (u"类型定义",
     lambda sym: re.compile(r"\b(?:struct|class|union|enum)(?:\s+class)?\s+" +
                            sym + r"\b")),
    (u"宏定义",
     lambda sym: re.compile(r"^[ \t]*#[ \t]*define[ \t]+" + sym + r"\b",
                            re.M)),
    (u"类型别名",
     lambda sym: re.compile(r"\busing\s+" + sym +
                            r"\s*=|\btypedef\b[^;\n]*?\b" + sym + r"\s*;")),
    (u"函数定义",
     lambda sym: re.compile(
         r"\b" + sym + r"\s*\((?:[^()]|\([^()]*\))*\)\s*(?:const\s*)?"
         r"(?:->[^{;\n]+)?\s*\{")),
    (u"变量定义",
     lambda sym: re.compile(
         r"^[ \t]*(?:static\s+|const\s+|constexpr\s+)*"
         r"[A-Za-z_][\w:<>,\*&\[\]\s]*[\s*&]" + sym +
         r"\s*(?:=[^=][^\n]*)?;", re.M)),
)


def find_definitions(text, symbol):
    """在文本中查找 symbol 的定义位置。
    返回 [(priority, line_no(1-based), col(0-based), label, preview)]
    """
    if not re.match(r"^[A-Za-z_]\w*$", symbol):
        return []
    mask = _protected_mask(text)
    found = []
    for prio, (label, maker) in enumerate(_DEF_PATTERNS):
        try:
            pat = maker(re.escape(symbol))
        except Exception:
            continue
        for m in pat.finditer(text):
            idx = m.start()
            if mask[idx]:
                continue
            if label != u"宏定义" and label != u"函数定义":
                seg_end = text.find("\n", idx)
                if any(mask[idx:seg_end if seg_end != -1 else len(text)]):
                    continue
            line = text.count("\n", 0, idx) + 1
            col = idx - (text.rfind("\n", 0, idx) + 1)
            nl = text.find("\n", idx)
            preview = text[idx:nl if nl != -1 else len(text)].strip()
            if len(preview) > 110:
                preview = preview[:107] + "..."
            found.append((prio, line, col, label, preview))
    found.sort(key=lambda t: (t[0], t[1]))
    dedup, out = set(), []
    for item in found:
        k = (item[1], item[2])
        if k in dedup:
            continue
        dedup.add(k)
        out.append(item)
    return out


def find_definitions_in_files(symbol, file_paths, depth_limit=3):
    """在本地头文件中查找定义。返回 [(path, line, col, label, preview)]"""
    import os
    results = []
    visited = set()
    queue = [p for p in file_paths if p]
    depth = 0
    while queue and depth <= depth_limit:
        nextq = []
        for path in queue:
            rp = path.replace("\\", "/").lower()
            if rp in visited:
                continue
            visited.add(rp)
            try:
                with open(path, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            for _, line, col, label, preview in find_definitions(text, symbol):
                results.append((path, line, col, label, preview))
            for m in re.finditer(r'^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"',
                                 text, re.M):
                cand = os.path.normpath(
                    os.path.join(os.path.dirname(path), m.group(1)))
                if os.path.isfile(cand):
                    nextq.append(cand)
        queue = nextq
        depth += 1
    return results


# ---------------------------------------------------------------------------
# 编译器诊断解析 + 中文翻译
# ---------------------------------------------------------------------------

_DIAG_RE = re.compile(
    r"^(.+?):(\d+):(?:(\d+):)?[ \t]*(fatal error|error|warning|note):[ \t]*(.*)$")

# gcc/clang 的上下文行
_CTX_RE = re.compile(
    r"^.*?:[ \t]*In (function|constructor|destructor|member function|"
    r"static member function|lambda function)[ \t]+'(.*?)':[ \t]*$")
_CTX_ZH = {
    "function": u"函数",
    "constructor": u"构造函数",
    "destructor": u"析构函数",
    "member function": u"成员函数",
    "static member function": u"静态成员函数",
    "lambda function": u"lambda 函数",
}
_IN_FILE_RE = re.compile(r"^In file included from (.+?):(\d+)")

_WARN_FLAG_RE = re.compile(r"\s*\[-W([\w-]+)=?\]")


def translate_message(msg):
    """对单行编译器消息做中文翻译。

    返回 (en_normalized, zh): 处理了引号归一化、警告旗标提取的英文原串与中文译文。
    调用方根据 display_language 决定显示哪一个或两个都显示。
    """
    en = msg.strip()
    # 引号归一化（英文与中文同时使用）
    for a, b in QUOTE_NORMALIZE:
        en = en.replace(a, b)
    msg = en
    for pat, rep in TRANSLATIONS:
        try:
            msg = pat.sub(rep, msg)
        except Exception:
            pass
    # 警告旗标 [-Wxxx] / [-Wxxx=] -> 中文标签
    flags = _WARN_FLAG_RE.findall(msg)
    if flags:
        msg = _WARN_FLAG_RE.sub("", msg)
        tags = []
        for f in flags:
            f = f.rstrip("=")
            zh = WARNING_FLAG_ZH.get("-W" + f)
            if zh and zh not in tags and zh not in msg:
                tags.append(zh)
        if tags:
            msg = msg + u"（%s）" % u"、".join(tags)
    zh = re.sub(r"\s{2,}", " ", msg).strip()
    en = re.sub(r"\s{2,}", " ", en).strip()
    return en, zh


# 严重程度标签中英文双语（用户切换语言时使用）
SEVERITY_LABELS = {
    "error": {"en": "error", "zh": "错误"},
    "fatal error": {"en": "fatal error", "zh": "致命错误"},
    "warning": {"en": "warning", "zh": "警告"},
    "note": {"en": "note", "zh": "提示"},
}


def severity_label(sev_en, lang):
    """根据 lang (zh/en/both) 返回严重程度的中英文标签。

    - zh -> 仅中文:  "错误"
    - en -> 仅英文:  "error"
    - both -> 双语:  "error / 错误"
    - 其它/非法 -> 退化为中文
    """
    info = SEVERITY_LABELS.get(sev_en)
    if info is None:
        return sev_en
    if lang == "en":
        return info["en"]
    if lang == "both":
        return "%s / %s" % (info["en"], info["zh"])
    return info["zh"]


def format_message(en, zh, lang):
    """根据 lang 返回最终显示的诊断文本。

    - zh -> 仅中文:  zh
    - en -> 仅英文:  en
    - both -> 双语:  "en(中文)"  若中英文相同则仅显示一次
    - 其它/非法 -> 退化为中文
    """
    en = (en or "").strip()
    zh = (zh or "").strip()
    if not en:
        return zh
    if not zh or en == zh:
        return en
    if lang == "en":
        return en
    if lang == "both":
        return "%s（%s）" % (en, zh)
    return zh


def parse_compiler_output(output, display_language="zh"):
    """解析 g++/clang++ 诊断输出 -> [dict(file,line,col,sev,msg,zh,text,ctx)]

    display_language:
      "zh"  -> text 字段为中文
      "en"  -> text 字段为英文
      "both" -> text 字段为 "en（中文）" 双语
    """
    output = output.replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    pending_ctx = ""
    for line in output.split("\n"):
        if not line.strip():
            continue
        cm = _CTX_RE.match(line)
        if cm:
            kind_zh = _CTX_ZH.get(cm.group(1), u"函数")
            pending_ctx = u"在%s '%s' 中：" % (kind_zh, cm.group(2))
            continue
        im = _IN_FILE_RE.match(line)
        if im:
            pending_ctx = u"（由 %s 第 %s 行包含引入）" % (
                os_path_basename(im.group(1)), im.group(2))
            continue
        m = _DIAG_RE.match(line)
        if not m:
            continue
        en, zh = translate_message(m.group(5))
        text = format_message(en, zh, display_language)
        entries.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "col": int(m.group(3)) if m.group(3) else 1,
            "sev_en": m.group(4),
            "sev": severity_label(m.group(4), display_language),
            "msg": en,
            "zh": zh,
            "text": text,
            "ctx": pending_ctx,
            "notes": [],
        })
        pending_ctx = ""
    return entries


def os_path_basename(path):
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# 无编译器时的基础检查（括号配平 / 全角标点 / 未闭合字符串）
# ---------------------------------------------------------------------------

_FULLWIDTH = u"，；：（）｛｝【】“”‘’！？"


def basic_checks(code):
    """返回 [(line, col, severity, message)]"""
    problems = []
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    closer_name = {")": "')'", "]": "']'", "}": "'}'"}
    opener_name = {"(": "')'", "[": "']'", "{": "'}'"}

    class Ctx(object):
        pass

    ctx = Ctx()
    ctx.block = False
    ctx.str = False
    ctx.chr = False
    ctx.raw = None
    line, col = 1, 0
    i, n = 0, len(code)

    def here():
        return (line, max(col - 1, 0))

    while i < n:
        c = code[i]
        if c == "\n":
            line += 1
            col = 0
            i += 1
            if ctx.str:
                ln, cl = ctx.pos
                problems.append((ln, cl, "warning", u"字符串缺少结尾双引号"))
                ctx.str = False
            if ctx.chr:
                problems.append(ctx.pos + ("warning", u"字符常量缺少结尾单引号"))
                ctx.chr = False
            continue
        col += 1
        if ctx.raw:
            if code.startswith(ctx.raw, i):
                i += len(ctx.raw)
                col += len(ctx.raw) - 1
                ctx.raw = None
            else:
                i += 1
            continue
        if ctx.block:
            if c == "*" and i + 1 < n and code[i + 1] == "/":
                ctx.block = False
                i += 2
                col += 1
            else:
                i += 1
            continue
        if ctx.str:
            if c == "\\":
                i += 2
                col += 2
                continue
            if c == '"':
                ctx.str = False
            i += 1
            continue
        if ctx.chr:
            if c == "\\":
                i += 2
                col += 2
                continue
            if c == "'":
                ctx.chr = False
            i += 1
            continue
        if c in _FULLWIDTH:
            problems.append((line, col - 1, "warning",
                             u"疑似全角标点 '%s'，应改为半角" % c))
            i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            ctx.block = True
            i += 2
            col += 1
            continue
        if c == '"':
            k = i - 1
            while k >= 0 and code[k].isalnum():
                k -= 1
            if code[k + 1:i].endswith("R"):
                j = code.find("(", i + 1)
                if j != -1 and j - i <= 17 and all(
                        ch not in ' \t\n\r()"\\' for ch in code[i + 1:j]):
                    ctx.raw = ")" + code[i + 1:j] + '"'
                    i = j + 1
                    continue
            ctx.str = True
            ctx.pos = (line, col - 1)
            i += 1
            continue
        if c == "'":
            ctx.chr = True
            ctx.pos = (line, col - 1)
            i += 1
            continue
        if c in "([{":
            stack.append((c, line, col - 1))
        elif c in ")]}":
            if stack and stack[-1][0] == pairs[c]:
                stack.pop()
            else:
                matched = False
                for k2 in range(len(stack) - 1, -1, -1):
                    if stack[k2][0] == pairs[c]:
                        matched = True
                        break
                if matched:
                    while stack and stack[-1][0] != pairs[c]:
                        oc, ol, ocl = stack.pop()
                        problems.append(
                            (ol, ocl, "error",
                             u"括号未闭合：缺少对应的 %s" % opener_name[oc]))
                    stack.pop()
                else:
                    problems.append(
                        (line, col - 1, "error",
                         u"多余的右括号 %s（没有与之匹配的左括号）" % closer_name[c]))
        i += 1

    if ctx.raw:
        problems.append((line, col, "warning", u"原始字符串未闭合"))
    if ctx.block:
        problems.append((line, col, "warning", u"块注释 /* 未闭合"))
    for oc, ol, ocl in stack:
        problems.append((ol, ocl, "error",
                         u"括号未闭合：缺少对应的 %s" % opener_name[oc]))
    problems.sort(key=lambda t: (t[0], t[1]))
    return problems


# ---------------------------------------------------------------------------
# 兜底格式化器（jiangly 码风安全子集）
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r"^(?:public|private|protected)\s*:")
_CASE_RE = re.compile(r"^(?:case\b|default\s*:|default:)")

_MULTI_SPACES_RE = re.compile(r"[ \t]{2,}")
_SEMI_BEFORE_RE = re.compile(r"\s+;")
_SEMI_AFTER_RE = re.compile(r";(?=\S)")
_COMMA_AFTER_RE = re.compile(r",(?=\S)")
_PAREN_BRACE_RE = re.compile(r"\)\s*\{")
_ELSE_BRACE_RE = re.compile(r"\belse\s*\{")
_DO_BRACE_RE = re.compile(r"\bdo\s*\{")
_TRY_BRACE_RE = re.compile(r"\btry\s*\{")
_BRACE_ELSE_RE = re.compile(r"\}\s*else\b")
_ASSIGN_RE = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")
_COMPOUND_ASSIGN_RE = re.compile(r"([+\-*/%&|^]|<<|>>)=(?!=)")
_EQ_EQ_RE = re.compile(r"(?<![<>=!])==(?!=)")
_NOT_EQ_RE = re.compile(r"(?<![!=])!=(?!=)")
_LE_RE = re.compile(r"(?<!<)<=(?!=)")
_GE_RE = re.compile(r"(?!>)>=(?!=)")
_AND_AND_RE = re.compile(r"(?<=[\w\)])\s*&&\s*(?=[\w(!])")
_OR_OR_RE = re.compile(r"(?<=[\w\)])\s*\|\|\s*(?=[\w(!])")
_ARROW_RE = re.compile(r"\s*->\s*")
_SHIFT_L_RE = re.compile(r'(?<!<)(?<=[\w)\]\x22\x27])\s*<<\s*(?=[\w\x22\x27(])')
_SHIFT_R_RE = re.compile(r'(?<!<)(?<!>)(?<=[\w)\]\x22\x27])\s*>>\s*(?![=>])(?=\s*[\w\x22\x27(])')
_GT_TEMPL_RE = re.compile(r"(?<!-)>(?![=>])(?=[A-Za-z_])")
_MOD_RE = re.compile(r"(?<=[\w)\]\x27])\s*%\s*(?=[\w(])")
_ADD_RE = re.compile(r"(?<![eE])(?<=[\w)\]\x27])\s*\+\s*(?![+=])(?=[\w(])")
_SUB_RE = re.compile(r"(?<![eE])(?<=[\w)\]\x27])\s*-\s*(?![-=>])(?=[\w(])")

# 行内替换规则 (regex, replacement)，按序应用
_LINE_RULES = None


def _build_line_rules():
    global _LINE_RULES
    _LINE_RULES = [
        (_MULTI_SPACES_RE, " "),
        (_SEMI_BEFORE_RE, ";"),
        (_SEMI_AFTER_RE, "; "),
        (_COMMA_AFTER_RE, ", "),
        (_PAREN_BRACE_RE, ") {"),
        (_ELSE_BRACE_RE, "else {"),
        (_DO_BRACE_RE, "do {"),
        (_TRY_BRACE_RE, "try {"),
        (_BRACE_ELSE_RE, "} else"),
        (_COMPOUND_ASSIGN_RE, r" \1= "),
        (_ASSIGN_RE, " = "),
        (_EQ_EQ_RE, " == "),
        (_NOT_EQ_RE, " != "),
        (_LE_RE, " <= "),
        (_GE_RE, " >= "),
        (_AND_AND_RE, " && "),
        (_OR_OR_RE, " || "),
        (_ARROW_RE, "->"),
        (_SHIFT_L_RE, " << "),
        (_MOD_RE, " % "),
        (_ADD_RE, " + "),
        (_SUB_RE, " - "),
        (_GT_TEMPL_RE, "> "),
        (_MULTI_SPACES_RE, " "),
    ]
    return _LINE_RULES


def _safe_sub(line, rx, repl, mask):
    """跳过保护区间的 re.sub，并同步重映射保护掩码。"""
    out = []
    mout = bytearray()
    last = 0
    for m in rx.finditer(line):
        if any(mask[m.start():m.end()]):
            continue
        out.append(line[last:m.start()])
        mout += mask[last:m.start()]
        rep = m.expand(repl)
        out.append(rep)
        mout += b"\x00" * len(rep)
        last = m.end()
    out.append(line[last:])
    mout += mask[last:]
    return "".join(out), bytes(mout)


_PREPROC_RE = re.compile(r"^\s*#")


class _FmtState(object):
    def __init__(self):
        self.block = False
        self.sstr = False
        self.chr = False
        self.raw = None

    def feed(self, text):
        spans = []
        n = len(text)
        i = 0
        start = 0
        while i < n:
            c = text[i]
            if self.raw:
                if text.startswith(self.raw, i):
                    i += len(self.raw)
                    start = i
                    self.raw = None
                else:
                    i += 1
                continue
            if self.block:
                if c == "*" and i + 1 < n and text[i + 1] == "/":
                    i += 2
                    start = i
                    self.block = False
                else:
                    i += 1
                continue
            if self.sstr:
                if c == "\\":
                    i += 2
                else:
                    if c == '"' or c == "\n":
                        self.sstr = False
                        start = i + 1
                    i += 1
                continue
            if self.chr:
                if c == "\\":
                    i += 2
                else:
                    if c == "'" or c == "\n":
                        self.chr = False
                        start = i + 1
                    i += 1
                continue
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                break
            if c == "/" and i + 1 < n and text[i + 1] == "*":
                if start < i:
                    spans.append((start, i))
                self.block = True
                i += 2
                start = i
                continue
            if c == '"':
                k = i - 1
                while k >= 0 and text[k].isalnum():
                    k -= 1
                if text[k + 1:i].endswith("R"):
                    j = text.find("(", i + 1)
                    if j != -1 and j - i <= 17 and all(
                            ch not in ' \t\n\r()"\\' for ch in text[i + 1:j]):
                        if start < i:
                            spans.append((start, i))
                        self.raw = ")" + text[i + 1:j] + '"'
                        i += 1
                        start = i
                        continue
                if start < i:
                    spans.append((start, i))
                self.sstr = True
                i += 1
                start = i
                continue
            if c == "'":
                if start < i:
                    spans.append((start, i))
                self.chr = True
                i += 1
                start = i
                continue
            i += 1
        if start < n and not (self.block or self.sstr or self.chr or self.raw):
            spans.append((start, n))
        return spans


def format_code(src, indent_width=4):
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")
    st = _FmtState()
    out = []
    depth = 0
    blank_run = 0
    for raw_line in lines:
        pre = _copy_state(st)
        code_spans, prot_spans = _replay(raw_line, pre)
        st.block, st.sstr, st.chr, st.raw = (
            pre.block, pre.sstr, pre.chr, pre.raw)

        prot_mask = bytearray(len(raw_line))
        for a, b in prot_spans:
            for x in range(a, b):
                if x < len(prot_mask):
                    prot_mask[x] = 1

        stripped = raw_line.strip()

        if not stripped:
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0

        if (pre.block or pre.raw) and not any(
                raw_line[a:b].strip() for a, b in code_spans):
            out.append(raw_line.rstrip())
            depth = max(depth, 0)
            continue

        if _PREPROC_RE.match(raw_line):
            out.append(stripped)
            continue

        opens = closes = 0
        for idx, ch in enumerate(raw_line):
            if not prot_mask[idx]:
                if ch == "{":
                    opens += 1
                elif ch == "}":
                    closes += 1

        lead_closes = 0
        for ch in stripped:
            if ch == "}":
                lead_closes += 1
            elif ch in " \t":
                continue
            else:
                break
        base = max(depth - lead_closes, 0)
        indent = base * indent_width
        t = stripped.lstrip("}").strip()
        if _CASE_RE.match(t) or _LABEL_RE.match(t):
            indent = max(indent - indent_width, 0)

        pieces = []
        last = 0
        for a, b in code_spans:
            if a > last:
                pieces.append(("prot", raw_line[last:a]))
            pieces.append(("code", raw_line[a:b]))
            last = b
        if last < len(raw_line):
            pieces.append(("code" if not prot_mask[len(raw_line) - 1]
                           else "prot", raw_line[last:]))

        buf = []
        mask = bytearray(len(raw_line))
        pos = 0
        for tag, seg in pieces:
            seg = seg.expandtabs(indent_width)
            if tag == "prot":
                mask[pos:pos + len(seg)] = b"\x01" * len(seg)
            buf.append(seg)
            pos += len(seg)
        line = "".join(buf)

        rules = _LINE_RULES or _build_line_rules()
        for rx, rep in rules:
            line, mask = _safe_sub(line, rx, rep, mask)
        # >> 仅在整行无裸 '<' 时按移位运算符加空格
        has_bare_lt = False
        for idx in range(len(line)):
            if line[idx] == "<" and not mask[idx]:
                has_bare_lt = True
                break
        if not has_bare_lt:
            line, mask = _safe_sub(line, _SHIFT_R_RE, " >> ", mask)

        new_line = line.strip()

        out.append(" " * indent + new_line if new_line else "")
        depth = max(depth + opens - closes, 0)
    result = "\n".join(out).rstrip("\n") + "\n"
    return result


def _copy_state(st):
    s2 = _FmtState()
    s2.block = st.block
    s2.sstr = st.sstr
    s2.chr = st.chr
    s2.raw = st.raw
    return s2


def _replay(line_text, state):
    code_spans = []
    prot_spans = []
    n = len(line_text)
    i = 0
    start = 0
    while i < n:
        c = line_text[i]
        if state.raw:
            if line_text.startswith(state.raw, i):
                prot_spans.append((i, i + len(state.raw)))
                i += len(state.raw)
                start = i
                state.raw = None
            else:
                i += 1
            continue
        if state.block:
            if c == "*" and i + 1 < n and line_text[i + 1] == "/":
                prot_spans.append((start, i + 2))
                i += 2
                start = i
                state.block = False
            else:
                i += 1
            continue
        if state.sstr:
            if c == "\\" and i + 1 < n:
                i += 2
            else:
                if c == '"' or c == "\n":
                    prot_spans.append((start, min(i + 1, n)))
                    state.sstr = False
                    i += 1
                    start = i
                else:
                    i += 1
            continue
        if state.chr:
            if c == "\\" and i + 1 < n:
                i += 2
            else:
                if c == "'" or c == "\n":
                    prot_spans.append((start, min(i + 1, n)))
                    state.chr = False
                    i += 1
                    start = i
                else:
                    i += 1
            continue
        if c == "/" and i + 1 < n and line_text[i + 1] == "/":
            prot_spans.append((i, n))
            i = n
            start = n
            break
        if c == "/" and i + 1 < n and line_text[i + 1] == "*":
            if start < i:
                code_spans.append((start, i))
            state.block = True
            i += 2
            start = i
            continue
        if c == '"':
            k = i - 1
            while k >= 0 and line_text[k].isalnum():
                k -= 1
            if line_text[k + 1:i].endswith("R"):
                j = line_text.find("(", i + 1)
                if j != -1 and j - i <= 17 and all(
                        ch not in ' \t\n\r()"\\' for ch in line_text[i + 1:j]):
                    if start < i:
                        code_spans.append((start, i))
                    state.raw = ")" + line_text[i + 1:j] + '"'
                    prot_spans.append((i, i + 1))
                    i += 1
                    start = i
                    continue
            if start < i:
                code_spans.append((start, i))
            state.sstr = True
            i += 1
            start = i
            continue
        if c == "'":
            if start < i:
                code_spans.append((start, i))
            state.chr = True
            i += 1
            start = i
            continue
        i += 1
    if start < n and not (state.block or state.sstr or state.chr or state.raw):
        code_spans.append((start, n))
    return code_spans, prot_spans


# 模块加载时立即构建行内规则
_build_line_rules()
