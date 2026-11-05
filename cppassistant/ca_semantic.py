# -*- coding: utf-8 -*-
"""SemanticEngine: optional libclang C API bindings for CppAssistant.

This is the "phase two" advanced engine described in the project blueprint.
It talks to the installed ``libclang`` shared library through ctypes; it does
not need the ``clangd`` executable, a compiler driver, or any third-party
Python package.

The module is deliberately self-contained and tolerant:

* :func:`find_libclang` looks in common install locations, the plugin's
  ``libs`` directory, ``LIBCLANG_PATH`` and the configured path.
* :class:`SemanticEngine` parses an unsaved buffer, returns diagnostics,
  code-completion items and go-to-definition locations.
* Every entry point raises :class:`SemanticError` on failure; callers should
  fall back to FastEngine instead of crashing the plugin.

The source keeps Python 3.3 compatible syntax because older Sublime Text 4
builds embed Python 3.3.
"""

import ctypes
import ctypes.util
import glob
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time

try:
    from ctypes import wintypes
except Exception:  # pragma: no cover - non-Windows
    wintypes = None  # type: ignore


# ---------------------------------------------------------------------------
# ctypes structures (clang-c/Index.h)
# ---------------------------------------------------------------------------

class CXString(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p),
                ("private_flags", ctypes.c_uint)]


class CXSourceLocation(ctypes.Structure):
    _fields_ = [("ptr_data", ctypes.c_void_p * 2),
                ("int_data", ctypes.c_uint)]


class CXSourceRange(ctypes.Structure):
    _fields_ = [("ptr_data", ctypes.c_void_p * 2),
                ("begin_int_data", ctypes.c_uint),
                ("end_int_data", ctypes.c_uint)]


class CXCursor(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int),
                ("xdata", ctypes.c_int),
                ("data", ctypes.c_void_p * 3)]


class CXType(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int),
                ("data", ctypes.c_void_p * 2)]


class CXUnsavedFile(ctypes.Structure):
    _fields_ = [("Filename", ctypes.c_char_p),
                ("Contents", ctypes.c_char_p),
                ("Length", ctypes.c_ulonglong)]


class CXCompletionResult(ctypes.Structure):
    _fields_ = [("CursorKind", ctypes.c_int),
                ("CompletionString", ctypes.c_void_p)]


class CXCodeCompleteResults(ctypes.Structure):
    _fields_ = [("Results", ctypes.POINTER(CXCompletionResult)),
                ("NumResults", ctypes.c_uint)]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CXDiagnostic_Ignored = 0
CXDiagnostic_Note = 1
CXDiagnostic_Warning = 2
CXDiagnostic_Error = 3
CXDiagnostic_Fatal = 4

CXTranslationUnit_DetailedPreprocessingRecord = 0x01
CXTranslationUnit_PrecompiledPreamble = 0x04
CXTranslationUnit_CacheCompletionResults = 0x08
CXTranslationUnit_IncludeBriefCommentsInCodeCompletion = 0x80
CXTranslationUnit_CreatePreambleOnFirstParse = 0x100
CXTranslationUnit_KeepGoing = 0x200
CXTranslationUnit_SingleFileParse = 0x400

CXCodeComplete_IncludeMacros = 0x01
CXCodeComplete_IncludeCodePatterns = 0x02
CXCodeComplete_IncludeBriefComments = 0x04

CXCompletionChunk_Optional = 0
CXCompletionChunk_TypedText = 1
CXCompletionChunk_Text = 2
CXCompletionChunk_Placeholder = 3
CXCompletionChunk_Informative = 4
CXCompletionChunk_CurrentParameter = 5
CXCompletionChunk_LeftParen = 6
CXCompletionChunk_RightParen = 7
CXCompletionChunk_LeftBracket = 8
CXCompletionChunk_RightBracket = 9
CXCompletionChunk_LeftBrace = 10
CXCompletionChunk_RightBrace = 11
CXCompletionChunk_LeftAngle = 12
CXCompletionChunk_RightAngle = 13
CXCompletionChunk_Comma = 14
CXCompletionChunk_ResultType = 15
CXCompletionChunk_Colon = 16
CXCompletionChunk_Semicolon = 17
CXCompletionChunk_Equal = 18
CXCompletionChunk_HorizontalSpace = 19
CXCompletionChunk_VerticalSpace = 20

CXAvailability_Available = 0
CXAvailability_Deprecated = 1
CXAvailability_NotAvailable = 2
CXAvailability_NotAccessible = 3

# CXCursorKind values used by completion/definition mapping.
_CURSOR_KIND_LABELS = {
    2: ("t", u"类型", "type"),
    3: ("t", u"类型", "type"),
    4: ("t", u"类型", "type"),
    5: ("t", u"枚举", "enum"),
    6: ("v", u"成员变量", "member"),
    7: ("c", u"枚举值", "enumerator"),
    8: ("f", u"函数", "function"),
    9: ("v", u"变量", "variable"),
    10: ("v", u"参数", "parameter"),
    20: ("t", u"类型别名", "type alias"),
    21: ("m", u"成员函数", "method"),
    22: ("u", u"命名空间", "namespace"),
    24: ("m", u"构造函数", "constructor"),
    25: ("m", u"析构函数", "destructor"),
    26: ("m", u"转换函数", "conversion"),
    27: ("t", u"模板参数", "template parameter"),
    30: ("f", u"函数模板", "function template"),
    31: ("t", u"类模板", "class template"),
    36: ("t", u"类型别名", "type alias"),
    70: ("u", u"符号", "symbol"),
    72: ("k", u"关键字", "keyword"),
    501: ("u", u"宏", "macro"),
    502: ("u", u"宏展开", "macro expansion"),
}

