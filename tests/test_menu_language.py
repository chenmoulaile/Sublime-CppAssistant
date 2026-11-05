# -*- coding: utf-8 -*-
"""Tests for the generated menu-language overlay."""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_menu_language as menu_lang


def _read_menu():
    with open(os.path.join(ROOT, "Main.sublime-menu"), "r",
              encoding="utf-8") as f:
        return json.load(f)


def _walk(items):
    for item in items:
        if isinstance(item, dict):
            yield item
            for child in _walk(item.get("children") or []):
                yield child


class MenuOverlayTests(unittest.TestCase):
    def test_build_overlay_translates_and_drops_commands(self):
        menu = [{
            "caption": "Preferences",
            "id": "preferences",
            "children": [{
                "caption": "CppAssistant",
                "id": "cppassistant-settings",
                "children": [{
                    "caption": "Settings",
                    "id": "cppassistant-settings-file",
                    "command": "edit_settings",
                }],
            }],
        }]
        overlay = menu_lang.build_overlay(menu, "zh")
        self.assertEqual(overlay[0]["id"], "preferences")
        self.assertNotIn("caption", overlay[0])
        item = overlay[0]["children"][0]["children"][0]
        self.assertEqual(item["id"], "cppassistant-settings-file")
        self.assertEqual(item["caption"], u"设置")
        self.assertNotIn("command", item)

    def test_build_overlay_skips_entries_without_ids(self):
        menu = [
            {"caption": "-"},
            {"caption": "Container", "children": [
                {"caption": "Settings", "id": "settings", "command": "x"},
            ]},
            {"caption": "Templates", "id": "templates", "children": [
                {"caption": "-"},
                {"caption": "Manage Templates", "id": "manage",
                 "command": "y"},
            ]},
        ]
        overlay = menu_lang.build_overlay(menu, "zh")
        self.assertEqual([n["id"] for n in overlay], ["templates"])
        self.assertEqual(len(overlay[0]["children"]), 1)
        self.assertEqual(overlay[0]["children"][0]["caption"], u"管理模板")

    def test_unknown_caption_is_kept(self):
        menu = [{"caption": "Brand New Item", "id": "new-item",
                 "command": "new"}]
        overlay = menu_lang.build_overlay(menu, "zh")
        self.assertEqual(overlay[0]["caption"], "Brand New Item")

    def test_english_build_keeps_english_captions(self):
        menu = [{"caption": "Settings", "id": "settings", "command": "x"}]
        overlay = menu_lang.build_overlay(menu, "en")
        self.assertEqual(overlay[0]["caption"], "Settings")

    def test_package_menu_ids_are_unique_and_translated(self):
        menu = _read_menu()
        items = list(_walk(menu))
        ids = [item["id"] for item in items if item.get("id")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("cppassistant", ids)
        captions = set(item["caption"] for item in items
                       if item.get("caption") and item["caption"] != "-"
                       and str(item.get("id", "")).startswith("cppassistant"))
        missing = captions - set(menu_lang.TRANSLATIONS_ZH)
        self.assertEqual(missing, set())

    def test_apply_writes_overlay_and_removes_stale_file(self):
        tmp = tempfile.mkdtemp(prefix="ca_menu_")
        try:
            stale = os.path.join(tmp, "User", "CppAssistant")
            os.makedirs(stale)
            stale_menu = os.path.join(stale, "Main.sublime-menu")
            with open(stale_menu, "w", encoding="utf-8") as f:
                f.write("[]")

            ok, err = menu_lang.apply_menu_language("zh", tmp)
            self.assertTrue(ok, err)
            root = os.path.join(tmp, menu_lang.OVERLAY_PACKAGE)
            self.assertTrue(os.path.isfile(
                os.path.join(root, menu_lang.MARKER)))
            with open(os.path.join(root, menu_lang.MENU_FILE), "r",
                      encoding="utf-8") as f:
                overlay = json.load(f)
            self.assertTrue(overlay)
            self.assertFalse(os.path.exists(stale_menu))

            ok, err = menu_lang.apply_menu_language("en", tmp)
            self.assertTrue(ok, err)
            with open(os.path.join(root, menu_lang.MENU_FILE), "r",
                      encoding="utf-8") as f:
                self.assertEqual(json.load(f), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_foreign_overlay_package_is_not_touched(self):
        tmp = tempfile.mkdtemp(prefix="ca_menu_")
        try:
            root = os.path.join(tmp, menu_lang.OVERLAY_PACKAGE)
            os.makedirs(root)
            path = os.path.join(root, menu_lang.MENU_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write('[{"caption": "Mine"}]')
            ok, err = menu_lang.apply_menu_language("zh", tmp)
            self.assertFalse(ok)
            self.assertIn("already exists", err)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), '[{"caption": "Mine"}]')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_overlay_requires_marker(self):
        tmp = tempfile.mkdtemp(prefix="ca_menu_")
        try:
            foreign = os.path.join(tmp, menu_lang.OVERLAY_PACKAGE)
            os.makedirs(foreign)
            menu_lang.remove_overlay(tmp)
            self.assertTrue(os.path.isdir(foreign))

            marker = os.path.join(foreign, menu_lang.MARKER)
            with open(marker, "w", encoding="utf-8") as f:
                f.write("generated")
            menu_lang.remove_overlay(tmp)
            self.assertFalse(os.path.isdir(foreign))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
