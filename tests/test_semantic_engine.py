# -*- coding: utf-8 -*-
"""Unit tests for the optional libclang SemanticEngine (ca_semantic.py).

These tests are skipped when libclang cannot be discovered, so the normal
zero-dependency test suite still passes on machines without LLVM.

Run from the repository root:

    python -m unittest discover -s tests -v
    CA_TEST_LIBCLANG_PCH=1 python -m unittest tests.test_semantic_engine -v
"""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cppassistant import ca_semantic


LIBCLANG = ca_semantic.find_libclang()
ARGS = ["-std=c++14"]


@unittest.skipUnless(LIBCLANG, "libclang not found")
class SemanticEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ca_semantic_")
        try:
            cls.engine = ca_semantic.SemanticEngine(
                LIBCLANG, cache_limit=2, enable_pch=False)
        except Exception as e:
            raise unittest.SkipTest("libclang cannot be loaded: %s" % e)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        try:
            import shutil
            shutil.rmtree(cls.tmp, ignore_errors=True)
        except Exception:
            pass

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_discovery_and_version(self):
        self.assertTrue(os.path.isfile(LIBCLANG))
        self.assertTrue(self.engine.version)
        self.assertIn("clang", self.engine.version.lower())
        self.assertEqual(ca_semantic.clang_version(LIBCLANG),
                         self.engine.version)

    def test_resource_dir_contains_builtin_headers(self):
        res = ca_semantic.find_resource_dir(LIBCLANG)
        if not res:
            self.skipTest("clang resource directory not found")
        self.assertTrue(os.path.isfile(os.path.join(res, "include",
                                                    "stddef.h")))

    def test_diagnostics(self):
        src = u"int main() {\n    int x = ;\n    return 0;\n}\n"
        diags = self.engine.diagnostics(src, self.path("diag.cpp"), ARGS)
        self.assertTrue(diags)
        first = diags[0]
        self.assertEqual(first["line"], 2)
        self.assertEqual(first["severity"], "error")
        self.assertGreaterEqual(first["col"], 1)
        self.assertTrue(first["en"])

    def test_compiler_diagnostic_shape(self):
        src = u"int main() { return missing_name; }\n"
        diags = self.engine.compiler_diagnostics(
            src, self.path("shape.cpp"), ARGS)
        self.assertTrue(diags)
        item = diags[0]
        for key in ("line", "col", "sev_en", "msg", "zh", "notes", "tier"):
            self.assertIn(key, item)
        self.assertEqual(item["tier"], "compiler")
        self.assertIsInstance(item["notes"], list)

    def test_completion_members(self):
        src = (u"struct Point { int x, y; };\n"
               u"void f() {\n    Point p;\n    p.\n}\n")
        off = src.index("    p.") + len("    p.")
        items = self.engine.complete(
            src, off, self.path("member.cpp"), ARGS)
        names = [i["trigger"] for i in items]
        self.assertIn("x", names)
        self.assertIn("y", names)

    def test_completion_standard_library(self):
        src = (u"#include <vector>\n"
               u"void f() {\n    std::vector<int> v;\n    v.\n}\n")
        off = src.index("    v.") + len("    v.")
        items = self.engine.complete(
            src, off, self.path("vector.cpp"), ARGS)
        names = [i["trigger"] for i in items]
        if not names:
            self.skipTest("standard library headers unavailable to libclang")
        self.assertIn("push_back", names)

    def test_definition_local(self):
        src = (u"int value = 1;\n"
               u"int main() {\n    return value;\n}\n")
        off = src.index("return value") + len("return value") - 1
        hits = self.engine.find_definitions(
            src, off, self.path("def.cpp"), ARGS)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 1)
        self.assertEqual(hits[0]["col"], 4)
        self.assertEqual(hits[0]["kind"], "variable")

    def test_definition_at_word_end(self):
        """The host passes word_region.end(), i.e. one past the identifier."""
        src = (u"int value = 1;\n"
               u"int main() {\n    return value;\n}\n")
        off = src.index("return value") + len("return value")
        hits = self.engine.find_definitions(
            src, off, self.path("def_end.cpp"), ARGS)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["line"], 1)

    def test_definition_cross_file(self):
        hdr = self.path("lib.h")
        with open(hdr, "w", encoding="utf-8") as f:
            f.write(u"int add(int a, int b) { return a + b; }\n")
        src = (u'#include "lib.h"\n'
               u"int main() { return add(1, 2); }\n")
        off = src.index("add(1") + 1
        hits = self.engine.find_definitions(
            src, off, self.path("main.cpp"), ARGS)
        self.assertTrue(hits)
        self.assertEqual(os.path.normcase(hits[0]["file"]),
                         os.path.normcase(hdr))
        self.assertEqual(hits[0]["line"], 1)
        self.assertEqual(hits[0]["kind"], "function")

    def test_reparse_updates_diagnostics(self):
        src_bad = u"int main() {\n    int x = ;\n    return 0;\n}\n"
        src_ok = u"int main() {\n    int x = 1;\n    return 0;\n}\n"
        fname = self.path("reparse.cpp")
        bad = self.engine.diagnostics(src_bad, fname, ARGS)
        self.assertTrue(bad)
        ok = self.engine.diagnostics(src_ok, fname, ARGS)
        self.assertEqual(ok, [])
        self.assertGreaterEqual(
            self.engine.stats["reparses"] + self.engine.stats["parses"], 2)

    def test_cache_prewarm_and_allow_parse_false(self):
        src = (u"struct Point { int x, y; };\n"
               u"void f() {\n    Point p;\n    p.\n}\n")
        fname = self.path("cache.cpp")
        key = "cache-test"
        self.assertFalse(self.engine.is_cached(src, fname, ARGS, key))
        off = src.index("    p.") + len("    p.")
        self.assertEqual(
            self.engine.complete(src, off, fname, ARGS, cache_key=key,
                                 allow_parse=False), [])
        self.engine.prewarm(src, fname, ARGS, key)
        self.assertTrue(self.engine.is_cached(src, fname, ARGS, key))
        items = self.engine.complete(
            src, off, fname, ARGS, cache_key=key, allow_parse=False)
        self.assertIn("x", [i["trigger"] for i in items])
        changed = src.replace("Point p;", "Point p2;")
        self.assertFalse(self.engine.is_cached(changed, fname, ARGS, key))

    def test_cache_eviction(self):
        eng = ca_semantic.SemanticEngine(
            LIBCLANG, cache_limit=1, enable_pch=False)
        try:
            for i in range(3):
                eng.diagnostics(u"int main() { return %d; }\n" % i,
                                self.path("evict%d.cpp" % i), ARGS)
            self.assertLessEqual(len(eng._cache), 1)
        finally:
            eng.close()

    def test_graceful_missing_library(self):
        with self.assertRaises(ca_semantic.SemanticError):
            ca_semantic.SemanticEngine(
                os.path.join(self.tmp, "does-not-exist.dll"))

    def test_universal_header_detection(self):
        self.assertTrue(ca_semantic._has_universal_header(
            u"#include <bits/stdc++.h>\n"))
        self.assertTrue(ca_semantic._has_universal_header(
            u"#include<bits/stdc++.h>\n"))
        self.assertFalse(ca_semantic._has_universal_header(
            u"#include <vector>\n"))

    def test_pch_status_when_disabled(self):
        status = self.engine.pch_status()
        self.assertIn("enabled", status)
        self.assertFalse(status["enabled"])


