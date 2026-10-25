# -*- coding: utf-8 -*-
"""CppAssistant 内嵌 clangd 语言服务器客户端（最小 LSP 协议实现）。

架构移植自 sublimelsp/LSP-clangd：直接与 clangd 语言服务器通过 stdio 上的
JSON-RPC 通信，把 clangd 的真实语义补全（编译器级准确度）接入插件，
替代纯静态数据库方案。

设计约束：
  - 保持与旧版 ST4 插件宿主（Python 3.3）兼容的语法；Sublime Text
    4215 使用 Python 3.14。不使用 f-string / pathlib / typing。
  - 不依赖 sublime，可独立单元测试。
  - 服务器发现逻辑与 LSP-clangd 一致：优先 PATH 中的 clangd，
    支持常见版本名（clangd / clangd-18 ...）与自定义路径。
  - Windows 下隐藏控制台窗口（STARTF_USESHOWWINDOW + CREATE_NO_WINDOW），
    与 Package Control 审查要求一致。
  - 初始化参数使用 clangd 的 fallbackFlags 注入 -std=（与 LSP-clangd 的
    initialization_options.fallbackFlags 相同机制），补全结果严格遵循
    当前 C++ 标准（CSP-S 推荐 c++14）。

协议实现范围（补全所需最小集 + 代码操作）：
  initialize / initialized / shutdown / exit
  textDocument/didOpen / didChange / didClose
  textDocument/completion            （同步等待 + 异步回调双模式）
  textDocument/hover                 （悬停文档，调用方放工作线程等待）
  textDocument/signatureHelp         （函数签名提示，同上）
  textDocument/codeAction            （快速修复 / 重构建议）
  codeAction/resolve                 （延迟计算的代码操作编辑）
  textDocument/rename                （语义重命名，返回 WorkspaceEdit）
  textDocument/references            （查找引用）
  textDocument/rangeFormatting       （只格式化选区）
  $/cancelRequest
  服务器反向请求（workspace/configuration、client/registerCapability、
  window/workDoneProgress/create）一律回空，避免 clangd 卡住。
"""

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse

# 旧版 ST4 插件宿主（Python 3.3）没有 subprocess.CREATE_NO_WINDOW，
# 直接用数值以保持兼容。
CREATE_NO_WINDOW = 0x08000000

# clangd 常见二进制名（覆盖 Linux 发行版分版本打包的习惯）
_CLANGD_NAMES = (
    "clangd", "clangd-21", "clangd-20", "clangd-19", "clangd-18",
    "clangd-17", "clangd-16", "clangd-15", "clangd-14", "clangd-13",
    "clangd-12", "clangd-11",
)

# LSP CompletionItemKind -> (英文标注, CppAssistant 内部 kind 键)
# 标注直接显示在补全弹窗右侧；内部 kind 键会被 CppAssistant._KIND_MAP
# 转成 Sublime 的图标 + 类型标签
LSP_KIND_MAP = {
    1: (u"text", "v"),
    2: (u"method", "m"),
    3: (u"function", "f"),
    4: (u"constructor", "m"),
    5: (u"field", "v"),
    6: (u"variable", "v"),
    7: (u"class", "t"),
    8: (u"interface", "t"),
    9: (u"module", "t"),
    10: (u"property", "v"),
    11: (u"unit", "v"),
    12: (u"value", "c"),
    13: (u"enum", "t"),
    14: (u"keyword", "k"),
    15: (u"snippet", "s"),
    16: (u"color", "c"),
    17: (u"file", "u"),
    18: (u"reference", "u"),
    19: (u"enum member", "c"),
    20: (u"constant", "c"),
    21: (u"struct", "t"),
    22: (u"event", "u"),
    23: (u"operator", "u"),
    24: (u"type parameter", "t"),
    25: (u"namespace", "t"),
}


def _hidden_window_kwargs():
    """Windows 下隐藏控制台窗口的 Popen 参数（审查要求）。"""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = si
        except Exception:
            pass
    return kwargs


def find_clangd(custom_path=None):
    """查找 clangd 可执行文件，返回绝对路径或 None。

    与 LSP-clangd 的 system_binary 逻辑对齐：
      1. 显式自定义路径（存在即用）；
      2. PATH 中依次尝试 clangd 与常见分版本名称。
    """
    if custom_path:
        p = custom_path
        if os.path.isfile(p):
            return p
        w = shutil.which(p)
        if w:
            return w
        return None
    for name in _CLANGD_NAMES:
        w = shutil.which(name)
        if w:
            return w
    return None


def path_to_uri(path):
    """本地路径 -> file:// URI（含中文/空格的百分号编码）。"""
    p = path.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + urllib.parse.quote(p)


