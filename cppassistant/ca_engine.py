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
    KEYWORDS,
    HEADERS, TRANSLATIONS, QUOTE_NORMALIZE,
    WARNING_FLAG_ZH, _ELEM_RULES,
    STD_SYMBOL_HEADER, STD_HEADERS_SET, en_detail,
    PBDS_NAMESPACE_ITEMS, PBDS_HEADERS, header_detail,
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
               r"basic_string|pair|tree|gp_hash_table|cc_hash_table|"
               r"stringstream|istringstream|ostringstream")
# 模板实参用括号配对扫描提取：正则形态 (?:[^<>]|<[^<>]*>)+ 只能覆盖
# 一层嵌套，vector<vector<vector<int>>> 三层即失配（XS8）。
_DECL_HEAD_RE = re.compile(r"\b(" + _TMPL_KINDS + r")\b\s*(<)?")
_DECL_TAIL_RE = re.compile(
    r"\s*&?\s*([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)"
    r"(?:\((?:[^()]|\([^()]*\))*\)\s*)?(?:=[^;\n]*)?[;,){}]")


def _match_angle(text, lt):
    """lt 指向 '<'，返回配对 '>' 之后的位置；未闭合返回 -1。"""
    depth = 0
    i, n = lt, len(text)
    while i < n:
        c = text[i]
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _iter_decl_matches(text):
    """yield (kind, targs, name, kind_start, name_start)。"""
    for m in _DECL_HEAD_RE.finditer(text):
        if m.group(2):
            gt = _match_angle(text, m.end(2) - 1)
            if gt == -1:
                continue
            targs = text[m.end(2):gt - 1]
            tail_from = gt
        else:
            targs = ""
            tail_from = m.end()
        tail = _DECL_TAIL_RE.match(text, tail_from)
        if not tail:
            continue
        yield (m.group(1), targs, tail.group(1), m.start(1), tail.start(1))
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


def _is_pbds_null_type(targs):
    args = _split_args(targs) if targs else []
    if len(args) >= 2:
        return args[1].strip().split("::")[-1] == "null_type"
    return False


def _pbds_receiver_kind(kind, targs):
    if kind == "tree":
        if _is_pbds_null_type(targs):
            return "pb_ds_tree_set"
        return "pb_ds_tree_map"
    if kind in ("gp_hash_table", "cc_hash_table"):
        if _is_pbds_null_type(targs):
            return "pb_ds_hash_set"
        return "pb_ds_hash_map"
    return None

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
                # 行注释状态要记下：光标在 // 内时补全须退场（XS12）。
                st["comment"] = True
                j = text.find("\n", i)
                i = n if j == -1 else j
                if i < n and text[i] == "\n":
                    st["comment"] = False   # 行注释不跨行
                    i += 1
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
    s = {"block": st["block"], "str": st["str"], "chr": st["chr"],
         "raw": st["raw"], "comment": st.get("comment", False)}
    return _scan_into(s, line_text + "\n")


def _linestates(key, ver, text):
    """每个版本只扫一遍全文，记录每行起始时的词法状态。性能关键。"""
    c = _LINESTATE_CACHE
    if c["states"] is not None:
        # 命中：调用方 (key, ver) 一致
        if c["key"] == key and c["ver"] == ver:
            return c["states"]
        # 命中：文本逐字节相同。补全与即时检查常常在同一次编辑里对同一份
        # 文本各调用一次，但传进来的 version 语义不同（change_count / crc32）
        # 且每次按键都会变；按内容复用可以省掉全文 split + 逐行重扫。
        # 用完整相等判断兜底，文本一旦变化绝不复用过期结果。
        if c["text"] is text or c["text"] == text:
            c["key"] = key
            c["ver"] = ver
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
    cur = {"block": st["block"], "str": st["str"], "chr": st["chr"],
           "raw": st["raw"], "comment": st.get("comment", False)}
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
    if st["block"] or st.get("comment"):
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
    if rest.endswith(_TAIL_SCOPE):
        # 嵌套限定名（std::ranges::、std::__gnu_pbds::）：返回最后一段
        # 限定名，子命名空间补全分支才能可达。
        return name
    if rest.endswith(_TAIL_DOT) or rest.endswith(_TAIL_ARROW):
        # obj.member. 的尾段不是接收者，保持通用成员回退。
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
    if kind in ("tree", "gp_hash_table", "cc_hash_table"):
        args = _split_args(targs) if targs else []
        if not args:
            return None
        if _is_pbds_null_type(targs):
            return args[0] or None
        value = args[1] if len(args) > 1 else ""
        return "pair<const %s, %s>" % (args[0], value)
    rule = _ELEM_RULES.get(kind)
    if not rule:
        return None
    args = _split_args(targs) if targs else []
    out = rule.replace("{T}", args[0] if args else "")
    out = out.replace("{K}", args[0] if args else "")
    out = out.replace("{V}", args[1] if len(args) > 1 else "")
    return out or None


_SEQ_KINDS = ("vector", "deque", "list")
_SET_KINDS = ("set", "multiset", "unordered_set", "unordered_multiset")
_MAP_KINDS = ("map", "multimap", "unordered_map", "unordered_multimap")
_PBDS_KINDS = ("pb_ds_tree_set", "pb_ds_tree_map",
               "pb_ds_hash_set", "pb_ds_hash_map")
_PBDS_ORDERED_OPS = ("insert", "emplace", "erase", "find", "count",
                     "lower_bound", "upper_bound", "equal_range",
                     "order_of_key", "find_by_order", "at", "operator[]")
_PBDS_HASH_OPS = ("insert", "emplace", "erase", "find", "count",
                  "at", "operator[]")


def _member_complexity(kind, name):
    """Append library complexity hints for common STL/PBDS ops."""
    if kind in ("vector", "deque", "list", "string", "basic_string"):
        if name in ("push_back", "push_front", "emplace_back",
                    "emplace_front", "pop_back", "pop_front", "back",
                    "front"):
            return "O(1)"
        if name in ("at", "assign", "resize", "reserve", "insert", "erase",
                    "replace"):
            return "O(n)" if name in ("insert", "erase", "replace", "assign",
                                      "resize") else "O(1)"
        if name == "find":
            return "O(n)"
    elif kind in _SET_KINDS or kind in _MAP_KINDS:
        if name in ("insert", "emplace", "erase", "find", "count",
                    "lower_bound", "upper_bound", "equal_range", "at"):
            return ("O(1) avg" if kind.startswith("unordered_")
                    else "O(log n)")
    elif kind in ("pb_ds_tree_set", "pb_ds_tree_map"):
        if name in _PBDS_ORDERED_OPS:
            return "O(log n)"
    elif kind in ("pb_ds_hash_set", "pb_ds_hash_map"):
        if name in _PBDS_HASH_OPS:
            return "O(1) avg"
    elif kind in ("queue", "stack"):
        if name in ("push", "emplace", "pop", "front", "back", "top"):
            return "O(1)"
    elif kind == "priority_queue":
        if name == "push" or name == "emplace":
            return "O(log n)"
        if name in ("top", "empty"):
            return "O(1)"
    return ""


