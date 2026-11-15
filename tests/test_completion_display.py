# -*- coding: utf-8 -*-
"""Completion-display tests: kinds, labels, placeholders and language.

The regression these guard against: ``sublime.KIND_ID_CONSTANT`` does not
exist in the API, and the old code referenced it directly, so every
completion raised ``AttributeError`` and the popup never appeared.
"""

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_jsonc(path):
    s = io.open(path, encoding="utf-8").read()
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    s = re.sub(r",(\s*[\]}])", r"\1", s)
    return json.loads(s)


DEFAULT_SETTINGS = _load_jsonc(
    os.path.join(ROOT, "CppAssistant.sublime-settings"))


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


# Real ST 4215 exposes these; KIND_ID_CONSTANT is deliberately absent.
_KIND_IDS = {
    "KIND_ID_AMBIGUOUS": 0,
    "KIND_ID_KEYWORD": 1,
    "KIND_ID_TYPE": 2,
    "KIND_ID_FUNCTION": 3,
    "KIND_ID_NAMESPACE": 4,
    "KIND_ID_NAVIGATION": 5,
    "KIND_ID_MARKUP": 6,
    "KIND_ID_VARIABLE": 7,
    "KIND_ID_SNIPPET": 8,
}


def _build_stub(packages_dir):
    settings = _Settings(DEFAULT_SETTINGS)
    sublime = types.ModuleType("sublime")

    def _getattr(name):
        if name in _KIND_IDS:
            return _KIND_IDS[name]
        if name == "KIND_AMBIGUOUS":
            return (_KIND_IDS["KIND_ID_AMBIGUOUS"], "?", u"")
        if name.startswith("KIND_ID_"):
            # Exactly what a newer/older build does: attribute missing.
            raise AttributeError(
                "module 'sublime' has no attribute '%s'" % name)
        if name == "Region":
            return lambda *a, **k: (a, k)
        if name.isupper():
            return 1
        return _Any()

    sublime.__getattr__ = _getattr
    sublime.Region = lambda *a, **k: (a, k)
    sublime.CompletionItem = lambda **kwargs: kwargs
    sublime.CompletionList = lambda items, flags=0: (items, flags)
    sublime.load_settings = lambda name: settings
    sublime.save_settings = lambda name: None
    sublime.packages_path = lambda: packages_dir
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
    sublime.version = lambda: "4215"

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


class CompletionDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {}
        for name in ("sublime", "sublime_plugin", "ca_display_mod"):
            cls._saved[name] = sys.modules.get(name)
        cls._tmp = tempfile.mkdtemp(prefix="ca_display_")
        sublime, plugin, settings = _build_stub(cls._tmp)
        sys.modules["sublime"] = sublime
        sys.modules["sublime_plugin"] = plugin

        pkg = types.ModuleType("ca_display_mod")
        pkg.__path__ = [ROOT]
        sys.modules["ca_display_mod"] = pkg
        spec = importlib.util.spec_from_file_location(
            "ca_display_mod.CppAssistant",
            os.path.join(ROOT, "CppAssistant.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["ca_display_mod.CppAssistant"] = module
        spec.loader.exec_module(module)
        cls.module = module
        cls.settings = settings

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)
        for name, value in cls._saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def setUp(self):
        self.settings._data.clear()
        self.settings._data.update(DEFAULT_SETTINGS)

    # -- the crash ---------------------------------------------------------
    def test_missing_kind_id_does_not_break_completion_items(self):
        for kind in ("f", "m", "v", "c", "t", "k", "u", "s", "d", None):
            item = self.module._make_item({
                "trigger": "x", "insert": "x", "kind": kind,
                "annotation": u"说明", "annotation_en": "note",
            })
            self.assertEqual(item["trigger"], "x")
            self.assertEqual(len(item["kind"]), 3)

    def test_constant_kind_maps_to_an_existing_id(self):
        kind = self.module._KIND_MAP["c"]()
        self.assertEqual(kind[0], _KIND_IDS["KIND_ID_VARIABLE"])
        self.assertEqual(kind[1], "c")

    # -- display cleaning --------------------------------------------------
    def test_signature_placeholders_are_resolved(self):
        clean = self.module._clean_signature
        self.assertEqual(clean("vector<${1:int}>"), "vector<int>")
        self.assertEqual(clean("push_back(${1:x})"), "push_back(x)")
        self.assertEqual(
            clean('scanf(${1:"%d"}, &x)'), 'scanf("%d", &x)')
        self.assertEqual(clean("${1:a}, ${2:b}"), "a, b")

    def test_detail_drops_std_prefix_and_placeholders(self):
        detail = self.module._detail({
            "trigger": "vector",
            "insert": "std::vector<${1:int}>",
            "annotation": u"动态数组",
        })
        self.assertEqual(detail, "vector<int>")

    # -- triggers ----------------------------------------------------------
    def test_no_trigger_contains_snippet_placeholders(self):
        from cppassistant import ca_stdlib_data as data
        bad = [x["trigger"] for x in data.STD_ITEMS_ALL
               if "$" in x["trigger"] or "<" in x["trigger"]]
        self.assertEqual(bad, [])

    def test_vector_has_exactly_one_entry(self):
        from cppassistant import ca_stdlib_data as data
        hits = [x for x in data.STD_ITEMS_ALL
                if x["trigger"] == "vector"]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["insert"].startswith("vector<"))

    # -- language ----------------------------------------------------------
    def test_language_command_sets_menu_and_diagnostics(self):
        command = self.module.CaSetLanguageCommand()
        command.run("zh")
        self.assertEqual(self.settings.get("display_language"), "zh")
        self.assertEqual(self.settings.get("menu_language"), "zh")
        self.assertTrue(command.is_checked("zh"))

    def test_bilingual_keeps_english_menu(self):
        command = self.module.CaSetLanguageCommand()
        command.run("both")
        self.assertEqual(self.settings.get("display_language"), "both")
        self.assertEqual(self.settings.get("menu_language"), "en")

    def test_annotation_and_detail_follow_language(self):
        entry = {
            "trigger": "push_back", "insert": "push_back(${1:x})",
            "annotation": u"尾部追加元素 O(1)",
            "annotation_en": "Append to the end, O(1)",
            "detail": u"尾部追加元素 O(1)",
            "detail_en": "Append to the end, O(1)",
        }
        self.settings.set("display_language", "zh")
        self.assertEqual(self.module._annotation(entry),
                         u"尾部追加元素 O(1)")
        self.settings.set("display_language", "en")
        self.assertEqual(self.module._annotation(entry),
                         "Append to the end, O(1)")
        self.settings.set("display_language", "both")
        self.assertIn(u"/", self.module._annotation(entry))

    def test_invalid_language_is_rejected(self):
        command = self.module.CaSetLanguageCommand()
        command.run("fr")
        self.assertEqual(self.settings.get("display_language"),
                         DEFAULT_SETTINGS["display_language"])


if __name__ == "__main__":
    unittest.main()
