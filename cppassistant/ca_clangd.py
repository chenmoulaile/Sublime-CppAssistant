# -*- coding: utf-8 -*-
"""CppAssistant 内嵌 clangd 语言服务器客户端（最小 LSP 协议实现）。

架构移植自 sublimelsp/LSP-clangd：直接与 clangd 语言服务器通过 stdio 上的
JSON-RPC 通信，把 clangd 的真实语义补全（编译器级准确度）接入插件，
替代纯静态数据库方案。

设计约束：
  - 兼容 Sublime Text 4 的 Python 3.3 插件宿主（无 f-string / pathlib / typing）。
  - 不依赖 sublime，可独立单元测试。
  - 服务器发现逻辑与 LSP-clangd 一致：优先 PATH 中的 clangd，
    支持常见版本名（clangd / clangd-18 ...）与自定义路径。
  - Windows 下隐藏控制台窗口（STARTF_USESHOWWINDOW + CREATE_NO_WINDOW），
    与 Package Control 审查要求一致。
  - 初始化参数使用 clangd 的 fallbackFlags 注入 -std=（与 LSP-clangd 的
    initialization_options.fallbackFlags 相同机制），补全结果严格遵循
    当前 C++ 标准（CSP-S 推荐 c++14）。

协议实现范围（补全所需最小集）：
  initialize / initialized / shutdown / exit
  textDocument/didOpen / didChange / didClose
  textDocument/completion            （同步等待 + 异步回调双模式）
  textDocument/hover                 （悬停文档，同步等待）
  textDocument/signatureHelp         （函数签名提示，同步等待）
  $/cancelRequest
  服务器反向请求（workspace/configuration、client/registerCapability、
  window/workDoneProgress/create）一律回空，避免 clangd 卡住。
"""

import json
import os
import shutil
import subprocess
import threading
import urllib.parse

# ST 嵌入式 Python 3.3 没有 subprocess.CREATE_NO_WINDOW 命名常量，直接用数值
CREATE_NO_WINDOW = 0x08000000

# clangd 常见二进制名（覆盖 Linux 发行版分版本打包的习惯）
_CLANGD_NAMES = (
    "clangd", "clangd-21", "clangd-20", "clangd-19", "clangd-18",
    "clangd-17", "clangd-16", "clangd-15", "clangd-14", "clangd-13",
    "clangd-12", "clangd-11",
)

