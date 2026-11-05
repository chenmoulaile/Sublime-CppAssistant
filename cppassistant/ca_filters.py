# -*- coding: utf-8 -*-
"""Diagnostic filter normalisation.

Sublime settings and compiler output use several spellings of the same
warning (``-Wunused-variable``, ``Wno-unused-variable``,
``unused-variable``).  The filter compares against the warning code that the
parsers preserve on each diagnostic, so all three spellings have to map to
the same value.  Anything that is not a plain warning name is rejected; it
is passed to the compiler as ``-Wno-<name>`` after all.
"""

import re

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9+_-]*$")

_UNUSED_CODES = ("unused-variable", "unused-but-set-variable")


def normalize_diagnostic_filter(value):
    if value is not None and not isinstance(value, str):
        return ""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.startswith("-Wno-"):
        s = s[5:]
    elif s.startswith("Wno-"):
        s = s[4:]
    elif s.startswith("-W"):
        s = s[2:]
    elif s.startswith("W") and len(s) > 1 and s[1].islower():
        s = s[1:]
    s = s.strip().lower()
    if not _CODE_RE.match(s):
        return ""
    return s


def diagnostic_filter_codes(values):
    out = []
    for item in values or ():
        code = normalize_diagnostic_filter(item)
        if code and code not in out:
            out.append(code)
    return out


def diagnostic_filter_flags(values, suppress_unused_warnings=True):
    flags = []
    for code in diagnostic_filter_codes(values):
        flag = "-Wno-" + code
        if flag not in flags:
            flags.append(flag)
    if suppress_unused_warnings:
        for code in _UNUSED_CODES:
            flag = "-Wno-" + code
            if flag not in flags:
                flags.append(flag)
    return flags
