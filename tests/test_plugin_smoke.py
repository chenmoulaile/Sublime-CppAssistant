# -*- coding: utf-8 -*-
"""Smoke test: the plugin module must import and wire its commands.

Import-time breakage is the most common way a Sublime plugin "disappears"
(no menu, no completion, no error dialog), so the whole module is imported
with a small ``sublime`` stub.  Only pure helpers are exercised here; the
editor-facing behaviour is covered by the engine tests.
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
    "enable_completions": True,
    "enable_clangd_engine": False,
    "enable_fast_engine": True,
    "auto_insert_includes": True,
    "cpp_auto_insert_pbds_includes": True,
    "show_status_hint": True,
    "display_language": "en",
    "menu_language": "en",
    "cxx_standard": "c++14",
    "lint_engine": "compiler",
    "lint_timeout": 12,
    "lint_debounce": 0.1,
    "indent_width": 4,
    "clangd_completion_wait_ms": 0,
}


class _Settings(object):
    def __init__(self, data):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def to_dict(self):
        return dict(self._data)


class _Any(object):
    """Permissive stand-in for sublime objects used at import time."""

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True


def _build_sublime_stub():
    sublime = types.ModuleType("sublime")
    settings = _Settings(DEFAULT_SETTINGS)

    def _getattr(name):
        if name.isupper():
            return 1
        if name == "Region":
            return lambda *a, **k: (a, k)
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
    return sublime, plugin


class PluginSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {}
        for name in ("sublime", "sublime_plugin", "ca_smoke_plugin"):
            cls._saved[name] = sys.modules.get(name)
        sublime, plugin = _build_sublime_stub()
        sys.modules["sublime"] = sublime
        sys.modules["sublime_plugin"] = plugin

        pkg = types.ModuleType("ca_smoke_plugin")
        pkg.__path__ = [ROOT]
        sys.modules["ca_smoke_plugin"] = pkg
        path = os.path.join(ROOT, "CppAssistant.py")
        spec = importlib.util.spec_from_file_location(
            "ca_smoke_plugin.CppAssistant", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ca_smoke_plugin.CppAssistant"] = module
        spec.loader.exec_module(module)
        cls.module = module

    @classmethod
    def tearDownClass(cls):
        for name, value in cls._saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def test_module_imports(self):
        self.assertTrue(hasattr(self.module, "plugin_loaded"))

    def test_new_commands_are_registered(self):
        for name in ("CaFixFullwidthPunctuationCommand",
                     "CaBrowseTemplatesCommand",
                     "CaManageUserSnippetsCommand",
                     "CaValidateSettingsCommand",
                     "CaCallHierarchyCommand",
                     "CaTypeHierarchyCommand"):
            self.assertTrue(hasattr(self.module, name), name)

    def test_type_hierarchy_reuses_call_hierarchy_flow(self):
        base = self.module.CaCallHierarchyCommand
        child = self.module.CaTypeHierarchyCommand
        self.assertTrue(issubclass(child, base))
        self.assertEqual(child.STATUS_KEY, "ca_types")
        self.assertEqual([d[2] for d in child.DIRECTIONS],
                         ["typeHierarchy/supertypes",
                          "typeHierarchy/subtypes"])

    def test_hierarchy_peer_and_start_extraction(self):
        cmd = self.module.CaTypeHierarchyCommand()
        bare = {"name": "Base", "uri": "file:///a.hpp",
                "selectionRange": {"start": {"line": 3, "character": 1}}}
        self.assertIs(cmd._peer_of(bare), bare)
        self.assertEqual(cmd._start_of({"name": "Base"}, bare),
                         {"line": 3, "character": 1})
        call = {"from": {"name": "solve", "uri": "file:///a.cpp"},
                "fromRanges": [{"start": {"line": 9, "character": 4}}]}
        self.assertEqual(cmd._peer_of(call)["name"], "solve")
        self.assertEqual(cmd._start_of(call, call["from"]),
                         {"line": 9, "character": 4})

    def test_snippet_preview_limits_lines(self):
        entry = {"trigger": "us", "insert": "a\nb\nc", "detail": "desc"}
        item = self.module._snippet_preview_item(entry, max_lines=2)
        self.assertEqual(item[0], "us  ·  desc  ·  3 line(s)")
        self.assertEqual(item[1:3], ["    a", "    b"])
        self.assertIn("1 more line", item[3])

    def test_pbds_includes_are_attached(self):
        from cppassistant import ca_engine
        src = "int main() {\n    __gnu_pbds::\n}\n"
        offset = src.index("__gnu_pbds::") + len("__gnu_pbds::")
        items = ca_engine.analyze(src, offset)
        self.assertTrue(items)
        for entry in items:
            self.assertEqual(entry.get("includes"),
                             ["ext/pb_ds/assoc_container.hpp",
                              "ext/pb_ds/tree_policy.hpp"],
                             entry["trigger"])

    def test_pbds_includes_from_global_completion(self):
        items = [{"trigger": "gp_hash_table", "insert": "gp_hash_table"},
                 {"trigger": "vector", "insert": "vector<int>"}]
        self.module._attach_pbds_includes(items)
        self.assertEqual(items[0]["includes"],
                         ["ext/pb_ds/assoc_container.hpp",
                          "ext/pb_ds/tree_policy.hpp"])
        self.assertNotIn("includes", items[1])

    def test_pbds_include_switch_can_disable(self):
        settings = sys.modules["sublime"].load_settings(None)
        settings.set("cpp_auto_insert_pbds_includes", False)
        try:
            items = [{"trigger": "gp_hash_table", "insert": "gp_hash_table"}]
            self.module._attach_pbds_includes(items)
            self.assertNotIn("includes", items[0])
        finally:
            settings.set("cpp_auto_insert_pbds_includes", True)

    def test_settings_validation_runs(self):
        settings = sys.modules["sublime"].load_settings(None)
        settings.set("lint_engine", "gcc")
        try:
            problems = self.module._settings_problems()
            self.assertTrue(any(p["key"] == "lint_engine"
                                for p in problems))
        finally:
            settings.set("lint_engine", "compiler")

    def test_snippet_file_parser_roundtrip(self):
        import tempfile
        xml = ("<snippet><content><![CDATA[int x;]]></content>"
               "<tabTrigger>ix</tabTrigger><scope>source.c++</scope>"
               "<description>int x</description></snippet>")
        directory = tempfile.mkdtemp(prefix="ca_snippet_")
        path = os.path.join(directory, "ix.sublime-snippet")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        try:
            parsed = self.module._parse_snippet_file(path)
            self.assertEqual(parsed["trigger"], "ix")
            self.assertEqual(parsed["content"], "int x;")
            self.assertEqual(parsed["desc"], "int x")
        finally:
            import shutil
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