def _member_signature(kind, targs, insert):
    """Concrete member signature for the built-in STL database.

    ``vector<int> v; v.push_back(`` should advertise ``push_back(int)``
    rather than a bare ``push_back``.  Only signatures we can state with
    confidence from the container's template arguments are returned; other
    members keep the generic label.
    """
    if not insert or "(" not in insert:
        return ""
    name = insert.split("(", 1)[0]
    args = _split_args(targs) if targs else []
    t = args[0] if args else ""
    v = args[1] if len(args) > 1 else t
    params = None
    if kind in _SEQ_KINDS:
        if name in ("push_back", "push_front", "emplace_back",
                    "emplace_front"):
            params = [v]
        elif name == "insert":
            params = ["iterator", v]
        elif name == "erase":
            params = ["iterator"]
        elif name in ("at", "resize", "reserve"):
            params = ["size_t"]
        elif name == "assign":
            params = ["size_t", v]
    elif kind == "string":
        if name == "push_back":
            params = ["char"]
        elif name in ("append", "find", "rfind", "find_first_of",
                      "find_last_of", "compare"):
            params = ["const string&"]
        elif name == "insert":
            params = ["size_t", "const string&"]
        elif name == "erase":
            params = ["size_t", "size_t"]
        elif name == "replace":
            params = ["size_t", "size_t", "const string&"]
        elif name == "substr":
            params = ["size_t", "size_t"]
        elif name in ("at", "resize"):
            params = ["size_t"]
    elif kind in _SET_KINDS:
        if name in ("insert", "emplace", "erase", "find", "count",
                    "lower_bound", "upper_bound", "equal_range") and t:
            params = ["const %s&" % t]
    elif kind in _MAP_KINDS:
        if name in ("at", "erase", "find", "count", "lower_bound",
                    "upper_bound", "equal_range") and t:
            params = ["const %s&" % t]
        elif name == "insert" and t:
            params = ["{const %s&, %s}" % (t, v)]
        elif name == "emplace" and t:
            params = [t, v]
    elif kind in ("queue", "stack", "priority_queue"):
        if name in ("push", "emplace") and t:
            params = [t]
    elif kind == "array":
        if name == "at":
            params = ["size_t"]
        elif name == "fill" and t:
            params = ["const %s&" % t]
    elif kind == "bitset":
        if name in ("test", "set", "reset", "flip"):
            params = ["size_t"]
    elif kind in _PBDS_KINDS:
        if kind.endswith("_map") and t and v:
            if name == "insert":
                params = [u"{const %s&, %s}" % (t, v)]
            elif name == "emplace":
                params = [t, v]
            elif name in ("erase", "find", "count",
                          "lower_bound", "upper_bound", "equal_range",
                          "order_of_key", "operator[]", "at"):
                params = ["const %s&" % t]
            elif name == "find_by_order":
                params = ["size_t"]
        elif name in ("insert", "emplace", "erase", "find", "count",
                    "lower_bound", "upper_bound", "equal_range",
                    "order_of_key", "operator[]", "at") and t:
            params = ["const %s&" % t]
        elif name == "find_by_order":
            params = ["size_t"]
    if params is None:
        return ""
    sig = name + "(" + ", ".join(params) + ")"
    complexity = _member_complexity(kind, name)
    if complexity:
        return u"%s · %s" % (sig, complexity)
    return sig


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
        "var_types": {},
        "param_types": {},
        # 变量声明处的名字偏移，用于补全时剔除"光标之后才声明"的符号
        # （C++ 同作用域名字在使用点之后声明则此处不可见）。
        "var_positions": {},
    }
    var_pos = env["var_positions"]

    def note_var_pos(name, pos):
        # XC-new-4b: 同一名字可能同时是函数参数和更晚的同名局部变量，
        # 取最早声明位置，避免可见参数被内层局部变量遮蔽。
        if name not in var_pos or pos < var_pos[name]:
            var_pos[name] = pos

    vars_ = env["vars"]
    elems = env["elems"]
    iters = env["iters"]
    var_types = env["var_types"]
    param_types = env["param_types"]

    for kind, targs, name, kind_start, name_start in \
            _iter_decl_matches(text):
        if name in _CTRL_WORDS:
            continue
        vars_[name] = (kind, targs)
        note_var_pos(name, name_start)
        type_text = _collapse_ws(text[kind_start:name_start])
        if type_text:
            var_types[name] = type_text

    for m in _LOCAL_VAR_DECL_RE.finditer(text):
        name = m.group("name")
        if name in _CTRL_WORDS or name in var_types:
            continue
        type_text = _collapse_ws(m.group("type"))
        vars_[name] = (type_text, "")
        note_var_pos(name, m.start("name"))
        if type_text:
            var_types[name] = type_text

    for name, ret, open_idx, close_idx in _function_signatures(text):
        params = text[open_idx + 1:close_idx]
        for part in _split_top_level(params):
            parsed = _parse_param(part)
            if parsed is None:
                continue
            param_name, param_type = parsed
            if param_name not in param_types:
                param_types[param_name] = param_type
            # 参数在函数签名处的位置早于函数体内同名局部变量（XC-new-4b）
            rel = params.find(part)
            if rel != -1 and part.find(param_name) >= 0:
                note_var_pos(param_name, open_idx + 1 + rel
                             + part.find(param_name))

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
            # `auto it = v.find(x)` 的类型是 vector<int>::iterator；
            # 补全里直接显示迭代器类型，比 "auto" 有用得多。
            ctype = var_types.get(cont)
            inferred = _iter_type_of(ctype, m.group(3)) if ctype else u""
            if inferred:
                current = str(var_types.get(itv) or "")
                if not current or current.startswith("auto"):
                    var_types[itv] = inferred

    # `auto x = m.at("k")` 等方法返回值推断（fast 引擎同款规则），
    # 接收者类型优先取局部变量，其次取函数参数。
    for m in _AUTO_CALL_RE.finditer(text):
        name, recv, method = m.group(1), m.group(2), m.group(3)
        rtype = var_types.get(recv) or param_types.get(recv) or ""
        if not rtype:
            continue
        inferred = _method_result_type(rtype, method)
        if inferred:
            current = str(var_types.get(name) or "")
            if not current or current.startswith("auto"):
                var_types[name] = inferred
    return env


