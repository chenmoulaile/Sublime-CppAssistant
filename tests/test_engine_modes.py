# -*- coding: utf-8 -*-
"""Completion-engine mode tests.

The plugin defaults to the pure-local ``builtin`` engine: no clangd
subprocess may be started and libclang must stay unloaded unless the user
explicitly asks for it.  These tests drive the real module with a stubbed
``sublime`` so the decision helpers and the switch command are covered
without an editor.
"""

import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_SETTINGS = {
    "completion_engine": "builtin",
    "builtin_uses_libclang": False,
    "enable_libclang_engine": True,
    "enable_completions": True,
    "enable_fast_engine": True,
    "lint_engine": "compiler",
    "cxx_standard": "c++14",
    "display_language": "en",
    "menu_language": "en",
}


class _Settings(object):
    def __init__(self, data):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def has(self, key):
        return key in self._data

    def erase(self, key):
        self._data.pop(key, None)

    def to_dict(self):
        return dict(self._data)


class _Any(object):
    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True


def _build_stub():
    settings = _Settings(DEFAULT_SETTINGS)
    sublime = types.ModuleType("sublime")

    def _getattr(name):
        if name.isupper():
            return 1
        return _Any()

    sublime.__getattr__ = _getattr
    sublime.Region = lambda *a, **k: (a, k)
    sublime.CompletionItem = lambda **kwargs: kwargs
    sublime.CompletionList = lambda items, flags=0: (items, flags)
    sublime.load_settings = lambda name: settings
    sublime.save_settings = lambda name: None
    sublime.packages_path = lambda: os.path.join(ROOT, "_fake_packages")
    sublime.platform = lambda: "linux"
    sublime.windows = lambda: []
    sublime.set_timeout = lambda fn, ms=0: None
    sublime.set_timeout_async = lambda fn, ms=0: None
    sublime.status_message = lambda msg: None
    sublime.error_message = lambda msg: None
    sublime.ok_cancel_dialog = lambda msg, ok="OK": True
    sublime.active_window = lambda: None
    sublime.find_resources = lambda pattern: []
    sublime.load_resource = lambda path: ""
    sublime.version = lambda: "4000"

    plugin = types.ModuleType("sublime_plugin")

    class _Command(object):
        def __init__(self, *args, **kwargs):
            self.view = None
            self.window = None

    plugin.TextCommand = _Command
    plugin.WindowCommand = _Command
    plugin.ApplicationCommand = _Command
    plugin.EventListener = _Command
    plugin.ViewEventListener = _Command
    return sublime, plugin, settings


class EngineModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {}
        for name in ("sublime", "sublime_plugin", "ca_engine_modes"):
            cls._saved[name] = sys.modules.get(name)
        sublime, plugin, settings = _build_stub()
        sys.modules["sublime"] = sublime
        sys.modules["sublime_plugin"] = plugin

        pkg = types.ModuleType("ca_engine_modes")
        pkg.__path__ = [ROOT]
        sys.modules["ca_engine_modes"] = pkg
        spec = importlib.util.spec_from_file_location(
            "ca_engine_modes.CppAssistant",
            os.path.join(ROOT, "CppAssistant.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["ca_engine_modes.CppAssistant"] = module
        spec.loader.exec_module(module)
        cls.module = module
        cls.settings = settings

    @classmethod
    def tearDownClass(cls):
        for name, value in cls._saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def setUp(self):
        self.settings._data.clear()
        self.settings._data.update(DEFAULT_SETTINGS)

    # -- decision helpers -------------------------------------------------
    def test_default_is_pure_local(self):
        self.assertEqual(self.module._completion_engine(), "builtin")
        self.assertFalse(self.module._clangd_enabled())
        self.assertFalse(self.module._libclang_allowed())

    def test_auto_starts_clangd_when_available(self):
        self.settings.set("completion_engine", "auto")
        self.assertTrue(self.module._clangd_enabled())
        self.assertTrue(self.module._libclang_allowed())

    def test_clangd_mode(self):
        self.settings.set("completion_engine", "clangd")
        self.assertEqual(self.module._completion_engine(), "clangd")
        self.assertTrue(self.module._clangd_enabled())

    def test_legacy_boolean_is_honoured_when_new_key_absent(self):
        self.settings.erase("completion_engine")
        self.settings.set("enable_clangd_engine", False)
        self.assertEqual(self.module._completion_engine(), "builtin")
        self.settings.set("enable_clangd_engine", True)
        self.assertEqual(self.module._completion_engine(), "clangd")

    def test_invalid_mode_falls_back(self):
        self.settings.set("completion_engine", "nonsense")
        self.assertEqual(self.module._completion_engine(), "builtin")

    def test_builtin_can_opt_into_libclang(self):
        self.settings.set("builtin_uses_libclang", True)
        self.assertTrue(self.module._libclang_allowed())

    def test_libclang_master_switch_wins(self):
        self.settings.set("enable_libclang_engine", False)
        self.settings.set("completion_engine", "clangd")
        self.assertFalse(self.module._libclang_allowed())
        self.settings.set("builtin_uses_libclang", True)
        self.assertFalse(self.module._libclang_allowed())

    # -- switch command ---------------------------------------------------
    def test_switch_command_writes_the_mode(self):
        command = self.module.CaSetCompletionEngineCommand()
        command.run("auto")
        self.assertEqual(self.settings.get("completion_engine"), "auto")
        self.assertTrue(command.is_checked("auto"))
        self.assertFalse(command.is_checked("builtin"))

    def test_switch_rejects_unknown_mode(self):
        command = self.module.CaSetCompletionEngineCommand()
        command.run("libclang")
        self.assertEqual(self.settings.get("completion_engine"), "builtin")

    # -- pure-local completion still works --------------------------------
    def test_builtin_completion_needs_no_clangd(self):
        from cppassistant import ca_fast
        src = "struct S { int member; };\nint main(){ S s; s.mem }\n"
        offset = src.index("s.mem") + 5
        items = ca_fast.complete(src, offset)
        self.assertTrue(items)
        triggers = [item["trigger"] for item in items]
        self.assertIn("member", triggers)

    def test_builtin_signature_comes_from_fast_engine(self):
        from cppassistant import ca_fast
        src = ("void solve(int n, vector<int>& a) {\n}\n"
               "int main(){\n    solve(n, a);\n}\n")
        info = ca_fast.call_info(src, src.index("a);") + 1)
        self.assertIsNotNone(info)
        self.assertIn("solve", info.signature)
        self.assertEqual(info.arg_count, 2)


if __name__ == "__main__":
    unittest.main()
