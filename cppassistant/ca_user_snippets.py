# -*- coding: utf-8 -*-
"""用户代码片段加载器。

扫描 Sublime 全部 .sublime-snippet 资源，筛选 C/C++ 作用域的片段，
供补全弹窗把它们排在最前面（用户自己的肌肉记忆片段 > 内置片段 >
语义补全）。

缓存策略：结果缓存 TTL 60 秒；片段文件极少变化，重新扫描成本低
（find_resources 为内存操作）。
"""

import re
import time
import xml.etree.ElementTree as ET

try:
    import sublime
except ImportError:  # 允许在 Sublime 之外做单元测试
    sublime = None

_TTL = 60.0
_cache = {"t": 0.0, "items": None}

# C/C++ 作用域匹配：source.c / source.c++ / source.c++11 等，
# 排除 source.css 之类的误伤
_CPP_SCOPE_RE = re.compile(r"source\.c(?:\+\+)?(?=$|[\s,])")


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
    return {
        "trigger": trigger,
        "insert": content,
        "annotation": u"用户片段",
        "kind": "s",
        "detail": desc,
        "snippet": True,
    }


def get_cpp_snippets(force_reload=False):
    """返回用户 C/C++ 片段条目列表（带 TTL 缓存）。"""
    if sublime is None:
        return []
    now = time.time()
    if (not force_reload and _cache["items"] is not None
            and now - _cache["t"] < _TTL):
        return _cache["items"]
    items = []
    try:
        for res in sublime.find_resources("*.sublime-snippet"):
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


def invalidate_cache():
    _cache["t"] = 0.0
    _cache["items"] = None
