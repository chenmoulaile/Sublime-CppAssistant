# -*- coding: utf-8 -*-
"""CppAssistant —— Sublime Text 4 C++ 辅助插件（内嵌真实 clangd 引擎 + 汉化版）

补全引擎（v1.4.0 起移植 LSP-clangd，直接驱动真实 clangd 语言服务器）：
  - 内嵌最小 LSP 客户端（cppassistant/ca_clangd.py）：stdio JSON-RPC 与
    clangd 通信，补全结果为编译器级语义准确度（签名、重载、成员、局部变量）
  - 用户代码片段（User 包里的 .sublime-snippet）+ 内置片段排最前面
  - 内置数据库兜底模式下按 C++14 档排前面，C++17/20/23 档排后面
  - 无 clangd 时自动回退内置 STL 数据库（零配置可用）

其他功能（沿用 LSP-clangd 架构设计）：
  - 实时语法检查：即时基础检查（毫秒级）+ 编译器完整检查（PCH + 结果缓存）
  - F12 跳转定义：当前文件 → 已打开文件 → 本地头文件递归搜索
  - jiangly 码风格式化：优先 clang-format，无则内置兜底

兼容 Sublime Text 4 的 Python 3.3 插件宿主。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib

import sublime
import sublime_plugin

# 子包导入：优先相对导入（ST 宿主把根级插件挂载在包命名空间下，
# 与 cph-by-chenkx 的 .core.* 模式一致）；若宿主把根级文件当独立
# 模块加载（无父包），则把包目录加入 sys.path 后走绝对导入兜底。
try:
    from .cppassistant import ca_clangd  # noqa: E402
    from .cppassistant import ca_engine  # noqa: E402
    from .cppassistant import ca_user_snippets  # noqa: E402
    from .cppassistant.ca_stdlib_data import SNIPPETS  # noqa: E402
except ImportError:  # pragma: no cover - 仅独立模块宿主触发
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from cppassistant import ca_clangd  # noqa: E402
    from cppassistant import ca_engine  # noqa: E402
    from cppassistant import ca_user_snippets  # noqa: E402
    from cppassistant.ca_stdlib_data import SNIPPETS  # noqa: E402


def _hidden_window_startupinfo():
    if os.name == "nt":
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            return si
        except Exception:
            pass
    return None

_SETTINGS = "CppAssistant.sublime-settings"
_settings_obj = None
_compiler_cache = {"path": None, "done": False}

CPP_SCOPE = "source.c++, source.c"

# 视图相关运行时状态 -------------------------------------------------------
_lint_timers = {}      # view_id -> threading.Timer（编译器检查防抖）
_basic_timers = {}     # view_id -> threading.Timer（即时基础检查微防抖）
_phantom_sets = {}     # view_id -> PhantomSet
_diag_store = {}       # view_id -> [str]
_lint_gen = {}         # view_id -> 代号（丢弃过期编译器结果）
_basic_gen = {}        # view_id -> 代号（丢弃过期基础检查结果）
_lint_procs = {}       # view_id -> 运行中的编译器进程（被取代时立刻终止）
_lint_state = {}       # view_id -> {"compiler": [...], "basic": [...]}
_LINT_CACHE = {}       # view_id -> (缓存键, 诊断)；文本与设置未变则零延迟复用


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

def _on_settings_changed():
    _compiler_cache["done"] = False
    _compiler_cache["path"] = None
    _LINT_CACHE.clear()
    # 通知引擎失效缓存
    ca_engine.invalidate_cache()
    ca_user_snippets.invalidate_cache()
    # clangd 相关设置变化时重启语言服务器（下次补全时懒重启）
    if _clangd_state["sig"] is not None and \
            _clangd_state["sig"] != _clangd_settings_sig():
        _stop_clangd_client()


def plugin_unloaded():
    if _settings_obj is not None:
        _settings_obj.clear_on_change("cppassistant")
    # 清理所有运行中的编译器进程
    for vid, proc in list(_lint_procs.items()):
        try:
            proc.kill()
        except Exception:
            pass
    _lint_procs.clear()
    # 关闭 clangd 语言服务器
    _stop_clangd_client()


def _s(key, default=None):
    if _settings_obj is None:
        return default
    return _settings_obj.get(key, default)


# 控制台日志限频：同一位置最多打印 3 次，避免每敲一键刷屏
_log_counts = {}


def _log_error(where, exc):
    """把被兜底捕获的异常打到 Sublime 控制台（View → Show Console），
    避免插件出错时完全黑箱。同一位置最多记录 3 次。"""
    n = _log_counts.get(where, 0)
    if n < 3:
        _log_counts[where] = n + 1
        import traceback
        print("[CppAssistant] %s 出错%s: %r" % (
            where, ("（该位置继续出错将不再打印）" if n == 2 else ""), exc))
        traceback.print_exc()


def _is_cpp(view):
    return view.score_selector(0, CPP_SCOPE) > 0


# ---------------------------------------------------------------------------
# 补全
# ---------------------------------------------------------------------------

def _kind_default():
    return sublime.KIND_AMBIGUOUS


# 全中文类型标签
_KIND_MAP = {
    "f": lambda: (sublime.KIND_ID_FUNCTION, "f", u"函数"),
    "m": lambda: (sublime.KIND_ID_FUNCTION, "m", u"成员函数"),
    "v": lambda: (sublime.KIND_ID_VARIABLE, "v", u"成员变量"),
    "c": lambda: (sublime.KIND_ID_CONSTANT, "c", u"常量"),
    "t": lambda: (sublime.KIND_ID_TYPE, "T", u"类型"),
    "k": lambda: (sublime.KIND_ID_KEYWORD, "k", u"关键字"),
    "u": lambda: (sublime.KIND_ID_FUNCTION, "u", u"自定义"),
    "s": lambda: (sublime.KIND_ID_SNIPPET, "S", u"代码片段"),
}


def _make_item(d):
    kind = _KIND_MAP.get(d.get("kind"), _kind_default)()
    insert = d["insert"]
    is_snippet = (bool(d.get("snippet")) or ("\n" in insert)
                  or ("$" in insert))
    fmt = (sublime.COMPLETION_FORMAT_SNIPPET if is_snippet
           else sublime.COMPLETION_FORMAT_TEXT)
    details = d.get("detail", "")
    ann = d.get("annotation", "")
    return sublime.CompletionItem(
        trigger=d["trigger"],
        annotation=ann,
        completion=insert,
        completion_format=fmt,
        kind=kind,
        details=details,
    )


# ---------------------------------------------------------------------------
# clangd 补全引擎（移植 LSP-clangd：直接驱动真实 clangd 语言服务器）
# ---------------------------------------------------------------------------

_clangd_state = {
    "client": None,
    "root": None,
    "sig": None,           # 相关设置签名（变化时重启 clangd）
    "last_fail": 0.0,
    "no_server": False,    # 已确认机器上没有 clangd，用内置数据库兜底
    "async_cache": {},     # (buffer_id, change_count, offset) -> [dict]
    "refreshed": None,     # 最近一次弹窗刷新键（防刷新循环）
    # buffer_id -> {"cc": 编辑代号, "items": [(trigger, 展开后插入文本, [头文件])]}
    # 记录最近一次补全候选附带的 #include 插入指令，供补全被接受后应用
    "pending_includes": {},
    # 补全来源统计（诊断用）：clangd 结果次数 / 内置兜底次数
    "stats": {"clangd": 0, "builtin": 0},
}

# 内置数据库条目按版本分档：C++14 及以下排前面，C++17/20/23 排后面
_LATER_STD_RE = re.compile(r"C\+\+(1[7-9]|2[0-9])")


def _clangd_settings_sig():
    return "%s|%s|%s|%s|%s|%s" % (
        _s("enable_clangd_engine", True),
        _s("cxx_standard", "c++14"),
        _s("clangd_binary", "") or "",
        _s("clangd_args", []) or [],
        _s("clangd_extra_fallback_flags", []) or [],
        _s("compiler_path", "") or "")


def _clangd_root_for(view):
    wd = _s("clangd_working_dir", "")
    if wd and os.path.isdir(wd):
        return wd
    fname = view.file_name()
    if fname:
        return os.path.dirname(fname)
    try:
        folders = view.window().folders()
    except Exception:
        folders = None
    return folders[0] if folders else None


def _clangd_cdb_dir():
    """compile_commands.json 所在目录（clangd 用 --compile-commands-dir 指向它）。

    优先用 Sublime 的缓存目录；不可用时退回系统临时目录。
    """
    try:
        base = sublime.cache_path()
    except Exception:
        base = None
    if not base:
        base = tempfile.gettempdir()
    d = os.path.join(base, "CppAssistant")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = tempfile.gettempdir()
    return d


_CDB_PATH = None
_CDB_CACHE = {"mtime": None, "db": {}}  # mtime -> 条目字典（避免每键读盘）


def _cdb_update(fname, std, force=False):
    """把单个源文件的编译参数写入 compile_commands.json（clangd 标准姿势）。

    这是让 clangd 按指定 C++ 标准与编译器头文件解析代码的可靠方式
    （比 --query-driver 的 glob 白名单更确定）。文件名 -> 条目字典缓存
    在内存里，只有内容变化才写盘。
    """
    global _CDB_PATH
    if _CDB_PATH is None:
        _CDB_PATH = os.path.join(_clangd_cdb_dir(), "compile_commands.json")
    path = _CDB_PATH
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    except Exception:
        mtime = None
    if _CDB_CACHE["mtime"] != mtime:
        db = {}
        try:
            if mtime is not None:
                with open(path, "r", encoding="utf-8") as f:
                    arr = sublime.decode_value(f.read()) or []
                if isinstance(arr, list):
                    for e in arr:
                        if isinstance(e, dict) and e.get("file"):
                            db[e["file"]] = e
        except Exception:
            db = {}
        _CDB_CACHE["db"] = db
        _CDB_CACHE["mtime"] = mtime
    db = _CDB_CACHE["db"]
    key = fname.replace("\\", "/")
    compiler = _compiler_cache.get("path") or find_compiler() or "clang++"
    args_list = [compiler.replace("\\", "/"), "-std=" + std]
    for a in (_s("compiler_extra_args", []) or []):
        args_list.append(a)
    entry = {
        "directory": os.path.dirname(key),
        "arguments": args_list + [key],
        # 注意：标准 compile_commands.json 的键是 "file"（不是 "filename"），
        # 键名错误会导致 clangd 拒绝加载整个 CDB（Unknown key），
        # 永远退回 fallback 编译模式
        "file": key,
    }
    old = db.get(key)
    if old == entry and not force:
        return
    db[key] = entry
    try:
        arr = [db[k] for k in sorted(db.keys())]
        payload = sublime.encode_value(arr, False)
        # 原子写（临时文件 + os.replace）：clangd 的 automaticReload 会
        # 监听 CDB 变化，写到一半的文件会被它读走导致解析失败。
        # 多个 Sublime 窗口/实例并发写同理。
        tmp_path = path + ".tmp%d" % os.getpid()
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, path)
        _CDB_CACHE["mtime"] = os.path.getmtime(path)
    except Exception as e:
        _log_error("clangd compile_commands 写入", e)


def _compiler_include_dirs():
    """提取编译器的 C++ 头文件搜索路径列表（结果缓存，只查一次）。

    用途：clangd 的 fallbackFlags 注入 -I。clangd 在 CDB 缺条目时进入
    fallback 模式，fallback 命令只有 clang 自带 resource-dir、没有
    libstdc++ 头路径，bits/stdc++.h 直接解析失败（补全/诊断全空）。
    把 g++ 的真实 include 列表喂给 fallbackFlags 后，fallback 也能
    正确解析标准库。
    """
    if _include_dirs_cache["done"]:
        return list(_include_dirs_cache["dirs"])
    dirs = []
    compiler = find_compiler()
    if compiler:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            p = subprocess.Popen(
                [compiler, "-E", "-x", "c++", "-", "-v"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=creationflags,
                startupinfo=_hidden_window_startupinfo())
            _, err = p.communicate(timeout=15)
            text = (err or b"").decode("utf-8", "replace")
            in_search = False
            for line in text.splitlines():
                if "#include <...> search starts here" in line:
                    in_search = True
                    continue
                if in_search:
                    if line.startswith("End of search list"):
                        break
                    d = line.strip()
                    if d and os.path.isdir(d) and d not in dirs:
                        dirs.append(d)
        except Exception as e:
            _log_error("编译器 include 路径探测", e)
    _include_dirs_cache.update(dirs=dirs, done=True)
    return list(dirs)


_include_dirs_cache = {"done": False, "dirs": []}


def _stop_clangd_client():
    c = _clangd_state["client"]
    if c is not None:
        try:
            c.shutdown()
        except Exception:
            pass
    _clangd_state["client"] = None
    _clangd_state["sig"] = None
    _clangd_state["ready_announced"] = False


def _on_clangd_notify(method, params):
    """clangd 读线程通知回调（勿做重活）。

    首次收到任意文件的 publishDiagnostics ≈ preamble 构建完成，
    状态栏提示用户"clangd 引擎已就绪"（此后补全自动升级为 clangd 结果）。
    """
    if method == "textDocument/publishDiagnostics":
        st = _clangd_state
        if not st.get("ready_announced"):
            st["ready_announced"] = True
            try:
                sublime.set_timeout(
                    lambda: sublime.status_message(
                        u"CppAssistant: clangd 引擎已就绪（语义补全已接管）"),
                    0)
            except Exception:
                pass


def _get_clangd_client(view):
    """懒启动 clangd（首次打开 C++ 视图后触发）；失败 5 秒冷却重试。"""
    st = _clangd_state
    if not _s("enable_clangd_engine", True):
        return None
    if st["no_server"]:
        return None
    c = st["client"]
    if c is not None and c.is_alive():
        return c
    if c is not None:
        _stop_clangd_client()
    now = time.time()
    if now - st["last_fail"] < 5.0:
        return None
    sig = _clangd_settings_sig()
    if st["sig"] != sig:
        st["sig"] = sig
    binary = ca_clangd.find_clangd(_s("clangd_binary", "") or None)
    if not binary:
        st["no_server"] = True
        sublime.status_message(
            u"CppAssistant: 未找到 clangd，补全使用内置数据库"
            u"（可设置 clangd_binary 指定路径）")
        return None
    root = _clangd_root_for(view)
    std = _s("cxx_standard", "c++14")
    fallback = ["-std=" + std]
    for f in (_s("clangd_extra_fallback_flags", []) or []):
        if f not in fallback:
            fallback.append(f)
    # fallback 模式兜底：把编译器的标准库 include 路径喂给 clangd，
    # 否则 CDB 缺条目时 fallback 命令解析不了 bits/stdc++.h
    for d in _compiler_include_dirs():
        fallback.append("-I" + d)
    args = ["--background-index=false",
            "--completion-style=detailed",
            "-j=2",
            # header-insertion 与 function-arg-placeholders 均用 clangd
            # 默认值（与 LSP-clangd 一致，不显式传 flag）：默认策略 iwyu
            # 会在补全项上附带 #include <X> 的 additionalTextEdits，由
            # 插件应用并做万能头智能判断（已有 bits/stdc++.h 时跳过）。
            # 注意：不同版本 clangd 的取值名不同（旧版 iws / 新版 iwyu），
            # 显式传 flag 反而会令其中一端启动失败，故省略。
            # clangd 读取我们动态维护的 compile_commands.json（标准姿势，
            # 决定 -std 与编译器头文件路径；比 query-driver 白名单可靠）
            "--compile-commands-dir=" + _clangd_cdb_dir().replace("\\", "/")]
    args.extend(_s("clangd_args", []) or [])
    try:
        c = ca_clangd.ClangdClient(binary, args, root,
                                   fallback_flags=fallback,
                                   on_notify=_on_clangd_notify)
        c.start()
    except Exception as e:
        st["last_fail"] = now
        _log_error("clangd 启动", e)
        return None
    st["client"] = c
    st["root"] = root
    return c


def _warm_clangd_document(view):
    """打开/切换 C++ 文档后把全文推给 clangd 预热（异步，不阻塞）。

    先确保该文件在 compile_commands.json 里（clangd 首次解析文件时
    就按正确参数建立 preamble），再推送全文。
    """
    try:
        if not _s("enable_clangd_engine", True) or not _is_cpp(view):
            return
        fname = view.file_name()
        if not fname:
            return
        _cdb_update(fname, _s("cxx_standard", "c++14"))
        client = _get_clangd_client(view)
        if client is None or not client.is_ready():
            return
        text = view.substr(sublime.Region(0, min(view.size(), 400000)))
        if client.has_document(fname):
            client.change_document(fname, text)
        else:
            client.open_document(fname, text)
    except Exception as e:
        _log_error("clangd 预热", e)


def _snippet_items_for_prefix(prefix):
    """用户自己的 .sublime-snippet + 内置片段，按触发器前缀过滤。

    这两组永远排在弹窗最前面（用户肌肉记忆优先）。
    """
    if not prefix:
        return []
    low = prefix.lower()
    out = []
    for d in ca_user_snippets.get_cpp_snippets():
        if d["trigger"].lower().startswith(low):
            out.append(d)
    for trig, body, desc, kd in SNIPPETS:
        if trig.lower().startswith(low):
            out.append({"trigger": trig, "insert": body,
                        "annotation": desc, "kind": kd,
                        "detail": desc, "snippet": True})
    return out


def _tier_sort_static(results, prefix):
    """内置数据库兜底：用户片段最前 → C++14 及以下档 → C++17/20/23 档。"""
    out = _snippet_items_for_prefix(prefix)
    early = []
    later = []
    for d in results:
        if _LATER_STD_RE.search(d.get("annotation") or ""):
            later.append(d)
        else:
            early.append(d)
    out.extend(early)
    out.extend(later)
    return out


# 补全被接受后的头文件插入（万能头智能判断）--------------------------------

_SNIPPET_FIELD_RE = re.compile(r"\$\{\d+:[^{}]*\}|\$\d+")


def _expand_snippet_text(s):
    """把补全模板里的 snippet 占位符展开成实际插入文本。

    ${1:默认值} -> 默认值；$1/$0 -> 空串。用于把 clangd 的 insert
    （--function-arg-placeholders=true 时形如 push_back(${1:x})）
    与编辑器实际插入的文本做匹配。
    """
    def _sub(m):
        tok = m.group(0)
        if tok.startswith("${"):
            return tok[tok.index(":") + 1:-1]
        return ""
    return _SNIPPET_FIELD_RE.sub(_sub, s or "")


def _stash_includes(view, dicts):
    """记录本次补全候选附带的 #include 插入指令（clangd header-insertion）。

    Sublime 没有"补全被接受"事件，这里把 (trigger, 展开后插入文本) ->
    头文件列表 暂存起来，on_text_changed 里检测到匹配的插入文本后应用。
    """
    try:
        if not _s("auto_insert_includes", True):
            return
        items = []
        for d in dicts:
            incs = d.get("includes")
            if not incs:
                continue
            items.append((d.get("trigger") or "",
                          _expand_snippet_text(d.get("insert") or ""),
                          list(incs)))
        bid = view.buffer_id()
        if not items:
            _clangd_state["pending_includes"].pop(bid, None)
            return
        _clangd_state["pending_includes"][bid] = {
            "cc": view.change_count(),
            "items": items,
        }
    except Exception as e:
        _log_error("头文件插入暂存", e)


def _apply_pending_includes(view, hdrs):
    """补全被接受后的实际应用入口（on_text_changed 回调后调）。

    规则（对齐用户需求）：
      - 缓冲里已有 #include <bits/stdc++.h>（万能头）-> 什么都不插；
      - 否则把补全项携带的头文件插入到最后一个 #include 行之后；
      - 目标头文件已经在文件里 -> 跳过该条。
    实际编辑交给 TextCommand（UI 线程执行），这里只做线程切换。
    """
    try:
        if not hdrs:
            return
        view.run_command("ca_insert_pending_includes",
                         {"includes": [str(h) for h in hdrs]})
    except Exception as e:
        _log_error("头文件插入", e)


class CaInsertPendingIncludesCommand(sublime_plugin.TextCommand):
    """把缺失的 #include <X> 插到最后一个 #include 行之后（无则插文件开头）。"""

    def run(self, edit, includes=None):
        view = self.view
        if not includes:
            return
        text = view.substr(sublime.Region(0, view.size()))
        if "bits/stdc++.h" in text:
            return
        missing = []
        seen = set()
        for h in includes:
            h = str(h).strip()
            if not h or h in seen:
                continue
            seen.add(h)
            if ('#include <%s>' % h) in text or ('#include "%s"' % h) in text:
                continue
            missing.append(h)
        if not missing:
            return
        lines = text.split("\n")
        last_inc = -1
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#include"):
                last_inc = i
        if last_inc >= 0:
            pt = view.text_point(last_inc, len(lines[last_inc]))
            block = "\n" + "\n".join("#include <%s>" % h for h in missing)
        else:
            pt = 0
            block = "\n".join("#include <%s>" % h for h in missing) + "\n"
        view.insert(edit, pt, block)