def _iter_type_of(container_type, method):
    """把容器类型 + 成员函数名推断成迭代器类型（保守：不确定就返回空）。"""
    name = _collapse_ws(container_type)
    if not name:
        return u""
    if method in ("begin", "end", "rbegin", "rend", "find",
                  "lower_bound", "upper_bound"):
        return name + u"::iterator"
    return u""


# `auto x = m.at("k")` 的方法返回值推断，规则与 ca_fast 引擎的
# _method_result_type 保持一致（CHANGELOG 宣称的 "shows int" 不分引擎）。
_AUTO_CALL_RE = re.compile(
    r"\bauto\s*&{0,2}\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*"
    r"(?:\.|->)\s*([A-Za-z_]\w*)\s*\(")
_AUTO_ITER_METHODS = frozenset((
    "begin", "end", "rbegin", "rend", "cbegin", "cend", "find",
    "lower_bound", "upper_bound", "equal_range", "insert", "emplace",
    "erase"))
_AUTO_VALUE_METHODS = frozenset(("at", "front", "back", "top"))
_AUTO_SIZE_METHODS = frozenset(("size", "length", "count"))
_AUTO_MAP_TYPES = frozenset(("map", "multimap", "unordered_map",
                             "unordered_multimap"))
_AUTO_SET_TYPES = frozenset(("set", "multiset", "unordered_set",
                             "unordered_multiset"))


def _container_type_args(type_text):
    """``map<string, int>`` -> ``("map", ["string", "int"])``。"""
    s = str(type_text or "").strip()
    for word in ("const", "volatile", "typename"):
        s = re.sub(r"\b" + word + r"\b", " ", s)
    s = s.replace("&", " ").replace("*", " ").strip()
    if s.startswith("std::"):
        s = s[5:]
    if "<" not in s or not s.endswith(">"):
        return s.split("::")[-1], []
    head = s[:s.index("<")].strip()
    args = _split_args(s[s.index("<") + 1:-1])
    return head.split("::")[-1], [a for a in args if a]


def _method_result_type(receiver_type, method):
    """推断 ``receiver.method(...)`` 的返回类型；不确定时返回 ``""``。"""
    base, args = _container_type_args(receiver_type)
    if not base or not args:
        return ""
    full = "%s<%s>" % (base, ", ".join(args))
    if method in _AUTO_ITER_METHODS:
        return full + "::iterator"
    if method in _AUTO_SIZE_METHODS:
        return "size_type"
    if base in _AUTO_MAP_TYPES and len(args) >= 2:
        if method in ("at", "operator[]"):
            return args[1]
        return "pair<const %s, %s>" % (args[0], args[1])
    if base in _AUTO_SET_TYPES:
        return args[0]
    if method in _AUTO_VALUE_METHODS:
        return args[0]
    return ""


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
            pbds = _pbds_receiver_kind(kind, info[1] or "")
            if pbds is not None:
                return pbds
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
_ARG_RE = re.compile(
    r"^(?P<type>.*?)\s*(?P<name>[A-Za-z_]\w*)\s*(?:=\s*.*)?$", re.S)
_WHITESPACE_RE = re.compile(r"\s+")

# 局部变量声明（声明语句整行开头），用于把 int/auto/string 等
# 非 STL 容器类型也纳入类型环境；容器类型由 DECL_RE 负责。
_LOCAL_VAR_DECL_RE = re.compile(
    r"^[ \t]*(?P<type>(?:const\s+)?(?:unsigned\s+|signed\s+)?"
    r"(?:long\s+long|long|short|int|char|bool|float|double|auto|size_t|"
    r"__int128_t|__uint128_t|__int128|ll|ull|pii|pll|ld))"
    r"(?:\s*<[^<>]*>)?\s*[&*]?\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=|;|\{)", re.M)


def _collapse_ws(text):
    return _WHITESPACE_RE.sub(u" ", text).strip()


def _match_paren(text, open_idx):
    """Return the index of the matching ``)`` or ``None``."""
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "(": 
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        if i - open_idx > 4000:
            return None
    return None


def _split_top_level(text):
    """Split ``a, b, c`` at commas not nested in brackets."""
    parts, depth, start = [], 0, 0
    pairs = {
        "<": 1, ">": -1,
        "(": 1, ")": -1,
        "[": 1, "]": -1,
        "{": 1, "}": -1,
    }
    for i, ch in enumerate(text):
        if ch in pairs:
            depth += pairs[ch]
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def _parse_param(text):
    """Return ``(name, type)`` for one function parameter, else ``None``."""
    m = _ARG_RE.match(text)
    if not m:
        return None
    name = m.group("name")
    type_text = m.group("type").strip()
    if not type_text or type_text.split()[0] in _CTRL_WORDS:
        return None
    return name, _collapse_ws(type_text)


def _function_signatures(text):
    """Yield ``(name, return_text, open_idx, close_idx)`` for definitions."""
    for m in _FUNC_LINE_RE.finditer(text):
        name = m.group(2)
        if name in _CTRL_WORDS:
            continue
        end = m.end()
        nl = text.find(u"\n", end)
        rest = text[end:nl if nl != -1 else len(text)]
        close = rest.find(u")")
        after = rest[close + 1:].lstrip() if close != -1 else u""
        if after.startswith(u";"):
            continue
        open_idx = m.end() - 1
        close_idx = _match_paren(text, open_idx)
        if close_idx is None:
            continue
        ret = _collapse_ws(text[m.start(1):m.start(2)])
        yield name, ret, open_idx, close_idx


