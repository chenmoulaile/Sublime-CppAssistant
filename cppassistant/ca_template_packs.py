# -*- coding: utf-8 -*-
"""Bundled template packs: discovery, XML generation and installation.

Template packs ship inside the package under ``templates/<id>/`` so they work
from a normal directory install and from a zipped Package Control install.
The installer copies the snippets into ``Packages/User/CppAssistantTemplates``
where Sublime's ``find_resources`` can see them and where the user can disable
a pack with a ``.disabled`` marker.
"""

import json
import os
import re
import xml.etree.ElementTree as ET

try:
    import sublime
except ImportError:  # allow unit tests outside Sublime Text
    sublime = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(ROOT, "templates")

PACK_IDS = ("basic", "geometry", "graph", "ds", "strings", "math")

_PACK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_pack_id(pack_id):
    text = str(pack_id or "")
    if not text or not _PACK_ID_RE.match(text):
        return ""
    return text


def safe_pack_id(pack_id):
    """Return ``pack_id`` if it is a safe directory name, else ``""``."""
    return _safe_pack_id(pack_id)


def load_catalog():
    """Load ``templates/index.json`` from the package or the filesystem.

    ``sublime.find_resources`` works for both directory and
    ``.sublime-package`` installs, unlike a hard-coded ``packages_path()``
    join.
    """
    if sublime is not None:
        try:
            paths = sublime.find_resources("templates/index.json")
        except Exception:
            paths = []
        for path in paths:
            try:
                data = json.loads(sublime.load_resource(path))
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("packs"), list):
                return data
    local = os.path.join(TEMPLATES_DIR, "index.json")
    try:
        with open(local, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"packs": []}


def catalog_packs():
    data = load_catalog()
    packs = data.get("packs") if isinstance(data, dict) else None
    return [p for p in (packs or []) if isinstance(p, dict) and p.get("id")]


def _local_path(rel):
    return os.path.join(ROOT, rel.replace("/", os.sep))


def load_pack(pack_id):
    """Return ``[(filename, xml_text), ...]`` for one bundled pack.

    Package resources are preferred (they also work when the package is a
    ``.sublime-package`` zip); the filesystem is the fallback for tests.
    """
    pack_id = _safe_pack_id(pack_id)
    if not pack_id:
        return []
    rel_dir = "templates/" + pack_id
    items = []
    if sublime is not None:
        try:
            paths = sublime.find_resources(rel_dir + "/*.sublime-snippet")
        except Exception:
            paths = []
        for path in sorted(paths):
            try:
                items.append((os.path.basename(path),
                              sublime.load_resource(path)))
            except Exception:
                continue
        if items:
            return items
    directory = os.path.join(TEMPLATES_DIR, str(pack_id))
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".sublime-snippet"):
            continue
        try:
            with open(os.path.join(directory, name), "r",
                      encoding="utf-8") as f:
                items.append((name, f.read()))
        except OSError:
            continue
    return items


def install_pack(pack_id, dest_dir):
    """Copy a bundled pack into ``dest_dir``; return the snippet count."""
    pack_id = _safe_pack_id(pack_id)
    if not pack_id or not dest_dir:
        return 0
    items = load_pack(pack_id)
    if not items:
        return 0
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    for name, text in items:
        target = os.path.join(dest_dir, os.path.basename(name))
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
    disabled = os.path.join(dest_dir, ".disabled")
    if os.path.exists(disabled):
        os.remove(disabled)
    return len(items)


def xml_escape(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def cdata(text):
    # A CDATA section cannot contain "]]>"; split it into two sections.
    return "<![CDATA[" + str(text or "").replace(
        "]]>", "]]]]><![CDATA[>") + "]]>"


def snippet_xml(trigger, content, description, scope="source.c++, source.c"):
    return (u"<snippet>\n"
            u"    <content>%s</content>\n"
            u"    <tabTrigger>%s</tabTrigger>\n"
            u"    <scope>%s</scope>\n"
            u"    <description>%s</description>\n"
            u"</snippet>\n" % (
                cdata(content), xml_escape(trigger), xml_escape(scope),
                xml_escape(description or trigger)))


def write_snippet(path, trigger, content, description,
                  scope="source.c++, source.c"):
    """Write one snippet atomically enough for Sublime's scanner."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    xml = snippet_xml(trigger, content, description, scope)
    # Validate before writing: a broken snippet silently disappears from the
    # completion popup, which is hard to diagnose.
    ET.fromstring(xml.encode("utf-8"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return xml
