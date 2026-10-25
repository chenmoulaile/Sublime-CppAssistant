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

保持与旧版 ST4 插件宿主（Python 3.3）兼容的语法；当前 ST4 版本使用
更新的 Python（4215 为 3.14），本插件在两者上都能加载。
"""
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zlib

import sublime
import sublime_plugin

# 子包导入：Sublime 始终以 "<包名>.模块" 的形式加载根级插件，相对导入
# 一定可用。这里不再修改模块搜索路径 —— 那会影响其它包（Package Control
# 审查意见），也没有必要。
from .cppassistant import ca_clangd  # noqa: E402
from .cppassistant import ca_engine  # noqa: E402
from .cppassistant import ca_user_snippets  # noqa: E402
from .cppassistant.ca_stdlib_data import SNIPPETS, en_detail  # noqa: E402


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
# 按“用户配置的编译器路径”作缓存键：改设置即刻换编译器，
# 无需监听设置变更事件。
_compiler_cache = {"configured": None, "path": None, "done": False,
                   "checked": 0.0}

CPP_SCOPE = "source.c++, source.c"

# clangd 需要完整文档才能保证 LSP 位置正确。超过这个大小就退回内置
# 数据库，避免每次按键把几 MB 文本全量同步给 clangd。
_CLANGD_MAX_CHARS = 1000000

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

def _settings():
    """设置对象（Sublime 内部缓存同一个对象，可随手读，无需自备监听）。"""
    global _settings_obj
    if _settings_obj is None:
        _settings_obj = sublime.load_settings(_SETTINGS)
    return _settings_obj


def _set_setting(key, value):
    """写一项设置并落盘。

    走 API 只写用户覆盖项；自己 decode/encode 整个 User 文件会把用户
    写在里面的注释和键序一起丢掉。
    """
    settings = _settings()
    if settings.get(key) == value:
        return False
    settings.set(key, value)
    sublime.save_settings(_SETTINGS)
    return True


def _refresh_cpp_views(rerun_lint=False):
    """设置变更后刷新已打开的 C++ 视图（无设置监听，由命令显式触发）。"""
    for w in sublime.windows():
        for v in w.views():
            try:
                if not _is_cpp(v):
                    continue
                if rerun_lint:
                    run_lint(v)
                else:
                    render_diagnostics(v.id())
            except Exception as e:
                _log_error("view refresh", e)


def plugin_unloaded():
    # 清理所有运行中的编译器进程和防抖定时器
    for vid, proc in list(_lint_procs.items()):
        try:
            proc.kill()
        except Exception:
            pass
    _lint_procs.clear()
    for timers in (_lint_timers, _basic_timers, _sig_timers):
        for t in list(timers.values()):
            try:
                t.cancel()
            except Exception:
                pass
        timers.clear()
    _phantom_sets.clear()
    _diag_store.clear()
    # 关闭 clangd 语言服务器
    _stop_clangd_client()


def _s(key, default=None):
    return _settings().get(key, default)


# 控制台日志限频：同一位置最多打印 3 次，避免每敲一键刷屏
_log_counts = {}


def _log_error(where, exc):
    """把被兜底捕获的异常打到 Sublime 控制台（View → Show Console），
    避免插件出错时完全黑箱。同一位置最多记录 3 次。"""
    n = _log_counts.get(where, 0)
    if n < 3:
        _log_counts[where] = n + 1
        import traceback
        print("[CppAssistant] error in %s%s: %r" % (
            where, (" (further errors from here are suppressed)"
                    if n == 2 else ""), exc))
        traceback.print_exc()


def _is_cpp(view):
    return view.score_selector(0, CPP_SCOPE) > 0


# ---------------------------------------------------------------------------
# 补全
# ---------------------------------------------------------------------------

# 补全压制旗标兼容：INHIBIT_SNIPPET_COMPLETIONS 仅较新 ST4 版本提供，
# 旧版（如 4213 stable）没有该常量，直接引用会让 on_query_completions
# 抛 AttributeError 导致补全整体失效（曾经的真实故障）。统一走 getattr。
_INHIBIT_SNIPPET = getattr(sublime, "INHIBIT_SNIPPET_COMPLETIONS", 0)
_INHIBIT_WORD = getattr(sublime, "INHIBIT_WORD_COMPLETIONS", 0)


def _kind_default():
    return sublime.KIND_AMBIGUOUS


# 类型标签按 display_language 取用：en 英文 / zh 中文 / both 双语
_KIND_LABEL = {
    "f": (u"function", u"函数"),
    "m": (u"member function", u"成员函数"),
    "v": (u"member variable", u"成员变量"),
    "c": (u"constant", u"常量"),
    "t": (u"type", u"类型"),
    "k": (u"keyword", u"关键字"),
    "u": (u"custom", u"自定义"),
    "s": (u"snippet", u"代码片段"),
}


def _kind_label(code):
    """按当前显示语言取 kind 标签（弹窗里每次展开时求值）。"""
    pair = _KIND_LABEL.get(code)
    if pair is None:
        return u""
    lang = _display_language()
    if lang == "zh":
        return pair[1]
    if lang == "both":
        return pair[0] + u" / " + pair[1]
    return pair[0]


_KIND_MAP = {
    "f": lambda: (sublime.KIND_ID_FUNCTION, "f", _kind_label("f")),
    "m": lambda: (sublime.KIND_ID_FUNCTION, "m", _kind_label("m")),
    "v": lambda: (sublime.KIND_ID_VARIABLE, "v", _kind_label("v")),
    "c": lambda: (sublime.KIND_ID_CONSTANT, "c", _kind_label("c")),
    "t": lambda: (sublime.KIND_ID_TYPE, "T", _kind_label("t")),
    "k": lambda: (sublime.KIND_ID_KEYWORD, "k", _kind_label("k")),
    "u": lambda: (sublime.KIND_ID_FUNCTION, "u", _kind_label("u")),
    "s": lambda: (sublime.KIND_ID_SNIPPET, "S", _kind_label("s")),
}


def _annotation(d):
    """补全弹窗右侧短注释：数据同时带中英，语言在渲染时选定。

    没有 annotation_en 的条目（clangd、用户片段）本来就是英文文本，原样用。
    """
    if "annotation_en" not in d:
        return d.get("annotation") or u""
    zh = d.get("annotation") or u""
    en = d.get("annotation_en") or u""
    lang = _display_language()
    if lang == "en":
        return en
    if lang == "both":
        if en and zh and en != zh:
            return u"%s / %s" % (en, zh)
        return en or zh
    return zh


def _detail(d):
    """补全详情面板文本，按当前显示语言选择。

    内置片段/用户符号同时带中英详情；clangd 和标准库条目本来就是
    英文，没有 detail_en 时直接原样返回。
    """
    if "detail_en" not in d:
        return d.get("detail") or u""
    en = d.get("detail_en") or u""
    zh = d.get("detail") or u""
    lang = _display_language()
    if lang == "en":
        return en or zh
    if lang == "both":
        if en and zh and en != zh:
            return u"%s / %s" % (en, zh)
        return en or zh
    return zh or en


def _make_item(d):
    kind = _KIND_MAP.get(d.get("kind"), _kind_default)()
    insert = d["insert"]
    is_snippet = (bool(d.get("snippet")) or ("\n" in insert)
                  or ("$" in insert))
    fmt = (sublime.COMPLETION_FORMAT_SNIPPET if is_snippet
           else sublime.COMPLETION_FORMAT_TEXT)
    details = _detail(d)
    ann = _annotation(d)
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
    "no_server_since": 0.0,  # 上次探测不到 clangd 的时间（定期重试）
    "async_cache": {},     # (buffer_id, change_count, offset, std) -> [dict]
    "refreshed": None,     # 最近一次弹窗刷新键（防刷新循环）
    # buffer_id -> {"cc": 编辑代号, "items": [(trigger, 展开后插入文本, [头文件])]}
    # 记录最近一次补全候选附带的 #include 插入指令，供补全被接受后应用
    "pending_includes": {},
    # (file, change_count, point, word) -> hover 文本；避免同一符号
    # 反复走一次 clangd 往返。
    "hover_cache": {},
    # 正在进行中的 hover / signature 请求键，防止鼠标抖动或
    # on_modified 重入时重复发请求。
    "hover_inflight": None,
    "sig_inflight": None,
    # 补全来源统计（诊断用）：clangd 结果次数 / 内置兜底次数
    "stats": {"clangd": 0, "builtin": 0},
}

# 内置数据库条目按版本分档：C++14 及以下排前面，C++17/20/23 排后面
_LATER_STD_RE = re.compile(r"C\+\+(1[7-9]|2[0-9])")


def _clangd_settings_sig():
    return "%s|%s|%s|%s|%s|%s|%s|%s|%s" % (
        _s("enable_clangd_engine", True),
        _s("cxx_standard", "c++14"),
        _s("clangd_binary", "") or "",
        repr(_s("clangd_args", []) or []),
        repr(_s("clangd_extra_fallback_flags", []) or []),
        _s("compiler_path", "") or "",
        repr(_s("compiler_extra_args", []) or []),
        repr(_s("include_paths", []) or []),
        _s("clangd_working_dir", "") or "")


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
_CDB_LOCK = threading.Lock()
_CDB_PRUNE = {"t": 0.0}


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
    for inc in (_s("include_paths", []) or []):
        args_list.append("-I" + str(inc))
    entry = {
        "directory": os.path.dirname(key),
        "arguments": args_list + [key],
        # 注意：标准 compile_commands.json 的键是 "file"（不是 "filename"），
        # 键名错误会导致 clangd 拒绝加载整个 CDB（Unknown key），
        # 永远退回 fallback 编译模式
        "file": key,
    }
    with _CDB_LOCK:
        old = db.get(key)
        if old == entry and not force:
            return
        db[key] = entry
        # CDB 长期只增不减会积累成千上万个已删除项目的条目。条目多时
        # 偶尔清理一次不存在的文件（每分钟最多一次，避免频繁 stat）。
        now = time.time()
        if len(db) > 500 and now - _CDB_PRUNE["t"] > 60:
            _CDB_PRUNE["t"] = now
            for k in list(db.keys()):
                try:
                    if not os.path.isfile(k):
                        db.pop(k, None)
                except Exception:
                    pass
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
            _log_error("clangd compile_commands write", e)


def _compiler_include_dirs(cached_only=False):
    """提取编译器的 C++ 头文件搜索路径列表（结果缓存）。

    用途：
      - clangd 的 fallbackFlags 注入 -I，让 fallback 模式也能解析
        libstdc++ 头文件（bits/stdc++.h）；
      - F12 把 std 符号定位到真实系统头文件。

    cached_only=True 时不启动编译器（用于 UI 线程上的 F12）；缓存未
    就绪就返回空列表，由后台预热线程稍后填充。
    """
    compiler = find_compiler()
    key = (compiler,
           repr(_s("compiler_extra_args", []) or []),
           repr(_s("include_paths", []) or []))
    if _include_dirs_cache["done"] and _include_dirs_cache["key"] == key:
        return list(_include_dirs_cache["dirs"])
    if cached_only:
        # 缓存还没预热好（或设置已变）：不要返回旧编译器的路径
        return []
    dirs = []
    if compiler:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            cmd = [compiler, "-E", "-x", "c++", "-v"]
            cmd += [str(a) for a in (_s("compiler_extra_args", []) or [])]
            for inc in (_s("include_paths", []) or []):
                cmd.append("-I" + str(inc))
            cmd.append("-")
            p = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
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
                    # clang 在 macOS 上会给路径加 " (framework directory)"
                    d = re.sub(r"\s+\([^)]*\)\s*$", "", d)
                    if d and os.path.isdir(d) and d not in dirs:
                        dirs.append(d)
        except Exception as e:
            _log_error("compiler include path probe", e)
    _include_dirs_cache.update(key=key, dirs=dirs, done=True)
    return list(dirs)


_include_dirs_cache = {"key": None, "dirs": [], "done": False}


def _warm_include_dirs_async():
    th = threading.Thread(target=_compiler_include_dirs)
    th.daemon = True
    th.start()


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
    _clangd_state["refreshed"] = None
    _clangd_state["hover_inflight"] = None
    _clangd_state["sig_inflight"] = None
    # 缓存的补全/悬停结果属于即将退役的服务端，必须一起丢弃
    _clangd_state["async_cache"].clear()
    _clangd_state["hover_cache"].clear()


def _view_for_uri(uri):
    """按 LSP uri 找对应 view（诊断回调用）。"""
    import urllib.parse
    try:
        p = urllib.parse.unquote(uri or "")
    except Exception:
        p = uri or ""
    if p.startswith("file://"):
        p = p[len("file://"):]
    p = p.replace("\\", "/")
    if re.match(r"^/[A-Za-z]:", p):
        p = p[1:]
    if os.name == "nt":
        p = p.lower()
    for w in sublime.windows():
        for v in w.views():
            fname = v.file_name()
            if not fname:
                continue
            f = fname.replace("\\", "/")
            if os.name == "nt":
                f = f.lower()
            if f == p:
                return v
    return None


def _apply_clangd_diagnostics(uri):
    """把 clangd 诊断交给 UI 线程渲染（lint_engine=clangd 模式）。

    这个函数由 clangd 读线程调用。sublime.windows()/view 不是线程安全
    的，不能在这里查视图；全部工作切回 UI 线程再做。
    """
    try:
        if str(_s("lint_engine", "compiler")) != "clangd":
            return
        sublime.set_timeout(
            lambda: _apply_clangd_diagnostics_ui(uri), 0)
    except Exception as e:
        _log_error("clangd diagnostics schedule", e)


def _apply_clangd_diagnostics_ui(uri):
    """UI 线程：读取 clangd 诊断并渲染。"""
    try:
        if str(_s("lint_engine", "compiler")) != "clangd":
            return
        client = _clangd_state["client"]
        if client is None:
            return
        view = _view_for_uri(uri)
        if view is None or not view.is_valid():
            return
        items = client.diagnostics_for(view.file_name())
        if items is None:
            return
        entries = []
        for (ln, cl, sev, msg) in items:
            sev_en = "error" if sev == 1 else "warning"
            # 只存双语原文，语言留给渲染层（translate_message 返回 (en, zh)）
            try:
                en, zh = ca_engine.translate_message(msg)
            except Exception:
                en, zh = msg, msg
            entries.append({
                "line": ln + 1, "col": cl + 1,
                "sev_en": sev_en,
                "msg": en, "zh": zh,
                "ctx": "", "ctx_zh": "", "notes": [], "tier": "compiler",
            })
        st = _lint_state.setdefault(view.id(), {})
        st["compiler"] = entries
        st["compiler_src_hash"] = None  # clangd 诊断无 src hash
        render_diagnostics(view.id())
    except Exception as e:
        _log_error("clangd diagnostics parse", e)


def _on_clangd_notify(method, params):
    """clangd 读线程通知回调（勿做重活）。

    - 首次收到 publishDiagnostics ≈ preamble 构建完成，状态栏提示
      "clangd 引擎已就绪"（此后补全自动升级为 clangd 结果）。
    - lint_engine=clangd 模式下，把诊断交给渲染管线（LSP 式代码审查）。
    """
    if method == "textDocument/publishDiagnostics":
        st = _clangd_state
        if not st.get("ready_announced"):
            st["ready_announced"] = True
            try:
                sublime.set_timeout(
                    lambda: sublime.status_message(
                        u"[CppAssistant] clangd engine ready "
                        u"(semantic completion active)"),
                    0)
            except Exception:
                pass
        uri = ((params or {}).get("textDocument") or {}).get("uri") or ""
        if uri:
            try:
                _apply_clangd_diagnostics(uri)
            except Exception as e:
                _log_error("clangd notify callback", e)


def _get_clangd_client(view):
    """懒启动 clangd（首次打开 C++ 视图后触发）。

    - 相关设置变化时立即重启客户端（之前只更新 sig，旧进程继续跑，
      导致 clangd_binary / cxx_standard / clangd_args 实际不生效）。
    - 没找到 clangd 时每 30 秒重试一次，用户中途安装 clangd 不必重启
      Sublime。
    - 启动失败 5 秒冷却，避免每次补全都重复拉起失败进程。
    """
    st = _clangd_state
    if not _s("enable_clangd_engine", True):
        return None
    sig = _clangd_settings_sig()
    if st["sig"] != sig:
        if st["client"] is not None:
            _stop_clangd_client()
        st["sig"] = sig
        st["no_server"] = False
        st["no_server_since"] = 0.0
        st["last_fail"] = 0.0
    c = st["client"]
    if c is not None and c.is_alive():
        return c
    if c is not None:
        _stop_clangd_client()
    now = time.time()
    if st["no_server"] and now - st["no_server_since"] < 30.0:
        return None
    if now - st["last_fail"] < 5.0:
        return None
    binary = ca_clangd.find_clangd(_s("clangd_binary", "") or None)
    if not binary:
        st["no_server"] = True
        st["no_server_since"] = now
        sublime.status_message(
            u"[CppAssistant] clangd not found (not in PATH), "
            u"completion uses the builtin database. "
            u"Set clangd_binary to point at the executable.")
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
            # 补全风格用 clangd 默认（bundled）：类型/模板只出一条简洁
            # 条目，完整签名显示在右侧详情面板——与 LSP-clangd 的体验
            # 一致。此前传 --completion-style=detailed 会把每个重载展开
            # 成独立长条目（十几个构造函数占满弹窗），既冗长又拖慢渲染
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
                                   on_notify=_on_clangd_notify,
                                   on_apply_edit=_on_clangd_apply_edit)
        c.start()
    except Exception as e:
        st["last_fail"] = now
        _log_error("clangd start", e)
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
        # 不能截断后再发给 clangd：LSP 位置是按全文计算的，截断会让
        # 大文件里的补全/诊断位置整体错位。超大文件直接不启用 clangd。
        size = view.size()
        if size > _CLANGD_MAX_CHARS:
            return
        text = view.substr(sublime.Region(0, size))
        if client.has_document(fname):
            client.change_document(fname, text)
        else:
            client.open_document(fname, text)
    except Exception as e:
        _log_error("clangd document warm-up", e)


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
            en = en_detail(trig) or u""
            out.append({"trigger": trig, "insert": body,
                        "annotation": desc, "annotation_en": en,
                        "kind": kd, "detail": desc,
                        "detail_en": en or desc, "snippet": True})
    return out


def _tier_sort_static(results, prefix):
    """内置数据库兜底：用户片段最前 → C++14 及以下档 → C++17/20/23 档。

    analyze() 本身也会返回内置片段，这里必须先按 (trigger, insert)
    去重，否则每个内置片段会在弹窗里出现两次。
    """
    out = _snippet_items_for_prefix(prefix)
    seen = set((d.get("trigger"), d.get("insert")) for d in out)
    early = []
    later = []
    for d in results:
        key = (d.get("trigger"), d.get("insert"))
        if key in seen:
            continue
        seen.add(key)
        if _LATER_STD_RE.search(d.get("annotation") or "") or \
                _LATER_STD_RE.search(d.get("annotation_en") or ""):
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
        _log_error("include insertion staging", e)


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
        _log_error("include insertion apply", e)


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
            # 光标在 400KB 之后：只有 clangd 能安全处理（它需要全文）。
            # 内置分析仍限制在 400KB 以内。
            if not _s("enable_clangd_engine", True) or \
                    size > _CLANGD_MAX_CHARS:
                return None
            text = view.substr(sublime.Region(0, size))
        # 补全匹配风格：true=LSP-clangd 风格（默认）；false=严格前缀基础模式
        clangd_style = bool(_s("enable_clangd_style_completion", True))
        flags = _INHIBIT_SNIPPET  # 片段由本插件统一供给（旧版 ST 无此常量为 0）
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
                _log_error("completion engine", e)
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
            flags |= _INHIBIT_WORD
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
        # on_query_completions 只读了前 400KB。若文件更大，必须重新取
        # 全文再发给 clangd，否则 LSP 位置/解析结果都会错位。
        size = view.size()
        if size > _CLANGD_MAX_CHARS:
            return None
        if size != len(text):
            text = view.substr(sublime.Region(0, size))
        if off > len(text):
            return None
        std = _s("cxx_standard", "c++14")
        # 键含 C++ 标准：改标准后 clangd 的答案集会变，不能让旧标准下
        # 生成的候选继续命中（文本未变时也必须重来）。
        key = (view.buffer_id(), view.change_count(), off, std)
        cache = st["async_cache"]
        if key in cache:
            items = cache.get(key)
            if items:
                dicts = _snippet_items_for_prefix(prefix) + items
                _stash_includes(view, dicts)
                return dicts
            return None
        # 默认完全异步：UI 线程零阻塞，首弹窗为内置数据库结果，clangd
        # 结果到达后自动刷新（on_arrival -> _maybe_refresh_popup）。
        # 需要更准的首弹窗可把 clangd_completion_wait_ms 调大。
        wait_ms = int(_s("clangd_completion_wait_ms", 0) or 0)

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

    def on_activated_async(self, view):
        # 切换标签页时补一次文档同步：clangd 需要当前文本才能给
        # 准确的补全/诊断，尤其是文件在外部被修改后。
        if _is_cpp(view) and _s("enable_clangd_engine", True):
            sublime.set_timeout(
                lambda: _warm_clangd_document(view), 50)

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
            _log_error("include insertion detect", e)

    def on_pre_save(self, view):
        if _s("format_on_save", False) and _is_cpp(view):
            view.run_command("ca_format_document", {"sync": True})

    def on_post_save_async(self, view):
        self._maybe_lint(view)
        if _is_cpp(view) and _s("enable_clangd_engine", True):
            sublime.set_timeout(
                lambda: _warm_clangd_document(view), 0)

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
            _log_error("hover trigger", e)

    # ---- signature help 触发（80ms 防抖）----
    def _maybe_signature_help(self, view):
        try:
            if not _is_cpp(view):
                return
            if not _s("enable_clangd_engine", True) or \
                    not _s("enable_signature_help", True):
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
            _log_error("signature help trigger", e)

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
        # 第二级：完整检查。
        # lint_engine=clangd 时诊断由 publishDiagnostics 推送（LSP 式），
        # 不启动编译器进程；compiler 模式走编译器检查（防抖）
        if str(_s("lint_engine", "compiler")) == "clangd":
            return
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
    "// CppAssistant precompiled header (jiangly style)\n"
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
        # 旧版 ST4（Python 3.3）没有 subprocess.CREATE_NO_WINDOW 命名常量
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
        _log_error("compiler version query", e)
    _compiler_version_cache[compiler] = ver
    return ver


def _pch_paths(compiler, std):
    # PCH 必须与真正的编译参数匹配：-D / -I / 额外 -std 变化后旧 .gch
    # 可能不兼容或内容不一致，签名里带上这些设置。
    flags = repr((_s("compiler_extra_args", []) or [],
                  _s("include_paths", []) or []))
    flags_sig = "%08x" % (zlib.crc32(flags.encode("utf-8")) & 0xffffffff)
    sig = (re.sub(r"[^\w]", "_", os.path.normcase(compiler))
           + "_" + re.sub(r"[^\w]", "_", _compiler_version(compiler))
           + "_" + std + "_" + flags_sig)
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
        # 旧版 ST4（Python 3.3）没有 subprocess.CREATE_NO_WINDOW 命名常量
        creationflags = 0x08000000 if os.name == "nt" else 0
        startupinfo = _hidden_window_startupinfo()
        cmd = [compiler, "-std=" + str(std), "-x", "c++-header"]
        cmd += [str(a) for a in (_s("compiler_extra_args", []) or [])]
        for inc in (_s("include_paths", []) or []):
            cmd.append("-I" + str(inc))
        cmd += [hdr, "-o", gch]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags,
            startupinfo=startupinfo)
        proc.wait(timeout=180)
        if proc.returncode == 0 and os.path.isfile(gch):
            _PCH_READY.add(sig)
            _sweep_old_pch(sig)
    except Exception as e:
        _log_error("PCH build", e)
    finally:
        with _PCH_LOCK:
            _PCH_BUILDING.discard(sig)


def _warm_pch_async():
    def worker():
        compiler = find_compiler()
        if compiler is None:
            return
        std = str(_s("cxx_standard", "c++14"))
        _, _, gch = _pch_paths(compiler, std)
        if not os.path.isfile(gch):
            _build_pch(compiler, std)

    th = threading.Thread(target=worker)
    th.daemon = True
    th.start()


def plugin_loaded():
    # 后台预热 PCH 和编译器 include 路径。设置全部按需从 API 读取，
    # 缓存按设置签名自愈，因此不需要 add_on_change 监听。
    if _s("enable_linting", True) and _s("enable_pch", True):
        # 尽早后台预热 PCH，让首次编译器检查就享受加速
        sublime.set_timeout(_warm_pch_async, 1200)
    # F12 的 std fallback 需要编译器 include 路径。先异步探一次，
    # 避免第一次按 F12 时在 UI 线程启动编译器。
    sublime.set_timeout(_warm_include_dirs_async, 800)


# ---------------------------------------------------------------------------
# 语法检查
# ---------------------------------------------------------------------------

def find_compiler():
    configured = _s("compiler_path", "") or ""
    now = time.time()
    if _compiler_cache["done"] and _compiler_cache["configured"] == configured:
        # 找到编译器后缓存整个会话；没找到时每 30 秒重探一次，
        # 用户中途把 g++ 加进 PATH 不必重启 Sublime。
        if _compiler_cache["path"] is not None or \
                now - _compiler_cache["checked"] < 30.0:
            return _compiler_cache["path"]
    if configured and os.path.isfile(configured):
        _compiler_cache.update(configured=configured, path=configured,
                               done=True, checked=now)
        return configured
    for cand in ("g++", "clang++"):
        w = shutil.which(cand)
        if w:
            _compiler_cache.update(configured=configured, path=w, done=True,
                                   checked=now)
            return w
    _compiler_cache.update(configured=configured, path=None, done=True,
                           checked=now)
    return None


def _settings_sig(compiler):
    """影响诊断"内容"的设置签名，用于结果缓存失效判断。

    显示语言与幽灵提示条不在其中：它们只影响渲染，不影响 clangd/编译器
    的产出，因此切换语言不会作废缓存（渲染时按当前语言取用）。
    """
    return (str(_s("cxx_standard", "c++14")),
            repr(_s("compiler_extra_args", [])),
            repr(_s("include_paths", [])),
            bool(_s("enable_pch", True)),
            compiler or "")


def _display_language():
    """读取用户设置的诊断显示语言。

    合法值: 'en' (英文, 默认), 'zh' (中文), 'both' (双语)
    """
    v = str(_s("display_language", "en")).lower().strip()
    if v in ("zh", "en", "both"):
        return v
    return "en"


def _compile_with_cmd(cmd, src, workdir, view_id):
    """执行一次编译器调用，返回合并的 stdout/stderr 字节串或 None（超时/被取代）。"""
    # Windows: CREATE_NO_WINDOW (0x08000000) hides console window
    # 旧版 ST4（Python 3.3）的 subprocess 没有 CREATE_NO_WINDOW 命名常量，
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
        _log_error("compiler launch", e)
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
           "-std=" + str(_s("cxx_standard", "c++14")),
           "-Wall", "-fno-diagnostics-show-caret", "-x", "c++"]
    cmd += [str(a) for a in _s("compiler_extra_args", [])]
    for inc in _s("include_paths", []):
        cmd.append("-I" + str(inc))
    # PCH 加速：使用 -include 直接挂载
    pch_hdr = pch_gch = None
    if _s("enable_pch", True) and "bits/stdc++.h" in src:
        std = str(_s("cxx_standard", "c++14"))
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
                _pch_paths(compiler, str(_s("cxx_standard", "c++14")))[0])
            std = str(_s("cxx_standard", "c++14"))
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
    entries = ca_engine.parse_compiler_output(text)
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
    """把 basic_checks 的输出包装为统一诊断结构（tier=basic）。

    basic_checks 每条给出 (行, 列, 级别, 英文, 中文)，语言仍由渲染层选定。
    """
    out = []
    for (ln, cl, sv, en, zh) in problems:
        out.append({
            "line": ln, "col": cl,
            "sev_en": "error" if sv == "error" else "warning",
            "msg": en, "zh": zh,
            "ctx": "", "ctx_zh": "", "notes": [],
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
            _log_error("basic check", e)
            return
        diags = _basic_diags(problems)

        def done():
            if not view.is_valid() or _basic_gen.get(vid) != gen:
                return
            st = _lint_state.setdefault(vid, {})
            # 关键修复：源文本已变, 立即清空过时的编译器诊断,
            # 避免删除错误行后还要等 1s 才消除标记
            comp = st.get("compiler")
            # clangd 模式下 compiler 槽位存的是 publishDiagnostics，
            # 没有 src hash；不要因为基础检查文本变了就把它清掉。
            if comp and str(_s("lint_engine", "compiler")) != "clangd":
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
        view.set_status("ca_diag", _STATUS_LINTING[_display_language()])
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


# 诊断文本里按语言取用的固定片段（诊断内容本身由 ca_engine 提供双语）
_BASIC_TAG = {"en": u"[instant]", "zh": u"[即时检查]",
              "both": u"[instant / 即时检查]"}
_STATUS_COUNT = {"en": u"\u2716 %d error  \u26a0 %d warning",
                 "zh": u"\u2716 %d 错误  \u26a0 %d 警告",
                 "both": u"\u2716 %d 错误(error)  \u26a0 %d 警告(warning)"}
_STATUS_CLEAN = {"en": u"\u2714 no syntax errors",
                 "zh": u"\u2714 无语法错误",
                 "both": u"\u2714 无语法错误(no errors)"}
_STATUS_LINTING = {"en": u"linting...",
                   "zh": u"正在语法检查...",
                   "both": u"正在语法检查(linting)..."}
_SEV_TAG = {"en": {True: u"[error]", False: u"[warning]"},
            "zh": {True: u"[错误]", False: u"[警告]"},
            "both": {True: u"[error / 错误]", False: u"[warning / 警告]"}}


def _loc_str(lang, line, col):
    """诊断面板里的位置前缀，按显示语言生成。"""
    if lang == "zh":
        return u"第%d行%d列" % (line, col)
    if lang == "both":
        return u"line %d:%d (第%d行%d列)" % (line, col, line, col)
    return u"line %d:%d" % (line, col)


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
    lang = _display_language()

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
        is_err = d.get("sev_en") in ("error", "fatal error")
        if is_err:
            err_regions.append(region)
            n_err += 1
        else:
            warn_regions.append(region)
            n_warn += 1

        # 语言在渲染时才决定：诊断缓存因此与 display_language 无关
        text = ca_engine.format_message(d.get("msg"), d.get("zh"), lang)
        if d.get("tier") == "basic":
            text = u"%s %s" % (_BASIC_TAG[lang], text)
        icon = u"\u2716" if is_err else u"\u26a0"
        color = "redish" if is_err else "yellowish"
        if _s("show_phantoms", True) and len(phantoms) < 40:
            # 诊断里经常出现 std::vector<int>、& 等 HTML 敏感字符，
            # 不转义会被 phantom 当成标签解析。
            html_text = _html_escape(text).replace("\n", "<br>")
            body = _PHANTOM_TMPL.format(color=color, icon=icon,
                                        text=html_text)
            phantoms.append(sublime.Phantom(
                region, body, sublime.LAYOUT_BELOW))
        # 面板输出
        tag = _SEV_TAG[lang][bool(is_err)]
        ctx = ca_engine.format_message(d.get("ctx"), d.get("ctx_zh"), lang)
        panel_lines.append(u"%s %s%s  %s" % (
            tag, ctx, _loc_str(lang, d["line"], d["col"]),
            text.replace("\n", " ")))
        for note in (d.get("notes") or []):
            note_text = ca_engine.format_message(
                note.get("msg"), note.get("zh"), lang)
            panel_lines.append(u"    note %s  %s" % (
                _loc_str(lang, note.get("line", d["line"]),
                         note.get("col", d["col"])),
                note_text.replace("\n", " ")))

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
        view.set_status("ca_diag", _STATUS_COUNT[lang] % (n_err, n_warn))
    elif comp or st.get("basic"):
        view.set_status("ca_diag", _STATUS_CLEAN[lang])
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
            _log_error("hover popup", e)


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
            _log_error("signature popup", e)


def run_hover(view, point):
    """Read the hovered symbol on the UI thread, then request the hover
    document on a worker thread. Only the finished popup hops back with
    set_timeout, so a slow clangd can never stall typing.
    """
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
        seq = st["hover_seq"] = st.get("hover_seq", 0) + 1
        size = view.size()
        if size > _CLANGD_MAX_CHARS:
            return
        text = view.substr(sublime.Region(0, size))
        cache_key = (fname, view.change_count(), point, word)
        cached = st["hover_cache"].get(cache_key)
        if cached:
            view.run_command("ca_hover", {"point": point, "body": cached})
            return
        if st.get("hover_inflight") == cache_key:
            return
        st["hover_inflight"] = cache_key

        def work():
            try:
                got = _clangd_fetch(fname, "hover", text, point)
                if not got:
                    st["hover_inflight"] = None
                    return

                def show():
                    try:
                        if st.get("hover_inflight") == cache_key:
                            st["hover_inflight"] = None
                        if not view.is_valid() or \
                                _clangd_state.get("hover_seq") != seq:
                            return
                        cache = st["hover_cache"]
                        cache[cache_key] = got[1]
                        if len(cache) > 32:
                            cache.clear()
                        view.run_command("ca_hover", {"point": point,
                                                      "body": got[1]})
                    except Exception as e:
                        _log_error("hover popup show", e)

                sublime.set_timeout(show, 0)
            except Exception as e:
                st["hover_inflight"] = None
                _log_error("hover", e)

        th = threading.Thread(target=work)
        th.daemon = True
        th.start()
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
        if size > _CLANGD_MAX_CHARS:
            return
        text = view.substr(sublime.Region(0, size))
        cc = view.change_count()
        inflight = (fname, cc, pt)
        if _clangd_state.get("sig_inflight") == inflight:
            return
        _clangd_state["sig_inflight"] = inflight
        seq = _clangd_state["sig_seq"] = \
            _clangd_state.get("sig_seq", 0) + 1

        def work():
            try:
                got = _clangd_fetch(fname, "signature", text, pt)
                if not got:
                    _clangd_state["sig_inflight"] = None
                    return

                def show():
                    try:
                        st = _clangd_state
                        if st.get("sig_inflight") == inflight:
                            st["sig_inflight"] = None
                        if not view.is_valid() or \
                                st.get("sig_seq") != seq:
                            return
                        # 结果生成期间又打了字或移动了光标：作废
                        if view.change_count() != cc:
                            return
                        sel = view.sel()
                        if not sel or sel[0].begin() != pt:
                            return
                        view.run_command("ca_signature_popup", {"got": got})
                    except Exception as e:
                        _log_error("signature popup", e)

                sublime.set_timeout(show, 0)
            except Exception as e:
                _clangd_state["sig_inflight"] = None
                _log_error("signature help", e)

        th = threading.Thread(target=work)
        th.daemon = True
        th.start()
    except Exception as e:
        _log_error("signature help", e)


# ---------------------------------------------------------------------------
# 语言切换命令
# ---------------------------------------------------------------------------

class CaSetDisplayLanguageCommand(sublime_plugin.ApplicationCommand):
    """Set display_language through the settings API and re-render open views.

    诊断条目同时携带中英文文本，语言在渲染时才选定，所以切换语言
    不需要作废任何缓存。
    """

    def run(self, lang):
        if lang not in ("en", "zh", "both"):
            sublime.status_message(
                "[CppAssistant] invalid language: %s" % lang)
            return
        if not _set_setting("display_language", lang):
            return
        _refresh_cpp_views()
        sublime.status_message("[CppAssistant] diagnostic language: %s" % lang)

    def is_checked(self, lang=None):
        return _display_language() == lang


class CaSetCompletionModeCommand(sublime_plugin.ApplicationCommand):
    """Switch the completion matching style.

    clangd = LSP-clangd 风格（作用域内前缀 + 模糊匹配）；
    basic  = 仅严格前缀匹配。补全缓存键含该模式，改完自愈。
    """

    def run(self, mode):
        if mode not in ("clangd", "basic"):
            sublime.status_message(
                "[CppAssistant] invalid completion mode: %s" % mode)
            return
        if not _set_setting("enable_clangd_style_completion",
                            mode == "clangd"):
            return
        sublime.status_message("[CppAssistant] completion mode: %s" % mode)

    def is_checked(self, mode=None):
        return bool(_s("enable_clangd_style_completion", True)) == \
            (mode == "clangd")


class CaSetCompletionEngineCommand(sublime_plugin.ApplicationCommand):
    """Switch the completion engine.

    clangd  = 内嵌 LSP 客户端驱动真实 clangd（语义补全）；
    builtin = 内置 STL 数据库兜底（零依赖）。
    """

    def run(self, engine):
        if engine not in ("clangd", "builtin"):
            sublime.status_message(
                "[CppAssistant] invalid completion engine: %s" % engine)
            return
        if not _set_setting("enable_clangd_engine", engine == "clangd"):
            return
        # 客户端和它的补全缓存都属于上一个引擎，一起退役；
        # 切回 clangd 时由首次补全懒启动。
        _stop_clangd_client()
        sublime.status_message("[CppAssistant] completion engine: %s" % engine)

    def is_checked(self, engine=None):
        return bool(_s("enable_clangd_engine", True)) == (engine == "clangd")


class CaSetLintEngineCommand(sublime_plugin.ApplicationCommand):
    """Switch the diagnostics source (lint_engine).

    compiler = 后台调用 g++/clang++ 完整检查；
    clangd   = 直接用 clangd 推送的 publishDiagnostics（要求
               enable_clangd_engine 开启）。
    """

    def run(self, engine):
        if engine not in ("compiler", "clangd"):
            sublime.status_message(
                "[CppAssistant] invalid lint engine: %s" % engine)
            return
        if not _set_setting("lint_engine", engine):
            return
        # clangd 模式等它推下一轮；compiler 模式立刻补一次检查
        _refresh_cpp_views(rerun_lint=(engine == "compiler"))
        if engine == "clangd":
            for w in sublime.windows():
                for v in w.views():
                    if _is_cpp(v):
                        _warm_clangd_document(v)
        sublime.status_message("[CppAssistant] lint engine: %s" % engine)

    def is_checked(self, engine=None):
        return str(_s("lint_engine", "compiler")) == engine


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
        txt = "\n".join(lines) if lines else u"No diagnostics."
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
            u"CppAssistant completion engine status",
            u"-----------------------------",
            u"clangd binary: %s" % (
                binary or u"not found (no clangd in PATH)"),
        ]
        if c is None:
            lines.append(u"clangd client: not started")
        else:
            try:
                lines.append(u"clangd client: alive=%s ready=%s" %
                             (c.is_alive(), c.is_ready()))
                try:
                    fname = view.file_name()
                    pre = c.is_preamble_ready(fname) if fname else False
                except Exception:
                    pre = False
                lines.append(u"preamble ready (diagnostics received): %s"
                             % pre)
                if not pre:
                    lines.append(u"(preamble still building: high standards "
                                 u"such as c++23 plus bits/stdc++.h need "
                                 u"about 10s from a cold start; completion "
                                 u"uses the builtin database meanwhile)")
            except Exception:
                lines.append(u"clangd client: status unavailable")
        lines.append(u"no_server flag: %s" % st.get("no_server"))
        lines.append(u"enable_clangd_engine: %s" %
                     _s("enable_clangd_engine", True))
        lines.append(u"clangd_style: %s" %
                     _s("enable_clangd_style_completion", True))
        lines.append(u"cxx_standard: %s" %
                     _s("cxx_standard", "c++14"))
        lines.append(u"completion source this session: clangd %d / "
                     u"builtin %d"
                     % (stats.get("clangd", 0), stats.get("builtin", 0)))
        lines.append(u"current file saved: %s (clangd skips unsaved files)"
                     % bool(view.file_name()))
        lines.append(u"current file detected as C++: %s" % _is_cpp(view))
        msg = u"\n".join(lines)
        print("[CppAssistant] engine status")
        print(msg)
        sublime.message_dialog(msg)


class CaTogglePhantomsCommand(sublime_plugin.ApplicationCommand):
    """显示/隐藏行内幽灵提示条（状态栏与波浪线不受影响）。"""

    def run(self):
        new = not bool(_s("show_phantoms", True))
        _set_setting("show_phantoms", new)
        _refresh_cpp_views()
        sublime.status_message("[CppAssistant] inline phantoms: %s" %
                               ("on" if new else "off"))

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


def _run_clang_format(src, clang_format, style, extra_args=None):
    """运行 clang-format；成功返回文本，失败返回 None。"""
    if clang_format is None:
        return None
    # 旧版 ST4（Python 3.3）没有 subprocess.CREATE_NO_WINDOW 命名常量
    creationflags = 0x08000000 if os.name == "nt" else 0
    startupinfo = _hidden_window_startupinfo()
    proc = None
    try:
        cmd = [clang_format, "--assume-filename=x.cpp",
               "--style=" + style] + list(extra_args or [])
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=creationflags,
            startupinfo=startupinfo)
        out, err = proc.communicate(input=src.encode("utf-8"), timeout=15)
    except Exception:
        out, err = None, b"timeout/error"
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    if out is not None and proc is not None and proc.returncode == 0:
        return _decode(out)
    if err:
        print("[CppAssistant] clang-format failed:",
              _decode(err).strip())
    return None


def _format_source(src, clang_format, style, width):
    """在工作线程里跑格式化，返回 (新文本, 引擎名)。

    只做纯计算，不碰 Sublime API；clang-format 不可用或失败时退回
    内置 jiangly 风格引擎。
    """
    formatted = _run_clang_format(src, clang_format, style)
    if formatted is not None:
        return formatted, u"clang-format"
    return ca_engine.format_code(src, width), u"builtin fallback engine"


def _format_range_source(src, clang_format, style, width,
                         start_line, end_line, start_offset, end_offset):
    """只格式化选区覆盖的完整行，返回 (新文本, 引擎名)。

    clang-format 的 ``--lines`` 会输出完整文件（选区外的行保持不变）；
    不可用时退回 ca_engine 的范围格式化器，同样只改选中的行。
    """
    formatted = _run_clang_format(
        src, clang_format, style,
        ["--lines=%d:%d" % (start_line, end_line)])
    if formatted is not None:
        return formatted, u"clang-format"
    return (ca_engine.format_code_range(
        src, start_offset, end_offset, width), u"builtin fallback engine")


class CaFormatDocumentCommand(sublime_plugin.TextCommand):
    """格式化整个文档。

    命令面板/键位调用时在线程里跑，避免大文件或 clang-format 启动
    时卡住界面；format_on_save 以 sync=True 调用，保证保存前文本已经
    替换完毕。异步期间若缓冲区被修改或视图关闭，结果直接丢弃。
    """

    def run(self, edit, sync=False):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_fmt", u"only C/C++ files are supported")
            return
        src = view.substr(sublime.Region(0, view.size()))
        if not src.strip():
            return
        try:
            anchor_row = view.rowcol(view.sel()[0].begin())[0]
        except Exception:
            anchor_row = 0
        clang_format = _find_clang_format()
        style = _s("clang_format_style", CLANG_FORMAT_STYLE_DEFAULT)
        width = int(_s("indent_width", 4))
        change_count = view.change_count()

        if sync:
            new_text, engine_name = _format_source(
                src, clang_format, style, width)
            self._apply(view, src, change_count, anchor_row,
                        new_text, engine_name)
            return

        view.set_status("ca_fmt", u"formatting...")

        def worker():
            try:
                result = _format_source(src, clang_format, style, width)
            except Exception as e:
                _log_error("format document", e)
                sublime.set_timeout(
                    lambda: view.set_status("ca_fmt", u"format failed"),
                    0)
                return
            sublime.set_timeout(
                lambda: self._apply(view, src, change_count, anchor_row,
                                    result[0], result[1]),
                0)

        threading.Thread(target=worker, daemon=True).start()

    def _apply(self, view, src, change_count, anchor_row,
               new_text, engine_name):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_fmt", u"cancelled: buffer changed")
                return
            if new_text == src:
                view.set_status("ca_fmt",
                                u"no changes, already jiangly style")
                return
            view.run_command("ca_format_apply", {"text": new_text})
            last_row = view.rowcol(view.size())[0]
            pt = view.text_point(min(anchor_row, last_row), 0)
            view.sel().clear()
            view.sel().add(sublime.Region(pt, pt))
            view.show_at_center(pt)
            view.set_status(
                "ca_fmt", u"formatted with jiangly style (%s)" % engine_name)
        except Exception as e:
            _log_error("format apply", e)


class CaFormatApplyCommand(sublime_plugin.TextCommand):
    def run(self, edit, text=None):
        if text is None:
            return
        view = self.view
        view.replace(edit, sublime.Region(0, view.size()), text)


def _range_line_bounds(view, region):
    """把选区扩展成完整行，返回 (start_row, end_row, start_pt, end_pt)。

    结束位置在行首时向前退一行，避免三击选中整行后把下一行也格式化。
    """
    start_row = view.rowcol(region.begin())[0]
    end_row = view.rowcol(region.end())[0]
    if region.end() > region.begin():
        end_col = view.rowcol(region.end())[1]
        if end_col == 0 and end_row > start_row:
            end_row -= 1
    start_pt = view.text_point(start_row, 0)
    end_pt = view.line(view.text_point(end_row, 0)).end()
    return start_row, end_row, start_pt, end_pt


class CaFormatSelectionCommand(sublime_plugin.TextCommand):
    """只格式化当前选区（或光标所在行）。

    优先请求 clangd 的 textDocument/rangeFormatting；clangd 未就绪时
    退回 clang-format --lines，再退回内置范围格式化器。选区外的文本
    不会被修改。
    """

    def run(self, edit, sync=False):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_fmt", u"only C/C++ files are supported")
            return
        selections = view.sel()
        if not selections:
            return
        region = selections[0]
        start_row, end_row, start_pt, end_pt = _range_line_bounds(view, region)
        src = view.substr(sublime.Region(0, view.size()))
        if not src.strip():
            return
        change_count = view.change_count()
        width = int(_s("indent_width", 4))
        clang_format = _find_clang_format()
        style = _s("clang_format_style", CLANG_FORMAT_STYLE_DEFAULT)
        fname = view.file_name()
        # 设置读取留在 UI 线程，工作线程只碰纯数据与客户端
        client = (_clangd_state["client"]
                  if _s("enable_clangd_engine", True) else None)

        if sync:
            result = self._compute(fname, src, start_pt, end_pt,
                                   start_row, end_row, clang_format,
                                   style, width, client)
            self._apply(view, src, change_count, start_pt, end_pt, *result)
            return

        view.set_status("ca_fmt", u"formatting selection...")

        def worker():
            try:
                result = self._compute(
                    fname, src, start_pt, end_pt, start_row, end_row,
                    clang_format, style, width, client)
            except Exception as e:
                _log_error("format selection", e)
                sublime.set_timeout(
                    lambda: view.set_status("ca_fmt", u"format failed"), 0)
                return
            sublime.set_timeout(
                lambda: self._apply(view, src, change_count, start_pt,
                                    end_pt, *result), 0)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _compute(fname, src, start_pt, end_pt, start_row, end_row,
                 clang_format, style, width, client):
        """返回 (新文本, 引擎名)；clangd 不可用/失败时走本地格式化。"""
        if (fname and client is not None
                and client.is_ready() and len(src) <= _CLANGD_MAX_CHARS):
            edits = client.range_formatting(
                fname, src, start_pt, end_pt, timeout=2.5)
            if edits is not None:
                if not edits:
                    return src, u"clangd (no changes)"
                return (ca_clangd.apply_text_edits(src, edits),
                        u"clangd")
        return _format_range_source(
            src, clang_format, style, width,
            start_row + 1, end_row + 1, start_pt, end_pt)

    def _apply(self, view, src, change_count, start_pt, end_pt,
               new_text, engine_name):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_fmt", u"cancelled: buffer changed")
                return
            if new_text == src:
                view.set_status("ca_fmt", u"no changes in selection")
                return
            delta = len(new_text) - len(src)
            view.run_command("ca_format_apply", {"text": new_text})
            size = view.size()
            new_start = min(max(start_pt, 0), size)
            new_end = min(max(end_pt + delta, new_start), size)
            view.sel().clear()
            view.sel().add(sublime.Region(new_start, new_end))
            view.show_at_center(new_start)
            view.set_status(
                "ca_fmt", u"formatted selection with %s" % engine_name)
        except Exception as e:
            _log_error("format selection apply", e)


# ---------------------------------------------------------------------------
# F12 跳转定义
# ---------------------------------------------------------------------------

# ca_engine 的定义类型标签以英文为主，中文按 display_language 还原
_DEF_LABEL_ZH = {
    u"type definition": u"类型定义",
    u"macro definition": u"宏定义",
    u"type alias": u"类型别名",
    u"function definition": u"函数定义",
    u"variable definition": u"变量定义",
}


def _goto_label(label, extra):
    lang = _display_language()
    if lang == "en":
        return label
    zh = extra.get("label_zh") or _DEF_LABEL_ZH.get(label, label)
    if lang == "both":
        return u"%s / %s" % (label, zh)
    return zh


def _goto_detail(detail, extra):
    lang = _display_language()
    zh = extra.get("detail_zh")
    if not zh or lang == "en":
        return detail
    if lang == "both":
        return u"%s / %s" % (detail, zh)
    return zh


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
            view.set_status("ca_goto", u"no valid identifier under the cursor")
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

        # include 搜索路径: 用户设置（项目头文件）+ 编译器系统头文件路径
        inc_paths = list(_s("include_paths", []))
        system_inc = _compiler_include_dirs(cached_only=True)
        if not system_inc:
            # 缓存还没预热好：后台补一次，本次 F12 先按已有路径查找
            _warm_include_dirs_async()

        # 用高级查找函数（包含 std 库 fallback）
        candidates_raw = ca_engine.goto_definition_advanced(
            symbol, text, view, inc_paths, all_views_text,
            system_include_paths=system_inc)

        # 转换为内部 candidate 格式
        candidates = []
        for prio, src, line, col, label, detail_or_preview, extra in candidates_raw:
            extra = extra or {}
            label = _goto_label(label, extra)
            detail_or_preview = _goto_detail(detail_or_preview, extra)
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
                            u"definition of '%s' not found "
                            u"(local files and standard library)" % symbol)
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
                                    u"cannot open '%s'" % path)
                return
            # 3. 标准库符号提示（无路径可打开）
            if src in ("std_symbol", "std_header"):
                # 标准库符号只给提示，没有可跳转的本地位置
                return

        if len(candidates) == 1:
            jump(0)
            c = candidates[0]
            src_label = {
                "local": u"This file", "open": u"Open file",
                "file": u"Local header", "std_symbol": u"Standard library",
                "std_header": u"System header",
                "system_header_path": u"System header"
            }.get(c[6], c[6])
            view.set_status("ca_goto",
                            u"jumped to '%s' (%s)" % (symbol, src_label))
            return

        shown = []
        for vid, path, line, col, label, preview, src, prio in candidates:
            src_label = {
                "local": u"This file", "open": u"Open file",
                "file": u"Local header", "std_symbol": u"Standard library",
                "std_header": u"System header",
                "system_header_path": u"System header"
            }.get(src, src)
            if path:
                where = os.path.basename(path)
            else:
                where = u"<unsaved>"
            shown.append([u"%s · %s · %s:%d" % (label, src_label, where, line),
                          preview])

        def on_done(idx):
            if idx >= 0:
                jump(idx)

        window.show_quick_panel(shown, on_done, sublime.MONOSPACE_FONT)


# ---------------------------------------------------------------------------
# clangd 代码操作 / 重命名 / 查找引用
# ---------------------------------------------------------------------------

def _clangd_client_for(view, status_key):
    """取得已就绪的 clangd 客户端；不可用时在状态栏说明原因。"""
    if not _s("enable_clangd_engine", True):
        view.set_status(status_key, u"clangd engine is disabled")
        return None
    if not view.file_name():
        view.set_status(status_key,
                        u"save the file before using clangd features")
        return None
    client = _get_clangd_client(view)
    if client is None or not client.is_ready():
        view.set_status(status_key, u"clangd is not ready")
        return None
    return client


def _read_text_file(path):
    """读取 UTF-8 文本文件，保留 BOM 信息；无法解码时抛出异常。

    工作区编辑会写回文件，因此这里不做替换字符解码，避免破坏非
    UTF-8 源文件。引用预览会捕获异常并显示空行。
    """
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    return data.decode("utf-8"), "utf-8"


def _newline_style(text):
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _find_open_view(path):
    """在所有窗口中查找已打开的视图；找不到返回 None。"""
    if not path:
        return None
    for window in sublime.windows():
        try:
            view = window.find_open_file(path)
        except Exception:
            view = None
        if view is not None:
            return view
    return None


def _on_clangd_apply_edit(params, respond):
    """clangd 读线程调用：把 workspace/applyEdit 排到 UI 线程执行。

    clangd 的 ``clangd.applyTweak`` 等命令通过这个反向请求落地编辑；
    必须回复 ``{"applied": bool}``，否则 executeCommand 会报错。
    """
    edit = params.get("edit") if isinstance(params, dict) else None
    if not isinstance(edit, dict):
        respond(False, "missing edit")
        return

    def apply():
        try:
            changed, edits, error = _apply_workspace_edit(
                None, edit, None)
            if error:
                respond(False, error)
                return
            respond(True)
            try:
                window = sublime.active_window()
                view = window.active_view() if window is not None else None
                if view is not None:
                    view.set_status(
                        "ca_action", u"applied clangd edit (%d file(s), "
                        u"%d edit(s))" % (changed, edits))
            except Exception:
                pass
        except Exception as e:
            _log_error("apply clangd edit", e)
            try:
                respond(False, "apply failed")
            except Exception:
                pass

    sublime.set_timeout(apply, 0)


def _apply_workspace_edit(view, edit, change_count):
    """应用 clangd 的 WorkspaceEdit。

    返回 ``(changed_files, edit_count, error)``。打开的文件整缓冲区替换
    （一次 undo），未打开的文件直接写回磁盘；包含文件创建/改名/删除的
    复杂重构暂不支持，会整体拒绝，避免只应用一半。所有目标先完成读取
    与编辑计算，再统一写回，避免解码失败时只改了一部分文件。
    """
    parsed = ca_clangd.parse_workspace_edit(edit)
    if parsed["resource_ops"]:
        return (0, 0, u"this action needs unsupported file operations")
    files = parsed["files"]
    if not files:
        return (0, 0, u"clangd returned no edits")
    if view is not None and change_count is not None:
        if not view.is_valid() or view.change_count() != change_count:
            return (0, 0, u"cancelled: buffer changed")

    pending_views = []
    pending_files = []
    for uri, edits in files:
        path = ca_clangd.uri_to_path(uri)
        if not path:
            continue
        target = _find_open_view(path)
        if target is not None:
            try:
                text = target.substr(sublime.Region(0, target.size()))
                new_text = ca_clangd.apply_text_edits(text, edits)
            except Exception as e:
                _log_error("workspace edit view", e)
                return (0, 0, u"could not apply clangd edits")
            if new_text != text:
                pending_views.append((target, new_text, len(edits)))
        else:
            try:
                text, encoding = _read_text_file(path)
                newline = _newline_style(text)
                normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                new_text = ca_clangd.apply_text_edits(normalized, edits)
                if new_text == normalized:
                    continue
                if newline != "\n":
                    new_text = new_text.replace("\n", newline)
                data = new_text.encode(encoding)
            except Exception as e:
                _log_error("workspace edit file", e)
                return (0, 0, u"could not read or write %s"
                        % os.path.basename(path))
            pending_files.append((path, data, len(edits)))

    changed_files = 0
    edit_count = 0
    for target, new_text, count in pending_views:
        try:
            target.run_command("ca_format_apply", {"text": new_text})
            changed_files += 1
            edit_count += count
        except Exception as e:
            _log_error("workspace edit apply view", e)
    for path, data, count in pending_files:
        try:
            with open(path, "wb") as f:
                f.write(data)
            changed_files += 1
            edit_count += count
        except Exception as e:
            _log_error("workspace edit apply file", e)
    return (changed_files, edit_count, None)


def _apply_edit_status(view, edit, change_count, status_key, title):
    """应用 WorkspaceEdit 并写状态栏，供代码操作和重命名共用。"""
    changed, edits, error = _apply_workspace_edit(
        view, edit, change_count)
    if error:
        view.set_status(status_key, error)
        return
    if changed == 0:
        view.set_status(status_key, u"no changes from: %s" % title)
        return
    view.set_status(
        status_key, u"applied '%s' (%d file(s), %d edit(s))"
        % (title, changed, edits))


def _location_start(loc):
    rng = loc.get("range") if isinstance(loc, dict) else None
    start = rng.get("start") if isinstance(rng, dict) else None
    if not isinstance(start, dict):
        return (0, 0)
    try:
        return (int(start.get("line", 0) or 0),
                int(start.get("character", 0) or 0))
    except Exception:
        return (0, 0)


def _location_preview(loc, cache):
    """取引用所在行的文本作为 quick panel 的第二行。"""
    path = ca_clangd.uri_to_path(loc.get("uri"))
    line0, _ = _location_start(loc)
    target = _find_open_view(path)
    if target is not None:
        try:
            pt = target.text_point(line0, 0)
            return target.substr(target.line(pt)).strip()[:160]
        except Exception:
            pass
    if path not in cache:
        try:
            text, _ = _read_text_file(path)
            cache[path] = text.split("\n")
        except Exception:
            cache[path] = []
    lines = cache[path]
    if 0 <= line0 < len(lines):
        return lines[line0].strip()[:160]
    return ""


def _jump_to_location(window, loc):
    """在已打开视图或新标签页中跳到引用位置。"""
    if window is None:
        return
    path = ca_clangd.uri_to_path(loc.get("uri"))
    if not path:
        return
    line0, char0 = _location_start(loc)
    target = _find_open_view(path)
    if target is None:
        window.open_file("%s:%d:%d" % (path, line0 + 1, char0 + 1),
                         sublime.ENCODED_POSITION)
        return
    try:
        text = target.substr(sublime.Region(0, target.size()))
        point = ca_clangd.offset_from_position(
            text, {"line": line0, "character": char0})
        target_window = target.window()
        if target_window is not None:
            target_window.focus_view(target)
        target.sel().clear()
        target.sel().add(sublime.Region(point, point))
        target.show_at_center(point)
    except Exception as e:
        _log_error("jump to reference", e)


def _find_workspace_edit(value, depth=0):
    """在命令参数里递归查找 WorkspaceEdit（兼容 clangd.applyFix）。"""
    if depth > 4:
        return None
    if isinstance(value, dict):
        if "changes" in value or "documentChanges" in value:
            return value
        for item in value.values():
            found = _find_workspace_edit(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_workspace_edit(item, depth + 1)
            if found is not None:
                return found
    return None


class CaCodeActionsCommand(sublime_plugin.TextCommand):
    """列出光标/选区处的 clangd 代码操作并应用。"""

    def run(self, edit):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_action",
                            u"only C/C++ files are supported")
            return
        client = _clangd_client_for(view, "ca_action")
        if client is None:
            return
        selections = view.sel()
        if not selections:
            return
        region = selections[0]
        text = view.substr(sublime.Region(0, view.size()))
        if len(text) > _CLANGD_MAX_CHARS:
            view.set_status("ca_action", u"file is too large for clangd")
            return
        fname = view.file_name()
        start, end = region.begin(), region.end()
        change_count = view.change_count()
        view.set_status("ca_action", u"loading code actions...")

        def worker():
            try:
                actions = client.code_actions(
                    fname, text, start, end, timeout=2.5)
            except Exception as e:
                _log_error("code actions", e)
                actions = None
            sublime.set_timeout(
                lambda: self._show(view, actions, change_count, client), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, view, actions, change_count, client):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_action", u"cancelled: buffer changed")
                return
            if actions is None:
                view.set_status(
                    "ca_action", u"clangd did not return code actions")
                return
            if not actions:
                view.set_status(
                    "ca_action", u"no code actions at this position")
                return
            items = []
            for action in actions:
                note = action.get("kind") or ""
                if action.get("preferred"):
                    note = (note + u" · preferred").strip(u" ·")
                if action.get("disabled"):
                    note = u"unavailable: " + action["disabled"]
                items.append([action.get("title") or "", note])

            def on_done(idx):
                if idx >= 0:
                    self._execute(view, actions[idx], change_count, client)

            window = view.window()
            if window is not None:
                window.show_quick_panel(
                    items, on_done, sublime.MONOSPACE_FONT)
        except Exception as e:
            _log_error("show code actions", e)

    def _execute(self, view, action, change_count, client):
        if action.get("disabled"):
            view.set_status(
                "ca_action", u"unavailable: " + action["disabled"])
            return
        title = (action.get("title") or u"code action").splitlines()[0]
        edit = action.get("edit")
        if edit:
            _apply_edit_status(
                view, edit, change_count, "ca_action", title)
            return
        command = action.get("command")
        if command:
            self._run_command(view, command, change_count, title, client)
            return
        view.set_status("ca_action", u"resolving code action...")
        raw = action.get("action") or {}

        def worker():
            try:
                resolved = client.resolve_code_action(raw, timeout=2.5)
            except Exception as e:
                _log_error("resolve code action", e)
                resolved = None
            sublime.set_timeout(
                lambda: self._after_resolve(
                    view, resolved, title, change_count, client), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _after_resolve(self, view, resolved, title, change_count, client):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_action", u"cancelled: buffer changed")
                return
            if not resolved:
                view.set_status(
                    "ca_action", u"clangd could not resolve this action")
                return
            edit = resolved.get("edit")
            if edit:
                _apply_edit_status(
                    view, edit, change_count, "ca_action", title)
                return
            command = resolved.get("command")
            if command:
                self._run_command(
                    view, command, change_count, title, client)
                return
            view.set_status("ca_action", u"this action returned no edits")
        except Exception as e:
            _log_error("apply resolved code action", e)

    @staticmethod
    def _run_command(view, command, change_count, title, client):
        if not isinstance(command, dict):
            view.set_status("ca_action", u"unsupported code action command")
            return
        name = command.get("command") or u""
        arguments = command.get("arguments") or []
        edit = _find_workspace_edit(arguments)
        if edit is not None:
            _apply_edit_status(
                view, edit, change_count, "ca_action", title)
            return
        if not name or client is None:
            view.set_status(
                "ca_action", u"unsupported clangd command: %s" % name)
            return
        # clangd.applyTweak 等命令通过 workspace/executeCommand 执行，
        # 编辑由服务端反向请求 workspace/applyEdit 送回。
        view.set_status("ca_action", u"running %s..." % name)

        def worker():
            try:
                result, error = client.execute_command(
                    name, arguments, timeout=15.0)
            except Exception as e:
                _log_error("execute code action command", e)
                result, error = None, u"command failed"
            sublime.set_timeout(
                lambda: CaCodeActionsCommand._after_command(
                    view, result, error, title), 0)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _after_command(view, result, error, title):
        try:
            if not view.is_valid():
                return
            if error:
                view.set_status(
                    "ca_action", u"clangd command failed: %s" % error)
            elif result:
                view.set_status(
                    "ca_action", u"clangd command applied: %s" % title)
            else:
                view.set_status(
                    "ca_action", u"clangd command finished: %s" % title)
        except Exception as e:
            _log_error("code action command finished", e)


class CaRenameCommand(sublime_plugin.TextCommand):
    """用 clangd 做语义重命名（跨文件，含头文件）。"""

    def run(self, edit):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_rename",
                            u"only C/C++ files are supported")
            return
        client = _clangd_client_for(view, "ca_rename")
        if client is None:
            return
        selections = view.sel()
        if not selections:
            return
        point = selections[0].begin()
        word = view.substr(view.word(point)).strip()
        if not re.match(r"^[A-Za-z_]\w*$", word):
            view.set_status(
                "ca_rename", u"place the cursor on an identifier")
            return
        window = view.window()
        if window is None:
            return
        change_count = view.change_count()

        def on_done(new_name):
            new_name = (new_name or "").strip()
            if not new_name or new_name == word:
                return
            if not re.match(r"^[A-Za-z_]\w*$", new_name):
                view.set_status(
                    "ca_rename", u"invalid C++ identifier: %s" % new_name)
                return
            if view.change_count() != change_count:
                view.set_status(
                    "ca_rename", u"buffer changed; try rename again")
                return
            self._start(view, client, point, word, new_name, change_count)

        window.show_input_panel(
            u"Rename '%s' to:" % word, word, on_done, None, None)

    @staticmethod
    def _start(view, client, point, old_name, new_name, change_count):
        fname = view.file_name()
        text = view.substr(sublime.Region(0, view.size()))
        if len(text) > _CLANGD_MAX_CHARS:
            view.set_status("ca_rename", u"file is too large for clangd")
            return
        view.set_status("ca_rename", u"renaming...")

        def worker():
            try:
                edit, error = client.rename(
                    fname, text, point, new_name, timeout=3.0)
            except Exception as e:
                _log_error("rename", e)
                edit, error = None, u"rename failed"
            sublime.set_timeout(
                lambda: CaRenameCommand._finish(
                    view, edit, error, old_name, new_name, change_count), 0)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _finish(view, edit, error, old_name, new_name, change_count):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_rename", u"cancelled: buffer changed")
                return
            if error:
                view.set_status(
                    "ca_rename", u"rename failed: %s" % error)
                return
            if not edit:
                view.set_status("ca_rename", u"clangd returned no edits")
                return
            window = view.window()
            if window is None:
                return
            changed, edits, err = _apply_workspace_edit(
                view, edit, change_count)
            if err:
                view.set_status("ca_rename", err)
                return
            view.set_status(
                "ca_rename", u"renamed '%s' to '%s' (%d file(s), %d edit(s))"
                % (old_name, new_name, changed, edits))
        except Exception as e:
            _log_error("rename apply", e)


class CaFindReferencesCommand(sublime_plugin.TextCommand):
    """用 clangd 查找符号引用，并在 quick panel 中选择跳转。"""

    def run(self, edit):
        view = self.view
        if not _is_cpp(view):
            view.set_status("ca_refs",
                            u"only C/C++ files are supported")
            return
        client = _clangd_client_for(view, "ca_refs")
        if client is None:
            return
        selections = view.sel()
        if not selections:
            return
        point = selections[0].begin()
        text = view.substr(sublime.Region(0, view.size()))
        if len(text) > _CLANGD_MAX_CHARS:
            view.set_status("ca_refs", u"file is too large for clangd")
            return
        fname = view.file_name()
        change_count = view.change_count()
        view.set_status("ca_refs", u"finding references...")

        def worker():
            try:
                refs = client.references(
                    fname, text, point, include_declaration=True,
                    timeout=2.5)
            except Exception as e:
                _log_error("find references", e)
                refs = None
            sublime.set_timeout(
                lambda: self._show(view, refs, change_count), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, view, refs, change_count):
        try:
            if not view.is_valid():
                return
            if view.change_count() != change_count:
                view.set_status("ca_refs", u"cancelled: buffer changed")
                return
            if refs is None:
                view.set_status(
                    "ca_refs", u"clangd did not return references")
                return
            # 去重：同一文件同一位置只显示一次
            seen = set()
            unique = []
            for loc in refs:
                key = (loc.get("uri"),
                       _location_start(loc))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(loc)
            if not unique:
                view.set_status("ca_refs", u"no references found")
                return
            shown = unique[:1000]
            cache = {}
            items = []
            for loc in shown:
                path = ca_clangd.uri_to_path(loc.get("uri"))
                line0, char0 = _location_start(loc)
                where = os.path.basename(path) or loc.get("uri") or u"?"
                items.append([
                    u"%s:%d:%d" % (where, line0 + 1, char0 + 1),
                    _location_preview(loc, cache),
                ])

            def on_done(idx):
                if idx >= 0:
                    _jump_to_location(view.window(), shown[idx])

            window = view.window()
            if window is not None:
                window.show_quick_panel(
                    items, on_done, sublime.MONOSPACE_FONT)
            suffix = u"" if len(unique) <= len(shown) else \
                u" (showing first %d)" % len(shown)
            view.set_status(
                "ca_refs", u"%d reference(s)%s" % (len(unique), suffix))
        except Exception as e:
            _log_error("show references", e)