def _maybe_refresh_popup(view, key):
    """clangd 异步结果到达后，若弹窗仍开着且文本未变，重开弹窗换上新结果。

    与 LSP 插件行为一致；refreshed 键防止刷新循环。
    """
    st = _clangd_state
    if st["refreshed"] == key:
        return
    try:
        if view.change_count() != key[1]:
            return
        if hasattr(view, "is_auto_complete_visible") and \
                not view.is_auto_complete_visible():
            return
        st["refreshed"] = key
        view.run_command("hide_auto_complete")
        sublime.set_timeout(
            lambda: view.run_command("auto_complete"), 0)
    except Exception:
        pass


class CaEventListener(sublime_plugin.EventListener):
    # ---- 补全 ----
    def on_query_completions(self, view, prefix, locations):
        if not _s("enable_completions", True):
            return None
        if not _is_cpp(view):
            return None
        # 性能：限制分析范围（前 400KB，覆盖绝大多数场景）
        off = locations[0]
        cap = 400000
        size = view.size()
        text = view.substr(sublime.Region(0, min(size, cap)))
        if off > len(text):
            return None
        # 补全匹配风格：true=LSP-clangd 风格（默认）；false=严格前缀基础模式
        clangd_style = bool(_s("enable_clangd_style_completion", True))
        flags = sublime.INHIBIT_SNIPPET_COMPLETIONS  # 片段由本插件统一供给
        dicts = None
        if _s("enable_clangd_engine", True):
            dicts = self._clangd_items(view, text, off, prefix)
        if dicts is None:
            # 内置数据库兜底（clangd 未启用/未找到/本次超时）
            try:
                results = ca_engine.analyze(
                    text, off,
                    cache_key=view.buffer_id(),
                    cache_version=view.change_count(),
                    clangd_style=clangd_style)
            except Exception as e:
                _log_error("补全引擎", e)
                return None
            dicts = _tier_sort_static(results, prefix)
            _clangd_state["stats"]["builtin"] += 1
        else:
            _clangd_state["stats"]["clangd"] += 1
        if not dicts:
            return None
        if clangd_style:
            # LSP-clangd 风格下压制 Sublime 内置单词补全（两种引擎都压：
            # 否则用户代码里的标识符如 revertDSU 会混进语义补全列表）
            flags |= sublime.INHIBIT_WORD_COMPLETIONS
        items = [_make_item(d) for d in dicts]
        return sublime.CompletionList(items, flags)

    def _clangd_items(self, view, text, off, prefix):
        """真实 clangd 补全；返回 dict 列表或 None（走兜底）。

        命中顺序：用户/内置片段（最前）→ clangd 结果（保持服务端
        相关性排序，全部符合当前 C++ 标准）。
        空结果视为"preamble 未就绪"，返回 None 走内置数据库兜底，
        且不缓存、不触发弹窗刷新（避免刷新成空弹窗）。
        """
        st = _clangd_state
        client = _get_clangd_client(view)
        if client is None:
            return None
        fname = view.file_name()
        if not fname:
            return None
        std = _s("cxx_standard", "c++14")
        key = (view.buffer_id(), view.change_count(), off)
        cache = st["async_cache"]
        if key in cache:
            items = cache.get(key)
            if items:
                dicts = _snippet_items_for_prefix(prefix) + items
                _stash_includes(view, dicts)
                return dicts
            return None
        wait_ms = int(_s("clangd_completion_wait_ms", 60) or 0)

        def on_arrival(parsed):
            if not parsed:
                return  # 未就绪/真无结果：不缓存不刷新
            cache[key] = parsed
            if len(cache) > 32:
                for k in list(cache.keys())[:-32]:
                    cache.pop(k, None)
            sublime.set_timeout(
                lambda: _maybe_refresh_popup(view, key), 0)

        # 首次补全前确保该文件在 compile_commands.json 中
        _cdb_update(fname, std)
        if not client.is_preamble_ready(fname):
            # preamble 还在构建（c++23 等高档标准 + bits/stdc++.h
            # 冷启动实测可达 10s+）：本回合直接用内置数据库兜底，
            # 只发起纯异步请求触发文档同步，结果到达后自动刷新弹窗。
            # （就绪前 clangd 的补全请求一律返回空，同步等待纯属白等）
            client.completion(fname, text, off, timeout=0,
                              on_arrival=on_arrival)
            return None
        parsed = client.completion(fname, text, off,
                                   timeout=wait_ms / 1000.0,
                                   on_arrival=on_arrival)
        if not parsed:
            return None  # 本次先弹内置数据库兜底，clangd 结果到了再刷新
        dicts = _snippet_items_for_prefix(prefix) + parsed
        _stash_includes(view, dicts)
        return dicts

    # ---- 语法检查触发 ----
    def on_load_async(self, view):
        self._maybe_lint(view)
        # clangd 预热：延迟 300ms，等编辑器把文件展示稳定后推送全文
        sublime.set_timeout(
            lambda: _warm_clangd_document(view), 300)

    # ---- 补全被接受后的头文件插入 ----
    def on_text_changed_async(self, view, changes):
        self._maybe_apply_pending_includes(view, changes)

    def _maybe_apply_pending_includes(self, view, changes):
        """检测"补全被接受"并触发智能头文件插入。

        Sublime 没有 completion-accepted 事件，这里比对本次编辑插入的
        文本与暂存的补全候选：匹配到即视为补全被接受，取出该候选携带
        的头文件列表交给 TextCommand 应用（万能头判断在 TextCommand 里）。
        误报场景（手打/粘贴出与候选完全一致的文本）后果只是补一条正确的
        #include，可接受。
        """
        try:
            if not _is_cpp(view):
                return
            if not _s("auto_insert_includes", True):
                return
            pend = _clangd_state["pending_includes"].get(view.buffer_id())
            if not pend:
                return
            cc = view.change_count()
            if cc <= pend["cc"] or cc > pend["cc"] + 8:
                # 太旧或与快照脱节：直接丢弃（下次补全会重新暂存）
                _clangd_state["pending_includes"].pop(view.buffer_id(), None)
                return
            inserted = []
            for ch in changes:
                try:
                    txt = ch[2]
                except Exception:
                    txt = None
                if isinstance(txt, str) and txt:
                    inserted.append(txt)
            if not inserted:
                return
            joined = "\n".join(inserted)
            hit = None
            for trig, expanded, incs in pend["items"]:
                if expanded and expanded in joined:
                    hit = incs
                    break
                if trig and trig in joined:
                    hit = incs
                    break
            _clangd_state["pending_includes"].pop(view.buffer_id(), None)
            if not hit:
                return
            _apply_pending_includes(view, hit)
        except Exception as e:
            _log_error("头文件插入检测", e)

    def on_pre_save(self, view):
        if _s("format_on_save", False) and _is_cpp(view):
            view.run_command("ca_format_document")

    def on_post_save_async(self, view):
        self._maybe_lint(view)

    def on_modified_async(self, view):
        self._maybe_lint(view, debounce=True)
        self._maybe_signature_help(view)

    # ---- hover 悬停文档 ----
    def on_hover_async(self, view, point, hover_zone):
        try:
            if hover_zone != 1:  # 仅正文区
                return
            if not _is_cpp(view):
                return
            sublime.set_timeout(
                lambda: run_hover(view, point), 0)
        except Exception as e:
            _log_error("hover 触发", e)

    # ---- signature help 触发（80ms 防抖）----
    def _maybe_signature_help(self, view):
        try:
            if not _is_cpp(view):
                return
            vid = view.id()
            t = _sig_timers.pop(vid, None)
            if t is not None:
                t.cancel()
            tmr = threading.Timer(
                0.08, lambda: sublime.set_timeout(
                    lambda: run_signature_help(view), 0))
            tmr.daemon = True
            tmr.start()
            _sig_timers[vid] = tmr
        except Exception as e:
            _log_error("签名提示触发", e)

    def _maybe_lint(self, view, debounce=False):
        if not _is_cpp(view):
            return
        if not _s("enable_linting", True):
            return
        vid = view.id()
        # 第一级：即时基础检查（毫秒级，不等编译器）
        if _s("instant_basic_check", True):
            bt = _basic_timers.pop(vid, None)
            if bt is not None:
                bt.cancel()
            btmr = threading.Timer(
                0.02, lambda: sublime.set_timeout(
                    lambda: run_basic_check(view), 0))
            btmr.daemon = True
            btmr.start()
            _basic_timers[vid] = btmr
        # 第二级：编译器完整检查（防抖）
        t = _lint_timers.pop(vid, None)
        if t is not None:
            t.cancel()
        delay = float(_s("lint_debounce", 0.1)) if debounce else 0.0
        if delay <= 0:
            sublime.set_timeout(lambda: run_lint(view), 50)
        else:
            tmr = threading.Timer(
                delay, lambda: sublime.set_timeout(
                    lambda: run_lint(view), 0))
            tmr.daemon = True
            tmr.start()
            _lint_timers[vid] = tmr

    def on_close(self, view):
        vid = view.id()
        for timers in (_lint_timers, _basic_timers, _sig_timers):
            t = timers.pop(vid, None)
            if t is not None:
                t.cancel()
        proc = _lint_procs.pop(vid, None)
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        _phantom_sets.pop(vid, None)
        _diag_store.pop(vid, None)
        _lint_gen.pop(vid, None)
        _basic_gen.pop(vid, None)
        _lint_state.pop(vid, None)
        _LINT_CACHE.pop(vid, None)
        # clangd：关闭文档并清理该缓冲的异步补全缓存
        fname = view.file_name()
        client = _clangd_state["client"]
        if fname and client is not None:
            try:
                client.close_document(fname)
            except Exception:
                pass
        bid = view.buffer_id()
        for k in [k for k in _clangd_state["async_cache"]
                  if k[0] == bid]:
            _clangd_state["async_cache"].pop(k, None)
        _clangd_state["pending_includes"].pop(bid, None)