@unittest.skipUnless(LIBCLANG, "libclang not found")
@unittest.skipUnless(os.environ.get("CA_TEST_LIBCLANG_PCH") == "1",
                     "set CA_TEST_LIBCLANG_PCH=1 to run the slow PCH test")
class SemanticPchTests(unittest.TestCase):
    def test_universal_header_pch(self):
        if not ca_semantic.find_clangpp(None, LIBCLANG):
            self.skipTest("clang++ driver not found")
        pch_root = tempfile.mkdtemp(prefix="ca_pch_test_")
        eng = ca_semantic.SemanticEngine(
            LIBCLANG, cache_limit=1, enable_pch=True,
            pch_dir=os.path.join(pch_root, "pch"))
        try:
            src = (u"#include <bits/stdc++.h>\n"
                   u"using namespace std;\n"
                   u"int main() {\n"
                   u"    vector<int> v;\n"
                   u"    v.\n}\n")
            fname = os.path.join(tempfile.gettempdir(),
                                 "ca_semantic_pch_test.cpp")
            first = eng.diagnostics(src, fname, ARGS)
            self.assertIsInstance(first, list)
            deadline = time.time() + 180
            while eng.pch_status()["building"] and time.time() < deadline:
                time.sleep(0.5)
            status = eng.pch_status()
            self.assertGreaterEqual(status["builds"], 1,
                                    status.get("error") or "")
            off = src.index("    v.") + len("    v.")
            items = eng.complete(src, off, fname, ARGS)
            self.assertIn("push_back", [i["trigger"] for i in items])
            self.assertGreaterEqual(eng.stats["pch_hits"], 1)
        finally:
            eng.close()
            try:
                import shutil
                shutil.rmtree(pch_root, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
