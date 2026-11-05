# -*- coding: utf-8 -*-
"""Tests for bundled snippet packs and the user-snippet loader."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_template_packs, ca_user_snippets

EXPECTED_PACKS = {"geometry", "graph", "ds", "strings", "math"}


def _snippet_resource(trigger, content="// body",
                      scope="source.c++, source.c",
                      description="desc"):
    return ("<snippet>\n"
            "    <content><![CDATA[%s]]></content>\n"
            "    <tabTrigger>%s</tabTrigger>\n"
            "    <scope>%s</scope>\n"
            "    <description>%s</description>\n"
            "</snippet>\n" % (content, trigger, scope, description))


class BundledPackCatalogTests(unittest.TestCase):
    def test_catalog_declares_only_bundled_packs(self):
        packs = ca_template_packs.catalog_packs()
        self.assertEqual({p["id"] for p in packs}, EXPECTED_PACKS)
        for pack in packs:
            self.assertTrue(pack.get("bundled"))
            self.assertNotIn("url", pack)
            self.assertTrue(pack.get("name"))
            self.assertTrue(pack.get("snippets"))

    def test_declared_snippets_exist_on_disk(self):
        catalog = ca_template_packs.load_catalog()
        items = {p["id"]: p["snippets"] for p in catalog["packs"]}
        for pack_id, declared in items.items():
            items_on_disk = sorted(
                name[:-len(".sublime-snippet")]
                for name, _ in ca_template_packs.load_pack(pack_id))
            self.assertEqual(sorted(declared), items_on_disk, pack_id)

    def test_every_bundled_snippet_is_valid_xml(self):
        for pack_id in sorted(EXPECTED_PACKS):
            self.assertTrue(ca_template_packs.load_pack(pack_id))
            for name, text in ca_template_packs.load_pack(pack_id):
                root = ET.fromstring(text.encode("utf-8"))
                self.assertEqual(root.tag, "snippet", name)
                self.assertTrue((root.findtext("tabTrigger") or "").strip())
                self.assertTrue(ca_user_snippets._CPP_SCOPE_RE.search(
                    root.findtext("scope") or ""))

    def test_pack_filenames_match_tab_trigger(self):
        for pack_id in sorted(EXPECTED_PACKS):
            for name, text in ca_template_packs.load_pack(pack_id):
                root = ET.fromstring(text.encode("utf-8"))
                trigger = (root.findtext("tabTrigger") or "").strip()
                self.assertEqual(name, trigger + ".sublime-snippet")

    def test_install_pack_copies_and_reenables(self):
        tmp = tempfile.mkdtemp(prefix="ca_pack_install_")
        try:
            dest = os.path.join(tmp, "geometry")
            os.makedirs(dest)
            with open(os.path.join(dest, ".disabled"), "w") as f:
                f.write("disabled")
            count = ca_template_packs.install_pack("geometry", dest)
            self.assertEqual(
                count, len(ca_template_packs.load_pack("geometry")))
            self.assertFalse(os.path.exists(os.path.join(dest, ".disabled")))
            self.assertTrue(any(
                n.endswith(".sublime-snippet") for n in os.listdir(dest)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_install_pack_rejects_traversal(self):
        tmp = tempfile.mkdtemp(prefix="ca_pack_bad_")
        try:
            self.assertEqual(ca_template_packs.install_pack("../x", tmp), 0)
            self.assertEqual(ca_template_packs.install_pack("a/../b", tmp), 0)
            self.assertEqual(ca_template_packs.install_pack("", tmp), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_snippet_xml_escapes_and_splits_cdata(self):
        xml = ca_template_packs.snippet_xml(
            u't<&"x"', u'a]]>b', u'd<&>')
        root = ET.fromstring(xml.encode("utf-8"))
        self.assertEqual(root.findtext("content"), u"a]]>b")
        self.assertEqual(root.findtext("tabTrigger"), u't<&"x"')
        self.assertEqual(root.findtext("description"), u"d<&>")


class FakeSublime(object):
    def __init__(self, files, packages_path):
        self._files = dict(files)
        self._packages_path = packages_path

    def find_resources(self, pattern):
        if pattern == "*.sublime-snippet":
            return sorted(self._files)
        return []

    def load_resource(self, path):
        return self._files[path]

    def packages_path(self):
        return self._packages_path


class UserSnippetLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ca_snippets_")

    def tearDown(self):
        ca_user_snippets.sublime = None
        ca_user_snippets.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, files):
        ca_user_snippets.sublime = FakeSublime(files, self.tmp)
        ca_user_snippets.clear_cache()
        return ca_user_snippets.get_cpp_snippets(force_reload=True)

    def _write_installed_pack(self, pack="geometry", trigger="bundlesnippet"):
        path = os.path.join(self.tmp, "User",
                            "CppAssistantTemplates", pack)
        if not os.path.isdir(path):
            os.makedirs(path)
        resource = ("Packages/User/CppAssistantTemplates/%s/%s.sublime-snippet" % (
            pack, trigger))
        local_files = {}
        with open(os.path.join(path, trigger + ".sublime-snippet"),
                  "w", encoding="utf-8") as f:
            f.write(_snippet_resource(trigger))
        local_files[resource] = _snippet_resource(trigger)
        return local_files

    def test_bundled_package_resources_are_not_loaded(self):
        files = {
            "Packages/CppAssistant/templates/geometry/bundlesnippet.sublime-snippet":
                _snippet_resource("bundlesnippet"),
        }
        self.assertEqual(self._load(files), [])

    def test_installed_pack_is_loaded_and_can_be_disabled(self):
        files = self._write_installed_pack()
        items = self._load(files)
        self.assertEqual([i["trigger"] for i in items], ["bundlesnippet"])

        marker = os.path.join(
            self.tmp, "User", "CppAssistantTemplates", "geometry", ".disabled")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("disabled")
        items = self._load(files)
        self.assertEqual(items, [])

    def test_snippets_carry_origin_annotation(self):
        template_files = {
            "Packages/User/CppAssistantTemplates/strings/kmp.sublime-snippet":
                _snippet_resource("kmp"),
            "Packages/User/mine.sublime-snippet":
                _snippet_resource("mine"),
        }
        items = self._load(template_files)
        by_trigger = dict((i["trigger"], i) for i in items)
        self.assertEqual(by_trigger["kmp"]["annotation"],
                         u"desc · 字符串模板")
        self.assertEqual(by_trigger["kmp"]["annotation_en"],
                         u"desc · Strings template")
        self.assertEqual(by_trigger["mine"]["annotation"],
                         u"desc · 用户片段")
        self.assertEqual(by_trigger["mine"]["annotation_en"],
                         u"desc · User snippet")

    def test_scope_filter_and_sort(self):
        files = {
            "Packages/User/css.sublime-snippet":
                _snippet_resource("zlong", scope="source.css"),
            "Packages/User/cpp.sublime-snippet":
                _snippet_resource("zlong"),
            "Packages/User/cpp11.sublime-snippet":
                _snippet_resource("ab", scope="source.c++11"),
            "Packages/User/cc.sublime-snippet":
                _snippet_resource("cd", scope="source.cc, source.c"),
        }
        items = self._load(files)
        self.assertEqual([i["trigger"] for i in items], ["ab", "cd", "zlong"])

    def test_broken_xml_is_skipped(self):
        files = {
            "Packages/User/bad.sublime-snippet": "<snippet><broken>",
            "Packages/User/good.sublime-snippet":
                _snippet_resource("good"),
        }
        items = self._load(files)
        self.assertEqual([i["trigger"] for i in items], ["good"])

    def test_package_catalog_parses(self):
        path = os.path.join(ROOT, "templates", "index.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(
            {p["id"] for p in data["packs"]}, EXPECTED_PACKS)


if __name__ == "__main__":
    unittest.main()