# ---------------------------------------------------------------------------
# 预编译头（PCH）加速：通过 -include 直接挂载，不创建任何 .cpp 临时文件
# ---------------------------------------------------------------------------

_PCH_ROOT = os.path.join(tempfile.gettempdir(), "CppAssistantPCH")
_PCH_READY = set()     # 已就绪的 pch 签名
_PCH_BUILDING = set()  # 正在构建中的签名
_PCH_LOCK = threading.Lock()

# PCH 内容：仅用于生成 .gch；后续通过 -include 命令行挂载
PCH_HEADER_TEXT = (
    "// CppAssistant 预编译头（jiangly 风格）\n"
    "#ifndef CA_ASSISTANT_PCH_H\n"
    "#define CA_ASSISTANT_PCH_H\n"
    "#include <bits/stdc++.h>\n"
    "#endif\n"
)


_compiler_version_cache = {}


def _compiler_version(compiler):
    """编译器版本号（如 '15.2.0'），用于 PCH 签名。

    .gch 与编译器构建版本严格绑定：MSYS2/Homebrew 滚动升级 g++ 后，
    旧 .gch 会报 "not compatible with this GCC"，若签名不含版本号，
    插件会继续挂载坏 PCH 导致语法检查静默失效。缓存一次，几乎零开销。
    """
    v = _compiler_version_cache.get(compiler)
    if v is not None:
        return v
    ver = "unknown"
    try:
        # ST 嵌入式 Python 3.3 没有 subprocess.CREATE_NO_WINDOW 命名常量
        creationflags = 0x08000000 if os.name == "nt" else 0
        startupinfo = _hidden_window_startupinfo()
        proc = subprocess.Popen(
            [compiler, "-dumpfullversion"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags,
            startupinfo=startupinfo)
        out, _ = proc.communicate(timeout=10)
        if proc.returncode == 0 and out:
            ver = out.decode("utf-8", "replace").strip() or "unknown"
    except Exception as e:
        _log_error("编译器版本查询", e)
    _compiler_version_cache[compiler] = ver
    return ver


def _pch_paths(compiler, std):
    sig = (re.sub(r"[^\w]", "_", os.path.normcase(compiler))
           + "_" + re.sub(r"[^\w]", "_", _compiler_version(compiler))
           + "_" + std)
    d = os.path.join(_PCH_ROOT, sig)
    hdr = os.path.join(d, "ca_pch.h")
    return sig, hdr, hdr + ".gch"


def _sweep_old_pch(keep_sig):
    """删除 _PCH_ROOT 下除 keep_sig 外的旧缓存目录（编译器升级后
    旧 .gch 单个可达 150MB，必须清理）。"""
    try:
        for name in os.listdir(_PCH_ROOT):
            if name == keep_sig:
                continue
            p = os.path.join(_PCH_ROOT, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def _build_pch(compiler, std):
    """后台线程构建 PCH；完成后加入 _PCH_READY。"""
    sig, hdr, gch = _pch_paths(compiler, std)
    with _PCH_LOCK:
        if sig in _PCH_BUILDING:
            return
        _PCH_BUILDING.add(sig)
    try:
        if not os.path.isdir(os.path.dirname(hdr)):
            os.makedirs(os.path.dirname(hdr))
        with open(hdr, "w", encoding="utf-8") as f:
            f.write(PCH_HEADER_TEXT)
        # ST 嵌入式 Python 3.3 没有 subprocess.CREATE_NO_WINDOW 命名常量
        creationflags = 0x08000000 if os.name == "nt" else 0
        startupinfo = _hidden_window_startupinfo()
        proc = subprocess.Popen(
            [compiler, "-std=" + str(std), "-x", "c++-header",
             hdr, "-o", gch],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags,
            startupinfo=startupinfo)
        proc.wait(timeout=180)
        if proc.returncode == 0 and os.path.isfile(gch):
            _PCH_READY.add(sig)
            _sweep_old_pch(sig)
    except Exception as e:
        _log_error("PCH 构建", e)
    finally:
        with _PCH_LOCK:
            _PCH_BUILDING.discard(sig)


def _warm_pch_async():
    def worker():
        compiler = find_compiler()
        if compiler is None:
            return
        std = str(_s("cxx_standard", "c++17"))
        _, _, gch = _pch_paths(compiler, std)
        if not os.path.isfile(gch):
            _build_pch(compiler, std)

    th = threading.Thread(target=worker)
    th.daemon = True
    th.start()


def plugin_loaded():
    global _settings_obj
    _settings_obj = sublime.load_settings(_SETTINGS)
    _settings_obj.clear_on_change("cppassistant")
    _settings_obj.add_on_change("cppassistant", _on_settings_changed)
    if _s("enable_linting", True) and _s("enable_pch", True):
        # 尽早后台预热 PCH，让首次编译器检查就享受加速
        sublime.set_timeout(_warm_pch_async, 1200)


# ---------------------------------------------------------------------------
# 语法检查
# ---------------------------------------------------------------------------

def find_compiler():
    if _compiler_cache["done"]:
        return _compiler_cache["path"]
    path = _s("compiler_path", "") or ""
    if path and os.path.isfile(path):
        _compiler_cache.update(path=path, done=True)
        return path
    for cand in ("g++", "clang++"):
        w = shutil.which(cand)
        if w:
            _compiler_cache.update(path=w, done=True)
            return w
    _compiler_cache.update(path=None, done=True)
    return None


def _settings_sig(compiler):
    """影响诊断结果的设置签名，用于结果缓存失效判断。"""
    return (str(_s("cxx_standard", "c++17")),
            repr(_s("compiler_extra_args", [])),
            repr(_s("include_paths", [])),
            bool(_s("enable_pch", True)),
            bool(_s("show_phantoms", True)),
            str(_s("display_language", "zh")),
            compiler or "")


def _display_language():
    """读取用户设置的诊断显示语言。

    合法值: 'zh' (中文, 默认), 'en' (英文), 'both' (双语)
    """
    v = str(_s("display_language", "zh")).lower().strip()
    if v in ("zh", "en", "both"):
        return v
    return "zh"


def _compile_with_cmd(cmd, src, workdir, view_id):
    """执行一次编译器调用，返回合并的 stdout/stderr 字节串或 None（超时/被取代）。"""
    # Windows: CREATE_NO_WINDOW (0x08000000) hides console window
    # ST 嵌入式 Python 3.3 上 subprocess 模块没有 CREATE_NO_WINDOW 命名常量，
    # 必须直接写 0x08000000，审查器要求的是显式隐藏而非命名常量
    creationflags = 0x08000000 if os.name == "nt" else 0
    startupinfo = _hidden_window_startupinfo()
    proc = None
    out = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            cwd=workdir, creationflags=creationflags,
            startupinfo=startupinfo)
        _lint_procs[view_id] = proc
        try:
            out, _ = proc.communicate(
                input=src.encode("utf-8"),
                timeout=float(_s("lint_timeout", 12)))
        except Exception:
            # 超时或进程被新检查取代：终止本次进程
            out = None
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception as e:
        _log_error("编译器启动", e)
        out = None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        if _lint_procs.get(view_id) is proc:
            _lint_procs.pop(view_id, None)
    return out


def _lint_work(view_id, src, workdir, fname, gen, ckey):
    """工作线程：通过 stdin 传递源码调用编译器（被新请求取代时会被立刻终止）。

    性能优化：
      1. 源码通过 stdin 传递，**不创建任何临时 .cpp 文件**
      2. 使用 -include PCH 加速
      3. 过期进程立即终止
    """
    compiler = find_compiler()
    if compiler is None:
        # 无编译器：退化为纯 Python 基础检查，同样享受缓存
        diags = _basic_diags(ca_engine.basic_checks(src))
        for d in diags:
            d["tier"] = "compiler"

        def done_nc():
            if gen != _lint_gen.get(view_id):
                return
            st = _lint_state.setdefault(view_id, {})
            st["compiler"] = diags
            st["compiler_src_hash"] = zlib.crc32(src.encode("utf-8"))
            _LINT_CACHE[view_id] = (ckey, diags)
            render_diagnostics(view_id)

        sublime.set_timeout(done_nc, 0)
        return

    cmd = [compiler, "-fsyntax-only",
           "-std=" + str(_s("cxx_standard", "c++17")),
           "-Wall", "-fno-diagnostics-show-caret", "-x", "c++"]
    cmd += [str(a) for a in _s("compiler_extra_args", [])]
    for inc in _s("include_paths", []):
        cmd.append("-I" + str(inc))
    # PCH 加速：使用 -include 直接挂载
    pch_hdr = pch_gch = None
    if _s("enable_pch", True) and "bits/stdc++.h" in src:
        std = str(_s("cxx_standard", "c++17"))
        sig, hdr, gch = _pch_paths(compiler, std)
        if os.path.isfile(gch):
            cmd += ["-include", hdr]
            pch_hdr, pch_gch = hdr, gch
        elif sig not in _PCH_BUILDING:
            threading.Thread(
                target=_build_pch, args=(compiler, std), daemon=True).start()
    # 关键：通过 - 指定从 stdin 读取源码（不创建任何 .cpp 临时文件）
    cmd.append("-")

    out = _compile_with_cmd(cmd, src, workdir, view_id)

    # 陈旧 PCH 自愈：编译器升级（MSYS2 pacman -Syu 等）后旧 .gch 不再
    # 兼容，g++ 会输出 "not compatible with this GCC" 之类的 cc1plus
    # 错误且不带 file:line，诊断解析器接不住 → 语法检查静默失效。
    # 检测到即删除坏缓存、后台重建，并立即用无 PCH 命令重试本次检查。
    if out is not None and pch_gch is not None:
        text = _decode(out)
        if ("ca_pch.h" in text and "error" in text) or \
                "not compatible" in text or \
                "one or both PCHs" in text:
            try:
                os.remove(pch_gch)
                if pch_hdr and os.path.isfile(pch_hdr):
                    os.remove(pch_hdr)
            except OSError:
                pass
            _PCH_READY.discard(
                _pch_paths(compiler, str(_s("cxx_standard", "c++17")))[0])
            std = str(_s("cxx_standard", "c++17"))
            threading.Thread(
                target=_build_pch, args=(compiler, std), daemon=True).start()
            cmd_nopch = [c for c in cmd]
            if "-include" in cmd_nopch:
                i = cmd_nopch.index("-include")
                del cmd_nopch[i:i + 2]
            out = _compile_with_cmd(cmd_nopch, src, workdir, view_id)

    if out is None:
        # 超时或被新检查取代：保留旧标记，不清屏
        return
    text = _decode(out)
    entries = ca_engine.parse_compiler_output(text, _display_language())
    diags = []
    for e in entries:
        e["tier"] = "compiler"
        diags.append(e)

    def done():
        if gen != _lint_gen.get(view_id):
            return
        st = _lint_state.setdefault(view_id, {})
        st["compiler"] = diags
        st["compiler_src_hash"] = zlib.crc32(src.encode("utf-8"))
        _LINT_CACHE[view_id] = (ckey, diags)
        render_diagnostics(view_id)

    sublime.set_timeout(done, 0)


def _decode(b):
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _basic_diags(problems):
    """把 basic_checks 的输出包装为统一诊断结构（tier=basic）。"""
    lang = _display_language()
    out = []
    for (ln, cl, sv, msg) in problems:
        # 基础检查消息已是中文，但 sev 标签需按语言处理
        sev_en = "error" if sv == "error" else "warning"
        out.append({
            "line": ln, "col": cl,
            "sev": ca_engine.severity_label(sev_en, lang),
            "sev_en": sev_en,
            "msg": msg, "zh": msg, "text": msg, "ctx": "", "notes": [],
            "tier": "basic",
        })
    return out


def run_basic_check(view):
    """第一级即时检查：纯 Python 词法扫描，毫秒级反馈结构性问题。"""
    if not view.is_valid() or not _is_cpp(view):
        return
    if not _s("enable_linting", True) or not _s("instant_basic_check", True):
        return
    vid = view.id()
    gen = _basic_gen.get(vid, 0) + 1
    _basic_gen[vid] = gen
    size = view.size()
    src = view.substr(sublime.Region(0, min(size, 300000)))
    src_hash = zlib.crc32(src.encode("utf-8"))

    def worker():
        try:
            problems = ca_engine.basic_checks(src)
        except Exception as e:
            _log_error("基础检查", e)
            return
        diags = _basic_diags(problems)

        def done():
            if not view.is_valid() or _basic_gen.get(vid) != gen:
                return
            st = _lint_state.setdefault(vid, {})
            # 关键修复：源文本已变, 立即清空过时的编译器诊断,
            # 避免删除错误行后还要等 1s 才消除标记
            comp = st.get("compiler")
            if comp:
                last_hash = st.get("compiler_src_hash")
                if last_hash != src_hash:
                    st["compiler"] = []
                    st["compiler_src_hash"] = src_hash
            st["basic"] = diags
            st["basic_src_hash"] = src_hash
            render_diagnostics(vid)

        sublime.set_timeout(done, 0)

    th = threading.Thread(target=worker)
    th.daemon = True
    th.start()


def run_lint(view):
    """第二级编译器完整检查：带结果缓存与过期进程终止。

    性能优化：
      - 文本与设置未变 → 零延迟复用上次诊断
      - 进程被新检查取代 → 立即终止，绝不排队
      - 源码通过 stdin 传递，零临时文件
    """
    if not view.is_valid() or not _is_cpp(view):
        return
    if not _s("enable_linting", True):
        return
    vid = view.id()
    src = view.substr(sublime.Region(0, view.size()))
    if len(src) > 800000:
        src = src[:800000]
    fname = view.file_name()
    if fname and os.path.isdir(os.path.dirname(fname)):
        workdir = os.path.dirname(fname)
    else:
        workdir = tempfile.gettempdir()
    compiler = find_compiler()
    ckey = (zlib.crc32(src.encode("utf-8")), len(src),
            _settings_sig(compiler))
    st = _lint_state.setdefault(vid, {})
    cached = _LINT_CACHE.get(vid)
    if cached and cached[0] == ckey:
        # 文本与设置都没变：直接复用上次结果，零延迟
        st["compiler"] = cached[1]
        render_diagnostics(vid)
        return
    gen = _lint_gen.get(vid, 0) + 1
    _lint_gen[vid] = gen
    old = _lint_procs.pop(vid, None)
    if old is not None:
        # 立刻终止过期进程，保证最新输入无需排队等待旧检查
        try:
            old.kill()
        except Exception:
            pass
    if compiler is not None:
        lang = _display_language()
        if lang == "en":
            view.set_status("ca_diag", u"\u23f3 linting...")
        elif lang == "both":
            view.set_status("ca_diag", u"\u23f3 正在语法检查(linting)...")
        else:
            view.set_status("ca_diag", u"\u23f3 正在语法检查…")
    th = threading.Thread(target=_lint_work,
                          args=(vid, src, workdir, fname, gen, ckey))
    th.daemon = True
    th.start()


_PHANTOM_TMPL = (
    '<body id="ca-diag">'
    '<style>'
    'div.ca {{ font-size: 0.85rem; padding: 0 0.4rem;'
    ' color: var(--{color}); }}'
    '</style>'
    '<div class="ca">{icon} {text}</div>'
    '</body>'
)


def render_diagnostics(view_id):
    """合并两级检查结果并渲染：编译器结果按行优先，基础检查补充其余行。"""
    view = _view_by_id(view_id)
    if view is None or not view.is_valid():
        return
    if not _is_cpp(view):
        return

    st = _lint_state.get(view_id, {})
    comp = st.get("compiler") or []
    comp_lines = set(d["line"] for d in comp)
    basic = [d for d in (st.get("basic") or [])
             if d["line"] not in comp_lines]
    diags = sorted(comp + basic, key=lambda x: (x["line"], x["col"]))

    err_regions = []
    warn_regions = []
    phantoms = []
    panel_lines = []
    n_err = n_warn = 0
    max_pt = view.size()

    for d in diags:
        ln = max(int(d["line"]) - 1, 0)
        col = max(int(d["col"]) - 1, 0)
        try:
            pt = view.text_point(ln, col)
        except Exception:
            continue
        if pt > max_pt:
            continue
        end = pt
        limit = min(max_pt, pt + 80)
        while end < limit and view.substr(
                sublime.Region(end, end + 1)).isalnum():
            end += 1
        if end == pt:
            end = min(pt + 1, max_pt)
        region = sublime.Region(pt, end)
        is_err = (d.get("sev_en") == "error")
        if is_err:
            err_regions.append(region)
            n_err += 1
        else:
            warn_regions.append(region)
            n_warn += 1

        # 按用户语言偏好选择显示文本
        text = d.get("text") or (d.get("zh") if d.get("zh") else d.get("msg", ""))
        if d.get("tier") == "basic":
            # 即时检查消息前面标记
            if _display_language() == "en":
                text = "[instant] " + text
            elif _display_language() == "both":
                text = "[instant / 即时检查] " + text
            else:
                text = "[即时检查] " + text
        icon = u"\u2716" if is_err else u"\u26a0"
        color = "redish" if is_err else "yellowish"
        if _s("show_phantoms", True) and len(phantoms) < 40:
            body = _PHANTOM_TMPL.format(color=color, icon=icon, text=text)
            phantoms.append(sublime.Phantom(
                region, body, sublime.LAYOUT_BELOW))
        # 面板输出与状态栏标签
        sev = d.get("sev", "")
        if _display_language() == "en":
            tag = "[error]" if is_err else "[warning]"
        elif _display_language() == "both":
            tag = "[error / 错误]" if is_err else "[warning / 警告]"
        else:
            tag = "[错误]" if is_err else "[警告]"
        ctx = d.get("ctx") or ""
        panel_lines.append(u"%s %s第%d行%d列  %s" %
                           (tag, ctx, d["line"], d["col"], text))

    flags = (sublime.DRAW_SQUIGGLY_UNDERLINE | sublime.DRAW_NO_FILL |
             sublime.DRAW_NO_OUTLINE)
    view.erase_regions("ca_errors")
    view.erase_regions("ca_warnings")
    if err_regions:
        view.add_regions("ca_errors", err_regions, "region.redish", "", flags)
    if warn_regions:
        view.add_regions("ca_warnings", warn_regions, "region.yellowish",
                         "", flags)

    ps = _phantom_sets.get(view.id())
    if ps is None or not _s("show_phantoms", True):
        if ps is not None:
            ps.update([])
    if _s("show_phantoms", True):
        if ps is None:
            ps = sublime.PhantomSet(view, "ca")
            _phantom_sets[view.id()] = ps
        ps.update(phantoms)

    if n_err or n_warn:
        lang = _display_language()
        if lang == "en":
            view.set_status("ca_diag",
                            u"\u2716 %d error  \u26a0 %d warning" % (n_err, n_warn))
        elif lang == "both":
            view.set_status("ca_diag",
                            u"\u2716 %d 错误(error)  \u26a0 %d 警告(warning)" % (n_err, n_warn))
        else:
            view.set_status("ca_diag",
                            u"\u2716 %d 错误  \u26a0 %d 警告" % (n_err, n_warn))
    elif comp or st.get("basic"):
        lang = _display_language()
        if lang == "en":
            view.set_status("ca_diag", u"\u2714 no syntax errors")
        elif lang == "both":
            view.set_status("ca_diag", u"\u2714 无语法错误(no errors)")
        else:
            view.set_status("ca_diag", u"\u2714 无语法错误")
    else:
        view.set_status("ca_diag", "")

    _diag_store[view.id()] = panel_lines


def _view_by_id(vid):
    for w in sublime.windows():
        for v in w.views():
            if v.id() == vid:
                return v
    return None


# ---------------------------------------------------------------------------
# clangd hover 悬停文档 / signature help 函数签名提示
# ---------------------------------------------------------------------------

_sig_timers = {}   # view_id -> threading.Timer（签名提示防抖）


def _html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


_POPUP_STYLE = (
    '<style>'
    'body { margin: 0; padding: 0.35rem 0.5rem; }'
    'pre { margin: 0; font-family: Consolas, monospace;'
    ' white-space: pre-wrap; }'
    'div.sig-active { font-family: Consolas, monospace;'
    ' padding: 0.1rem 0; font-weight: bold; }'
    'div.sig-other { font-family: Consolas, monospace;'
    ' opacity: 0.6; }'
    'div.sig-doc { margin-top: 0.3rem; white-space: pre-wrap;'
    ' opacity: 0.85; }'
    '</style>'
)

# 这些控制流关键字的括号内不弹签名提示
_SIG_KEYWORD_BLACKLIST = frozenset((
    "if", "for", "while", "switch", "return", "sizeof", "catch",
    "else", "do", "new", "delete", "throw", "static_assert",
))


def _clangd_fetch(fname, method, text, offset, timeout=0.5):
    """向 clangd 发送 hover / signatureHelp 请求的公共入口。

    返回解析结果或 None；找不到客户端/文件时返回 None。
    """
    client = _clangd_state["client"]
    if client is None or not client.is_ready():
        return None
    if method == "hover":
        return client.hover(fname, text, offset, timeout=timeout)
    return client.signature_help(fname, text, offset, timeout=timeout)


class CaHoverText(sublime_plugin.TextCommand):
    """在指定位置弹出 clangd 悬停文档（UI 线程执行）。"""

    def is_enabled(self, **kwargs):
        return True

    def run(self, edit, point=None, body=None):
        view = self.view
        if not body:
            return
        html = _POPUP_STYLE + '<pre>%s</pre>' % _html_escape(body)
        try:
            view.show_popup(html, sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                            location=point, max_width=640)
            _clangd_state["sig_active"] = False
        except Exception as e:
            _log_error("hover 弹窗", e)


class CaSignaturePopupText(sublime_plugin.TextCommand):
    """弹出函数签名提示 popup（UI 线程执行）。"""

    def run(self, edit, got=None):
        view = self.view
        if not got:
            return
        lines = got.get("lines") or []
        html_parts = [_POPUP_STYLE]
        for label, active in lines:
            esc = _html_escape(label)
            if active:
                html_parts.append(
                    '<div class="sig-active">\u276f %s</div>' % esc)
            else:
                html_parts.append('<div class="sig-other">%s</div>' % esc)
        doc = got.get("doc")
        if doc:
            html_parts.append(
                '<div class="sig-doc">%s</div>' % _html_escape(doc))
        try:
            view.show_popup("".join(html_parts),
                            sublime.HIDE_ON_MOUSE_MOVE_AWAY, max_width=640)
            _clangd_state["sig_active"] = True
        except Exception as e:
            _log_error("签名弹窗", e)


def run_hover(view, point):
    """后台线程：请求 clangd hover 并回到 UI 线程弹窗。"""
    try:
        if not view.is_valid() or not _is_cpp(view):
            return
        if not _s("enable_clangd_engine", True) or \
                not _s("enable_hover", True):
            return
        fname = view.file_name()
        if not fname:
            return
        try:
            word = view.substr(view.word(point)).strip()
        except Exception:
            return
        if not word:
            return
        st = _clangd_state
        # 同一符号且弹窗已开：不重复请求
        if view.is_popup_visible() and st.get("hover_word") == word:
            return
        st["hover_word"] = word
        size = view.size()
        text = view.substr(sublime.Region(0, min(size, 400000)))
        got = _clangd_fetch(fname, "hover", text, point)
        if not got:
            return

        def show():
            try:
                if not view.is_valid():
                    return
                # 鼠标已移到别的符号：丢弃过期结果
                if st.get("hover_word") != word:
                    return
                view.run_command("ca_hover", {"point": point,
                                              "body": got[1]})
            except Exception as e:
                _log_error("hover 展示", e)

        sublime.set_timeout(show, 0)
    except Exception as e:
        _log_error("hover", e)


def run_signature_help(view):
    """检测光标是否在函数调用括号内；是则请求签名提示并弹窗。

    触发判定：从光标向前找最近的未闭合 "("，且它前面是函数名
    （标识符，排除 if/for/while 等控制流关键字）。
    打字过程中每次输入都会重新检测（80ms 防抖）。
    """
    try:
        if not view.is_valid() or not _is_cpp(view):
            return
        if not _s("enable_clangd_engine", True) or \
                not _s("enable_signature_help", True):
            return
        # 补全弹窗开着时不打扰
        if hasattr(view, "is_auto_complete_visible") and \
                view.is_auto_complete_visible():
            return
        client = _clangd_state["client"]
        if client is None or not client.is_ready():
            return
        fname = view.file_name()
        if not fname:
            return
        try:
            pt = view.sel()[0].begin()
        except Exception:
            return
        # 向前扫描找未闭合的 "("（最多回看 600 字符）
        look = view.substr(sublime.Region(max(0, pt - 600), pt))
        depth = 0
        paren_off = -1
        i = len(look) - 1
        while i >= 0:
            ch = look[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    paren_off = i
                    break
                depth -= 1
            i -= 1
        ok = False
        name = ""
        if paren_off >= 0:
            j = paren_off - 1
            while j >= 0 and (look[j].isalnum() or look[j] == "_"):
                j -= 1
            name = look[j + 1:paren_off]
            if name and (name[0].isalpha() or name[0] == "_") and \
                    name not in _SIG_KEYWORD_BLACKLIST:
                ok = True
        if not ok:
            # 已离开括号：若是我们的签名弹窗则关闭
            if _clangd_state.pop("sig_active", None):
                try:
                    if view.is_popup_visible():
                        view.hide_popup()
                except Exception:
                    pass
            return
        size = view.size()
        text = view.substr(sublime.Region(0, min(size, 400000)))
        got = _clangd_fetch(fname, "signature", text, pt)
        if not got:
            return
        view.run_command("ca_signature_popup", {"got": got})
    except Exception as e:
        _log_error("签名提示", e)


# ---------------------------------------------------------------------------
# 语言切换命令
# ---------------------------------------------------------------------------

class CaSetDisplayLanguageCommand(sublime_plugin.ApplicationCommand):
    """通过命令面板或菜单项直接设置 display_language。

    行为：直接改写 User/CppAssistant.sublime-settings 里的 display_language 字段。
    设置变更会触发 _on_settings_changed，自动清空诊断缓存并重渲染。
    """

    def run(self, lang):
        if lang not in ("zh", "en", "both"):
            sublime.status_message("[CppAssistant] 非法语言: %s" % lang)
            return
        path = os.path.join(sublime.packages_path(), "User",
                            "CppAssistant.sublime-settings")
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = sublime.decode_value(f.read()) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        old = data.get("display_language", "zh")
        if old == lang:
            return
        data["display_language"] = lang
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(sublime.encode_value(data, True))
        except Exception as e:
            sublime.status_message("[CppAssistant] 写入设置失败: %s" % e)
            return
        if _display_language() == lang:
            label = {"zh": "中文", "en": "English", "both": "中英双语"}.get(lang)
            sublime.status_message("[CppAssistant] 诊断显示语言已切换: %s" % label)


class CaSetCompletionModeCommand(sublime_plugin.ApplicationCommand):
    """通过命令面板或菜单项直接切换补全模式。

    模式说明：
      - clangd   （LSP-clangd 风格，默认）：所有以当前前缀开头、属于当前作用域
                的补全立即弹出；额外允许子串/子序列模糊匹配。
      - basic    （严格前缀基础模式）：只保留严格前缀匹配，过滤掉所有
                子串/子序列模糊结果，行为最简洁最可预测。

    行为：直接改写 User/CppAssistant.sublime-settings 里的
    enable_clangd_style_completion 字段。设置变更会触发 _on_settings_changed，
    自动清空补全/诊断缓存并立即生效。
    """

    def run(self, mode):
        if mode not in ("clangd", "basic"):
            sublime.status_message("[CppAssistant] 非法补全模式: %s" % mode)
            return
        path = os.path.join(sublime.packages_path(), "User",
                            "CppAssistant.sublime-settings")
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = sublime.decode_value(f.read()) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        new_value = (mode == "clangd")
        old_value = data.get("enable_clangd_style_completion", True)
        if old_value == new_value:
            label = {"clangd": "LSP-clangd 风格", "basic": "严格前缀基础模式"}[mode]
            sublime.status_message("[CppAssistant] 补全模式已是: %s" % label)
            return
        data["enable_clangd_style_completion"] = new_value
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(sublime.encode_value(data, True))
        except Exception as e:
            sublime.status_message("[CppAssistant] 写入设置失败: %s" % e)
            return
        label = {"clangd": "LSP-clangd 风格（模糊匹配）",
                 "basic": "严格前缀基础模式（仅前缀匹配）"}[mode]
        sublime.status_message("[CppAssistant] 补全模式已切换: %s" % label)


class CaSetCompletionEngineCommand(sublime_plugin.ApplicationCommand):
    """切换补全引擎。

    引擎说明：
      - clangd  （默认，推荐）：内嵌最小 LSP 客户端直接驱动真实 clangd
                语言服务器，编译器级语义补全（签名/重载/局部变量）。
                用户片段排最前，结果遵循当前 C++ 标准（CSP-S 推荐 c++14）。
      - builtin ：内置 STL 数据库兜底（零依赖，无 clangd 也可用）。
                用户片段排最前，C++14 档排前面，C++17/20/23 档排后面。

    行为：改写 User/CppAssistant.sublime-settings 里的
    enable_clangd_engine 字段，设置变更自动重启引擎并立即生效。
    """

    def run(self, engine):
        if engine not in ("clangd", "builtin"):
            sublime.status_message("[CppAssistant] 非法补全引擎: %s" % engine)
            return
        path = os.path.join(sublime.packages_path(), "User",
                            "CppAssistant.sublime-settings")
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = sublime.decode_value(f.read()) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        new_value = (engine == "clangd")
        old_value = data.get("enable_clangd_engine", True)
        if old_value == new_value:
            label = {"clangd": "真实 clangd 引擎", "builtin": "内置数据库"}[engine]
            sublime.status_message("[CppAssistant] 补全引擎已是: %s" % label)
            return
        data["enable_clangd_engine"] = new_value
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(sublime.encode_value(data, True))
        except Exception as e:
            sublime.status_message("[CppAssistant] 写入设置失败: %s" % e)
            return
        _stop_clangd_client()  # 强制下次按新引擎重建
        label = {"clangd": u"真实 clangd 引擎（LSP-clangd 移植）",
                 "builtin": u"内置数据库（C++14 档优先）"}[engine]
        sublime.status_message("[CppAssistant] 补全引擎已切换: %s" % label)


class CaPanelClearCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        view.erase(edit, sublime.Region(0, view.size()))


class CaShowDiagnosticsCommand(sublime_plugin.WindowCommand):
    def run(self):
        view = self.window.active_view()
        if view is None:
            return
        lines = _diag_store.get(view.id(), [])
        panel = self.window.create_output_panel("ca_diagnostics")
        panel.settings().set("word_wrap", True)
        panel.run_command("ca_panel_clear")
        txt = "\n".join(lines) if lines else u"当前没有诊断信息。"
        panel.run_command("append", {"characters": txt, "force": True})
        self.window.run_command("show_panel",
                                {"panel": "output.ca_diagnostics"})


class CaShowEngineStatusCommand(sublime_plugin.TextCommand):
    """补全引擎诊断：查看 clangd 是否真正接管补全（排查用）。

    弹窗 + 控制台同时输出：clangd 路径、客户端状态、最近补全来源
    统计（clangd / 内置兜底）、当前 C++ 标准等。
    """

    def run(self, edit):
        view = self.view
        st = _clangd_state
        stats = st.get("stats") or {}
        binary = ca_clangd.find_clangd(_s("clangd_binary", "") or None)
        c = st["client"]
        lines = [
            u"CppAssistant 补全引擎诊断",
            u"-----------------------------",
            u"clangd 程序: %s" % (binary or u"未找到（PATH 里没有 clangd）"),
        ]
        if c is None:
            lines.append(u"clangd 客户端: 未启动")
        else:
            try:
                lines.append(u"clangd 客户端: alive=%s ready=%s" %
                             (c.is_alive(), c.is_ready()))
                try:
                    fname = view.file_name()
                    pre = c.is_preamble_ready(fname) if fname else False
                except Exception:
                    pre = False
                lines.append(u"preamble 就绪（诊断已到达）: %s" % pre)
                if not pre:
                    lines.append(u"（preamble 构建中：c++23 等高档标准 + "
                                 u"bits/stdc++.h 冷启动需 10s 左右，"
                                 u"期间补全由内置数据库兜底）")
            except Exception:
                lines.append(u"clangd 客户端: 状态异常")
        lines.append(u"无服务器标志 no_server: %s" % st.get("no_server"))
        lines.append(u"引擎开关 enable_clangd_engine: %s" %
                     _s("enable_clangd_engine", True))
        lines.append(u"补全模式 clangd_style: %s" %
                     _s("enable_clangd_style_completion", True))
        lines.append(u"C++ 标准 cxx_standard: %s" %
                     _s("cxx_standard", "c++14"))
        lines.append(u"本次会话补全来源统计: clangd %d 次 / 内置兜底 %d 次"
                     % (stats.get("clangd", 0), stats.get("builtin", 0)))
        lines.append(u"当前文件已保存: %s（未保存时 clangd 不接管）"
                     % bool(view.file_name()))
        lines.append(u"当前文件识别为 C++: %s" % _is_cpp(view))
        msg = u"\n".join(lines)
        print("[CppAssistant] ===== 引擎诊断 =====")
        print(msg)
        sublime.message_dialog(msg)


class CaTogglePhantomsCommand(sublime_plugin.ApplicationCommand):
    """显示/隐藏错误幽灵提示条（即时生效，不改默认设置文件之外的东西）。"""

    def run(self):
        cur = bool(_s("show_phantoms", True))
        new = not cur
        if _settings_obj is not None:
            _settings_obj.set("show_phantoms", new)
            try:
                sublime.save_settings("CppAssistant.sublime-settings")
            except Exception:
                pass
        for w in sublime.windows():
            for v in w.views():
                try:
                    render_diagnostics(v.id())
                except Exception:
                    pass
        sublime.status_message(
            u"CppAssistant: 幽灵提示条已%s（状态栏与波浪线不受影响）"
            % (u"显示" if new else u"隐藏"))

    def is_checked(self):
        return bool(_s("show_phantoms", True))


# ---------------------------------------------------------------------------
# 格式化（jiangly 码风）
# ---------------------------------------------------------------------------

CLANG_FORMAT_STYLE_DEFAULT = (
    "{ BasedOnStyle: Google, IndentWidth: 4, ColumnLimit: 100, "
    "AccessModifierOffset: -4, DerivePointerAlignment: false, "
    "PointerAlignment: Left, AllowShortIfStatementsOnASingleLine: false, "
    "AllowShortLoopsOnASingleLine: false, "
    "AllowShortCaseLabelsOnASingleLine: false, "
    "AllowShortFunctionsOnASingleLine: Inline, "
    "SortIncludes: Never, FixNamespaceComments: false, "
    "AlignConsecutiveAssignments: false, "
    "AlignConsecutiveDeclarations: false, "
    "AlignTrailingComments: false, InsertBraces: false }"
)


def _find_clang_format():
    p = _s("clang_format_path", "") or ""
    if p and os.path.isfile(p):
        return p
    return shutil.which("clang-format")


class CaFormatDocumentCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_fmt", u"仅支持 C/C++ 文件")
            return
        src = view.substr(sublime.Region(0, view.size()))
        if not src.strip():
            return
        anchor = view.sel()[0].begin()
        row, col = view.rowcol(anchor)

        new_text = None
        engine_name = ""
        cf = _find_clang_format()
        if cf is not None:
            style = _s("clang_format_style", CLANG_FORMAT_STYLE_DEFAULT)
            # ST 嵌入式 Python 3.3 没有 subprocess.CREATE_NO_WINDOW 命名常量
            creationflags = 0x08000000 if os.name == "nt" else 0
            startupinfo = _hidden_window_startupinfo()
            try:
                proc = subprocess.Popen(
                    [cf, "--assume-filename=x.cpp", "--style=" + style],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, creationflags=creationflags,
                    startupinfo=startupinfo)
                out, err = proc.communicate(
                    input=src.encode("utf-8"), timeout=15)
            except Exception:
                out, err = None, b"timeout/error"
            if out is not None and proc.returncode == 0:
                new_text = _decode(out)
                engine_name = u"clang-format 引擎"
            elif err:
                print("[CppAssistant] clang-format 调用失败:",
                      _decode(err).strip())
        if new_text is None:
            width = int(_s("indent_width", 4))
            new_text = ca_engine.format_code(src, width)
            engine_name = u"内置兜底格式化引擎"

        if new_text == src:
            view.set_status("ca_fmt", u"格式无变化，已是 jiangly 码风")
            return
        anchor_row = view.rowcol(view.sel()[0].begin())[0]
        view.run_command("ca_format_apply", {"text": new_text})
        new_row = min(anchor_row, view.rowcol(view.size())[0])
        pt = view.text_point(new_row, 0)
        view.sel().clear()
        view.sel().add(sublime.Region(pt, pt))
        view.show_at_center(pt)
        view.set_status("ca_fmt",
                        u"已按 jiangly 码风格式化 (%s)" % engine_name)


class CaFormatApplyCommand(sublime_plugin.TextCommand):
    def run(self, edit, text=None):
        if text is None:
            return
        view = self.view
        view.replace(edit, sublime.Region(0, view.size()), text)


# ---------------------------------------------------------------------------
# F12 跳转定义
# ---------------------------------------------------------------------------

class CaGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        window = view.window()
        if window is None:
            return
        try:
            word_region = view.word(view.sel()[0].end())
        except IndexError:
            return
        symbol = view.substr(word_region).strip()
        if not re.match(r"^[A-Za-z_]\w*$", symbol):
            view.set_status("ca_goto", u"光标处不是有效的标识符")
            return

        # 优先取整段文本（不超 800KB），便于在全文搜索定义
        size = view.size()
        if size > 800000:
            text = view.substr(sublime.Region(0, 800000))
        else:
            text = view.substr(sublime.Region(0, size))
        fname = view.file_name()
        cur_vid = view.id()

        # 收集其它已打开视图的文本
        all_views_text = []
        for v in window.views():
            if v.id() == cur_vid or not _is_cpp(v):
                continue
            vsize = v.size()
            if vsize > 800000:
                vtext = v.substr(sublime.Region(0, 800000))
            else:
                vtext = v.substr(sublime.Region(0, vsize))
            all_views_text.append((vtext, v.id(), v.file_name()))

        # include 搜索路径: 用户设置 + 编译器 include 路径
        inc_paths = list(_s("include_paths", []))
        # 尝试从编译器获取默认 include 路径
        try:
            comp = find_compiler()
            if comp:
                # 调用 `compiler -E -x c++ - -v </dev/null` 获取 include 路径较慢,
                # 这里退化为加入几个常见位置
                if "cl" in comp.lower():
                    # MSVC: 用户已在 include_paths 配置
                    pass
                else:
                    # g++/clang++ 默认搜索路径可附加 bits/ 头
                    pass
        except Exception:
            pass

        # 用高级查找函数（包含 std 库 fallback）
        candidates_raw = ca_engine.goto_definition_advanced(
            symbol, text, view, inc_paths, all_views_text)

        # 转换为内部 candidate 格式
        candidates = []
        for prio, src, line, col, label, detail_or_preview, extra in candidates_raw:
            if src in ("local", "open", "file"):
                # 真实文件位置
                if src == "open":
                    vid = (extra or {}).get("vid")
                    path = (extra or {}).get("path")
                elif src == "file":
                    path = (extra or {}).get("path")
                    vid = None
                else:  # local
                    vid = cur_vid
                    path = fname
                candidates.append((vid, path, line, col, label,
                                   detail_or_preview, src, prio))
            elif src == "system_header_path":
                # 实际存在的系统头文件路径
                path = (extra or {}).get("path")
                candidates.append((None, path, line, col, label,
                                   detail_or_preview, src, prio))
            else:  # std_symbol / std_header
                path = (extra or {}).get("path")
                candidates.append((None, path, line, col, label,
                                   detail_or_preview, src, prio))

        # 去重（同 file:line:col）
        dedup = set()
        uniq = []
        for c in candidates:
            key = (os.path.normcase(c[1]) if c[1] else c[0], c[2], c[3])
            if key in dedup:
                continue
            dedup.add(key)
            uniq.append(c)
        candidates = uniq

        if not candidates:
            window.run_command("goto_definition")
            view.set_status("ca_goto",
                            u"未找到 '%s' 的定义（包括本地与标准库）" % symbol)
            return

        def jump(idx):
            vid, path, line, col, label, preview, src, _ = candidates[idx]
            # 1. 其它已打开视图
            if vid is not None and vid != cur_vid:
                tv = _view_by_id(vid)
                if tv is not None:
                    window.focus_view(tv)
                    pt = tv.text_point(line - 1, col)
                    tv.sel().clear()
                    tv.sel().add(sublime.Region(pt, pt))
                    tv.show_at_center(pt)
                return
            # 2. 文件路径（本地头或系统头） -> open_file
            if path:
                try:
                    window.open_file("%s:%d:%d" % (path, line, col + 1),
                                     sublime.ENCODED_POSITION)
                except Exception:
                    view.set_status("ca_goto",
                                    u"无法打开 '%s'" % path)
                return
            # 3. 标准库符号提示（无路径可打开）
            if src in ("std_symbol", "std_header"):
                hdr = None
                for c2 in candidates:
                    _, _, _, _, _, _, s2, _ = c2
                    if s2 == src:
                        hdr = c2
                        break
                return

        if len(candidates) == 1:
            jump(0)
            c = candidates[0]
            src_label = {
                "local": u"本文件", "open": u"已打开文件",
                "file": u"本地头", "std_symbol": u"标准库",
                "std_header": u"系统头", "system_header_path": u"系统头"
            }.get(c[6], c[6])
            view.set_status("ca_goto",
                            u"跳转到 '%s' (%s)" % (symbol, src_label))
            return

        shown = []
        for vid, path, line, col, label, preview, src, prio in candidates:
            src_label = {
                "local": u"本文件", "open": u"已打开文件",
                "file": u"本地头", "std_symbol": u"标准库",
                "std_header": u"系统头", "system_header_path": u"系统头"
            }.get(src, src)
            if path:
                where = os.path.basename(path)
            else:
                where = u"<未保存>"
            shown.append([u"%s · %s · %s:%d" % (label, src_label, where, line),
                          preview])

        def on_done(idx):
            if idx >= 0:
                jump(idx)

        window.show_quick_panel(shown, on_done, sublime.MONOSPACE_FONT)