def line_col_utf16(text, offset):
    """把 Sublime 的字符偏移换成 LSP 的 (line, character)。

    LSP 的 character 单位是 UTF-16 码元：非 BMP 字符（如 emoji、部分
    生僻字）占 2 个单位；BMP 内字符（含中文）占 1 个。
    """
    line_start = text.rfind("\n", 0, offset) + 1
    line = text.count("\n", 0, line_start)
    col = 0
    for ch in text[line_start:offset]:
        col += 2 if ord(ch) > 0xFFFF else 1
    return line, col


def offset_from_position(text, position):
    """把 LSP (line, character) 换成 Python 字符偏移（UTF-16 码元）。

    越界位置钳制到文本末尾；CRLF 的 ``\\r`` 不会被算进位置。
    这是 line_col_utf16 的逆运算，rename / references / rangeFormatting
    的返回位置都要经过它才能安全地映射回 Sublime 的 Region。
    """
    if not isinstance(position, dict):
        return 0
    try:
        line = int(position.get("line", 0) or 0)
        character = int(position.get("character", 0) or 0)
    except Exception:
        return 0
    if line < 0:
        line = 0
    if character < 0:
        character = 0
    pos = 0
    for _ in range(line):
        nl = text.find("\n", pos)
        if nl < 0:
            return len(text)
        pos = nl + 1
    i = pos
    units = 0
    n = len(text)
    while i < n and text[i] != "\n" and units < character:
        width = 2 if ord(text[i]) > 0xFFFF else 1
        if units + width > character:
            break
        units += width
        i += 1
    if i > pos and text[i - 1] == "\r":
        i -= 1
    return i


def range_to_offsets(text, rng):
    """LSP Range -> (start_offset, end_offset)，并保证 start <= end。"""
    if not isinstance(rng, dict):
        return (0, 0)
    start = offset_from_position(text, rng.get("start") or {})
    end = offset_from_position(text, rng.get("end") or {})
    if end < start:
        start, end = end, start
    return (start, end)


def uri_to_path(uri):
    """file:// URI -> 本地路径（Windows 下去掉 /C:/ 的前导斜杠）。"""
    if not uri:
        return ""
    path = uri
    if path.startswith("file://"):
        path = path[7:]
    elif path.startswith("file:"):
        path = path[5:]
    try:
        path = urllib.parse.unquote(path)
    except Exception:
        pass
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        path = path.replace("/", "\\")
    return path


def parse_text_edits(result):
    """规范化 textDocument/rangeFormatting 等返回的 TextEdit 列表。"""
    if not isinstance(result, list):
        return []
    out = []
    for edit in result:
        if not isinstance(edit, dict):
            continue
        rng = edit.get("range")
        if not isinstance(rng, dict):
            continue
        out.append({"range": rng,
                    "newText": edit.get("newText") or ""})
    return out


def apply_text_edits(text, edits):
    """把一组 LSP TextEdit 应用到文本，返回新文本。

    编辑按起始偏移从后往前应用，避免前面的编辑把后面的偏移推歪。
    重叠编辑（协议上不应出现）会被跳过，保证结果可预测。
    """
    resolved = []
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        start, end = range_to_offsets(text, edit.get("range") or {})
        resolved.append((start, end, edit.get("newText") or ""))
    resolved.sort(key=lambda item: (item[0], item[1]), reverse=True)
    out = text
    last_start = len(text) + 1
    for start, end, new_text in resolved:
        if end > last_start:
            continue
        out = out[:start] + new_text + out[end:]
        last_start = start
    return out


def parse_workspace_edit(edit):
    """解析 WorkspaceEdit，返回 ``{"files": [(uri, edits)], "resource_ops": []}``。

    同时支持 ``changes``（uri -> TextEdit[]）和 ``documentChanges``
    （TextDocumentEdit[]，clangd 重命名时常用）。文件创建/改名/删除等
    资源操作单独返回，由调用方决定是否支持。
    """
    files = []
    resource_ops = []
    if not isinstance(edit, dict):
        return {"files": files, "resource_ops": resource_ops}
    changes = edit.get("changes")
    if isinstance(changes, dict):
        for uri, edits in changes.items():
            parsed = parse_text_edits(edits)
            if uri and parsed:
                files.append((uri, parsed))
    docs = edit.get("documentChanges")
    if isinstance(docs, list):
        for change in docs:
            if not isinstance(change, dict):
                continue
            if change.get("kind"):
                resource_ops.append(change)
                continue
            td = change.get("textDocument") or {}
            uri = td.get("uri") or ""
            parsed = parse_text_edits(change.get("edits"))
            if uri and parsed:
                files.append((uri, parsed))
    # 同一文件可能同时出现在两种字段里：合并后按首次出现顺序返回
    merged = {}
    order = []
    for uri, edits in files:
        if uri not in merged:
            merged[uri] = []
            order.append(uri)
        merged[uri].extend(edits)
    return {"files": [(uri, merged[uri]) for uri in order],
            "resource_ops": resource_ops}