def user_items(text, env=None):
    """用户符号补全项：函数、局部变量、参数、类型、宏、别名。"""
    if env is None:
        env = build_env(text)
    items = {}

    def add(name, kind, annotation, annotation_en, detail):
        items.setdefault(name, {
            "kind": kind,
            "annotation": annotation,
            "annotation_en": annotation_en,
            "detail": detail,
            "detail_en": detail,
        })

    for name, sig in env.get("param_types", {}).items():
        add(name, "v", u"参数", u"parameter", sig)
    for name, type_text in env.get("var_types", {}).items():
        add(name, "v", u"局部变量", u"local variable", type_text)

    for name, ret, open_idx, close_idx in _function_signatures(text):
        params = text[open_idx + 1:close_idx]
        detail = _collapse_ws(u"%s %s(%s)" % (ret, name, params))
        add(name, "f", u"自定义函数", u"function", detail)

    for m in _STRUCT_RE.finditer(text):
        name = m.group(1)
        add(name, "t", u"结构体/类", u"type", u"type " + name)
    for m in _ENUM_RE.finditer(text):
        name = m.group(1)
        add(name, "t", u"枚举", u"enum", u"enum " + name)
    for m in _DEFINE_RE.finditer(text):
        name = m.group(1)
        add(name, "d", u"宏定义", u"macro", u"#define " + name)
    for m in _ALIAS_RE.finditer(text):
        name = m.group(1)
        end = text.find(u";", m.start())
        alias = _collapse_ws(text[m.start():end if end != -1 else m.end()])
        add(name, "t", u"类型别名", u"type alias", alias)
    return items


def user_symbols(text):
    """从源码提取用户定义符号: {name: (英文类型, 中文类型)}。

    补全弹窗的语言在渲染时决定，所以这里同时保留两种文案，不能只存
    中文——英文模式下用户符号的右侧注释会变成中文。
    """
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
        syms.setdefault(name, ("function", u"自定义函数"))
    for m in _STRUCT_RE.finditer(text):
        syms.setdefault(m.group(1), ("type", u"结构体/类"))
    for m in _ENUM_RE.finditer(text):
        syms.setdefault(m.group(1), ("enum", u"枚举"))
    for m in _DEFINE_RE.finditer(text):
        syms.setdefault(m.group(1), ("macro", u"宏定义"))
    for m in _ALIAS_RE.finditer(text):
        syms.setdefault(m.group(1), ("type alias", u"类型别名"))
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

# 补全结果缓存：相同（key, ver, offset, prefix, accessor, receiver, mode）直接返回
# mode 字段记录本次使用的补全模式（True=LSP-clangd 风格 / False=严格前缀基础模式），
# 避免切换模式后命中另一模式留下的旧结果
_COMPLETION_CACHE = {"key": None, "ver": None, "offset": -1, "ctx": None,
                     "results": None, "using_std": False, "mode": None,
                     "items": None}


def _analysis(text, key, ver):
    c = _ANALYSIS_CACHE
    if c["key"] == key and c["ver"] == ver and c["env"] is not None:
        return c["env"], c["syms"], c["items"]
    if c["text"] is text and c["env"] is not None:
        return c["env"], c["syms"], c["items"]
    env = build_env(text)
    syms = user_symbols(text)
    items = user_items(text, env)
    c.update(key=key, ver=ver, env=env, syms=syms, items=items, text=text)
    return env, syms, items


def analyze(text, offset, cache_key=None, cache_version=None,
           clangd_style=True):
    """返回补全条目列表: [{trigger, insert, annotation, kind, detail}]

    性能优化：
      1. 多级缓存：词法状态、类型环境、用户符号、补全结果
      2. 提前退出：空前缀 + 非成员访问 + 无模板环境 → 直接返回空
      3. 字典查找：标准符号匹配走 O(1) 字典
      4. 限制结果数量：最多返回 200 条

    clangd_style=True （默认，LSP-clangd 风格）：
        立即显示所有以当前前缀开头、且属于当前作用域（容器/算法/全局）的
        补全条目；同时允许子串/子序列模糊匹配作为兜底（输入习惯宽松时很顺手）。
    clangd_style=False （基础补全模式）：
        只保留严格前缀匹配（大小写不敏感），过滤掉所有模糊/子串/子序列结果，
        行为接近 Sublime 内置单词补全，最简洁、最可预测。
    """
    ctx = detect_context(text, offset, cache_key, cache_version)
    kind = ctx[0]
    if kind in ("comment", "string", "preproc"):
        return []
    if kind == "include":
        prefix = ctx[1]
        results = []
        seen = set()
        # 基础模式下仅保留严格前缀匹配（score < 5），过滤掉子串/子序列
        max_score = 999 if clangd_style else 5
        for h in HEADERS:
            sc = _score(h, prefix)
            if sc >= max_score or h in seen:
                continue
            seen.add(h)
            results.append({"trigger": h, "insert": h,
                            "annotation": u"头文件", "annotation_en": "header",
                            "kind": "t",
                            "detail": header_detail(h), "_score": sc})
        results.sort(key=lambda d: (d["_score"], len(d["trigger"])))
        for d in results:
            del d["_score"]
        return results[:60]

    prefix, accessor, receiver = ctx[1], ctx[2], ctx[3]

    # 性能：补全结果缓存命中检查（mode 决定缓存分组，避免切换模式后误命中）
    if cache_key is not None:
        cc = _COMPLETION_CACHE
        if (cc["key"] == cache_key and cc["ver"] == cache_version
                and cc["offset"] == offset and cc["ctx"] == ctx
                and cc["mode"] == clangd_style):
            return cc["results"]

    if cache_key is not None:
        env, syms, items = _analysis(text, cache_key, cache_version)
    else:
        env = build_env(text)
        syms = user_symbols(text)
        items = user_items(text, env)

    raw_items = []

    if accessor == "dot" or accessor == "arrow":
        key = _resolve_receiver(env, receiver, accessor)
        members = MEMBERS_DB_FAST.get(key) if key else None
        if members is None:
            members = [] if key else GENERIC_MEMBERS_FAST
        targs = ""
        info = env["vars"].get(receiver)
        if info is None and accessor == "arrow":
            cont = env["iters"].get(receiver)
            if cont is not None:
                info = env["vars"].get(cont)
        if info:
            targs = info[1] or ""
        for it in members:
            sig = _member_signature(key, targs, it["insert"])
            detail = sig or it["trigger"]
            raw_items.append(
                (2, it["trigger"], it["insert"], it["annotation"], it["kind"],
                 False,
                 en_detail(it["insert"], key) or en_detail(it["insert"]) or u"",
                 detail, detail))
    elif accessor == "scope" and receiver in ("__gnu_pbds", "pb_ds"):
        # PBDS 命名空间成员：trigger 是短名，insert 带完整模板骨架。
        # want_std=False：__gnu_pbds 不是 std 的子命名空间，绝不能补 std::。
        for it in PBDS_NAMESPACE_ITEMS:
            raw_items.append(
                (2, it["trigger"], it["insert"], it["annotation"],
                 it["kind"], False, it["annotation_en"],
                 it["insert"], it["insert"]))
    elif accessor == "scope" and receiver in ("ranges", "views"):
        # std::ranges:: / std::views:: 作用域：只列出该子命名空间的成员，
        # 并去掉前缀（用户已经打出了 ranges:: / views::）。
        ns = receiver + "::"
        for it in STD_ITEMS_ALL:
            insert = it["insert"]
            if not insert.startswith(ns):
                continue
            short_insert = insert[len(ns):]
            short_trigger = it["trigger"][len(ns):] \
                if it["trigger"].startswith(ns) else short_insert
            raw_items.append(
                (2, short_trigger, short_insert, it["annotation"],
                 it["kind"], False,
                 en_detail(insert) or u"", short_insert, short_insert))
    elif accessor == "scope":
        raw_items = [(2, it["trigger"], it["insert"], it["annotation"],
                      it["kind"], False, en_detail(it["insert"]) or u"",
                      it["trigger"], it["trigger"])
                     for it in STD_ITEMS_ALL]
    else:
        for kw in KEYWORDS:
            raw_items.append((3, kw, kw, u"关键字", "k", False, u"keyword",
                              kw, kw))
        var_positions = env.get("var_positions", {})
        for name, it in items.items():
            # 同作用域内光标之后才声明的局部变量此时不可见；剔除，
            # 避免与 FastEngine 的声明顺序过滤保持一致（XC-new-4）。
            if it["kind"] == "v" and name in var_positions and \
                    var_positions[name] >= offset:
                continue
            priority = 0 if it["kind"] == "v" else 1
            raw_items.append((priority, name, name, it["annotation"],
                              it["kind"], False, it["annotation_en"],
                              it["detail_en"], it["detail"]))
        for it in STD_ITEMS_ALL:
            raw_items.append((2, it["trigger"], it["insert"],
                              it["annotation"],
                              it["kind"], it["want_std"],
                              en_detail(it["insert"]) or u"",
                              it["trigger"], it["trigger"]))

    already_std = (accessor == "scope" and receiver == "std")
    need_prefix = not env["using_std"]

    results = []
    seen = set()
    # 基础模式下只保留严格前缀匹配（score < 5），过滤掉子串/子序列模糊结果
    max_score = 999 if clangd_style else 5
    for (priority, trigger, insert, ann, kd, want_std, ann_en,
         detail_en, detail_zh) in raw_items:
        sc = _score(trigger, prefix)
        if sc >= max_score or trigger in seen:
            continue
        seen.add(trigger)
        if want_std and need_prefix and not already_std:
            insert = "std::" + insert
            detail_en = "std::" + trigger
            detail_zh = detail_en
        results.append({"trigger": trigger, "insert": insert,
                        "annotation": ann, "annotation_en": ann_en,
                        "kind": kd, "detail": detail_zh,
                        "detail_en": detail_en,
                        "_priority": priority, "_score": sc})
    results.sort(key=lambda d: (d["_priority"], d["_score"],
                                len(d["trigger"]), d["trigger"]))
    for d in results:
        del d["_priority"]
        del d["_score"]
    # 弹窗上限要盖住全库条目数（204+）：bare std:: 按 trigger 长度排序
    # 后，带模板骨架的 PBDS 条目排最尾，上限太小恰好把它们裁掉。
    if len(results) > 256:
        results = results[:256]

    if accessor == "scope" and receiver in ("__gnu_pbds", "pb_ds"):
        # 这些条目全部来自 PBDS：补全被接受后必须插入 ext/pb_ds 头文件
        # （bits/stdc++.h 不包含它们）。
        for d in results:
            d["includes"] = list(PBDS_HEADERS)

    # 写入缓存（mode 也写进去，下次切换模式时会自动失效）
    if cache_key is not None:
        _COMPLETION_CACHE.update(key=cache_key, ver=cache_version,
                                 offset=offset, ctx=ctx, results=results,
                                 mode=clangd_style)
    return results


