# -*- coding: utf-8 -*-
"""Tests for run_command() reference integrity across the plugin sources.

Every ``run_command("ca_...")`` call site in CppAssistant.py / cppassistant/
must name a command the plugin actually defines, and every command class
must follow the Sublime naming convention: a CamelCase name ending in
``Command`` whose remainder maps back to the snake_case command name.
Regression guard for XC1 (CaHoverText / CaSignaturePopupText were never
registered because their class names lacked the Command suffix).
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# view/window.run_command("ca_...") 调用点（只查字面量 ca_ 前缀命令）。
_RUN_COMMAND_RE = re.compile(r'run_command\(\s*["\'](ca_[a-z0-9_]+)["\']')
# 命令类识别，两种形态都算（并集）：
#   直接继承：class X(sublime_plugin.TextCommand) —— 能抓到 XC1 那类
#   「基名是命令基类但类名漏了 Command 后缀」的回归；
#   命名约定：class XCommand(...) —— 覆盖间接继承
#   （class CaTypeHierarchyCommand(CaCallHierarchyCommand)）与多行定义。
_COMMAND_CLASS_RES = (
    re.compile(r"class\s+([A-Za-z0-9_]+)\(\s*sublime_plugin\.\w+Command\s*\)"),
    re.compile(r"class\s+([A-Za-z0-9_]+Command)\b"),
)


def _plugin_sources():
    """插件全部 Python 源文件路径（宿主 + cppassistant 包）。"""
    files = [os.path.join(ROOT, "CppAssistant.py")]
    pkg = os.path.join(ROOT, "cppassistant")
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py"):
            files.append(os.path.join(pkg, name))
    return files


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _command_name(class_name):
    """CamelCase + Command 后缀 -> snake_case 命令名（Sublime 约定）。"""
    base = class_name[:-len("Command")]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", base).lower()


def _command_classes():
    """{类名: 首个定义文件}，两种识别形态取并集。"""
    out = {}
    for path in _plugin_sources():
        src = _read(path)
        names = set()
        for rx in _COMMAND_CLASS_RES:
            for m in rx.finditer(src):
                names.add(m.group(1))
        for name in names:
            out.setdefault(name, os.path.relpath(path, ROOT))
    return out


def _defined_commands():
    """{命令名: 类名}，来自全部命令类定义。"""
    commands = {}
    for name in _command_classes():
        commands.setdefault(_command_name(name), name)
    return commands


class RunCommandIntegrityTests(unittest.TestCase):
    def test_every_run_command_reference_resolves(self):
        missing = []
        seen = []
        for path in _plugin_sources():
            rel = os.path.relpath(path, ROOT)
            for m in _RUN_COMMAND_RE.finditer(_read(path)):
                seen.append((rel, m.group(1)))
                if m.group(1) not in _defined_commands():
                    missing.append((rel, m.group(1)))
        self.assertTrue(seen,
                        "no run_command('ca_...') call sites found — "
                        "the scan regex went stale")
        self.assertEqual(missing, [])

    def test_command_classes_end_with_command_suffix(self):
        # XC1 回归：CaHoverText 这种不带 Command 后缀的命令类不会被
        # Sublime 注册，run_command 会找不到命令。
        for name, defined_in in _command_classes().items():
            self.assertTrue(
                name.endswith("Command"),
                "%s: command class %r must end with 'Command'"
                % (defined_in, name))
        # 间接继承的命令类（如 CaTypeHierarchyCommand 继承
        # CaCallHierarchyCommand）也要进清单。
        self.assertIn("CaTypeHierarchyCommand", _command_classes())

    def test_derived_command_names_are_unique(self):
        """不同类名映射到同一命令名时，后者会静默覆盖前者。"""
        owners = {}
        dup = []
        for name, defined_in in _command_classes().items():
            cmd = _command_name(name)
            if cmd in owners:
                dup.append((cmd, owners[cmd], name))
            else:
                owners[cmd] = name
        self.assertEqual(dup, [])


if __name__ == "__main__":
    unittest.main()