def parse_code_actions(result):
    """解析 textDocument/codeAction 的返回值。

    返回 [{title, kind, preferred, disabled, edit, command, data, action}]。
    ``disabled`` 是 clangd 给出的不可用原因（空串表示可用）。
    """
    if not isinstance(result, list):
        return []
    out = []
    for action in result:
        if not isinstance(action, dict):
            continue
        title = action.get("title") or action.get("label") or ""
        if not title:
            continue
        disabled = action.get("disabled")
        reason = ""
        if isinstance(disabled, dict):
            reason = disabled.get("reason") or ""
        elif isinstance(disabled, str):
            reason = disabled
        out.append({
            "title": title,
            "kind": action.get("kind") or "",
            "preferred": bool(action.get("isPreferred")),
            "disabled": reason,
            "edit": action.get("edit"),
            "command": action.get("command"),
            "data": action.get("data"),
            "action": action,
        })
    return out


def parse_locations(result):
    """解析 references 返回的 Location / LocationLink 列表。"""
    if not isinstance(result, list):
        return []
    out = []
    for loc in result:
        if not isinstance(loc, dict):
            continue
        uri = loc.get("uri") or ""
        rng = loc.get("range")
        if not uri and loc.get("targetUri"):
            uri = loc.get("targetUri")
            rng = loc.get("targetSelectionRange") or loc.get("targetRange")
        if not uri or not isinstance(rng, dict):
            continue
        out.append({"uri": uri, "range": rng})
    return out


def parse_publish_diagnostics(params):
    """解析 textDocument/publishDiagnostics 通知。

    LSP 标准字段是顶层的 ``uri``；部分旧客户端把 uri 放在
    ``textDocument`` 里，这里两种形态都兼容。返回 ``(uri, items)``，
    items 为 ``[(line0, col0, severity, message)]``。
    """
    if not isinstance(params, dict):
        return ("", [])
    uri = params.get("uri") or ""
    if not uri:
        td = params.get("textDocument") or {}
        uri = td.get("uri") or ""
    items = []
    for diag in params.get("diagnostics") or []:
        if not isinstance(diag, dict):
            continue
        rng = diag.get("range") or {}
        start = rng.get("start") or {}
        try:
            line = int(start.get("line", 0))
            col = int(start.get("character", 0))
        except Exception:
            line, col = 0, 0
        try:
            severity = int(diag.get("severity", 1) or 1)
        except Exception:
            severity = 1
        message = diag.get("message") or ""
        if message:
            items.append((line, col, severity, message))
    return (uri, items)


def _norm_uri(u):
    """规范化 LSP uri 用于集合比较。

    clangd 会把我们的 file:///C%3A/... 规范化为 file:///C:/... 再回发
    （diagnostics 等通知），直接字符串比较永远匹配不上。

    只在 Windows 上忽略大小写：Linux / macOS 的文件名区分大小写，
    统一 lower() 会把 /tmp/Foo.cpp 和 /tmp/foo.cpp 当成同一个文件。
    """
    if not u:
        return ""
    try:
        u = urllib.parse.unquote(u)
    except Exception:
        pass
    u = u.replace("\\", "/")
    if os.name == "nt":
        u = u.lower()
    return u


def _clamp_insert(it):
    """从 CompletionItem 提取插入文本。"""
    te = it.get("textEdit")
    if isinstance(te, dict):
        new = te.get("newText")
        if new:
            fmt = te.get("insertTextFormat", it.get("insertTextFormat", 1))
            return new, bool(fmt == 2)
    insert = it.get("insertText") or it.get("label") or ""
    return insert, bool(it.get("insertTextFormat", 1) == 2)


_INCLUDE_RE_TEXT = u"#include"
_INCLUDE_RE = re.compile(
    r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"\n]+)[>"]', re.M)


def _extract_includes(it):
    """从 additionalTextEdits 中提取补全附带的 #include 插入指令。

    clangd 的 header-insertion（默认 iws）把 `#include <vector>` 作为
    additionalTextEdits 附在补全项上，由客户端在补全被接受时应用。
    这里只提取 include 型编辑（其余类型的附加编辑忽略），返回头文件
    名列表，如 ["vector", "utility"]。

    一个 additionalTextEdit 里可能同时包含多行 include（clangd 合并
    编辑时会出现），所以逐行扫描而不是只取第一个尖括号。
    """
    edits = it.get("additionalTextEdits")
    if not isinstance(edits, list):
        return []
    out = []
    for e in edits:
        if not isinstance(e, dict):
            continue
        new = e.get("newText") or ""
        if _INCLUDE_RE_TEXT not in new:
            continue
        for m in _INCLUDE_RE.finditer(new):
            hdr = m.group(1).strip()
            if hdr and hdr not in out:
                out.append(hdr)
    return out


