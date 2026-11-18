# -*- coding: utf-8 -*-
"""Localized overlay for CppAssistant's own menu and command-palette entries.

Sublime Text has no menu translation API.  Menus from every package are
merged by ``id``: a later entry with the same ``id`` may override the
``caption`` of an earlier one.  Command-palette entries are merged the same
way, keyed by ``command`` + ``args`` instead of ``id``.  We use that
documented behavior to generate a tiny overlay package
(``Packages/CppAssistantLocalization``) that carries only translated
captions.  The package's real ``Main.sublime-menu`` and
``CppAssistant.sublime-commands`` stay untouched, and so do the user's own
files.

The overlay files are written even in English (as empty lists).  Rewriting
the files makes Sublime reload the menus/palette immediately, which means
switching back to English does not leave stale Chinese captions behind
until a restart.
"""

import json
import os
import shutil

try:
    import sublime
except ImportError:  # allow unit tests outside Sublime Text
    sublime = None

OVERLAY_PACKAGE = "CppAssistantLocalization"
MARKER = ".cppassistant-generated"
MENU_FILE = "Main.sublime-menu"
COMMANDS_FILE = "CppAssistant.sublime-commands"

# English caption -> Chinese caption.  Unknown captions are left untouched so
# a newly added menu item is still usable before its translation is added.
TRANSLATIONS_ZH = {
    "CppAssistant": "CppAssistant",
    "Code Actions": u"代码操作",
    "Rename Symbol": u"重命名符号",
    "Find References": u"查找引用",
    "Call Hierarchy": u"调用层级",
    "Type Hierarchy": u"类型层级",
    "Format Selection": u"格式化选中",
    "Format Document": u"格式化文档",
    "Go to Definition": u"跳转定义",
    "Show Diagnostics": u"显示诊断",
    "Copy Diagnostic": u"复制诊断",
    "Fix Full-width Punctuation": u"修复全角标点",
    "Templates": u"模板",
    "Browse Templates": u"浏览模板",
    "Install Template Pack": u"安装模板包",
    "Manage Templates": u"管理模板",
    "Create User Snippet": u"创建用户片段",
    "Manage User Snippets": u"管理用户片段",
    "Edit User Snippets": u"编辑用户片段",
    "Import Snippets": u"导入片段",
    "Validate Settings": u"校验设置",
    "Settings": u"设置",
    "Key Bindings": u"快捷键",
    "Diagnostic language": u"诊断语言",
    "Chinese": u"中文",
    "English": u"英文",
    "English + Chinese": u"英文 + 中文",
    "简体中文": u"简体中文",
    "Toggle Menu Language": u"切换菜单语言",
    "Completion engine": u"补全引擎",
    "Language": u"语言",
    "Bilingual": u"双语",
    "Navigate": u"导航",
    "Edit": u"编辑",
    "Diagnostics": u"诊断",
    "clangd engine (semantic completions)": u"clangd 引擎（语义补全）",
    "Auto (clangd when available)": u"自动（有 clangd 就用）",
    "Built-in only (pure Python, no clangd)": u"仅内置引擎（纯 Python，不启动 clangd）",
    "Built-in database (no clangd needed)": u"内置数据库（无需 clangd）",
    "Completion mode": u"补全模式",
    "LSP-clangd style (fuzzy matching)": u"LSP-clangd 风格（模糊匹配）",
    "Strict prefix (basic)": u"严格前缀（基础）",
    "Show inline error phantoms": u"显示内联错误提示",
    "Completion engine status": u"补全引擎状态",
    "Diagnostic engine": u"诊断引擎",
    "clangd diagnostics (LSP-style)": u"clangd 诊断（LSP 风格）",
    "Compiler check (g++/clang++)": u"编译器检查（g++/clang++）",
    "libclang semantic engine (PCH)": u"libclang 语义引擎（PCH）",
    "Key Bindings \u2013 User": u"用户快捷键",
}

