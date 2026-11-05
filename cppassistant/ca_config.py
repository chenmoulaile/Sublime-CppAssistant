# -*- coding: utf-8 -*-
"""设置项校验。

Sublime 对 ``.sublime-settings`` 只做 JSON 解析，非法值（``"c++2x"``、
``lint_timeout: 0``、``"lint_engine": "gcc"``）会被静默忽略，用户只会
觉得"设置没生效"。这里把常见的坑集中检查一遍，插件加载时和
``CppAssistant: Validate Settings`` 命令都会调用。

纯函数实现，不依赖 sublime，方便单元测试。
"""

import re

CXX_STANDARDS = (
    "c++98", "c++03", "c++11", "c++14", "c++17", "c++20", "c++23",
    "c++2a", "c++2b", "c++2c",
    "gnu++98", "gnu++03", "gnu++11", "gnu++14", "gnu++17", "gnu++20",
    "gnu++23", "gnu++2a", "gnu++2b", "gnu++2c",
)

LINT_ENGINES = ("compiler", "clangd", "libclang")
DISPLAY_LANGUAGES = ("en", "zh", "both")
MENU_LANGUAGES = ("en", "zh")

_STD_RE = re.compile(r"^(?:gnu\+\+|c\+\+)(?:\d{2}|2[a-c])$")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_number(out, key, value, low, high, unit=u""):
    if not _is_number(value):
        out.append({
            "key": key, "value": value,
            "en": u"%s must be a number" % key,
            "zh": u"%s 必须是数字" % key,
        })
        return
    if value < low or value > high:
        out.append({
            "key": key, "value": value,
            "en": u"%s must be between %s and %s%s"
                  % (key, low, high, unit),
            "zh": u"%s 必须在 %s~%s%s 之间" % (key, low, high, unit),
        })


def validate(data):
    """校验设置字典，返回问题列表 ``[{key, value, en, zh}]``。"""
    out = []
    if not isinstance(data, dict):
        return [{
            "key": "(root)", "value": data,
            "en": u"settings root must be a JSON object",
            "zh": u"设置文件根节点必须是 JSON 对象",
        }]

    std = data.get("cxx_standard", "c++14")
    if not isinstance(std, str) or not _STD_RE.match(std.strip()):
        out.append({
            "key": "cxx_standard", "value": std,
            "en": u"cxx_standard %r is not a C++ standard name "
                  u"(expected e.g. c++14, c++17, c++20)" % (std,),
            "zh": u"cxx_standard 的值 %r 不是合法的 C++ 标准名"
                  u"（例如 c++14、c++17、c++20）" % (std,),
        })

    engine = data.get("lint_engine", "compiler")
    if engine not in LINT_ENGINES:
        out.append({
            "key": "lint_engine", "value": engine,
            "en": u"lint_engine %r is unknown; use one of %s"
                  % (engine, u", ".join(LINT_ENGINES)),
            "zh": u"lint_engine 的值 %r 无法识别，可用：%s"
                  % (engine, u"、".join(LINT_ENGINES)),
        })

    lang = data.get("display_language", "en")
    if lang not in DISPLAY_LANGUAGES:
        out.append({
            "key": "display_language", "value": lang,
            "en": u"display_language %r is unknown; use en, zh or both"
                  % (lang,),
            "zh": u"display_language 的值 %r 无法识别，可用 en、zh、both"
                  % (lang,),
        })

    menu_lang = data.get("menu_language", "en")
    if menu_lang not in MENU_LANGUAGES:
        out.append({
            "key": "menu_language", "value": menu_lang,
            "en": u"menu_language %r is unknown; use en or zh"
                  % (menu_lang,),
            "zh": u"menu_language 的值 %r 无法识别，可用 en 或 zh"
                  % (menu_lang,),
        })

    _check_number(out, "lint_timeout", data.get("lint_timeout", 12),
                  0.5, 120, u"s")
    _check_number(out, "lint_debounce", data.get("lint_debounce", 0.1),
                  0, 5, u"s")
    _check_number(out, "indent_width", data.get("indent_width", 4), 1, 16)

    wait = data.get("clangd_completion_wait_ms", 0)
    if not _is_int(wait) or wait < 0 or wait > 2000:
        out.append({
            "key": "clangd_completion_wait_ms", "value": wait,
            "en": u"clangd_completion_wait_ms must be an integer between 0 "
                  u"and 2000 (0 = never block the editor)",
            "zh": u"clangd_completion_wait_ms 必须是 0~2000 的整数"
                  u"（0 = 从不阻塞编辑器）",
        })

    for key in ("compiler_extra_args", "include_paths", "clangd_args",
                "diagnostic_filters", "libclang_args",
                "clangd_extra_fallback_flags"):
        value = data.get(key, [])
        if not isinstance(value, list) or \
                any(not isinstance(v, str) for v in value):
            out.append({
                "key": key, "value": value,
                "en": u"%s must be a list of strings" % key,
                "zh": u"%s 必须是字符串列表" % key,
            })

    style = data.get("clang_format_style", "")
    if not isinstance(style, (str, dict)):
        out.append({
            "key": "clang_format_style", "value": style,
            "en": u"clang_format_style must be a string or an object",
            "zh": u"clang_format_style 必须是字符串或对象",
        })

    return out


def summary(problems, limit=3):
    """一行摘要，用于状态栏。"""
    if not problems:
        return u""
    head = str(problems[0].get("en") or u"")
    if len(problems) > 1:
        head += u" (+%d more)" % (len(problems) - 1)
    return head


def report_text(problems):
    """控制台/输出面板用的多行文本。"""
    lines = []
    for p in problems:
        lines.append(u"%s = %r\n    en: %s\n    zh: %s"
                     % (p.get("key"), p.get("value"), p.get("en"),
                        p.get("zh")))
    return u"\n".join(lines)