_DEFINITION_KIND = {
    "f": "function",
    "m": "method",
    "v": "variable",
    "t": "type",
    "c": "enumerator",
    "u": "symbol",
}


class SemanticError(Exception):
    """Raised when libclang is missing, cannot be loaded, or fails to parse."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _library_names():
    if os.name == "nt":
        return ["libclang.dll", "clang.dll"]
    if platform.system() == "Darwin":
        return ["libclang.dylib"]
    return ["libclang.so", "libclang.so.1"]


def _add_dir(out, path):
    if not path:
        return
    for name in _library_names():
        out.append(os.path.join(path, name))


def _add_glob(out, pattern):
    try:
        out.extend(sorted(glob.glob(pattern), reverse=True))
    except Exception:
        pass


def _version_key(path):
    """Natural sort key for clang resource directories (18 < 21)."""
    parts = []
    for piece in re.split(r"(\d+)", path):
        if piece.isdigit():
            parts.append((1, int(piece)))
        elif piece:
            parts.append((0, piece.lower()))
    return parts


def find_resource_dir(libclang_path):
    """Return clang's resource directory (the one containing ``include/``).

    libclang locates its builtin headers relative to the *executable* that
    loaded it.  When embedded in Sublime Text that executable is Sublime
    itself, so ``<bits/stdc++.h>`` fails with ``'mm_malloc.h' file not
    found`` and ``'float.h' file not found``.  Passing ``-resource-dir``
    explicitly makes parsing independent of the host executable.
    """
    if not libclang_path:
        return None
    env = os.environ.get("CLANG_RESOURCE_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    lib_dir = os.path.dirname(os.path.abspath(libclang_path))
    roots = [
        os.path.join(lib_dir, "clang"),
        os.path.join(lib_dir, "..", "lib", "clang"),
        os.path.join(lib_dir, "..", "lib64", "clang"),
        os.path.join(lib_dir, "..", "..", "lib", "clang"),
    ]
    found = []
    for root in roots:
        try:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                inc = os.path.join(path, "include")
                if os.path.isdir(inc) and (
                        os.path.isfile(os.path.join(inc, "stddef.h")) or
                        os.path.isfile(os.path.join(
                            inc, "__stddef_max_align_t.h"))):
                    found.append(os.path.abspath(path))
        except OSError:
            continue
    if not found:
        return None
    found.sort(key=_version_key)
    return found[-1]


def find_libclang(configured=None):
    """Return a loadable libclang path, or ``None``.

    Search order: explicit setting, ``LIBCLANG_PATH``, the plugin's ``libs``
    directory, the directory containing clangd/clang, ``ctypes.util`` and
    common LLVM/MSYS2/Xcode/Homebrew locations.
    """
    out = []
    if configured:
        configured = os.path.expanduser(os.path.expandvars(configured))
        if os.path.isdir(configured):
            _add_dir(out, configured)
        elif os.path.isabs(configured) and not os.path.isfile(configured):
            # An explicit absolute path that does not exist should fail
            # instead of silently loading a different libclang.
            return None
        else:
            out.append(configured)

    env = os.environ.get("LIBCLANG_PATH")
    if env:
        env = os.path.expanduser(os.path.expandvars(env))
        if os.path.isdir(env):
            _add_dir(out, env)
        else:
            out.append(env)

    _add_dir(out, os.path.join(_plugin_root(), "libs"))

    for exe in ("clangd", "clang", "clang++"):
        found = shutil.which(exe)
        if found:
            _add_dir(out, os.path.dirname(os.path.abspath(found)))

    try:
        found = ctypes.util.find_library("clang")
        if found:
            out.append(found)
    except Exception:
        pass

    system = platform.system()
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("ProgramW6432")):
            if root:
                _add_dir(out, os.path.join(root, "LLVM", "bin"))
        for root in (r"C:\msys64", r"D:\msys64", r"C:\MSYS2",
                     r"D:\MSYS2"):
            for env_name in ("mingw64", "clang64", "ucrt64"):
                _add_dir(out, os.path.join(root, env_name, "bin"))
    elif system == "Darwin":
        out.append("/Library/Developer/CommandLineTools/usr/lib/"
                   "libclang.dylib")
        out.append("/Applications/Xcode.app/Contents/Developer/"
                   "Toolchains/XcodeDefault.xctoolchain/usr/lib/"
                   "libclang.dylib")
        _add_glob(out, "/opt/homebrew/opt/llvm/lib/libclang*.dylib")
        _add_glob(out, "/usr/local/opt/llvm/lib/libclang*.dylib")
    else:
        _add_glob(out, "/usr/lib/llvm-*/lib/libclang.so*")
        _add_glob(out, "/usr/lib/*/libclang-*.so*")
        _add_glob(out, "/usr/lib/libclang.so*")
        _add_glob(out, "/usr/local/lib/libclang.so*")

    seen = set()
    for path in out:
        if not path:
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(path):
            return path
        # find_library may return a soname that the loader can resolve.
        # Only accept absolute paths or paths that exist.
        if not os.path.isabs(path) and not os.path.exists(path):
            continue
        # Try to load it - if it works, return the soname
        try:
            loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
            loader(path)
            return path
        except OSError:
            continue
    return None


def clang_version(path=None):
    """Return the libclang version string, or ``None`` when unavailable."""
    try:
        lib = _load_library(path or find_libclang())
        return _version_of(lib)
    except Exception:
        return None


def find_clangpp(configured=None, libclang_path=None):
    """Return a clang++ driver compatible with libclang, or ``None``.

    The driver is only needed for optional PCH precompilation; the semantic
    engine itself talks to libclang directly.
    """
    out = []
    if configured:
        configured = os.path.expanduser(os.path.expandvars(configured))
        if os.path.isdir(configured):
            for name in ("clang++.exe", "clang++", "clang.exe", "clang"):
                out.append(os.path.join(configured, name))
        else:
            out.append(configured)
    if libclang_path:
        d = os.path.dirname(os.path.abspath(libclang_path))
        for name in ("clang++.exe", "clang++", "clang.exe", "clang"):
            out.append(os.path.join(d, name))
        for sub in ("bin", os.path.join("..", "bin")):
            for name in ("clang++.exe", "clang++", "clang.exe", "clang"):
                out.append(os.path.join(d, sub, name))
    for name in ("clang++", "clang"):
        found = shutil.which(name)
        if found:
            out.append(found)
    seen = set()
    for path in out:
        if not path:
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _version_tuple(text):
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)),
            int(match.group(3) or 0))


def _clangpp_version(path):
    try:
        creationflags = 0x08000000 if os.name == "nt" else 0
        startupinfo = _hidden_startupinfo()
        proc = subprocess.Popen(
            [path, "--version"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=creationflags, startupinfo=startupinfo)
        out, _ = proc.communicate(timeout=15)
        return _version_tuple(out.decode("utf-8", "replace"))
    except Exception:
        return None


def _hidden_startupinfo():
    if os.name != "nt":
        return None
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    except Exception:
        return None


def _has_universal_header(text):
    return bool(re.search(
        r"^\s*#\s*include\s*[<\"](?:bits/)?stdc\+\+\.h[>\"]",
        text or "", re.MULTILINE))


def _load_library(path):
    if not path:
        raise SemanticError("libclang not found")
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    try:
        return loader(path)
    except OSError as e:
        raise SemanticError("cannot load libclang at %s: %s" % (path, e))


def _version_of(lib):
    try:
        lib.clang_getClangVersion.restype = CXString
        lib.clang_getClangVersion.argtypes = []
        lib.clang_getCString.restype = ctypes.c_char_p
        lib.clang_getCString.argtypes = [CXString]
        lib.clang_disposeString.argtypes = [CXString]
        value = lib.clang_getClangVersion()
        raw = lib.clang_getCString(value)
        text = raw.decode("utf-8", "replace") if raw else u""
        lib.clang_disposeString(value)
        return text
    except Exception:
        return u""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SemanticEngine(object):
    """A small, thread-safe wrapper around the libclang C API."""

    def __init__(self, libclang_path=None, cache_limit=4,
                 resource_dir=None, args=None, clang_path=None,
                 enable_pch=True, pch_dir=None):
        self.path = find_libclang(libclang_path)
        if not self.path:
            raise SemanticError("libclang not found")
        self.lib = _load_library(self.path)
        self._bind()
        self.resource_dir = (os.path.abspath(os.path.expanduser(resource_dir))
                             if resource_dir else
                             find_resource_dir(self.path))
        self.base_args = [str(a) for a in (args or [])]
        self.cache_limit = max(1, int(cache_limit))
        self._cache = {}
        self._order = []
        self._lock = threading.RLock()
        self._index = self.clang_createIndex(0, 0)
        if not self._index:
            raise SemanticError("clang_createIndex failed")
        self.version = _version_of(self.lib)
        self.clang_path = find_clangpp(clang_path, self.path)
        self.enable_pch = bool(enable_pch) and self.clang_path is not None
        self.pch_dir = pch_dir or os.path.join(
            tempfile.gettempdir(), "CppAssistant", "semantic_pch")
        self._pch_lock = threading.Lock()
        self._pch_building = set()
        self._pch_disabled = set()
        self._pch_errors = {}
        if self.enable_pch and not self._clangpp_matches():
            self.enable_pch = False
        # Initial PCH sweep to clean up old/failed builds
        if self.enable_pch:
            self._sweep_pch(None)
        self.stats = {
            "parses": 0,
            "reparses": 0,
            "completions": 0,
            "definitions": 0,
            "diagnostics": 0,
            "last_ms": 0.0,
            "pch_builds": 0,
            "pch_hits": 0,
            "pch_misses": 0,
        }

    def _clangpp_matches(self):
        """Only use a driver whose major.minor matches libclang."""
        ours = _version_tuple(self.version)
        theirs = _clangpp_version(self.clang_path) if self.clang_path else None
        if ours is None or theirs is None:
            return True
        return ours[:2] == theirs[:2]

    # -- binding -----------------------------------------------------------

    def _bind(self):
        lib = self.lib

        def fn(name, restype, argtypes):
            try:
                func = getattr(lib, name)
            except AttributeError:
                raise SemanticError("libclang is missing %s" % name)
            func.restype = restype
            func.argtypes = argtypes
            return func

        P = ctypes.POINTER
        c = ctypes
        void_p = c.c_void_p
        char_p = c.c_char_p
        uint = c.c_uint
        int_ = c.c_int

        self.clang_createIndex = fn(
            "clang_createIndex", void_p, [int_, int_])
        self.clang_disposeIndex = fn(
            "clang_disposeIndex", None, [void_p])
        self.clang_parseTranslationUnit2 = fn(
            "clang_parseTranslationUnit2", int_,
            [void_p, char_p, P(char_p), int_, P(CXUnsavedFile), uint,
             uint, P(void_p)])
        self.clang_reparseTranslationUnit = fn(
            "clang_reparseTranslationUnit", int_,
            [void_p, uint, P(CXUnsavedFile), uint])
        self.clang_disposeTranslationUnit = fn(
            "clang_disposeTranslationUnit", None, [void_p])
        self.clang_getTranslationUnitCursor = fn(
            "clang_getTranslationUnitCursor", CXCursor, [void_p])

        self.clang_getNumDiagnostics = fn(
            "clang_getNumDiagnostics", uint, [void_p])
        self.clang_getDiagnostic = fn(
            "clang_getDiagnostic", void_p, [void_p, uint])
        self.clang_disposeDiagnostic = fn(
            "clang_disposeDiagnostic", None, [void_p])
        self.clang_getDiagnosticSeverity = fn(
            "clang_getDiagnosticSeverity", int_, [void_p])
        self.clang_getDiagnosticSpelling = fn(
            "clang_getDiagnosticSpelling", CXString, [void_p])
        self.clang_formatDiagnostic = fn(
            "clang_formatDiagnostic", CXString, [void_p, uint])
        self.clang_getDiagnosticLocation = fn(
            "clang_getDiagnosticLocation", CXSourceLocation, [void_p])

        self.clang_getExpansionLocation = fn(
            "clang_getExpansionLocation", None,
            [CXSourceLocation, P(void_p), P(uint), P(uint), P(uint)])
        self.clang_getSpellingLocation = fn(
            "clang_getSpellingLocation", None,
            [CXSourceLocation, P(void_p), P(uint), P(uint), P(uint)])
        self.clang_getFileLocation = fn(
            "clang_getFileLocation", None,
            [CXSourceLocation, P(void_p), P(uint), P(uint), P(uint)])
        self.clang_getFileName = fn(
            "clang_getFileName", CXString, [void_p])
        self.clang_Location_isFromMainFile = fn(
            "clang_Location_isFromMainFile", int_, [CXSourceLocation])
        self.clang_getFile = fn(
            "clang_getFile", void_p, [void_p, char_p])
        self.clang_getLocationForOffset = fn(
            "clang_getLocationForOffset", CXSourceLocation,
            [void_p, void_p, uint])
        self.clang_getCursor = fn(
            "clang_getCursor", CXCursor, [void_p, CXSourceLocation])

        self.clang_getCursorKind = fn(
            "clang_getCursorKind", int_, [CXCursor])
        self.clang_getCursorKindSpelling = fn(
            "clang_getCursorKindSpelling", CXString, [int_])
        self.clang_getCursorSpelling = fn(
            "clang_getCursorSpelling", CXString, [CXCursor])
        self.clang_getCursorDisplayName = fn(
            "clang_getCursorDisplayName", CXString, [CXCursor])
        self.clang_getCursorLocation = fn(
            "clang_getCursorLocation", CXSourceLocation, [CXCursor])
        self.clang_getCursorExtent = fn(
            "clang_getCursorExtent", CXSourceRange, [CXCursor])
        self.clang_getCursorType = fn(
            "clang_getCursorType", CXType, [CXCursor])
        self.clang_getTypeSpelling = fn(
            "clang_getTypeSpelling", CXString, [CXType])
        self.clang_getCursorReferenced = fn(
            "clang_getCursorReferenced", CXCursor, [CXCursor])
        self.clang_getCursorDefinition = fn(
            "clang_getCursorDefinition", CXCursor, [CXCursor])
        self.clang_isCursorDefinition = fn(
            "clang_isCursorDefinition", uint, [CXCursor])
        self.clang_isDeclaration = fn(
            "clang_isDeclaration", uint, [int_])
        self.clang_getCursorSemanticParent = fn(
            "clang_getCursorSemanticParent", CXCursor, [CXCursor])
        self.clang_getCursorUSR = fn(
            "clang_getCursorUSR", CXString, [CXCursor])
        self.clang_Cursor_isNull = fn(
            "clang_Cursor_isNull", int_, [CXCursor])
        self.clang_equalCursors = fn(
            "clang_equalCursors", uint, [CXCursor, CXCursor])

        self.clang_codeCompleteAt = fn(
            "clang_codeCompleteAt", P(CXCodeCompleteResults),
            [void_p, char_p, uint, uint, P(CXUnsavedFile), uint, uint])
        self.clang_disposeCodeCompleteResults = fn(
            "clang_disposeCodeCompleteResults", None,
            [P(CXCodeCompleteResults)])
        self.clang_codeCompleteGetNumDiagnostics = fn(
            "clang_codeCompleteGetNumDiagnostics", uint,
            [P(CXCodeCompleteResults)])
        self.clang_codeCompleteGetDiagnostic = fn(
            "clang_codeCompleteGetDiagnostic", void_p,
            [P(CXCodeCompleteResults), uint])
        self.clang_getNumCompletionChunks = fn(
            "clang_getNumCompletionChunks", uint, [void_p])
        self.clang_getCompletionChunkText = fn(
            "clang_getCompletionChunkText", CXString, [void_p, uint])
        self.clang_getCompletionChunkKind = fn(
            "clang_getCompletionChunkKind", int_, [void_p, uint])
        self.clang_getCompletionChunkCompletionString = fn(
            "clang_getCompletionChunkCompletionString", void_p,
            [void_p, uint])
        self.clang_getCompletionNumAnnotations = fn(
            "clang_getCompletionNumAnnotations", uint, [void_p])
        self.clang_getCompletionAnnotation = fn(
            "clang_getCompletionAnnotation", CXString, [void_p, uint])
        self.clang_getCompletionBriefComment = fn(
            "clang_getCompletionBriefComment", CXString, [void_p])
        self.clang_getCompletionPriority = fn(
            "clang_getCompletionPriority", uint, [void_p])
        self.clang_getCompletionAvailability = fn(
            "clang_getCompletionAvailability", int_, [void_p])
        self.clang_getCompletionParent = fn(
            "clang_getCompletionParent", CXString,
            [void_p, P(int_)])
        self.clang_getCString = fn(
            "clang_getCString", char_p, [CXString])
        self.clang_disposeString = fn(
            "clang_disposeString", None, [CXString])
        self.clang_getClangVersion = fn(
            "clang_getClangVersion", CXString, [])

    # -- helpers -----------------------------------------------------------

    def _cxstring(self, value):
        try:
            raw = self.lib.clang_getCString(value)
            if raw:
                return raw.decode("utf-8", "replace")
            return u""
        except Exception:
            return u""
        finally:
            try:
                self.lib.clang_disposeString(value)
            except Exception:
                pass

    def _location(self, location, spelling=False):
        file_ptr = ctypes.c_void_p()
        line = ctypes.c_uint()
        col = ctypes.c_uint()
        offset = ctypes.c_uint()
        func = (self.clang_getSpellingLocation if spelling
                else self.clang_getExpansionLocation)
        func(location, ctypes.byref(file_ptr), ctypes.byref(line),
             ctypes.byref(col), ctypes.byref(offset))
        fname = u""
        if file_ptr:
            fname = self._cxstring(self.clang_getFileName(file_ptr))
        return {
            "file": fname,
            "line": int(line.value),
            "col": int(col.value),
            "offset": int(offset.value),
        }

    def _line_col(self, text, offset):
        offset = max(0, min(offset, len(text)))
        line_start = text.rfind("\n", 0, offset) + 1
        line = text.count("\n", 0, offset) + 1
        # libclang expects a byte column for UTF-8 source.
        col = len(text[line_start:offset].encode("utf-8")) + 1
        return line, col

    def _filename(self, filename):
        if not filename:
            return os.path.join(tempfile.gettempdir(),
                                "cppassistant_unsaved.cpp")
        return os.path.abspath(filename)

    def _unsaved(self, filename, text):
        fname = filename.encode("utf-8")
        contents = text.encode("utf-8")
        value = CXUnsavedFile(fname, contents, len(contents))
        return fname, contents, value

    def _cache_key(self, filename, args, cache_key):
        return (cache_key, os.path.normcase(filename), tuple(args))

    def _normalize_args(self, args):
        """Merge engine defaults and inject the discovered resource dir."""
        merged = list(self.base_args)
        merged.extend(str(a) for a in (args or []))
        if self.resource_dir and not any(
                a.startswith("-resource-dir") or
                a.startswith("--resource-dir") for a in merged):
            merged.append("-resource-dir=" + self.resource_dir)
        return merged

    # -- PCH acceleration --------------------------------------------------

    def _pch_signature(self, args):
        payload = "\0".join([
            os.path.normcase(self.path),
            self.version or "",
            os.path.normcase(self.clang_path or ""),
            self.resource_dir or "",
            "\0".join(args),
            "bits/stdc++.h",
        ])
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]

    def _pch_paths(self, sig):
        d = os.path.join(self.pch_dir, sig)
        return d, os.path.join(d, "ca_semantic_pch.h"), \
            os.path.join(d, "ca_semantic_pch.pch")

    def _pch_ready(self, sig):
        _, _, pch = self._pch_paths(sig)
        try:
            return os.path.isfile(pch) and os.path.getsize(pch) > 0
        except OSError:
            return False

    def _strip_pch(self, args):
        out = []
        i = 0
        while i < len(args):
            value = args[i]
            if value == "-include-pch":
                i += 2
                continue
            if value.startswith("-include-pch="):
                i += 1
                continue
            out.append(value)
            i += 1
        return out

    def _build_pch(self, args, sig):
        d, hdr, pch = self._pch_paths(sig)
        tmp = pch + ".tmp"
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
            with open(hdr, "w", encoding="utf-8") as f:
                f.write(u"#include <bits/stdc++.h>\n")
            cmd = [self.clang_path]
            clean = self._strip_pch(args)
            i = 0
            while i < len(clean):
                value = clean[i]
                if value in ("-o", "-c", "-S", "-E", "-fsyntax-only",
                             "-x"):
                    if value in ("-o", "-x"):
                        i += 2
                    else:
                        i += 1
                    continue
                cmd.append(value)
                i += 1
            cmd += ["-x", "c++-header", hdr, "-o", tmp]
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, creationflags=creationflags,
                startupinfo=_hidden_startupinfo())
            _, err = proc.communicate(timeout=180)
            if proc.returncode == 0 and os.path.isfile(tmp):
                os.replace(tmp, pch)
                self.stats["pch_builds"] += 1
                self._sweep_pch(sig)
            else:
                self._pch_errors[sig] = (err or b"").decode(
                    "utf-8", "replace")[-400:]
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        except Exception as e:
            self._pch_errors[sig] = str(e)
        finally:
            with self._pch_lock:
                self._pch_building.discard(sig)

    def _sweep_pch(self, keep_sig):
        try:
            for name in os.listdir(self.pch_dir):
                if name == keep_sig:
                    continue
                path = os.path.join(self.pch_dir, name)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    def _ensure_pch(self, args, sig):
        if not self.enable_pch or sig in self._pch_disabled:
            return
        if self._pch_ready(sig):
            return
        with self._pch_lock:
            if sig in self._pch_building:
                return
            self._pch_building.add(sig)
        th = threading.Thread(target=self._build_pch, args=(args, sig))
        th.daemon = True
        th.start()

    def _effective_args(self, text, args):
        """Add the precompiled universal header when it is useful/ready."""
        merged = self._normalize_args(args)
        if not self.enable_pch or not _has_universal_header(text):
            return merged
        sig = self._pch_signature(merged)
        if sig in self._pch_disabled:
            return merged
        if self._pch_ready(sig):
            self.stats["pch_hits"] += 1
            return merged + ["-include-pch", self._pch_paths(sig)[2]]
        self.stats["pch_misses"] += 1
        self._ensure_pch(merged, sig)
        return merged

    def pch_status(self):
        """Return a small dictionary for the host's engine-status panel."""
        return {
            "enabled": bool(self.enable_pch),
            "clang": self.clang_path,
            "dir": self.pch_dir,
            "builds": self.stats.get("pch_builds", 0),
            "hits": self.stats.get("pch_hits", 0),
            "misses": self.stats.get("pch_misses", 0),
            "building": len(self._pch_building),
            "error": (sorted(self._pch_errors.values())[-1]
                      if self._pch_errors else u""),
        }

    def _store(self, key, entry):
        if key in self._cache:
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._cache[key] = entry
        self._order.append(key)
        while len(self._order) > self.cache_limit:
            old = self._order.pop(0)
            evicted = self._cache.pop(old, None)
            if evicted is not None:
                try:
                    self.clang_disposeTranslationUnit(evicted["tu"])
                except Exception:
                    pass

    def clear(self):
        with self._lock:
            for entry in self._cache.values():
                try:
                    self.clang_disposeTranslationUnit(entry["tu"])
                except Exception:
                    pass
            self._cache = {}
            self._order = []

    def close(self):
        with self._lock:
            self.clear()
            if self._index:
                try:
                    self.clang_disposeIndex(self._index)
                except Exception:
                    pass
                self._index = None

    # -- parsing -----------------------------------------------------------

    def _create_tu(self, filename, args, unsaved, options):
        fname = filename.encode("utf-8")
        arg_array = (ctypes.c_char_p * len(args))(
            *[a.encode("utf-8") for a in args]) if args else None
        tu = ctypes.c_void_p()
        err = self.clang_parseTranslationUnit2(
            self._index, fname, arg_array, len(args),
            ctypes.byref(unsaved), 1, options, ctypes.byref(tu))
        return tu, err

    def _parse(self, text, filename=None, args=None, cache_key=None,
               options=None, allow_parse=True):
        filename = self._filename(filename)
        effective = self._effective_args(text, args)
        key = self._cache_key(filename, effective, cache_key)
        entry = self._cache.get(key)
        fname, contents, unsaved = self._unsaved(filename, text)
        if options is None:
            options = (CXTranslationUnit_KeepGoing |
                       CXTranslationUnit_CacheCompletionResults |
                       CXTranslationUnit_IncludeBriefCommentsInCodeCompletion |
                       CXTranslationUnit_CreatePreambleOnFirstParse)

        if entry is not None and entry["text"] == text:
            return entry["tu"]
        if not allow_parse:
            return None
        t0 = time.perf_counter()
        if entry is not None:
            # Incremental reparse: libclang keeps the preamble and only
            # reprocesses what changed.
            err = self.clang_reparseTranslationUnit(
                entry["tu"], 1, ctypes.byref(unsaved), options)
            if err == 0:
                entry["text"] = text
                self.stats["reparses"] += 1
                self.stats["last_ms"] = (time.perf_counter() - t0) * 1000.0
                return entry["tu"]
            # Reparse failed: fall through and rebuild from scratch.
            try:
                self.clang_disposeTranslationUnit(entry["tu"])
            except Exception:
                pass
            self._cache.pop(key, None)
            try:
                self._order.remove(key)
            except ValueError:
                pass
            entry = None

        parse_args = effective
        tu, err = self._create_tu(filename, parse_args, unsaved, options)
        if (err != 0 or not tu) and "-include-pch" in parse_args:
            # The cached PCH may be stale or built with incompatible
            # language options.  Retry without it and stop using it for the
            # rest of this session.
            base = self._strip_pch(parse_args)
            self._pch_disabled.add(self._pch_signature(base))
            parse_args = base
            key = self._cache_key(filename, parse_args, cache_key)
            tu, err = self._create_tu(filename, parse_args, unsaved, options)
        if err != 0 or not tu:
            raise SemanticError("libclang parse failed (error %d)" % err)
        entry = {"tu": tu, "text": text, "args": tuple(parse_args),
                 "filename": filename}
        self._store(key, entry)
        self.stats["parses"] += 1
        self.stats["last_ms"] = (time.perf_counter() - t0) * 1000.0
        return tu

    # -- diagnostics -------------------------------------------------------

    def diagnostics(self, text, filename=None, args=None, cache_key=None,
                    version=None, main_file_only=True):
        """Return diagnostics as dictionaries.

        ``line`` and ``col`` are 1-based (compiler-diagnostic convention),
        ``offset`` is 0-based.  Notes are attached to the preceding
        error/warning when possible.
        """
        with self._lock:
            tu = self._parse(text, filename, args, cache_key)
            self.stats["diagnostics"] += 1
            out = []
            count = self.clang_getNumDiagnostics(tu)
            for i in range(count):
                diag = self.clang_getDiagnostic(tu, i)
                if not diag:
                    continue
                try:
                    severity = self.clang_getDiagnosticSeverity(diag)
                    if severity == CXDiagnostic_Ignored:
                        continue
                    loc = self.clang_getDiagnosticLocation(diag)
                    if main_file_only and not \
                            self.clang_Location_isFromMainFile(loc):
                        continue
                    info = self._location(loc)
                    message = self._cxstring(
                        self.clang_getDiagnosticSpelling(diag))
                    if severity == CXDiagnostic_Note:
                        note = {
                            "line": info["line"] or 1,
                            "col": info["col"] or 1,
                            "msg": message,
                            "zh": message,
                        }
                        if out:
                            out[-1]["notes"].append(note)
                        continue
                    sev = ("error" if severity >= CXDiagnostic_Error
                           else "warning")
                    code_match = re.search(r"\[-W([\w-]+)\]", message)
                    code = code_match.group(1) if code_match else "libclang"
                    en, zh = _translate(message)
                    out.append({
                        "line": info["line"] or 1,
                        "col": info["col"] or 1,
                        "severity": sev,
                        "en": en,
                        "zh": zh,
                        "msg": en,
                        "code": code,
                        "offset": info["offset"],
                        "file": info["file"],
                        "notes": [],
                    })
                finally:
                    self.clang_disposeDiagnostic(diag)
            return out

    def compiler_diagnostics(self, text, filename=None, args=None,
                             cache_key=None, version=None):
        """Return diagnostics in the host plugin's compiler-diagnostic shape."""
        diags = self.diagnostics(text, filename, args, cache_key, version)
        out = []
        for d in diags:
            item = {
                "line": d["line"],
                "col": d["col"],
                "sev_en": d["severity"],
                "msg": d["en"],
                "zh": d["zh"],
                "ctx": "",
                "ctx_zh": "",
                "notes": d.get("notes") or [],
                "tier": "compiler",
                "code": d.get("code") or "",
            }
            out.append(item)
        return out

    # -- completion --------------------------------------------------------

    def is_cached(self, text, filename=None, args=None, cache_key=None):
        """Return True when a matching translation unit is already parsed."""
        with self._lock:
            filename = self._filename(filename)
            effective = self._effective_args(text, args)
            key = self._cache_key(filename, effective, cache_key)
            entry = self._cache.get(key)
            return entry is not None and entry["text"] == text

    def prewarm(self, text, filename=None, args=None, cache_key=None):
        """Parse in the caller's background thread (used for F12/completion)."""
        with self._lock:
            self._parse(text, filename, args, cache_key)

    def is_busy(self):
        """True while another thread is parsing/completing.

        The host uses this to keep libclang off the UI thread's critical
        path instead of blocking on the engine lock.
        """
        acquired = self._lock.acquire(False)
        if acquired:
            self._lock.release()
            return False
        return True

    def complete(self, text, offset, filename=None, args=None, cache_key=None,
                 version=None, prefix=None, limit=200, allow_parse=True):
        """Return plugin-shaped completion dictionaries, sorted by libclang."""
        with self._lock:
            filename = self._filename(filename)
            if not allow_parse and not self.is_cached(
                    text, filename, args, cache_key):
                return []
            tu = self._parse(text, filename, args, cache_key,
                             allow_parse=allow_parse)
            if tu is None:
                return []
            line, col = self._line_col(text, offset)
            fname, contents, unsaved = self._unsaved(filename, text)
            results = self.clang_codeCompleteAt(
                tu, fname, line, col, ctypes.byref(unsaved), 1,
                CXCodeComplete_IncludeMacros |
                CXCodeComplete_IncludeCodePatterns |
                CXCodeComplete_IncludeBriefComments)
            if not results:
                return []
            try:
                self.stats["completions"] += 1
                out = []
                count = results.contents.NumResults
                for i in range(count):
                    item = self._completion_item(results.contents.Results[i])
                    if item is not None:
                        out.append(item)
                out.sort(key=lambda d: (d.get("_score", 9999),
                                        len(d["trigger"]), d["trigger"]))
                for d in out:
                    d.pop("_score", None)
                return out[:limit]
            finally:
                self.clang_disposeCodeCompleteResults(results)

    def _completion_item(self, result):
        cs = result.CompletionString
        if not cs:
            return None
        try:
            availability = self.clang_getCompletionAvailability(cs)
            if availability in (CXAvailability_NotAvailable,
                                CXAvailability_NotAccessible):
                return None
        except Exception:
            pass
        kind = result.CursorKind
        chunks = []
        typed = []
        detail = u""
        param_index = 0
        try:
            count = self.clang_getNumCompletionChunks(cs)
        except Exception:
            count = 0
        for i in range(count):
            try:
                chunk_kind = self.clang_getCompletionChunkKind(cs, i)
                text = self._cxstring(
                    self.clang_getCompletionChunkText(cs, i))
            except Exception:
                continue
            if chunk_kind == CXCompletionChunk_TypedText:
                typed.append(text)
                chunks.append(text)
            elif chunk_kind in (CXCompletionChunk_Text,
                                CXCompletionChunk_Informative):
                chunks.append(text)
            elif chunk_kind == CXCompletionChunk_Placeholder:
                param_index += 1
                chunks.append("${%d:%s}" % (param_index, text))
            elif chunk_kind == CXCompletionChunk_ResultType:
                detail = text
            elif chunk_kind == CXCompletionChunk_CurrentParameter:
                continue
            elif chunk_kind == CXCompletionChunk_VerticalSpace:
                chunks.append(" ")
            elif chunk_kind == CXCompletionChunk_Optional:
                continue
            else:
                chunks.append(text)
        insert = u"".join(chunks).strip()
        trigger = u"".join(typed).strip()
        if not trigger:
            trigger = insert.split("(", 1)[0].strip()
        if not trigger:
            return None
        code, zh, en = _CURSOR_KIND_LABELS.get(
            kind, ("u", u"符号", "symbol"))
        if not detail:
            try:
                detail = self._cxstring(
                    self.clang_getCompletionBriefComment(cs))
            except Exception:
                detail = u""
        if not detail:
            try:
                detail = self._cxstring(
                    self.clang_getCursorKindSpelling(kind))
            except Exception:
                detail = en
        try:
            priority = int(self.clang_getCompletionPriority(cs))
        except Exception:
            priority = 9999
        return {
            "trigger": trigger,
            "insert": insert or trigger,
            "annotation": zh,
            "annotation_en": en,
            "kind": code,
            "detail": detail,
            "detail_en": detail,
            "_score": priority,
        }

    # -- definitions -------------------------------------------------------

    def _cursor_target_at(self, tu, file_ptr, text, char_offset):
        """Return (target cursor, score) at a character offset.

        Sublime's ``word_region.end()`` points just *after* the identifier,
        so the caller probes both the given offset and the preceding
        character.  Scores let a real reference beat the surrounding
        statement cursor.
        """
        char_offset = max(0, min(char_offset, len(text)))
        byte_offset = len(text[:char_offset].encode("utf-8"))
        loc = self.clang_getLocationForOffset(tu, file_ptr, byte_offset)
        cursor = self.clang_getCursor(tu, loc)
        if self.clang_Cursor_isNull(cursor):
            return None, 0
        kind = self.clang_getCursorKind(cursor)
        referenced = self.clang_getCursorReferenced(cursor)
        if not self.clang_Cursor_isNull(referenced):
            return referenced, 3
        if self.clang_isDeclaration(kind):
            return cursor, 2
        spelling = self._cxstring(self.clang_getCursorSpelling(cursor))
        if spelling:
            return cursor, 1
        return None, 0

    def find_definitions(self, text, offset=0, filename=None, args=None,
                         cache_key=None, version=None, allow_parse=True):
        """Return go-to-definition candidates for the symbol at ``offset``.

        ``col`` is 0-based and ``line`` is 1-based, matching the host
        plugin's F12 candidate format.  Cross-file locations include ``file``.
        """
        with self._lock:
            filename = self._filename(filename)
            if not allow_parse and not self.is_cached(
                    text, filename, args, cache_key):
                return []
            tu = self._parse(text, filename, args, cache_key,
                             allow_parse=allow_parse)
            if tu is None:
                return []
            fname = filename.encode("utf-8")
            file_ptr = self.clang_getFile(tu, fname)
            if not file_ptr:
                return []
            offset = max(0, min(offset, len(text)))
            target = None
            best_score = -1
            for pos in (offset, offset - 1):
                if pos < 0:
                    continue
                candidate, score = self._cursor_target_at(
                    tu, file_ptr, text, pos)
                if score > best_score:
                    target, best_score = candidate, score
                if best_score >= 3:
                    break
            if target is None:
                return []
            definition = self.clang_getCursorDefinition(target)
            if not self.clang_Cursor_isNull(definition):
                target = definition
            self.stats["definitions"] += 1
            info = self._cursor_info(target)
            if info is None:
                return []
            return [info]

    def _cursor_info(self, cursor):
        if self.clang_Cursor_isNull(cursor):
            return None
        kind = self.clang_getCursorKind(cursor)
        spelling = self._cxstring(self.clang_getCursorSpelling(cursor))
        type_text = self._cxstring(
            self.clang_getTypeSpelling(self.clang_getCursorType(cursor)))
        display = self._cxstring(
            self.clang_getCursorDisplayName(cursor)) or spelling
        loc = self._location(self.clang_getCursorLocation(cursor))
        code, _zh, _en = _CURSOR_KIND_LABELS.get(
            kind, ("u", u"符号", "symbol"))
        display = display or spelling or type_text
        if code in ("f", "m"):
            label = display
            preview = (u"%s %s" % (type_text, display)).strip() \
                if type_text else label
        else:
            if type_text and display and type_text != display:
                label = u"%s %s" % (type_text, display)
            else:
                label = display or type_text
            preview = label
        return {
            "file": loc["file"],
            "line": loc["line"] or 1,
            "col": max(loc["col"] - 1, 0),
            "offset": loc["offset"],
            "label": label,
            "preview": preview,
            "kind": _DEFINITION_KIND.get(code, "symbol"),
            "is_definition": bool(self.clang_isCursorDefinition(cursor)),
        }