# Full command-palette caption -> Chinese caption.  The "CppAssistant:" /
# "Preferences:" prefixes follow Sublime's own naming convention and are
# kept so captions stay searchable by package name.  Unknown captions are
# left untouched so a newly added command is still usable before its
# translation is added.  Entries keyed by "command" + "args" (e.g. the three
# "Language - ..." variants) each get their own translated caption.
COMMAND_TRANSLATIONS_ZH = {
    u"CppAssistant: Format Document (jiangly style)":
        u"CppAssistant: 格式化文档（jiangly 风格）",
    u"CppAssistant: Format Selection": u"CppAssistant: 格式化选中",
    u"CppAssistant: Go to Definition": u"CppAssistant: 跳转定义",
    u"CppAssistant: Code Actions": u"CppAssistant: 代码操作",
    u"CppAssistant: Rename Symbol": u"CppAssistant: 重命名符号",
    u"CppAssistant: Find References": u"CppAssistant: 查找引用",
    u"CppAssistant: Call Hierarchy": u"CppAssistant: 调用层级",
    u"CppAssistant: Type Hierarchy": u"CppAssistant: 类型层级",
    u"CppAssistant: Fix Full-width Punctuation": u"CppAssistant: 修复全角标点",
    u"CppAssistant: Show Diagnostics": u"CppAssistant: 显示诊断",
    u"CppAssistant: Copy Diagnostic": u"CppAssistant: 复制诊断",
    u"CppAssistant: Browse Templates": u"CppAssistant: 浏览模板",
    u"CppAssistant: Install Template Pack": u"CppAssistant: 安装模板包",
    u"CppAssistant: Manage Templates": u"CppAssistant: 管理模板",
    u"CppAssistant: Create User Snippet": u"CppAssistant: 创建用户片段",
    u"CppAssistant: Manage User Snippets": u"CppAssistant: 管理用户片段",
    u"CppAssistant: Edit User Snippets": u"CppAssistant: 编辑用户片段",
    u"CppAssistant: Import Snippets": u"CppAssistant: 导入片段",
    u"CppAssistant: Validate Settings": u"CppAssistant: 校验设置",
    u"CppAssistant: Diagnostic Language - Chinese": u"CppAssistant: 诊断语言 - 中文",
    u"CppAssistant: Diagnostic Language - English": u"CppAssistant: 诊断语言 - 英文",
    u"CppAssistant: Diagnostic Language - Both": u"CppAssistant: 诊断语言 - 双语",
    u"CppAssistant: Completion Mode - LSP-clangd Style":
        u"CppAssistant: 补全模式 - LSP-clangd 风格",
    u"CppAssistant: Completion Mode - Strict Prefix":
        u"CppAssistant: 补全模式 - 严格前缀",
    u"CppAssistant: Language - English": u"CppAssistant: 语言 - 英文",
    u"CppAssistant: Language - \u7b80\u4f53\u4e2d\u6587":
        u"CppAssistant: 语言 - \u7b80\u4f53\u4e2d\u6587",  # already Chinese
    u"CppAssistant: Language - Bilingual": u"CppAssistant: 语言 - 双语",
    u"CppAssistant: Completion Engine - clangd":
        u"CppAssistant: 补全引擎 - clangd",
    u"CppAssistant: Completion Engine - Auto": u"CppAssistant: 补全引擎 - 自动",
    u"CppAssistant: Completion Engine - Built-in only (pure Python)":
        u"CppAssistant: 补全引擎 - 仅内置（纯 Python）",
    u"CppAssistant: Completion Engine Status": u"CppAssistant: 补全引擎状态",
    u"CppAssistant: Toggle Inline Error Phantoms":
        u"CppAssistant: 切换内联错误提示",
    u"CppAssistant: Toggle Menu Language": u"CppAssistant: 切换菜单语言",
    u"CppAssistant: Diagnostic Engine - clangd":
        u"CppAssistant: 诊断引擎 - clangd",
    u"CppAssistant: Diagnostic Engine - Compiler":
        u"CppAssistant: 诊断引擎 - 编译器",
    u"CppAssistant: Diagnostic Engine - libclang (PCH)":
        u"CppAssistant: 诊断引擎 - libclang（PCH）",
    u"Preferences: CppAssistant Settings": u"首选项: CppAssistant 设置",
    u"Preferences: CppAssistant Key Bindings":
        u"首选项: CppAssistant 快捷键",
}


def translate_caption(caption, lang):
    """Translate one caption; unknown captions are returned unchanged."""
    if lang == "zh":
        return TRANSLATIONS_ZH.get(caption, caption)
    return caption


def build_overlay(menu, lang="zh"):
    """Build an id/caption tree that Sublime can merge into the real menu.

    Entries without an ``id`` (separators, for example) cannot be targeted
    and are skipped.  Containers keep their ``children`` so nested entries
    can be reached, but they only override what is listed here: captions.
    """
    if not isinstance(menu, list):
        return []
    out = []
    for item in menu:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not item_id:
            continue
        node = {"id": item_id}
        # Only our own entries may have their captions overridden.  Parent
        # containers (Preferences/Package Settings) are traversed so nested
        # items are reachable, but Sublime's own captions stay untouched.
        if "caption" in item and item_id not in (
                "preferences", "package-settings"):
            node["caption"] = translate_caption(item["caption"], lang)
        children = item.get("children")
        if isinstance(children, list):
            sub = build_overlay(children, lang)
            if sub:
                node["children"] = sub
        # An id-only node is a traversal container (Preferences/Package
        # Settings); keep it when it has children, drop it otherwise.
        if "caption" in node or "children" in node:
            out.append(node)
    return out


