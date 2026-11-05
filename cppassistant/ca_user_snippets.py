# -*- coding: utf-8 -*-
"""用户代码片段加载器。

扫描 Sublime 全部 .sublime-snippet 资源，筛选 C/C++ 作用域的片段，
供补全弹窗把它们排在最前面（用户自己的肌肉记忆片段 > 内置片段 >
语义补全）。

缓存策略：结果缓存 TTL 60 秒；片段文件极少变化，重新扫描成本低
（find_resources 为内存操作）。

模板包（templates/<pack>/）必须通过 “Install Template Pack” 安装到
Packages/User/CppAssistantTemplates/<pack>/ 后才参与补全；未安装的
内置模板不会出现在弹窗里。带 .disabled 标记的已安装包也会被跳过。
"""

import os
import re
import time
import xml.etree.ElementTree as ET

try:
    import sublime
except ImportError:  # 允许在 Sublime 之外做单元测试
    sublime = None

_TTL = 60.0
_cache = {"t": 0.0, "items": None}

# C/C++ 作用域匹配：source.c / source.cc / source.cpp / source.c++ / source.c++11...
_CPP_SCOPE_RE = re.compile(
    r"source\.(?:c(?:\+\+\d*)?|cc|cpp|cxx)(?=$|[\s,])")

# 内置模板包目录名；这些资源只有安装后才应参与补全。
_BUNDLED_PACK_IDS = frozenset((
    "geometry", "graph", "ds", "strings", "math",
))

_TEMPLATE_INSTALL_DIR = "CppAssistantTemplates"
_PACK_LABELS = {
    "geometry": ("几何模板", "Geometry template"),
    "graph": ("图论模板", "Graph template"),
    "ds": ("数据结构模板", "Data structures template"),
    "strings": ("字符串模板", "Strings template"),
    "math": ("数学模板", "Math template"),
}
_PACK_LABEL_FALLBACK = ("模板包", "template pack")


def _norm_resource(path):
    return str(path or "").replace("\\", "/")


def _resource_parts(path):
    return [p for p in _norm_resource(path).split("/") if p]


def _source_info(res):
    """Return ``(zh, en)`` origin labels for a snippet resource."""
    parts = _resource_parts(res)
    for i, part in enumerate(parts):
        if part == _TEMPLATE_INSTALL_DIR and i + 1 < len(parts):
            pack = parts[i + 1]
            zh, en = _PACK_LABELS.get(pack,
                                      (pack.title() + u" 模板包",
                                       pack.title() + u" template pack"))
            return zh, en
    if "User" in parts:
        return u"用户片段", u"User snippet"
    return u"包片段", u"Package snippet"


def _is_bundled_template(resource):
    """True for ``Packages/<pkg>/templates/<pack>/...`` resources.

    Those files are the installer's source data.  They must not be picked
    up directly, otherwise every pack would be active before installation
    and disabling a pack would not remove its snippets.
    """
    parts = _resource_parts(resource)
    for i in range(len(parts) - 2):
        if parts[i] == "templates" and parts[i + 1] in _BUNDLED_PACK_IDS:
            return True
    return False


def _disabled_pack_names():
    if sublime is None:
        return set()
    root = os.path.join(sublime.packages_path(), "User",
                        _TEMPLATE_INSTALL_DIR)
    names = set()
    try:
        entries = os.listdir(root)
    except OSError:
        return names
    for name in entries:
        try:
            if os.path.exists(os.path.join(root, name, ".disabled")):
                names.add(name)
        except OSError:
            continue
    return names


def _is_disabled_pack(resource, disabled):
    if not disabled:
        return False
    parts = _resource_parts(resource)
    for i, part in enumerate(parts):
        if part == _TEMPLATE_INSTALL_DIR and i + 1 < len(parts):
            return parts[i + 1] in disabled
    return False


def _parse_snippet_resource(res):
    """解析单个 .sublime-snippet 资源，返回条目字典或 None。"""
    try:
        xml_text = sublime.load_resource(res)
        root = ET.fromstring(xml_text.encode("utf-8"))
    except Exception:
        return None
    if root.tag != "snippet":
        return None
    try:
        scope = (root.findtext("scope") or "").strip()
        trigger = (root.findtext("tabTrigger") or "").strip()
        content = root.findtext("content") or ""
        desc = (root.findtext("description") or "").strip() or trigger
    except Exception:
        return None
    if not trigger or not content:
        return None
    if not _CPP_SCOPE_RE.search(scope):
        return None
    src_zh, src_en = _source_info(res)
    base = desc
    return {
        "trigger": trigger,
        "insert": content,
        "annotation": base + u" · " + src_zh,
        "annotation_en": base + u" · " + src_en,
        "kind": "s",
        "detail": desc,
        "detail_en": desc,
        "snippet": True,
        "source": src_en,
    }


def clear_cache():
    """Drop the cached snippet list (called after install/enable/disable)."""
    _cache["t"] = 0.0
    _cache["items"] = None


def get_cpp_snippets(force_reload=False):
    """返回用户 C/C++ 片段条目列表（带 TTL 缓存）。"""
    if sublime is None:
        return []
    now = time.time()
    if (not force_reload and _cache["items"] is not None
            and now - _cache["t"] < _TTL):
        return _cache["items"]
    items = []
    disabled = _disabled_pack_names()
    try:
        for res in sublime.find_resources("*.sublime-snippet"):
            if _is_bundled_template(res):
                continue
            if _is_disabled_pack(res, disabled):
                continue
            item = _parse_snippet_resource(res)
            if item is not None:
                items.append(item)
    except Exception:
        pass
    # 触发器短的排前面（如 us 排在 using-namespace-std 前）
    items.sort(key=lambda d: (len(d["trigger"]), d["trigger"]))
    _cache["t"] = now
    _cache["items"] = items
    return items