# ---------------------------------------------------------------------------
# 定义查找（F12 跳转）
# ---------------------------------------------------------------------------

_DEF_PATTERNS = (
    (u"type definition",
     lambda sym: re.compile(r"\b(?:struct|class|union|enum)(?:\s+class)?\s+" +
                            sym + r"\b")),
    (u"macro definition",
     lambda sym: re.compile(r"^[ \t]*#[ \t]*define[ \t]+" + sym + r"\b",
                            re.M)),
    (u"type alias",
     lambda sym: re.compile(r"\busing\s+" + sym +
                            r"\s*=|\btypedef\b[^;\n]*?\b" + sym + r"\s*;")),
    (u"function definition",
     lambda sym: re.compile(
         # SYM( 前必须有返回类型词或作用域前缀（同一行内）：否则
         # REP(i,1,n) { 这类宏调用行会被误判成函数定义（XS7）。
         r"(?:(?:[A-Za-z_][\w:<>,\*&\[\]\t ]*[ \t*&]+)"
         r"|(?:[A-Za-z_]\w*::)+)"
         r"(?P<sym>\b" + sym + r"\s*\((?:[^()]|\([^()]*\))*\)\s*"
         r"(?:const\s*)?(?:->[^{;\n]+)?\s*\{)")),
    (u"variable definition",
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
            # 带 "sym" 命名组的模式（函数定义）前缀含返回类型，定位
            # 要落在符号本身上。
            idx = m.start("sym") if "sym" in pat.groupindex else m.start()
            if mask[idx]:
                continue
            if label != u"macro definition" and label != u"function definition":
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


_HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp")


def _header_files_in_dir(path):
    """列出 include 目录下的头文件（直接子文件 + bits/ 子目录）。

    之前 include_paths 被当成文件路径传给 open()，目录全部抛异常，
    所以 F12 实际上从未搜索过 include_paths。这里只扫一层，避免
    把整个编译器 include 树递归一遍拖慢 F12。
    """
    import os
    out = []
    try:
        names = os.listdir(path)
    except OSError:
        return out
    for name in names:
        p = os.path.join(path, name)
        if os.path.isfile(p) and name.lower().endswith(_HEADER_EXTS):
            out.append(p)
    bits = os.path.join(path, "bits")
    if os.path.isdir(bits):
        try:
            for name in os.listdir(bits):
                p = os.path.join(bits, name)
                if os.path.isfile(p) and name.lower().endswith(_HEADER_EXTS):
                    out.append(p)
        except OSError:
            pass
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
            if os.path.isdir(path):
                # include_paths 是目录：展开成头文件再搜
                for p in _header_files_in_dir(path):
                    rp = os.path.normcase(p.replace("\\", "/"))
                    if rp not in visited:
                        nextq.append(p)
                continue
            rp = os.path.normcase(path.replace("\\", "/"))
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
# 标准库符号 fallback (LSP-style: 找不到本地定义时回退到 std 头文件)
# ---------------------------------------------------------------------------

# 系统头文件实际路径搜索（用于 F12 跳转后用 ST 打开真实文件）
def find_system_header(header_name, include_paths=None):
    """在 -I 搜索路径下查找系统头文件的实际路径。

    返回绝对路径或 None。
    - header_name 不含尖括号，如 'vector'、'bits/stdc++.h'
    - include_paths 是 -I 列表
    """
    import os
    if not header_name:
        return None
    candidates = []
    for root in (include_paths or []):
        if not root:
            continue
        try:
            cand = os.path.join(root, header_name)
            if os.path.isfile(cand):
                candidates.append(cand)
        except Exception:
            continue
    if candidates:
        return candidates[0]
    return None


# STD_SYMBOL_HEADER 里的符号类别是中文，转成英文主文案
_SYM_KIND_EN = {u"类型": "type", u"符号": "symbol"}


def find_definition_std_fallback(symbol, include_paths=None):
    """对 std 命名空间下的符号做头文件 fallback。

    返回 [dict] 列表（候选），每个元素含：
      - kind: 'std_symbol' / 'std_header' / 'system_header_path'
      - label / detail: 英文主文案
      - label_zh / detail_zh: 中文文案（display_language 为 zh/both 时由调用方取用）
      - header: 头文件名（不含尖括号）
      - path: 实际文件路径（仅 system_header_path）
    """
    results = []
    if not symbol:
        return results

    # 1. 符号本身是已知标准头文件？ (光标停在 #include <xxx> 上)
    if symbol in STD_HEADERS_SET:
        path = find_system_header(symbol, include_paths)
        results.append({
            "kind": "std_header",
            "label": u"system header",
            "label_zh": u"系统头文件",
            "header": symbol,
            "path": path,
            "detail": u"Standard library header <%s>, found in the compiler "
                      u"include path" % symbol,
            "detail_zh": u"标准库头文件 <%s>，位于编译器 include 路径" % symbol,
        })
        return results

    # 2. 符号在已知 std 符号表里？
    info = STD_SYMBOL_HEADER.get(symbol)
    if info:
        header, sym_kind = info
        path = find_system_header(header, include_paths)
        kind_en = _SYM_KIND_EN.get(sym_kind, u"symbol")
        results.append({
            "kind": "std_symbol",
            "label": u"standard library %s" % kind_en,
            "label_zh": u"标准库%s" % sym_kind,
            "header": header,
            "path": path,
            "detail": u"std::%s is defined in <%s> (consider #include <%s>)" % (
                symbol, header, header),
            "detail_zh": u"std::%s 定义于 <%s> 系统头文件中(可考虑 #include <%s>)"
                         % (symbol, header, header),
        })
        return results

    # 3. 通用提示
    return results


def goto_definition_advanced(symbol, view_text, view, include_paths=None,
                              all_views_text=None, system_include_paths=None):
    """F12 跳转定义 - 多源合并查找。

    按以下顺序返回候选：
      1. 当前文件中的定义
      2. 已打开文件中的定义
      3. 本地头文件（递归 #include "..."）中的定义
      4. 标准库符号 fallback（std 命名空间下的类型/函数）
      5. 已知系统头文件本身

    include_paths 用于本地头文件搜索；system_include_paths 是编译器
    自带的标准库搜索路径，只用于把 std 符号定位到真实头文件，避免
    把整个编译器 include 树当成项目头文件扫描。

    返回 [(priority, src, line, col, label, preview_or_detail, extra)]
    src: 'local' | 'open' | 'file' | 'std_symbol' | 'std_header'
    extra: dict, 可能含 'path' 字段用于打开系统头文件
    """
    import os
    import re as _re
    results = []

    # 1. 当前文件
    for line, col, label, preview in (
        (l, c, lab, p) for _, l, c, lab, p in find_definitions(view_text, symbol)
    ):
        results.append((0, "local", line, col, label, preview, None))

    # 2. 已打开文件
    if all_views_text:
        for vtext, vid, vfname in all_views_text:
            if vtext is view_text:
                continue
            for line, col, label, preview in find_definitions(vtext, symbol):
                results.append((1, "open", line, col, label, preview,
                                {"vid": vid, "path": vfname}))

    # 3. 本地头文件（递归 include）
    fname = view.file_name()
    if fname:
        heads = []
        try:
            head_text = view_text
            for m in _re.finditer(
                    r'^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"',
                    head_text, _re.M):
                rel = m.group(1).replace("/", os.sep)
                heads.append(os.path.normpath(
                    os.path.join(os.path.dirname(fname), rel)))
        except Exception:
            pass
        for inc in (include_paths or []):
            if inc:
                heads.append(str(inc))
        for path, line, col, label, preview in find_definitions_in_files(
                symbol, heads):
            results.append((2, "file", line, col, label, preview, {"path": path}))

    # 4. 标准库 fallback
    std_hits = find_definition_std_fallback(
        symbol, system_include_paths or include_paths)
    for hit in std_hits:
        zh = {"label_zh": hit.get("label_zh"), "detail_zh": hit.get("detail_zh")}
        # 优先看是否能找到系统头文件路径
        if hit.get("path"):
            extra = {"path": hit["path"]}
            extra.update(zh)
            results.append((3, "system_header_path", 1, 0,
                            hit["label"], hit["detail"], extra))
        # 标准符号提示
        extra = {"path": hit.get("path"), "header": hit.get("header")}
        extra.update(zh)
        results.append((3, hit["kind"], 1, 0,
                        hit["label"], hit["detail"], extra))

    return results


# ---------------------------------------------------------------------------
# 编译器诊断解析 + 中文翻译
# ---------------------------------------------------------------------------

_DIAG_RE = re.compile(
    r"^(.+?):(\d+):(?:(\d+):)?[ \t]*(fatal error|error|warning|note):[ \t]*(.*)$")

# 驱动级/链接器错误（无 file:line:col 前缀）：g++: error: /
# cc1plus: error: / collect2: error: / clang: error: / ld: ...（XC3）。
_DRIVER_RE = re.compile(
    r"^(?:.*[/\\])?"
    r"(g\+\+|gcc|cc1plus|collect2|clang\+\+|clang|ld)(?:\.exe)?"
    r"(?:\([^)]*\))?:\s*"
    r"(fatal error|error|warning|note):[ \t]*(.*)$", re.IGNORECASE)

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


def parse_compiler_output(output):
    """解析 g++/clang++ 诊断输出。

    返回 [dict(file,line,col,sev_en,msg,zh,ctx,ctx_zh,notes)]：msg/ctx 是英文
    原文，zh/ctx_zh 是译文。这里不选显示语言——那是渲染层按 display_language
    决定的事，因此诊断结果缓存与语言设置无关。
    """
    output = output.replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    pending_ctx = ("", "")
    matched_any = False
    for line in output.split("\n"):
        if not line.strip():
            continue
        # gcc10+ 在部分 locale 下用花引号（U+2018/2019）包上下文名，
        # 先归一化成 ASCII 再匹配（XS5）。
        for a, b in QUOTE_NORMALIZE:
            line = line.replace(a, b)
        cm = _CTX_RE.match(line)
        if cm:
            matched_any = True
            kind = cm.group(1)
            pending_ctx = (
                u"In %s '%s': " % (kind, cm.group(2)),
                u"在%s '%s' 中：" % (_CTX_ZH.get(kind, u"函数"), cm.group(2)))
            continue
        im = _IN_FILE_RE.match(line)
        if im:
            matched_any = True
            base = os_path_basename(im.group(1))
            pending_ctx = (
                u"(included from %s line %s) " % (base, im.group(2)),
                u"（由 %s 第 %s 行包含引入）" % (base, im.group(2)))
            continue
        dm = _DRIVER_RE.match(line)
        if dm:
            matched_any = True
            en, zh = translate_message(dm.group(3))
            entries.append({
                "file": "", "line": 1, "col": 1,
                "sev_en": dm.group(2),
                "msg": en, "zh": zh,
                "ctx": pending_ctx[0], "ctx_zh": pending_ctx[1],
                "notes": [], "code": "",
            })
            pending_ctx = ("", "")
            continue
        m = _DIAG_RE.match(line)
        if not m:
            continue
        matched_any = True
        sev = m.group(4)
        raw_msg = m.group(5)
        flag_match = _WARN_FLAG_RE.search(raw_msg)
        code = flag_match.group(1).rstrip("=") if flag_match else ""
        en, zh = translate_message(raw_msg)
        if sev == "note":
            if entries:
                # note 是上一条诊断的补充（"did you mean ...?"），
                # 不能单独算成一条警告。
                entries[-1]["notes"].append({
                    "line": int(m.group(2)),
                    "col": int(m.group(3)) if m.group(3) else 1,
                    "msg": en,
                    "zh": zh,
                })
            pending_ctx = ("", "")
            continue
        entries.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "col": int(m.group(3)) if m.group(3) else 1,
            "sev_en": sev,
            "msg": en,
            "zh": zh,
            "ctx": pending_ctx[0],
            "ctx_zh": pending_ctx[1],
            "notes": [],
            "code": code,
        })
        pending_ctx = ("", "")
    if not entries and not matched_any and output.strip():
        # 输出非空却没有任何可识别的诊断行：多半是规则未覆盖的
        # 驱动/链接器形态（undefined reference 块等）。兜底成一条
        # 错误，否则编译参数配置错误会静默失效（XC3）。
        head = "\n".join(
            ln for ln in output.split("\n") if ln.strip())[:400]
        en, zh = translate_message(head)
        entries.append({
            "file": "", "line": 1, "col": 1, "sev_en": "error",
            "msg": en, "zh": zh, "ctx": "", "ctx_zh": "",
            "notes": [], "code": "",
        })
    return entries