# LSP CompletionItemKind -> (中文标注, CppAssistant 内部 kind 键)
# 内部 kind 键会被 CppAssistant._KIND_MAP 转成 Sublime 的图标 + 中文标签
LSP_KIND_MAP = {
    1: (u"文本", "v"),
    2: (u"成员函数", "m"),
    3: (u"函数", "f"),
    4: (u"构造函数", "m"),
    5: (u"成员变量", "v"),
    6: (u"变量", "v"),
    7: (u"类", "t"),
    8: (u"接口", "t"),
    9: (u"模块", "t"),
    10: (u"属性", "v"),
    11: (u"单元", "v"),
    12: (u"值", "c"),
    13: (u"枚举", "t"),
    14: (u"关键字", "k"),
    15: (u"代码片段", "s"),
    16: (u"颜色", "c"),
    17: (u"文件", "u"),
    18: (u"引用", "u"),
    19: (u"枚举值", "c"),
    20: (u"常量", "c"),
    21: (u"结构体", "t"),
    22: (u"事件", "u"),
    23: (u"操作符", "u"),
    24: (u"类型参数", "t"),
    25: (u"命名空间", "t"),
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


def _norm_uri(u):
    """规范化 LSP uri 用于集合比较。

    clangd 会把我们的 file:///C%3A/... 规范化为 file:///C:/... 再回发
    （diagnostics 等通知），直接字符串比较永远匹配不上。
    """
    if not u:
        return ""
    try:
        u = urllib.parse.unquote(u)
    except Exception:
        pass
    return u.replace("\\", "/").lower()


def _clamp_insert(it):
    """从 CompletionItem 提取插入文本。"""
    te = it.get("textEdit")
    if isinstance(te, dict):
        new = te.get("newText")
        if new:
            return new, bool(it.get("insertTextFormat", 1) == 2)
    insert = it.get("insertText") or it.get("label") or ""
    return insert, bool(it.get("insertTextFormat", 1) == 2)


_INCLUDE_RE_TEXT = u"#include"


def _extract_includes(it):
    """从 additionalTextEdits 中提取补全附带的 #include 插入指令。

    clangd 的 header-insertion（默认 iws）把 `#include <vector>` 作为
    additionalTextEdits 附在补全项上，由客户端在补全被接受时应用。
    这里只提取 include 型编辑（其余类型的附加编辑忽略），返回头文件
    名列表，如 ["vector", "utility"]。
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
        # 提取 #include <X> / #include "X" 中的 X
        lt = new.find("<")
        if lt == -1:
            lt = new.find('"')
            rt = new.find('"', lt + 1)
        else:
            rt = new.find(">", lt)
        if lt == -1 or rt == -1:
            continue
        hdr = new[lt + 1:rt].strip()
        if hdr:
            out.append(hdr)
    return out


# clangd header-insertion 装饰符：label 前的圆点（会插 include 时添加）。
# 不同版本 clangd 用不同符号：U+2022（•）/ U+25E6（◦）
_HEADER_DECORATORS = (u"\u2022", u"\u25e6")


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
        insert, is_snippet = _clamp_insert(it)
        kd_code = it.get("kind", 0) or 0
        ann, kind_key = LSP_KIND_MAP.get(kd_code, (u"符号", "u"))
        detail = it.get("detail") or ""
        if isinstance(detail, dict):
            detail = detail.get("value") or ""
        out.append({
            "trigger": label,
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
    for i, sig in enumerate(sigs):
        if not isinstance(sig, dict):
            continue
        label = sig.get("label") or ""
        if not label:
            continue
        lines.append((label, i == act))
        if i == act:
            d = sig.get("documentation")
            if isinstance(d, dict):
                doc = d.get("value") or None
            elif isinstance(d, str):
                doc = d or None
    if not lines:
        return None
    return {"active": lines[act][0] if act < len(lines) else "",
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
                 on_notify=None):
        self.binary = binary
        self.args = list(args or [])
        self.root_dir = root_dir
        self.fallback_flags = list(fallback_flags or [])
        self.on_notify = on_notify  # fn(method, params)，读线程调用，谨慎使用
        self.proc = None
        self._wlock = threading.Lock()
        self._pending = {}      # id -> {"event": Event, "resp": dict|None}
        self._idgen = [0]
        self._doc_versions = {}  # uri -> int
        # 已收到 publishDiagnostics 的 uri 集合：首次诊断到达 ≈ 该文件的
        # preamble 构建完成（大标准 + bits/stdc++.h 冷启动可达 10s+，
        # 在此之前 clangd 补全返回空，客户端应先走本地兜底）
        self._preamble_ready = set()
        # uri 规范化映射缓存（clangd 回发的是规范化 uri）
        self._uri_norm = {}
        # clangd 诊断存储：norm_uri -> [(line0, col0, severity, message)]
        # 供诊断引擎模式（lint_engine=clangd）渲染 LSP 式代码审查
        self._diagnostics = {}
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
                },
                "workspace": {
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
            self._pending[rid] = {"event": threading.Event(), "cb": cb}
        else:
            self._pending[rid] = {"event": threading.Event(), "cb": None}
        self._write_frame(msg)
        return rid

    def request(self, method, params, timeout=1.0):
        """同步请求；超时或进程已死返回 None。"""
        if self.proc is None or self._dead.is_set():
            return None
        rid = self._send_request(method, params, cb=None)
        entry = self._pending.get(rid)
        if entry is None:
            return None
        entry["event"].wait(timeout)
        resp = entry.get("resp")
        self._pending.pop(rid, None)
        if resp is None:
            return None
        if isinstance(resp, dict) and "error" in resp:
            return None
        return resp.get("result") if isinstance(resp, dict) else None

    def cancel_all(self):
        """取消所有未完成的请求（快速输入时减少 clangd 空转）。"""
        for rid in list(self._pending.keys()):
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
            # 服务器反向请求：一律回空结果，避免 clangd 等待
            self._write_frame({"jsonrpc": "2.0", "id": rid, "result": None})
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
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            if uri:
                nu = _norm_uri(uri)
                self._preamble_ready.add(nu)
                items = []
                for d in (params.get("diagnostics") or []):
                    if not isinstance(d, dict):
                        continue
                    rng = d.get("range") or {}
                    start = rng.get("start") or {}
                    try:
                        ln = int(start.get("line", 0))
                        cl = int(start.get("character", 0))
                    except Exception:
                        ln, cl = 0, 0
                    try:
                        sev = int(d.get("severity", 1) or 1)
                    except Exception:
                        sev = 1
                    m = d.get("message") or ""
                    if m:
                        items.append((ln, cl, sev, m))
                self._diagnostics[nu] = items
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
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "cpp",
                "version": self._version_for(uri, True),
                "text": text,
            }})

    def change_document(self, path, text):
        uri = path_to_uri(path)
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
        self.cancel_all()
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
