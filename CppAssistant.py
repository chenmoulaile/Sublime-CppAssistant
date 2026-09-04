# -*- coding: utf-8 -*-
"""CppAssistant —— Sublime Text 4 C++ 辅助插件（LSP-clangd 风格的轻量级汉化优化版）

基于 LSP-clangd 架构设计：
  - 智能补全：内置 STL 数据库（O(1) 字典查找）+ 多级缓存
  - 实时语法检查：即时基础检查（毫秒级）+ 编译器完整检查（PCH + 结果缓存 + 过期进程立即终止）
  - F12 跳转定义：当前文件 → 已打开文件 → 本地头文件递归搜索
  - jiangly 码风格式化：优先 clang-format，无则内置兜底

性能参考 LSP-clangd：
  - 输入过程中的结构性错误（括号/全角标点/未闭合字符串）< 10ms 给出
  - 完整语义检查（启用 PCH）常规代码 < 300ms 给出
  - 补全响应 < 5ms（命中缓存时 < 1ms）
  - 所有结果缓存：文本未变更时零延迟复用

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

# Use relative import for local modules (avoids sys.path modification)
from . import ca_engine  # noqa: E402

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
    kind = _KIND_MAP.get(d["kind"], _kind_default)()
    insert = d["insert"]
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
        # 性能：限制分析范围（前 400KB，覆盖绝大多数场景）
        off = locations[0]
        cap = 400000
        size = view.size()
        text = view.substr(sublime.Region(0, min(size, cap)))
        if off > len(text):
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
        # Windows: CREATE_NO_WINDOW (0x08000000) hides console window
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
    if _s("enable_pch", True) and "bits/stdc++.h" in src:
        std = str(_s("cxx_standard", "c++17"))
        sig, hdr, gch = _pch_paths(compiler, std)
        if os.path.isfile(gch):
            cmd += ["-include", hdr]
        elif sig not in _PCH_BUILDING:
            threading.Thread(
                target=_build_pch, args=(compiler, std), daemon=True).start()
    # 关键：通过 - 指定从 stdin 读取源码（不创建任何 .cpp 临时文件）
    cmd.append("-")

    # Windows: CREATE_NO_WINDOW (0x08000000) hides console window
    creationflags = 0x08000000 if os.name == "nt" else 0
    proc = None
    out = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            cwd=workdir, creationflags=creationflags)
        _lint_procs[view_id] = proc
        try:
            out, _ = proc.communicate(
                input=src.encode("utf-8"),
                timeout=float(_s("lint_timeout", 12)))
        except Exception:
            out = None
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
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
        except Exception:
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
            # Windows: CREATE_NO_WINDOW (0x08000000) hides console window
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