# clangd header-insertion 装饰符：label 前的圆点（会插 include 时添加）。
# 不同版本 clangd 用不同符号：U+2022（•）/ U+25E6（◦）
_HEADER_DECORATORS = (u"\u2022", u"\u25e6")


def _short_label(label):
    """把 clangd 的完整签名 label 裁成短名（对齐 LSP-clangd 的显示）。

    "std::vector<typename Tp, typename Alloc>" -> "std::vector"
    "push_back(const value_type &x)"           -> "push_back"
    "operator<"（无参数列表、非 name<...> 形式）-> 原样保留
    完整签名由调用方放进详情面板显示。
    """
    # 运算符名字里本身就可能带 < / >（operator<、operator<<、
    # operator<=>），不能按模板参数裁剪，否则会变成 "operator"。
    if label.startswith("operator"):
        return label
    i = label.find("(")
    if i > 0:
        return label[:i].rstrip()
    if label.endswith(">") and "<" in label:
        return label[:label.index("<")].rstrip()
    return label


def parse_completion_result(result):
    """解析 textDocument/completion 的返回值为统一条目字典列表。

    返回 [{trigger, insert, annotation, kind, detail, snippet,
           includes}, ...]，保持 clangd 自己的相关性排序（服务端已按
    sortText 排好）。includes 为补全被接受时应插入的头文件（可为空）。
    """
    if result is None:
        return []
    items = result
    if isinstance(result, dict):
        items = result.get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = it.get("label") or ""
        # 剥掉 header-insertion 装饰符（•/◦/空格：是否插 include 以
        # additionalTextEdits 为准，装饰符只会污染触发词）
        if label:
            label = label.lstrip()
            if label and label[0] in _HEADER_DECORATORS:
                label = label[1:].lstrip()
        if not label:
            continue
        # 短名触发词（LSP-clangd 同款显示）：剥函数参数列表与模板参数，
        # 完整签名保留进详情，弹窗不再被超长签名刷屏
        short = _short_label(label)
        insert, is_snippet = _clamp_insert(it)
        kd_code = it.get("kind", 0) or 0
        ann, kind_key = LSP_KIND_MAP.get(kd_code, (u"symbol", "u"))
        detail = it.get("detail") or ""
        if isinstance(detail, dict):
            detail = detail.get("value") or ""
        if short != label:
            detail = (label + ("\n" + detail if detail else "")) if detail \
                else label
        out.append({
            "trigger": short,
            "insert": insert,
            "annotation": ann,
            "kind": kind_key,
            "detail": detail,
            "snippet": is_snippet,
            "includes": _extract_includes(it),
        })
    return out


def parse_hover(result):
    """解析 textDocument/hover 的返回值为 (语言, 纯文本) 或 None。

    contents 兼容三种形态：MarkupContent{language,value}、
    [{language,value}...] 列表、纯字符串。
    """
    if not isinstance(result, dict):
        return None
    contents = result.get("contents")
    if contents is None:
        return None
    parts = []
    lang = ""
    if isinstance(contents, dict):
        lang = contents.get("language") or ""
        v = contents.get("value")
        if isinstance(v, str) and v.strip():
            parts.append(v)
    elif isinstance(contents, list):
        for c in contents:
            if isinstance(c, dict):
                lang = lang or (c.get("language") or "")
                v = c.get("value") or ""
            else:
                v = str(c)
            if v.strip():
                parts.append(v)
    elif isinstance(contents, str):
        if contents.strip():
            parts.append(contents)
    if not parts:
        return None
    return (lang, "\n\n".join(parts))


def parse_signature_help(result):
    """解析 textDocument/signatureHelp 的返回值。

    返回 {"active": 标签, "param": 当前参数序号, "lines": [(文本, 是否激活)],
          "doc": 文档或 None} 或 None。
    lines 里 (文本, True) 表示当前激活的重载签名。
    """
    if not isinstance(result, dict):
        return None
    sigs = result.get("signatures")
    if not isinstance(sigs, list) or not sigs:
        return None
    act = result.get("activeSignature") or 0
    if not isinstance(act, int) or act < 0 or act >= len(sigs):
        act = 0
    ap = result.get("activeParameter") or 0
    if not isinstance(ap, int) or ap < 0:
        ap = 0
    lines = []
    doc = None
    active_line = 0
    for i, sig in enumerate(sigs):
        if not isinstance(sig, dict):
            continue
        label = sig.get("label") or ""
        if not label:
            continue
        if i == act:
            active_line = len(lines)
        lines.append((label, i == act))
        if i == act:
            d = sig.get("documentation")
            if isinstance(d, dict):
                doc = d.get("value") or None
            elif isinstance(d, str):
                doc = d or None
    if not lines:
        return None
    if active_line >= len(lines):
        active_line = 0
    return {"active": lines[active_line][0],
            "param": ap, "lines": lines, "doc": doc}