def os_path_basename(path):
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# 无编译器时的基础检查（括号配平 / 全角标点 / 未闭合字符串）
# ---------------------------------------------------------------------------

_FULLWIDTH = u"，；：（）｛｝【】“”‘’！？"

# 英文消息里指出"应该改成哪个半角字符"
_HALFWIDTH = {u"，": ",", u"；": ";", u"：": ":", u"（": "(", u"）": ")",
              u"｛": "{", u"｝": "}", u"【": "[", u"】": "]",
              u"“": '"', u"”": '"', u"‘": "'", u"’": "'",
              u"！": "!", u"？": "?"}


def basic_checks(code):
    """返回 [(line, col, severity, 英文消息, 中文消息)]

    两种语言一起给，显示哪一种由调用方按 display_language 在渲染时决定。
    """
    problems = []
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    closer_name = {")": "')'", "]": "']'", "}": "'}'"}
    opener_name = {"(": "')'", "[": "']'", "{": "'}'"}
    _UNCLOSED_EN = "Unclosed bracket: missing matching %s"
    _UNCLOSED_ZH = u"括号未闭合：缺少对应的 %s"

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
                problems.append((ln, cl, "warning",
                                 u'Unterminated string: missing closing "',
                                 u"字符串缺少结尾双引号"))
                ctx.str = False
            if ctx.chr:
                problems.append(ctx.pos + (
                    "warning",
                    u"Unterminated character literal: missing closing '",
                    u"字符常量缺少结尾单引号"))
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
                if i + 1 < n and code[i + 1] == "\n":
                    # 反斜杠+换行拼接：物理行号必须推进，否则后续所有
                    # 诊断的行号整体偏移（XS10）。
                    line += 1
                    col = 0
                    i += 2
                elif i + 2 < n and code[i + 1] == "\r" and code[i + 2] == "\n":
                    line += 1
                    col = 0
                    i += 3
                else:
                    col += 2
                    i += 2
                continue
            if c == '"':
                ctx.str = False
            i += 1
            continue
        if ctx.chr:
            if c == "\\":
                if i + 1 < n and code[i + 1] == "\n":
                    line += 1
                    col = 0
                    i += 2
                elif i + 2 < n and code[i + 1] == "\r" and code[i + 2] == "\n":
                    line += 1
                    col = 0
                    i += 3
                else:
                    col += 2
                    i += 2
                continue
            if c == "'":
                ctx.chr = False
            i += 1
            continue
        if c in _FULLWIDTH:
            problems.append((line, col - 1, "warning",
                             u"Full-width character '%s'; use the ASCII '%s'"
                             % (c, _HALFWIDTH.get(c, "?")),
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
                             _UNCLOSED_EN % opener_name[oc],
                             _UNCLOSED_ZH % opener_name[oc]))
                    stack.pop()
                else:
                    problems.append(
                        (line, col - 1, "error",
                         u"Extra closing bracket %s: no matching opener"
                         % closer_name[c],
                         u"多余的右括号 %s（没有与之匹配的左括号）" % closer_name[c]))
        i += 1

    if ctx.raw:
        problems.append((line, col, "warning",
                         u"Unterminated raw string literal",
                         u"原始字符串未闭合"))
    if ctx.block:
        problems.append((line, col, "warning",
                         u"Unterminated block comment: missing closing */",
                         u"块注释 /* 未闭合"))
    if ctx.str:
        # EOF 前无尾换行时未闭合字符串在这里兜底（XS11）。
        ln, cl = ctx.pos
        problems.append((ln, cl, "warning",
                         u'Unterminated string: missing closing "',
                         u"字符串缺少结尾双引号"))
    if ctx.chr:
        problems.append(ctx.pos + (
            "warning",
            u"Unterminated character literal: missing closing '",
            u"字符常量缺少结尾单引号"))
    for oc, ol, ocl in stack:
        problems.append((ol, ocl, "error",
                         _UNCLOSED_EN % opener_name[oc],
                         _UNCLOSED_ZH % opener_name[oc]))
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
# e/E 保护科学计数法（既有行为）。十六进制浮点的 p± 改由 _replay 把
# 整个字面量标记为 prot 区间来统一保护，覆盖任意位十六进制尾数，
# 同时不误伤 cap/help/tmp 这类 p 结尾标识符（XC-new-9）。
_ADD_RE = re.compile(r"(?<![eE])(?<=[\w)\]\x27])\s*\+\s*(?![+=])(?=[\w(])")
_SUB_RE = re.compile(r"(?<![eE])(?<=[\w)\]\x27])\s*-\s*(?![-=>])(?=[\w(])")

