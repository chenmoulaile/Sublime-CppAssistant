# -*- coding: utf-8 -*-
"""CppAssistant —— Sublime Text 4 C++ 辅助插件（jiangly 码风）

功能：
  1. 智能代码补全：STL 函数/容器成员，自动识别 using namespace std;
     未声明时自动补 std:: 前缀，已声明则只给裸名。
  2. 实时语法检查（三级加速）：
     a. 即时基础检查 —— 纯 Python 词法扫描，毫秒级反馈括号配平、
        全角标点、未闭合字符串等问题；
     b. 编译器检查 —— g++/clang++ -fsyntax-only + PCH 预编译头，
        新输入到来时立刻终止过期进程，绝不排队堆积；
     c. 结果缓存 —— 文本与设置未变时直接复用上次诊断，零延迟刷新。
     报错信息全部翻译成中文：波浪线标注 + 行内幽灵提示 + 状态栏统计。
  3. F12 跳转定义：本文件 -> 已打开文件 -> 本地头文件递归搜索。
  4. jiangly 码风格式化：优先 clang-format（内置 jiangly 风格配置），
     无 clang-format 时使用内置兜底格式化器。

兼容 Sublime Text 4 的 Python 3.3 插件宿主。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zlib

import sublime
import sublime_plugin

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import ca_engine  # noqa: E402

_SETTINGS = "CppAssistant.sublime-settings"
_settings_obj = None
_compiler_cache = {"path": None, "done": False}

CPP_SCOPE = "source.c++, source.c"

# 视图相关运行时状态 -------------------------------------------------------
_lint_timers = {}      # view_id -> threading.Timer（编译器检查防抖）
_basic_timers = {}     # view_id -> threading.Timer（即时基础检查微防抖）
_phantom_sets = {}     # view_id -> PhantomSet
_diag_store = {}       # view_id -> [str]
_temp_files = {}       # view_id -> [path]
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


def plugin_unloaded():
    if _settings_obj is not None:
        _settings_obj.clear_on_change("cppassistant")


def _s(key, default=None):
    if _settings_obj is None:
        return default
    return _settings_obj.get(key, default)


def _is_cpp(view):
    return view.score_selector(0, CPP_SCOPE) > 0


# ---------------------------------------------------------------------------
# 补全
# ---------------------------------------------------------------------------

def _kind_default():
    return sublime.KIND_AMBIGUOUS


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
    kind = _KIND_MAP.get(d["kind"], _kind_default)()
    insert = d["insert"]
    # 含 $ 占位符或多行内容的一律按 snippet 插入，
    # 否则 ${1:...} 会原样插入且光标停在末尾
    is_snippet = ("\n" in insert) or ("$" in insert)
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


class CaEventListener(sublime_plugin.EventListener):
    # ---- 补全 ----
    def on_query_completions(self, view, prefix, locations):
        if not _s("enable_completions", True):
            return None
        if not _is_cpp(view):
            return None
        off = locations[0]
        cap = 400000
        size = view.size()
        text = view.substr(sublime.Region(0, min(size, cap)))
        if off > len(text):
            return None
        ctx = ca_engine.detect_context(text, off)
        if ctx[0] == "code" and not prefix and ctx[2] == "none":
            # 空前缀且非成员访问时不打扰（Ctrl+Space 也不会刷屏）
            return None
        try:
            results = ca_engine.analyze(
                text, off,
                cache_key=view.buffer_id(),
                cache_version=view.change_count())
        except Exception:
            return None
        if not results:
            return None
        items = [_make_item(d) for d in results]
        # 不抑制 ST 自身的单词补全，与本地变量补全共存
        return sublime.CompletionList(items, 0)

    # ---- 语法检查触发 ----
    def on_load_async(self, view):
        self._maybe_lint(view)

    def on_pre_save(self, view):
        if _s("format_on_save", False) and _is_cpp(view):
            view.run_command("ca_format_document")

    def on_post_save_async(self, view):
        self._maybe_lint(view)

    def on_modified_async(self, view):
        self._maybe_lint(view, debounce=True)

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
                0.06, lambda: sublime.set_timeout(
                    lambda: run_basic_check(view), 0))
            btmr.daemon = True
            btmr.start()
            _basic_timers[vid] = btmr
        # 第二级：编译器完整检查（防抖）
        t = _lint_timers.pop(vid, None)
        if t is not None:
            t.cancel()
        delay = float(_s("lint_debounce", 0.4)) if debounce else 0.0
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
        for timers in (_lint_timers, _basic_timers):
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
        for tp in _temp_files.pop(vid, []):
            try:
                os.remove(tp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 预编译头（PCH）加速：bits/stdc++.h 的解析结果只算一次
# ---------------------------------------------------------------------------

_PCH_ROOT = os.path.join(tempfile.gettempdir(), "CppAssistantPCH")
_PCH_READY = set()     # 已就绪的 pch 签名
_PCH_BUILDING = set()  # 正在构建中的签名
_PCH_LOCK = threading.Lock()

PCH_HEADER_TEXT = (
    "#ifndef CA_ASSISTANT_PCH_H\n"
    "#define CA_ASSISTANT_PCH_H\n"
    "#include <bits/stdc++.h>\n"
    "#endif\n"
)


def _pch_paths(compiler, std):
    sig = re.sub(r"[^\w]", "_", os.path.normcase(compiler)) + "_" + std
    d = os.path.join(_PCH_ROOT, sig)
    hdr = os.path.join(d, "ca_pch.h")
    return sig, hdr, hdr + ".gch"


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
        creationflags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.Popen(
            [compiler, "-std=" + str(std), "-x", "c++-header",
             hdr, "-o", gch],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags)
        proc.wait(timeout=180)
        if proc.returncode == 0 and os.path.isfile(gch):
            _PCH_READY.add(sig)
    except Exception:
        pass
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


def _cleanup_temps(view_id, keep_last=1):
    """清理本视图残留的临时文件，只保留最近 keep_last 个。"""
    paths = _temp_files.get(view_id) or []
    if len(paths) <= keep_last:
        return
    for tp in paths[:-keep_last]:
        try:
            os.remove(tp)
        except OSError:
            pass
    _temp_files[view_id] = paths[-keep_last:]


def _settings_sig(compiler):
    """影响诊断结果的设置签名，用于结果缓存失效判断。"""
    return (str(_s("cxx_standard", "c++17")),
            repr(_s("compiler_extra_args", [])),
            repr(_s("include_paths", [])),
            bool(_s("enable_pch", True)),
            compiler or "")


def _lint_work(view_id, src, workdir, fname, gen, ckey):
    """工作线程：写临时文件并调用编译器（被新请求取代时会被立刻终止）。"""
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
            _LINT_CACHE[view_id] = (ckey, diags)
            render_diagnostics(view_id)

        sublime.set_timeout(done_nc, 0)
        return
    base = os.path.basename(fname) if fname else "untitled_%d.cpp" % view_id
    stem = os.path.splitext(base)[0] or "untitled"
    # 每代使用独立临时文件，避免与被终止的旧进程产生写入竞争
    tmp = os.path.join(workdir, "_ca_lint_%d_%s_%d.cpp" % (view_id, stem, gen))
    _temp_files.setdefault(view_id, []).append(tmp)
    try:
        with open(tmp, "wb") as f:
            f.write(src.encode("utf-8"))
    except OSError:
        return
    cmd = [compiler, "-fsyntax-only",
           "-std=" + str(_s("cxx_standard", "c++17")),
           "-Wall", "-fno-diagnostics-show-caret"]
    cmd += [str(a) for a in _s("compiler_extra_args", [])]
    for inc in _s("include_paths", []):
        cmd.append("-I" + str(inc))
    # 仅当源码确实包含 bits/stdc++.h 时才挂 PCH（避免掩盖漏写头文件的错误）
    if _s("enable_pch", True) and "bits/stdc++.h" in src:
        std = str(_s("cxx_standard", "c++17"))
        sig, hdr, gch = _pch_paths(compiler, std)
        if os.path.isfile(gch):
            cmd += ["-include", hdr, "-I", os.path.dirname(hdr)]
        elif sig not in _PCH_BUILDING:
            threading.Thread(
                target=_build_pch, args=(compiler, std), daemon=True).start()
    cmd.append(tmp)

    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = None
    out = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                cwd=workdir, creationflags=creationflags)
        _lint_procs[view_id] = proc
        out, _ = proc.communicate(timeout=float(_s("lint_timeout", 12)))
    except Exception:
        out = None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        if _lint_procs.get(view_id) is proc:
            _lint_procs.pop(view_id, None)

    if out is None:
        # 超时或被新检查取代：保留旧标记，不清屏
        _cleanup_temps(view_id)
        return
    text = _decode(out)
    entries = ca_engine.parse_compiler_output(text)
    norm = os.path.normcase(os.path.normpath(tmp))
    diags = []
    for e in entries:
        ef = os.path.normcase(os.path.normpath(e["file"]))
        if ef != norm:
            continue
        e["tier"] = "compiler"
        diags.append(e)

    def done():
        if gen != _lint_gen.get(view_id):
            return
        st = _lint_state.setdefault(view_id, {})
        st["compiler"] = diags
        _LINT_CACHE[view_id] = (ckey, diags)
        render_diagnostics(view_id)

    sublime.set_timeout(done, 0)
    _cleanup_temps(view_id)


def _decode(b):
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _basic_diags(problems):
    """把 basic_checks 的输出包装为统一诊断结构（tier=basic）。"""
    sev_map = {"error": u"错误", "warning": u"警告"}
    return [{
        "line": ln, "col": cl,
        "sev": sev_map.get(sv, sv),
        "sev_en": "error" if sv == "error" else "warning",
        "msg": msg, "zh": msg, "ctx": "", "notes": [],
        "tier": "basic",
    } for (ln, cl, sv, msg) in problems]


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

    def worker():
        try:
            problems = ca_engine.basic_checks(src)
        except Exception:
            return
        diags = _basic_diags(problems)

        def done():
            if not view.is_valid() or _basic_gen.get(vid) != gen:
                return
            st = _lint_state.setdefault(vid, {})
            st["basic"] = diags
            render_diagnostics(vid)

        sublime.set_timeout(done, 0)

    th = threading.Thread(target=worker)
    th.daemon = True
    th.start()


def run_lint(view):
    """第二级编译器完整检查：带结果缓存与过期进程终止。"""
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
        is_err = (d.get("sev_en") == "error") or (u"错误" in d.get("sev", ""))
        if is_err:
            err_regions.append(region)
            n_err += 1
        else:
            warn_regions.append(region)
            n_warn += 1

        text = d["zh"] if d.get("zh") else d["msg"]
        if d.get("tier") == "basic":
            text = u"[即时检查] " + text
        icon = u"\u2716" if is_err else u"\u26a0"
        color = "redish" if is_err else "yellowish"
        if _s("show_phantoms", True) and len(phantoms) < 40:
            body = _PHANTOM_TMPL.format(color=color, icon=icon, text=text)
            phantoms.append(sublime.Phantom(
                region, body, sublime.LAYOUT_BELOW))
        tag = u"[错误]" if is_err else u"[警告]"
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
        view.set_status("ca_diag",
                        u"\u2716 %d 错误  \u26a0 %d 警告" % (n_err, n_warn))
    elif comp or st.get("basic"):
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
            creationflags = 0x08000000 if os.name == "nt" else 0
            try:
                proc = subprocess.Popen(
                    [cf, "--assume-filename=x.cpp", "--style=" + style],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, creationflags=creationflags)
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
        # 尽量恢复视口与光标位置
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

        text = view.substr(sublime.Region(0, view.size()))
        fname = view.file_name()

        # (vid, path, line, col, label, preview)
        candidates = []
        for _, line, col, label, preview in \
                ca_engine.find_definitions(text, symbol):
            candidates.append((view.id(), fname, line, col, label, preview))

        cur_vid = view.id()
        for v in window.views():
            if v.id() == cur_vid or not _is_cpp(v):
                continue
            vtext = v.substr(sublime.Region(0, v.size()))
            for _, line, col, label, preview in \
                    ca_engine.find_definitions(vtext, symbol):
                candidates.append((v.id(), v.file_name(), line, col,
                                   label, preview))

        if fname:
            heads = []
            try:
                with open(fname, "rb") as f:
                    head_txt = f.read().decode("utf-8", "replace")
            except OSError:
                head_txt = ""
            base = os.path.dirname(fname)
            for m in re.finditer(
                    r'^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"', head_txt, re.M):
                rel = m.group(1).replace("/", os.sep)
                heads.append(os.path.normpath(os.path.join(base, rel)))
            for inc in _s("include_paths", []):
                heads.append(str(inc))
            file_hits = ca_engine.find_definitions_in_files(symbol, heads)
            for path, line, col, label, preview in file_hits:
                candidates.append((None, path, line, col, label, preview))

        # 去重
        dedup = set()
        uniq = []
        for c in candidates:
            key = (os.path.normcase(c[1]) if c[1] else c[0],
                   c[2], c[3])
            if key in dedup:
                continue
            dedup.add(key)
            uniq.append(c)
        candidates = uniq

        if not candidates:
            window.run_command("goto_definition")
            view.set_status("ca_goto",
                            u"本地未找到 '%s' 的定义，已尝试内置符号索引" % symbol)
            return

        def jump(idx):
            vid, path, line, col, _, _ = candidates[idx]
            if vid is not None and vid != cur_vid:
                tv = _view_by_id(vid)
                if tv is not None:
                    window.focus_view(tv)
                    pt = tv.text_point(line - 1, col)
                    tv.sel().clear()
                    tv.sel().add(sublime.Region(pt, pt))
                    tv.show_at_center(pt)
                return
            target_path = path or fname
            if target_path:
                window.open_file("%s:%d:%d" % (target_path, line, col + 1),
                                 sublime.ENCODED_POSITION)
            else:
                pt = view.text_point(line - 1, col)
                view.sel().clear()
                view.sel().add(sublime.Region(pt, pt))
                view.show_at_center(pt)

        if len(candidates) == 1:
            jump(0)
            view.set_status("ca_goto", u"跳转到 '%s' (%s)" %
                            (symbol, candidates[0][3]))
            return

        shown = []
        for path, line, col, label, preview in candidates:
            where = os.path.basename(path) if path else view.file_name()
            if where is None:
                where = u"<未保存>"
            shown.append([u"%s  ·  %s:%d" % (label, where, line),
                          preview])

        def on_done(idx):
            if idx >= 0:
                jump(idx)

        window.show_quick_panel(shown, on_done, sublime.MONOSPACE_FONT)