class ClangdClient(object):
    """与单个 clangd 进程通信的最小 LSP 客户端。

    线程模型：
      - 读线程：解析 Content-Length 分帧的 JSON-RPC，响应唤醒等待者，
        服务器反向请求回空，通知（publishDiagnostics 等）忽略。
      - 写操作：统一持锁，防止多线程交错写帧。
      - 同步补全：on_query_completions 所在线程等待 Event，带超时。
    """

    def __init__(self, binary, args, root_dir, fallback_flags=None,
                 on_notify=None, on_apply_edit=None):
        self.binary = binary
        self.args = list(args or [])
        self.root_dir = root_dir
        self.fallback_flags = list(fallback_flags or [])
        self.on_notify = on_notify  # fn(method, params)，读线程调用，谨慎使用
        # fn(params, respond)：服务端 workspace/applyEdit 请求。respond
        # 接受 (applied, reason)，由插件在 UI 线程应用编辑后调用。
        self.on_apply_edit = on_apply_edit
        self.proc = None
        self._wlock = threading.Lock()
        self._pending = {}      # id -> {"event": Event, "resp": dict|None}
        self._idgen = [0]
        self._doc_versions = {}  # uri -> int
        self._doc_texts = {}     # uri -> 最近一次同步的全文
        # 已收到 publishDiagnostics 的 uri 集合：首次诊断到达 ≈ 该文件的
        # preamble 构建完成（大标准 + bits/stdc++.h 冷启动可达 10s+，
        # 在此之前 clangd 补全返回空，客户端应先走本地兜底）
        self._preamble_ready = set()
        # uri 规范化映射缓存（clangd 回发的是规范化 uri）
        self._uri_norm = {}
        # clangd 诊断存储：norm_uri -> [(line0, col0, severity, message)]
        # 供诊断引擎模式（lint_engine=clangd）渲染 LSP 式代码审查
        self._diagnostics = {}
        # 原始诊断（含 code/range），供 textDocument/codeAction 的
        # context.diagnostics 使用；clangd 靠它筛选 quickfix。
        # norm_uri -> (version, [diagnostic dict])
        self._diagnostics_raw = {}
        self._ready = threading.Event()
        self._dead = threading.Event()
        self._send_init_id = [None]

    # ---- 生命周期 ----

    def start(self):
        """启动 clangd 并发送 initialize（握手在后台线程完成）。"""
        cmd = [self.binary] + self.args
        cwd = self.root_dir if (self.root_dir and
                                os.path.isdir(self.root_dir)) else None
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            **_hidden_window_kwargs())
        t = threading.Thread(target=self._reader_loop)
        t.daemon = True
        t.start()
        params = {
            "processId": os.getpid(),
            "rootUri": (path_to_uri(self.root_dir)
                        if self.root_dir else None),
            "rootPath": self.root_dir or None,
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "didSave": False,
                        "willSave": False,
                    },
                    "completion": {
                        "dynamicRegistration": False,
                        "completionItem": {
                            "snippetSupport": True,
                            "documentationFormat": ["plaintext"],
                        },
                        "contextSupport": False,
                    },
                    "signatureHelp": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False,
                              "contentFormat": ["plaintext"]},
                    "codeAction": {
                        "dynamicRegistration": False,
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "", "quickfix", "refactor",
                                    "refactor.extract", "refactor.inline",
                                    "refactor.rewrite", "source",
                                    "source.organizeImports",
                                ],
                            },
                        },
                        "isPreferredSupport": True,
                        "disabledSupport": True,
                        "dataSupport": True,
                        "resolveSupport": {"properties": ["edit"]},
                    },
                    "rename": {"dynamicRegistration": False,
                               "prepareSupport": False},
                    "references": {"dynamicRegistration": False},
                    "rangeFormatting": {"dynamicRegistration": False},
                },
                "workspace": {
                    "applyEdit": True,
                    "workspaceEdit": {"documentChanges": True},
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": False},
                },
            },
            "initializationOptions": {
                # 与 LSP-clangd 相同的机制：无 compile_commands.json 时
                # clangd 用 fallbackFlags 兜底编译参数（调用方负责把
                # -std=... 放在列表首位）
                "fallbackFlags": list(self.fallback_flags),
                "clangdFileStatus": False,
            },
        }
        self._send_request("initialize", params, cb=self._on_initialized)

    def _on_initialized(self, resp):
        # initialize 成功与否都以 _ready 放行；失败时补全请求会超时走兜底
        self._ready.set()
        if resp is not None:
            self.notify("initialized", {})

    def is_alive(self):
        if self.proc is None or self._dead.is_set():
            return False
        return self.proc.poll() is None

    def is_ready(self):
        return self._ready.is_set() and self.is_alive()

    def shutdown(self):
        """礼貌关闭：shutdown -> exit -> kill（尽力而为，不阻塞）。"""
        try:
            self.request("shutdown", None, timeout=0.3)
        except Exception:
            pass
        try:
            self.notify("exit", None)
        except Exception:
            pass
        try:
            if self.proc is not None:
                self.proc.terminate()
        except Exception:
            pass
        self._dead.set()

    # ---- 底层收发 ----

    def _write_frame(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        frame = (u"Content-Length: %d\r\n\r\n"
                 % len(data)).encode("ascii") + data
        with self._wlock:
            try:
                self.proc.stdin.write(frame)
                self.proc.stdin.flush()
            except Exception:
                self._dead.set()

    def notify(self, method, params):
        if self.proc is None or self._dead.is_set():
            return
        self._write_frame({"jsonrpc": "2.0", "method": method,
                           "params": params})

    def _send_request(self, method, params, cb=None):
        """发送请求；cb 在读线程被调用（参数为响应 dict 或 None=进程退出）。"""
        self._idgen[0] += 1
        rid = self._idgen[0]
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        if cb is not None:
            self._pending[rid] = {"event": threading.Event(), "cb": cb,
                                  "method": method}
        else:
            self._pending[rid] = {"event": threading.Event(), "cb": None,
                                  "method": method}
        self._write_frame(msg)
        return rid

    def request_full(self, method, params, timeout=1.0):
        """同步请求，返回完整响应 dict（含 error），超时/进程退出返回 None。"""
        if self.proc is None or self._dead.is_set():
            return None
        rid = self._send_request(method, params, cb=None)
        entry = self._pending.get(rid)
        if entry is None:
            return None
        entry["event"].wait(timeout)
        resp = entry.get("resp")
        self._pending.pop(rid, None)
        return resp if isinstance(resp, dict) else None

    def request(self, method, params, timeout=1.0):
        """同步请求；超时、进程已死或服务端报错返回 None。"""
        resp = self.request_full(method, params, timeout=timeout)
        if resp is None or "error" in resp:
            return None
        return resp.get("result")

    def cancel_pending(self, method):
        """只取消指定方法的未完成请求。

        补全请求会随打字不断重发，需要取消上一轮；但 hover /
        signatureHelp 与补全并发在跑，一刀切取消会把它们的结果
        一起丢掉（弹窗永远出不来）。
        """
        for rid in list(self._pending.keys()):
            entry = self._pending.get(rid)
            if entry is None or entry.get("method") != method:
                continue
            self.notify("$/cancelRequest", {"id": rid})

    def _reader_loop(self):
        headers = {}
        try:
            stdout = self.proc.stdout
            while True:
                line = stdout.readline()
                if not line:
                    break
                sline = line.strip()
                if not sline:
                    if "content-length" in headers:
                        try:
                            length = int(headers["content-length"])
                        except Exception:
                            headers = {}
                            continue
                        body = self._read_exact(stdout, length)
                        headers = {}
                        if body is not None:
                            self._dispatch(body)
                    continue
                k, _, v = sline.partition(b":")
                try:
                    headers[k.decode("latin1").strip().lower()] = \
                        v.decode("latin1").strip()
                except Exception:
                    headers = {}
        except Exception:
            pass
        self._on_exit()

    @staticmethod
    def _read_exact(f, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = f.read(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _on_exit(self):
        self._dead.set()
        self._ready.set()  # 让等待者立刻醒来并拿到 None
        for entry in list(self._pending.values()):
            try:
                ev = entry.get("event")
                if ev is not None:
                    ev.set()
            except Exception:
                pass
        self._pending.clear()

    def _respond(self, rid, result):
        """回复服务器反向请求（workspace/configuration / applyEdit 等）。"""
        self._write_frame({"jsonrpc": "2.0", "id": rid, "result": result})

    def _dispatch(self, body):
        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        rid = msg.get("id")
        method = msg.get("method")
        if rid is not None and method is not None:
            # 服务器反向请求。workspace/configuration 必须回数组，否则
            # 部分 clangd 版本会报错或卡住初始化；workspace/applyEdit
            # 用于重构类代码操作，必须回 {"applied": bool} 对象。
            params = msg.get("params") or {}
            if method == "workspace/configuration":
                result = [{} for _ in (params.get("items") or [])]
                self._respond(rid, result)
            elif method == "workspace/applyEdit":
                if self.on_apply_edit is None:
                    self._respond(
                        rid, {"applied": False,
                              "failureReason": "unsupported"})
                else:
                    def respond(applied, reason=None):
                        result = {"applied": bool(applied)}
                        if reason:
                            result["failureReason"] = reason
                        self._respond(rid, result)
                    try:
                        self.on_apply_edit(params, respond)
                    except Exception:
                        self._respond(
                            rid, {"applied": False,
                                  "failureReason": "applyEdit failed"})
            else:
                self._respond(rid, None)
            return
        if rid is not None:
            entry = self._pending.get(rid)
            if entry is not None:
                entry["resp"] = msg
                cb = entry.get("cb")
                if cb is not None:
                    self._pending.pop(rid, None)
                    try:
                        cb(msg.get("result"))
                    except Exception:
                        pass
                    return
                entry["event"].set()
            return
        # 通知
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri, items = parse_publish_diagnostics(params)
            if uri:
                nu = _norm_uri(uri)
                self._preamble_ready.add(nu)
                self._diagnostics[nu] = items
                raw = [d for d in (params.get("diagnostics") or [])
                       if isinstance(d, dict)]
                self._diagnostics_raw[nu] = (params.get("version"), raw)
        if method and self.on_notify is not None:
            try:
                self.on_notify(method, msg.get("params"))
            except Exception:
                pass

    # ---- 文档同步 ----

    def _version_for(self, uri, bump):
        cur = self._doc_versions.get(uri, 0)
        nxt = cur + 1 if bump else cur
        self._doc_versions[uri] = nxt
        return nxt

    def open_document(self, path, text):
        uri = path_to_uri(path)
        self._doc_texts[uri] = text
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "cpp",
                "version": self._version_for(uri, True),
                "text": text,
            }})

    def change_document(self, path, text):
        uri = path_to_uri(path)
        # 文本没变就不发 didChange：避免每次 hover / codeAction 都把
        # 版本号推高，导致 publishDiagnostics 的 version 对不上，从而
        # 丢失 codeAction 的 diagnostics context。
        if self._doc_texts.get(uri) == text:
            return
        self._doc_texts[uri] = text
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": uri,
                             "version": self._version_for(uri, True)},
            "contentChanges": [{"text": text}],  # 全量同步（客户端声明）
        })

    def close_document(self, path):
        uri = path_to_uri(path)
        self.notify("textDocument/didClose",
                    {"textDocument": {"uri": uri}})
        self._doc_versions.pop(uri, None)
        self._doc_texts.pop(uri, None)
        self._diagnostics_raw.pop(_norm_uri(uri), None)

    def has_document(self, path):
        return path_to_uri(path) in self._doc_versions

    def is_preamble_ready(self, path):
        """该文件的 preamble 是否构建完成（首次诊断通知到达即视为就绪）。

        就绪前 clangd 的补全请求返回空结果；调用方应先用本地数据库
        兜底，避免白等与服务器空转。
        """
        return _norm_uri(path_to_uri(path)) in self._preamble_ready

    def diagnostics_for(self, path):
        """该文件最新的 clangd 诊断列表（[(line0, col0, severity, msg)]）。

        无该文件诊断时返回 None；空列表表示 clangd 认为"无诊断"。
        """
        nu = _norm_uri(path_to_uri(path))
        if nu not in self._diagnostics:
            return None
        return list(self._diagnostics[nu])

    def diagnostics_context_for(self, path):
        """返回可用于 codeAction context 的原始诊断。

        只在该诊断版本与当前文档版本一致时返回，避免把旧位置传给
        clangd 导致 quickfix 落在错误位置。无诊断时返回空列表。
        """
        uri = path_to_uri(path)
        nu = _norm_uri(uri)
        entry = self._diagnostics_raw.get(nu)
        if not entry:
            return []
        version, raw = entry
        current = self._doc_versions.get(uri)
        if version is not None and current is not None and version != current:
            return []
        return list(raw)

    # ---- 补全 ----

    def completion(self, path, text, offset, timeout=0.06,
                   on_arrival=None):
        """请求补全。

        - timeout>0：最多等待 timeout 秒；命中返回条目列表；
          超时返回 None（若给了 on_arrival，结果到达后会以
          on_arrival(items) 在读线程回调一次）。
        - timeout<=0：纯异步模式，立刻返回 None，结果仅经 on_arrival。
        - 请求前先把当前全文同步给 clangd，保证位置与最新文本一致。
        """
        if not self.is_ready():
            return None
        uri = path_to_uri(path)
        if uri not in self._doc_versions:
            self.open_document(path, text)
        else:
            self.change_document(path, text)
        line, col = line_col_utf16(text, offset)
        self.cancel_pending("textDocument/completion")
        rid = self._send_request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"triggerKind": 1},
        }, cb=None)
        entry = self._pending.get(rid)
        if entry is None:
            return None
        if timeout and timeout > 0:
            got = entry["event"].wait(timeout)
            if got:
                resp = entry.get("resp")
                self._pending.pop(rid, None)
                if not isinstance(resp, dict):
                    return None
                return parse_completion_result(resp.get("result"))
        # 超时：挂上异步回调，结果稍后到达时在读线程回调一次
        if on_arrival is not None:
            entry["cb"] = self._wrap_arrival(on_arrival)
        return None

    def _wrap_arrival(self, on_arrival):
        def cb(result):
            try:
                on_arrival(parse_completion_result(result))
            except Exception:
                pass
        return cb

    # ---- 悬停文档 / 签名提示 ----

    def _sync_and_position(self, path, text, offset):
        """把当前全文同步给 clangd 并换算 LSP (line, character) 位置。

        hover / signatureHelp 等单发请求共用；返回 (uri, line, col)
        或 None（服务器未就绪）。
        """
        if not self.is_ready():
            return None
        uri = path_to_uri(path)
        if uri not in self._doc_versions:
            self.open_document(path, text)
        else:
            self.change_document(path, text)
        line, col = line_col_utf16(text, offset)
        return (uri, line, col)

    def hover(self, path, text, offset, timeout=0.4):
        """请求悬停文档；返回 (语言, 文本) 或 None。"""
        pos = self._sync_and_position(path, text, offset)
        if pos is None:
            return None
        uri, line, col = pos
        result = self.request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }, timeout=timeout)
        return parse_hover(result)

    def signature_help(self, path, text, offset, timeout=0.4):
        """请求函数签名提示；返回 parse_signature_help 的结构或 None。"""
        pos = self._sync_and_position(path, text, offset)
        if pos is None:
            return None
        uri, line, col = pos
        result = self.request("textDocument/signatureHelp", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        }, timeout=timeout)
        return parse_signature_help(result)

    # ---- 代码操作 / 重命名 / 查找引用 / 范围格式化 ----

    def code_actions(self, path, text, start, end, timeout=2.0,
                     diagnostics=None):
        """请求 textDocument/codeAction；返回 parse_code_actions 的列表。

        服务器未就绪或超时返回 None（与"没有可用操作"的空列表区分）。
        """
        pos = self._sync_and_position(path, text, start)
        if pos is None:
            return None
        uri, line, col = pos
        end_line, end_col = line_col_utf16(text, end)
        if diagnostics is None:
            diagnostics = self.diagnostics_context_for(path)
        result = self.request("textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": line, "character": col},
                "end": {"line": end_line, "character": end_col},
            },
            "context": {"diagnostics": diagnostics or []},
        }, timeout=timeout)
        return parse_code_actions(result)

    def resolve_code_action(self, action, timeout=2.0):
        """请求 codeAction/resolve，返回补全后的原始 CodeAction dict。"""
        if not self.is_ready() or not isinstance(action, dict):
            return None
        result = self.request("codeAction/resolve", action, timeout=timeout)
        return result if isinstance(result, dict) else None

    def execute_command(self, command, arguments=None, timeout=10.0):
        """执行 workspace/executeCommand。

        clangd 的 ``clangd.applyTweak`` 通过服务端反向请求
        workspace/applyEdit 落地，插件需提供 on_apply_edit 回调。
        返回 ``(结果或 None, 错误消息或 None)``。
        """
        if not self.is_ready():
            return (None, "clangd is not ready")
        resp = self.request_full("workspace/executeCommand", {
            "command": command,
            "arguments": list(arguments or []),
        }, timeout=timeout)
        if resp is None:
            return (None, "clangd did not respond")
        err = resp.get("error")
        if isinstance(err, dict):
            return (None, err.get("message") or "command failed")
        return (resp.get("result"), None)

    def rename(self, path, text, offset, new_name, timeout=2.0):
        """请求 textDocument/rename。

        返回 ``(WorkspaceEdit 或 None, 错误消息或 None)``；错误消息来自
        clangd 的 JSON-RPC error（例如该符号不允许重命名）。
        """
        pos = self._sync_and_position(path, text, offset)
        if pos is None:
            return (None, "clangd is not ready")
        uri, line, col = pos
        resp = self.request_full("textDocument/rename", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "newName": new_name,
        }, timeout=timeout)
        if resp is None:
            return (None, "clangd did not respond")
        err = resp.get("error")
        if isinstance(err, dict):
            return (None, err.get("message") or "rename failed")
        result = resp.get("result")
        if not isinstance(result, dict):
            return (None, "clangd returned no edits")
        return (result, None)

    def references(self, path, text, offset, include_declaration=True,
                   timeout=2.0):
        """请求 textDocument/references；返回 parse_locations 的列表。"""
        pos = self._sync_and_position(path, text, offset)
        if pos is None:
            return None
        uri, line, col = pos
        result = self.request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": bool(include_declaration)},
        }, timeout=timeout)
        return parse_locations(result)

    def range_formatting(self, path, text, start, end, timeout=2.0):
        """请求 textDocument/rangeFormatting；返回 TextEdit 列表或 None。"""
        pos = self._sync_and_position(path, text, start)
        if pos is None:
            return None
        uri, line, col = pos
        end_line, end_col = line_col_utf16(text, end)
        result = self.request("textDocument/rangeFormatting", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": line, "character": col},
                "end": {"line": end_line, "character": end_col},
            },
            "options": {"tabSize": 4, "insertSpaces": True},
        }, timeout=timeout)
        return parse_text_edits(result)