# 完整十六进制浮点字面量：0xABp+2 / 0x1.8p-3 / 0xFFP+1 等（XC-new-9）。
_HEX_FLOAT_RE = re.compile(
    r"(?<![\w.])0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)[pP][+-][0-9]+")

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


def _format_one_line(raw_line, st, depth, indent_width):
    """格式化一行并推进词法状态，返回 ``(文本, 新深度, 是否空行)``。

    ``format_code`` / ``format_code_range`` 共用同一套行内规则，保证
    整篇格式化与选区格式化的结果一致。
    """
    pre = _copy_state(st)
    # 行首状态要在 _replay 变异 pre 之前取：续行字符串/字符的判定
    # 看的是本行从什么状态开始（XS9）。
    enter_sstr, enter_chr = st.sstr, st.chr
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
        return "", depth, True

    # XC-new-7: 只统计真正的代码区间。续行字符串未闭合时整行没有
    # code_spans，字面量里的孤立括号不会被误计；闭合后的代码照常统计。
    opens = closes = 0
    for a, b in code_spans:
        seg = raw_line[a:b]
        opens += seg.count("{")
        closes += seg.count("}")

    if (pre.block or pre.raw) and not any(
            raw_line[a:b].strip() for a, b in code_spans):
        return raw_line.rstrip(), max(depth, 0), False

    if enter_sstr or enter_chr:
        # 反斜杠续行的字符串/字符：整行原样保留（行首空白属字面量）。
        # 本行若已闭合且后面有真实代码，code_spans 会带上那段代码，
        # 深度照常推进；未闭合时 opens/closes 为 0，深度不变（XC-new-7）。
        return raw_line.rstrip(), max(depth + opens - closes, 0), False

    if _PREPROC_RE.match(raw_line):
        return stripped, depth, False

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

    # line 与 mask 同循环同步构建（XC5）：code 分片 expandtabs 后变长，
    # 按原始行长度分配的 mask 会与字符错位，深缩进行里的字符串会被改写。
    # 只有 code 分片做 expandtabs，prot（字符串/注释）分片原样拼接，
    # 字面量内容一个字节都不动（XC4）。
    buf = []
    mask = bytearray()
    for tag, seg in pieces:
        if tag == "code":
            seg = seg.expandtabs(indent_width)
        mask.extend(b"\x01" * len(seg) if tag == "prot"
                    else b"\x00" * len(seg))
        buf.append(seg)
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
    return (" " * indent + new_line if new_line else "",
            max(depth + opens - closes, 0), False)


