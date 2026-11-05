# CppAssistant

**English** · [简体中文](README.zh-CN.md)

[![Package Control](https://img.shields.io/packagecontrol/v/CppAssistant.svg)](https://packagecontrol.io/packages/CppAssistant)
[![Package Control Downloads](https://img.shields.io/packagecontrol/dt/CppAssistant.svg)](https://packagecontrol.io/packages/CppAssistant)
[![Latest release](https://img.shields.io/github/v/release/chenmoulaile/Sublime-CppAssistant.svg)](https://github.com/chenmoulaile/Sublime-CppAssistant/releases)
[![GitHub Actions](https://github.com/chenmoulaile/Sublime-CppAssistant/workflows/tests/badge.svg)](https://github.com/chenmoulaile/Sublime-CppAssistant/actions)
[![License](https://img.shields.io/github/license/chenmoulaile/Sublime-CppAssistant.svg)](LICENSE)
[![Sublime Text](https://img.shields.io/badge/Sublime%20Text-4-blue.svg)](https://www.sublimetext.com/)

> **C++ assistance for Sublime Text 4**, built around a real `clangd` engine with
> a zero-configuration, zero-dependency fallback. **Tuned for competitive
> programming (OI / ICPC / CSP-S).**

CppAssistant starts working the moment you install it: clangd gives
compiler-grade answers when it is available, and an incremental pure-Python
engine plus a curated STL/CP database take over when it is not. Nothing to
configure, nothing to index, no background daemon you have to babysit.

---

## 🆕 What's new in 2.2.0

| Change | Why it matters |
|--------|----------------|
| **PBDS headers are inserted for you** | `bits/stdc++.h` does not include `ext/pb_ds`; completing `__gnu_pbds::tree` now adds both headers automatically |
| **`auto` shows its real type** | `auto it = v.find(x)` completes as `vector<int>::iterator`, not `auto` |
| **Complexity in signatures** | `push_back(int) · O(1)`, `find(const long long&) · O(log n)`, `insert(...) · O(1) avg` |
| **One-key full-width fix** | `Ctrl+Alt+,` rewrites `，；：（）` to half-width **in code only**, leaving comments and strings alone |
| **Template browser** | Preview the first 20 lines of any installed snippet before inserting it |
| **Call / type hierarchy** | Who calls this function, what it calls, and the class inheritance chain |
| **Status bar hints** | `[clangd] · void solve(int n, vector<int>& a) · arg 2/2: int n` |
| **Validate Settings** | Invalid values (`c++2x`, `lint_timeout: 0`) are reported instead of silently ignored |

Full history: [CHANGELOG.md](CHANGELOG.md).

---

## 🏆 Why OIers like it

| Feature | Benefit in a contest |
|---------|----------------------|
| **`bits/stdc++.h` PCH acceleration** | Warm diagnostics in ~200 ms, member completion in ~10–20 ms |
| **Array bounds checking** | Catches `int a[10]; a[10] = 1` instantly |
| **Template parameter deduction** | `vector<int> v; v.` → `push_back(int)`, not `push_back(value_type)` |
| **PBDS without the header trivia** | `tree` / `gp_hash_table` completions insert `ext/pb_ds/*` for you |
| **CP-specific database** | `__int128`, `__gnu_pbds`, C++20 `ranges`, C-lib I/O |
| **Full-width punctuation fix** | `，` in code is a `stray '\357'` error; one keystroke fixes every occurrence in code |
| **Diagnostic one-click copy** | `Ctrl+Shift+C` → paste the error into a group chat or search |
| **Jiangly-style formatting** | 4 spaces, K&R, 100 columns — the contest standard |
| **Template packs** | Geometry, Graph, DS, Strings, Math — bundled, installed on demand |
| **Snippet tab stops** | `$0`, `$1`, `${1:default}` navigation in every template |
| **Bilingual diagnostics** | Chinese / English / both, switchable without rerunning the compiler |
| **Menu localization** | `CppAssistant: Toggle Menu Language` translates this plugin's own menus |

---

## ✨ Features

### 🎯 Completion

- **clangd semantic completion** — compiler-grade signatures, overloads,
  container members, locals, macros; async results replace the fallback popup
  as soon as the preamble is ready.
- **FastEngine fallback** — a zero-dependency incremental lexer and tolerant
  parser: same-file symbols, scopes, struct/class/enum members, array bounds,
  template deduction, all in pure Python.
- **Optional libclang engine** — ctypes + PCH acceleration, used only when its
  translation unit is already warm; never blocks typing.
- **Template parameter deduction** — `vector<int> v; v.push_back(` suggests
  `push_back(int)`; also for map/set/string/PBDS and nested containers.
- **`auto` inference** — `auto it = v.find(x)` → `vector<int>::iterator`,
  `auto value = m.at("k")` → `int`.
- **Complexity hints** — every hot STL/PBDS member shows `O(1)` / `O(log n)` /
  `O(1) avg` / `O(n)`.
- **Origin labels** — `kmp · Strings template`, `total · local variable`,
  `MAXN · macro`, so you know where an entry comes from before pressing Tab.
- **Smart `#include` insertion** — respects `bits/stdc++.h`, skips headers that
  are already included, inserts after the last `#include` line.
- **PBDS header insertion** — `ext/pb_ds/assoc_container.hpp` and
  `tree_policy.hpp` are added even when the universal header is present, and
  clangd re-syncs immediately afterwards.
- **`__gnu_pbds::` namespace** — `tree`, `gp_hash_table`, `cc_hash_table`,
  `rb_tree_tag`, `splay_tree_tag`, `ov_tree_tag`,
  `tree_order_statistics_node_update`, `null_type` and resize policies, each
  with the full template skeleton.
- **C++20 subset** — `ranges::sort`, `views::filter`, `integral<T>` and
  friends, tagged `C++20` so they rank after the C++14 database until you
  raise `cxx_standard`; `ranges::` and `views::` scopes list only their own
  members.
- **Include details** — `#include <bits/stdc++.h>` explains that it pulls in
  all standard headers **but not PBDS**.

### 🔍 Diagnostics

- **Instant basic check** (< 10 ms) — brackets, full-width punctuation,
  unclosed strings/comments, malformed `#include`, unbalanced `#if`.
- **Compiler check** — `g++` / `clang++ -fsyntax-only` in the background with
  PCH caching, debounce, timeout and automatic compiler discovery.
- **clangd check** — LSP `publishDiagnostics` with `relatedInformation`
  context; no compiler process.
- **libclang check** — no compiler process either, PCH-accelerated, precise
  cross-file locations.
- **Array bounds checking** — fixed-size arrays, multi-dimensional, hex/bin/oct
  literals, correct shadowing; `obj.a[10]` is not mistaken for a local array.
- **Bilingual messages** — `display_language = en | zh | both` re-renders
  existing diagnostics without rerunning anything.
- **Inline phantoms** — a short message under the offending line.
- **One-click copy** — `Ctrl+Shift+C`, error first, warning code preserved.
- **Filtering** — `diagnostic_filters` accepts `-Wfoo`, `Wno-foo` or `foo`,
  normalized and applied to the compiler, clangd fallback flags and libclang.
- **Unused warnings suppressed by default** — CSP-S / NOIP style.
- **One-key full-width fix** — `Ctrl+Alt+,` rewrites full-width punctuation in
  code only; comments, string/char literals and raw strings are untouched.

### 🧭 Navigation

- **F12 Go to Definition** — scope-aware same-file search, other open files,
  local headers (recursive), libclang cross-file, standard-library fallback.
- **Find References** — clangd results, grouped by file and numbered `[3/7]`,
  with an inline preview of each line.
- **Call Hierarchy** — incoming and outgoing calls (clangd LSP 3.16).
- **Type Hierarchy** — base classes and derived classes.
- **Semantic Rename** — project-wide, atomic, refuses when the buffer changed.
- **Status bar hints** — completion source plus the signature under the cursor
  and which argument you are typing.

### ⚡ Editing, actions and formatting

- **Code Actions** (`Ctrl+.`) — quick fixes, include insertion, preferred fix,
  disabled-reason reporting.
- **Jiangly-style formatting** — clang-format with a bundled style (4 spaces,
  K&R, 100 columns), or a built-in fallback when clang-format is missing.
- **Format Document / Selection** — clangd `rangeFormatting` first, then
  clang-format `--lines`.

### 📦 Templates and snippets

- **Bundled packs** — Geometry, Graph, DS, Strings, Math; install, enable,
  disable or remove them from the command palette. Uninstalled packs never
  appear in completion.
- **Browse Templates** — quick panel with a 20-line preview, origin and line
  count; selection inserts a real snippet with tab stops.
- **Create / import / manage snippets** — from a selection, from JSON, or
  per-file open / rename / recycle.
- **Template browser + user snippets share one list** — whatever appears in
  completion is what the browser shows.

### 🌐 Localization

- **English / Chinese / bilingual** — diagnostics, completion details,
  annotation text.
- **Menu localization** — only CppAssistant's own menu entries; built-in
  Sublime menus and your user menu files are never touched.

---

## 🚀 Quick start

1. Install the package (Package Control, git clone or the `.sublime-package`
   release asset — see **Installation** below).
2. Open a `.cpp` file and start typing. Completion appears with no setup.
3. Press **F12** to jump to a definition, **Shift+Alt+F** to format.
4. Put the file next to a `compile_commands.json` (or just a local
   `g++`/`clangd` on `PATH`) to get compiler-grade answers.

A five-line example of what the popup shows for `vector<int> v; v.`:

```text
push_back     void push_back(int)                 · O(1)     [STL]
at            int at(size_type) const             · O(1)     [STL]
find          iterator find(const int&)           · O(n)     [STL]
```

And for a PBDS ordered set:

```text
order_of_key      size_type order_of_key(const int&) const   · O(log n)
find_by_order     iterator find_by_order(size_t)             · O(log n)
```

---

## 📦 Installation

### Package Control (recommended)

1. `Ctrl+Shift+P` → **Package Control: Install Package**
2. Search **CppAssistant** → install

### Git clone

```bash
# Windows
cd "%APPDATA%\Sublime Text\Packages"
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant

# Linux / macOS
cd ~/.config/sublime-text/Packages
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```

### Manual package

Download `CppAssistant.sublime-package` from the
[latest release](https://github.com/chenmoulaile/Sublime-CppAssistant/releases),
drop it into `Packages/` (or `Installed Packages/`), and restart Sublime Text.

---

## ⌨️ Commands

### Navigation

| Command | Purpose |
|---------|---------|
| `CppAssistant: Go to Definition` | Jump to definition (F12) |
| `CppAssistant: Find References` | References grouped by file |
| `CppAssistant: Call Hierarchy` | Incoming / outgoing calls |
| `CppAssistant: Type Hierarchy` | Base and derived classes |
| `CppAssistant: Rename Symbol` | Project-wide rename |
| `CppAssistant: Code Actions` | Quick fixes and refactors |

### Editing

| Command | Purpose |
|---------|---------|
| `CppAssistant: Format Document (jiangly style)` | Format the whole file |
| `CppAssistant: Format Selection` | Format selected lines |
| `CppAssistant: Fix Full-width Punctuation` | `，；：（）` → `,;:()` in code only |

### Diagnostics

| Command | Purpose |
|---------|---------|
| `CppAssistant: Show Diagnostics` | Diagnostics panel |
| `CppAssistant: Copy Diagnostic` | Copy the current diagnostic |
| `CppAssistant: Diagnostic Language - …` | English / Chinese / both |
| `CppAssistant: Diagnostic Engine - …` | compiler / clangd / libclang |
| `CppAssistant: Toggle Inline Error Phantoms` | Inline messages on/off |
| `CppAssistant: Completion Engine Status` | Which engine answered, and why |

### Templates and settings

| Command | Purpose |
|---------|---------|
| `CppAssistant: Browse Templates` | Preview and insert a snippet |
| `CppAssistant: Install Template Pack` | Install a bundled pack |
| `CppAssistant: Manage Templates` | Enable / disable installed packs |
| `CppAssistant: Create User Snippet` | Create from the selection |
| `CppAssistant: Manage User Snippets` | Open / rename / recycle |
| `CppAssistant: Import Snippets` | Import from JSON |
| `CppAssistant: Validate Settings` | Report values Sublime ignores |
| `CppAssistant: Toggle Menu Language` | Menu EN ↔ ZH |
| `Preferences: CppAssistant Settings` | Edit settings |

---

## ⌨️ Key bindings

These ship in `Example.sublime-keymap`. Delete or override any entry that
conflicts with your own setup.

| Keys | Command |
|------|---------|
| `F12` | Go to Definition |
| `Shift+F12` | Find References |
| `Shift+Alt+F` | Format Document |
| `Ctrl+Alt+F` | Format Selection |
| `Ctrl+.` | Code Actions |
| `Ctrl+Alt+R` | Rename Symbol |
| `Ctrl+Alt+D` | Show Diagnostics |
| `Ctrl+Shift+C` | Copy Diagnostic |
| `Ctrl+Alt+,` | Fix Full-width Punctuation |

---

## ⚙️ Settings

Open **Preferences: CppAssistant Settings**, or edit
`Packages/User/CppAssistant.sublime-settings`. Every key is documented inline
in the shipped settings file; run `CppAssistant: Validate Settings` to catch
values Sublime would otherwise ignore.

### Engines

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_completions` | `true` | Completion master switch |
| `enable_clangd_engine` | `true` | Use clangd; `false` = built-in database |
| `enable_fast_engine` | `true` | FastEngine for same-file symbols |
| `enable_libclang_engine` | `true` | Auto-discover libclang |
| `libclang_path` | `""` | libclang path; empty = auto-discover |
| `libclang_pch` | `true` | Build `bits/stdc++.h` PCH |
| `libclang_completion` | `true` | Merge libclang completion when warm |
| `libclang_goto` | `true` | Use libclang for cross-file F12 |
| `clangd_binary` | `""` | clangd executable; empty = `PATH` |
| `clangd_args` | `[]` | Extra clangd arguments |
| `clangd_completion_wait_ms` | `0` | Synchronous wait for clangd; `0` = never block |
| `enable_clangd_style_completion` | `true` | `true` = fuzzy LSP style, `false` = strict prefix |

### Completion behaviour

| Setting | Default | Description |
|---------|---------|-------------|
| `auto_insert_includes` | `true` | Insert `#include` when a completion is accepted |
| `cpp_auto_insert_pbds_includes` | `true` | Insert `ext/pb_ds/*` for PBDS completions |
| `show_status_hint` | `true` | Status bar: engine, call signature, argument index |
| `enable_hover` | `true` | clangd hover popup |
| `enable_signature_help` | `true` | Signature help inside call parentheses |

### Diagnostics

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_linting` | `true` | Diagnostics master switch |
| `lint_engine` | `"compiler"` | `compiler`, `clangd` or `libclang` |
| `instant_basic_check` | `true` | Millisecond bracket/punctuation check |
| `lint_debounce` | `0.1` | Idle seconds before the compiler check |
| `lint_timeout` | `12` | Compiler timeout in seconds |
| `enable_pch` | `true` | Build the `bits/stdc++.h` PCH cache |
| `show_phantoms` | `true` | Inline diagnostic messages |
| `display_language` | `"en"` | `en`, `zh` or `both` |
| `menu_language` | `"en"` | `en` or `zh`; follows `display_language` |
| `cxx_standard` | `"c++14"` | Standard for checks and clangd |
| `compiler_path` | `""` | Compiler executable; empty = `PATH` |
| `compiler_extra_args` | `[]` | Extra compiler arguments |
| `include_paths` | `[]` | Extra header directories |
| `suppress_unused_warnings` | `true` | Add `-Wno-unused-variable` etc. |
| `diagnostic_filters` | `[]` | Warning codes to suppress |

### Formatting

| Setting | Default | Description |
|---------|---------|-------------|
| `indent_width` | `4` | Indent width for the built-in formatter |
| `format_on_save` | `false` | Format the document before saving |
| `clang_format_path` | `""` | clang-format executable |
| `clang_format_style` | jiangly config | clang-format style string |

---

## 🔧 How it works

### Completion pipeline

1. **clangd** (primary) — async JSON-RPC over stdio, `compile_commands.json`
   generated and cached per file, fallback flags from your settings.
2. **FastEngine** (fallback) — incremental lexer + tolerant parser, scope-aware
   symbols, template deduction, array bounds; answers in single-digit
   milliseconds and also backs F12 and the instant check.
3. **libclang** (optional) — ctypes, PCH-accelerated, used only when the
   translation unit is already warm.
4. **Built-in database** — STL members, CP types (`__int128`, `__gnu_pbds`),
   C++20 ranges/views/concepts, all with English and Chinese descriptions.
5. **User snippets and template packs** — always first in the popup, with tab
   stops and origin labels.

### Diagnostics engines

| Engine | Speed | Notes |
|--------|-------|-------|
| Instant basic check | < 10 ms | Brackets, punctuation, strings, `#include`, `#if` |
| Compiler | ~300 ms (PCH) | Full syntax, `-Wall`, PCH cache |
| clangd | ~50 ms | Live LSP diagnostics, no compiler process |
| libclang | ~200 ms | Precise cross-file locations, no compiler process |

### F12 resolution order

1. FastEngine same-file (scope-aware ranking)
2. Other open files
3. Included headers / `include_paths`, recursively
4. libclang cross-file (when warm)
5. Standard-library symbol → header mapping

---

## 📋 Requirements

- **Sublime Text 4** (build 4000+), Windows / macOS / Linux
- **Optional**: [clangd](https://clangd.llvm.org/) — semantic completion, hover,
  signature help, hierarchy, LSP diagnostics
- **Optional**: `g++` / `clang++` on `PATH` (or `compiler_path`) — compiler
  diagnostics, PCH
- **Optional**: `libclang` (`libclang.dll` / `.so` / `.dylib`) — libclang
  diagnostics, semantic completion, cross-file F12
- **Optional**: `clang-format` — formatting

> The plugin source is Python 3.3 compatible (older ST4 hosts) and runs on
> 3.14 in current builds. No third-party Python packages are required.

---

## 🧪 Development

```bash
# Full test suite (208 tests)
python -m unittest discover -s tests -v

# libclang tests (opt-in, needs libclang + clang++)
CA_TEST_LIBCLANG_PCH=1 python -m unittest tests.test_semantic_engine -v

# Lint / types (same commands as CI)
flake8 . --select=E9,F63,F7,F82
mypy --ignore-missing-imports --python-version 3.10 cppassistant/ CppAssistant.py
```

### Modules

| File | Responsibility |
|------|----------------|
| `CppAssistant.py` | Sublime integration: commands, events, diagnostics, formatting, hierarchy, settings UI |
| `cppassistant/ca_clangd.py` | Minimal LSP client: JSON-RPC, completion/hover/signature/rename/code actions/references/hierarchy |
| `cppassistant/ca_fast.py` | FastEngine: incremental lexer, tolerant parser, symbol index, call context |
| `cppassistant/ca_engine.py` | Legacy engine: type environment, member signatures, formatting fallbacks, compiler output parsing |
| `cppassistant/ca_semantic.py` | Optional libclang ctypes engine with PCH acceleration |
| `cppassistant/ca_stdlib_data.py` | STL/CP completion data, PBDS namespace, C++20 subset, translations |
| `cppassistant/ca_template_packs.py` | Bundled template pack catalogue, XML generation, installation |
| `cppassistant/ca_user_snippets.py` | User `.sublime-snippet` loader with caching and origin labels |
| `cppassistant/ca_fixes.py` | Full-width punctuation scanner/rewriter (code regions only) |
| `cppassistant/ca_config.py` | Settings schema validation |
| `cppassistant/ca_filters.py` | Diagnostic filter normalization |
| `cppassistant/ca_menu_language.py` | Menu translation overlay generator |

CI runs flake8, mypy, the unit tests on Python 3.9 / 3.10 / 3.12 / 3.14, the
libclang tests, and builds the `.sublime-package` on every tag.

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| **clangd not found** | Install clangd (`pacman -S mingw-w64-x86_64-clang`, `apt install clangd`, `brew install llvm`) or set `clangd_binary` |
| **F12 does nothing** | Check the key binding in `Example.sublime-keymap`, or run the command from the palette |
| **Enter inserts a newline** | Sublime default. Add `{ "keys": ["enter"], "command": "insert", "args": {"characters": "\n"}, "context": [{ "key": "auto_complete_visible" }] }` to your user keymap |
| **Formatting does nothing** | Install `clang-format` or set `clang_format_path`; the built-in fallback only handles basic indentation |
| **Code actions / rename / hierarchy do nothing** | They need clangd. Save the file first and check **Completion Engine Status** |
| **`bits/stdc++.h` still reports missing PBDS** | PBDS headers are inserted automatically now; if you disabled `cpp_auto_insert_pbds_includes`, add `ext/pb_ds/*` manually |
| **Full-width fix skipped my comment** | Intentional: only code regions are rewritten, so Chinese punctuation in comments and strings survives |
| **Diagnostics stopped** | Restart Sublime Text and check the console for `[CppAssistant]` messages |
| **libclang not found** | Install LLVM / MSYS2 / Xcode, or set `libclang_path`; the compiler engine takes over automatically |
| **libclang PCH unused** | Needs a `clang++` matching libclang's major.minor on `PATH` |
| **Settings seem ignored** | Run `CppAssistant: Validate Settings` — invalid values are reported in the output panel |

---

## 📄 License

[MIT](LICENSE) © chenmoulaile

- Release history: [CHANGELOG.md](CHANGELOG.md)
- Package Control page: [packagecontrol.io/packages/CppAssistant](https://packagecontrol.io/packages/CppAssistant)

## 🙏 Acknowledgments

- [clangd](https://clangd.llvm.org/) — the C++ language server
- [jiangly](https://github.com/jiangly) — formatting style reference
- [Sublime Text](https://www.sublimetext.com/) — the editor
- All contributors and OIers who provided feedback