# ---------------------------------------------------------------------------
# Translation helper
# ---------------------------------------------------------------------------

def _translate(message):
    """Best-effort compiler-style translation, without a hard dependency."""
    try:
        from . import ca_engine
        en, zh = ca_engine.translate_message(message)
        return en, zh
    except Exception:
        return message, message


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ENGINE = None
_ENGINE_PATH = None
_ENGINE_SIG = None
_ENGINE_LOCK = threading.Lock()


def engine(path=None, clang_path=None, enable_pch=True, args=None,
           resource_dir=None):
    """Return the shared engine, creating it on first use.

    The singleton is recreated when the relevant configuration changes, so
    settings take effect without restarting Sublime Text.
    """
    global _ENGINE, _ENGINE_PATH, _ENGINE_SIG
    sig = (path or "", clang_path or "", bool(enable_pch),
           tuple(args or []), resource_dir or "")
    with _ENGINE_LOCK:
        if _ENGINE is not None and sig == _ENGINE_SIG:
            return _ENGINE
        if _ENGINE is not None:
            try:
                _ENGINE.close()
            except Exception:
                pass
            _ENGINE = None
        _ENGINE = SemanticEngine(
            path, clang_path=clang_path, enable_pch=enable_pch,
            args=args, resource_dir=resource_dir)
        _ENGINE_PATH = path
        _ENGINE_SIG = sig
        # Start periodic PCH sweep (every 30 minutes)
        if _ENGINE.enable_pch:
            def _periodic_pch_sweep():
                try:
                    _ENGINE._sweep_pch(None)
                except Exception:
                    pass
                # Reschedule
                threading.Timer(1800, _periodic_pch_sweep).start()
            t = threading.Timer(1800, _periodic_pch_sweep)
            t.daemon = True
            t.start()
        return _ENGINE


def available(path=None):
    """Return True when libclang can be discovered and loaded."""
    try:
        engine(path)
        return True
    except Exception:
        return False


def version(path=None):
    try:
        return engine(path).version
    except Exception:
        return None