def translate_command_caption(caption, lang):
    """Translate one command-palette caption; unknown ones are unchanged."""
    if lang == "zh":
        return COMMAND_TRANSLATIONS_ZH.get(caption, caption)
    return caption


def build_commands_overlay(commands, lang="zh"):
    """Build the translated ``.sublime-commands`` overlay.

    Sublime merges command-palette entries by ``command`` + ``args``: a
    later-loaded entry with the same key overrides the earlier ``caption``.
    We copy every field unchanged and replace only ``caption``, so each
    overlay entry pairs with (and overrides) exactly one entry from the real
    package file.  The "same command, different args" entries (the
    ``Language - ...`` / ``... Engine - ...`` families) each keep their own
    args and therefore stay distinct -- no duplicate entries are introduced.
    """
    if not isinstance(commands, list):
        return []
    out = []
    for entry in commands:
        if not isinstance(entry, dict):
            continue
        caption = entry.get("caption")
        if caption is None:
            continue
        node = dict(entry)
        node["caption"] = translate_command_caption(caption, lang)
        out.append(node)
    return out


def load_package_menu():
    """Load the package's own Main.sublime-menu (zip or directory install)."""
    if sublime is not None:
        try:
            text = sublime.load_resource(
                "Packages/CppAssistant/Main.sublime-menu")
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        MENU_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


def load_package_commands():
    """Load the package's own commands file (zip or directory install)."""
    if sublime is not None:
        try:
            text = sublime.load_resource(
                "Packages/CppAssistant/" + COMMANDS_FILE)
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        COMMANDS_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


def _packages_path(packages_path=None):
    if packages_path is None and sublime is not None:
        packages_path = sublime.packages_path()
    return packages_path or ""


def overlay_root(packages_path=None):
    root = _packages_path(packages_path)
    if not root:
        return None
    return os.path.join(root, OVERLAY_PACKAGE)


def cleanup_stale(packages_path=None):
    """Remove the ignored menu file written by older plugin versions."""
    root = _packages_path(packages_path)
    if not root:
        return
    stale_dir = os.path.join(root, "User", "CppAssistant")
    stale_menu = os.path.join(stale_dir, MENU_FILE)
    if os.path.isfile(stale_menu):
        try:
            os.remove(stale_menu)
        except OSError:
            pass
    try:
        if os.path.isdir(stale_dir) and not os.listdir(stale_dir):
            os.rmdir(stale_dir)
    except OSError:
        pass


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def apply_menu_language(lang, packages_path=None):
    """Write (or clear) the generated overlay package.

    Returns ``(ok, error_message)``.  In English mode both overlay files are
    empty lists rather than a deleted directory, because rewriting the files
    is what makes Sublime reload captions immediately.
    """
    lang = "zh" if lang == "zh" else "en"
    root = overlay_root(packages_path)
    if not root:
        return False, "Sublime packages directory is unavailable"
    cleanup_stale(packages_path)
    marker = os.path.join(root, MARKER)
    # Never take over a real package that happens to use our overlay name.
    if os.path.isdir(root) and not os.path.isfile(marker):
        return False, ("Package %s already exists and was not created by "
                       "CppAssistant" % OVERLAY_PACKAGE)
    try:
        if not os.path.isdir(root):
            os.makedirs(root)
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as f:
                f.write("Generated by CppAssistant. Safe to delete.\n")
        if lang == "zh":
            menu = load_package_menu()
            if menu is None:
                return False, "Cannot load CppAssistant/" + MENU_FILE
            overlay = build_overlay(menu, "zh")
            commands = load_package_commands()
            if commands is None:
                return False, "Cannot load CppAssistant/" + COMMANDS_FILE
            command_overlay = build_commands_overlay(commands, "zh")
        else:
            overlay = []
            command_overlay = []
        _write_json(os.path.join(root, MENU_FILE), overlay)
        _write_json(os.path.join(root, COMMANDS_FILE), command_overlay)
        return True, ""
    except Exception as e:
        return False, str(e)


def remove_overlay(packages_path=None):
    """Delete the generated overlay (used when the plugin is unloaded)."""
    root = overlay_root(packages_path)
    if not root:
        return
    marker = os.path.join(root, MARKER)
    if os.path.isfile(marker):
        shutil.rmtree(root, ignore_errors=True)