def format_code(src, indent_width=4):
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")
    st = _FmtState()
    out = []
    depth = 0
    blank_run = 0
    for raw_line in lines:
        text, depth, blank = _format_one_line(
            raw_line, st, depth, indent_width)
        if blank:
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        out.append(text)
    result = "\n".join(out).rstrip("\n") + "\n"
    return result


def _state_depth_before(lines, end_line):
    """扫描 ``lines[:end_line]``，返回起始词法状态与花括号深度。"""
    st = _FmtState()
    depth = 0
    for raw_line in lines[:end_line]:
        pre = _copy_state(st)
        code_spans, _ = _replay(raw_line, pre)
        st = pre
        if _PREPROC_RE.match(raw_line):
            continue
        for a, b in code_spans:
            segment = raw_line[a:b]
            depth += segment.count("{") - segment.count("}")
    return st, max(depth, 0)


def format_code_range(src, start_offset, end_offset, indent_width=4):
    """只格式化 ``[start_offset, end_offset)`` 覆盖的完整行。

    选区外的文本原样保留；起始缩进深度由选区之前的代码推导，因此
    在函数体、命名空间或类内部选中几行也能得到正确缩进。返回完整
    新文本，调用方可直接替换整个缓冲区。
    """
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    n = len(src)
    if start_offset < 0:
        start_offset = 0
    if end_offset > n:
        end_offset = n
    if end_offset < start_offset:
        start_offset, end_offset = end_offset, start_offset
    start_line = src.count("\n", 0, start_offset)
    end_line = src.count("\n", 0, end_offset)
    lines = src.split("\n")
    if end_line >= len(lines):
        end_line = len(lines) - 1
    if start_line > end_line:
        return src
    st, depth = _state_depth_before(lines, start_line)
    out = []
    blank_run = 0
    for raw_line in lines[start_line:end_line + 1]:
        text, depth, blank = _format_one_line(
            raw_line, st, depth, indent_width)
        if blank:
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        out.append(text)
    return "\n".join(lines[:start_line] + out + lines[end_line + 1:])


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
        # XC-new-9: 十六进制浮点字面量整体作为 prot 区间，内部的 p±
        # 不会被行内规则当成二元加减法加空格；尾数任意长度都覆盖。
        hm = _HEX_FLOAT_RE.match(line_text, i)
        if hm:
            if start < i:
                code_spans.append((start, i))
            prot_spans.append((hm.start(), hm.end()))
            i = hm.end()
            start = i
            continue
        i += 1
    if start < n and not (state.block or state.sstr or state.chr or state.raw):
        code_spans.append((start, n))
    return code_spans, prot_spans


# 模块加载时立即构建行内规则
_build_line_rules()
