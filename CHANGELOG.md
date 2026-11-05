# Changelog

All notable changes to CppAssistant are documented here. The project follows
semantic versioning; Package Control installs the newest tag.

## v2.1.1

Completes and fixes the OIer-focused 2.1.0 update. The 2.1.0 tag pointed at
a partially implemented release; this patch makes the new features work
end-to-end and closes the defects found in a full code audit.

### Features

- **`__gnu_pbds` offline database**: `__gnu_pbds::tree`, `gp_hash_table` and
  `cc_hash_table` now appear in completion and in member completion, with
  `order_of_key` / `find_by_order` details for ordered trees and concrete
  `insert` / `emplace` / `find` signatures derived from the key type.
  `__int128`, `__int128_t` and `__uint128_t` are recognised as known types.
- **Bundled template packs**: all six packs (Basic, Geometry, Graph, DS,
  Strings, Math) ship inside the package and install on demand from
  `Preferences → CppAssistant → Install/Manage Templates`. Uninstalled packs
  no longer appear in completion; a `.disabled` marker removes them again.
- **Snippet management**: create snippets from a selection, import them from
  JSON, and open the snippet folder cross-platform. Overwriting an existing
  snippet asks for confirmation.
- **Diagnostic filtering**: `diagnostic_filters` is normalised
  (`-Wunused-variable` / `Wno-unused-variable` / `unused-variable` /
  `Wunused-variable`) and applied to the compiler command, the clangd
  fallback flags and the libclang arguments. Warning codes are preserved
  before messages are translated, so the filter matches the right diagnostic.
- **Menu localization**: `CppAssistant: Toggle Menu Language` (or switching
  the diagnostic language to Chinese) translates CppAssistant's own menu
  entries immediately by generating a tiny
  `Packages/CppAssistantLocalization` overlay. It never touches Sublime's
  built-in menus or the user's menu files; switching back to English clears
  the overlay.

### Fixes

- **Array bounds checking** no longer flags `int a[10];` as an access,
  follows lexical scopes (shadowed arrays and pointers are handled
  correctly), checks multi-dimensional chains such as `a[i][j]`, and parses
  C-style hex/binary/octal/suffixed integer constants. Member access such as
  `obj.a[10]` is not resolved against an unrelated global array.
- **Template deduction** produces concrete details such as
  `push_back(int)`, `at(const string&)`, `find(const long long&)` and
  `insert({const string&, int})`, including nested templates and PBDS maps.
- **Diagnostic copy** (command `ca_copy_diagnostic`, default `Ctrl+Shift+C`)
  reads the structured diagnostics instead of the rendered panel text,
  prefers errors on the current line over warnings, and keeps the compiler
  warning code when available.
- **Template packs** download path gained zip-slip protection, invalid pack
  ids are rejected, bundled packs fail with a clear message, and snippet
  files are validated as XML before being written. Packaged snippets are no
  longer picked up before their pack is installed.
- **Menu localization** no longer writes an ignored file under
  `Packages/User/CppAssistant`, no longer duplicates menu entries, and works
  for directory and `.sublime-package` installs. Entries without ids are not
  overridden.
- **Diagnostics** preserve `-W...` codes before translation, so
  `diagnostic_filters` works for compiler, clangd and libclang paths.
- **CI** runs on Ubuntu 24.04 with clang-18 for the libclang tests, a
  meaningful mypy invocation (not `|| true`), a Python 3.14 unit-test job,
  and packages the repository docs and `LICENSE`.

## v2.0.0

Phase two: an optional libclang semantic engine on top of the zero-dependency
FastEngine. The plugin now has three progressive layers — FastEngine, clangd,
and libclang — and stays usable with none of the optional tools installed.

### libclang semantic engine

- **`cppassistant/ca_semantic.py`**: a `ctypes` wrapper around the libclang C
  API with no Python package dependency. It discovers `libclang.dll` /
  `libclang.so` / `libclang.dylib` from `LIBCLANG_PATH`, the plugin `libs/`
  directory, the clangd/clang directory, and common LLVM, MSYS2, Xcode, and
  Homebrew locations.
- **`lint_engine: "libclang"`**: background diagnostics through libclang with
  no compiler process. If libclang cannot be loaded, the compiler engine is
  used instead.
- **Automatic resource directory discovery**: builtin headers such as
  `stddef.h`, `float.h`, and `mm_malloc.h` resolve correctly, so
  `#include <bits/stdc++.h>` works with MSYS2/LLVM installations.
- **PCH acceleration**: when a buffer includes `bits/stdc++.h` and a
  `clang++` with the same major.minor version as libclang is available, a
  precompiled header is built once in the background and reused via
  `-include-pch`. Warm diagnostics drop from roughly 1.1 s to 210 ms, and
  member completion from 630 ms to 12 ms in local measurements. A stale or
  incompatible PCH is detected and disabled automatically.
- **Warm-cache completion merge**: libclang results are merged ahead of
  FastEngine/database results only when the translation unit is already
  parsed. `is_busy()` and `allow_parse=False` keep libclang off the UI
  thread's critical path.
- **Cross-file F12 fallback**: libclang definitions (including standard
  library and header locations) are added with higher priority when the
  translation unit is warm. Word-end offsets are probed correctly.
- **Engine status** now reports the libclang path/version, resource directory,
  PCH state, parse/reparse/completion/definition counts, and timings.
- New settings: `enable_libclang_engine`, `libclang_path`, `libclang_args`,
  `libclang_pch`, `libclang_completion`, and `libclang_goto`.
- New commands/menu entries: **Diagnostic Engine - libclang (PCH)**.

### FastEngine (zero dependency)

- **FastEngine** (`cppassistant/ca_fast.py`): a pure-Python incremental lexer
  and tolerant structural parser. It needs no tree-sitter, libclang, clangd,
  compiler, or third-party package.
- **Scope-aware fallback completion**: same-file locals, parameters, class and
  struct members, enum values, namespaces, aliases, macros, template
  parameters, and qualified names are merged ahead of the existing STL and
  snippet database. Member completion works after `.`, `->`, and `::`.
- **Faster same-file F12**: structural definition candidates are prepended and
  ranked by visibility and declaration/definition role before the text-based
  search runs.
- **Richer instant checks**: unterminated raw strings, malformed `#include`
  directives, unbalanced `#if`/`#endif`, and full-width punctuation, in
  addition to brackets, strings, and block comments.
- **Incremental analysis cache**: per-buffer revisions are cached, and edits
  re-lex only from the first changed line while reusing the unchanged token
  prefix.
- New setting `enable_fast_engine` (default `true`). Set it to `false` to use
  the previous regex-only fallback path. clangd remains the semantic engine
  whenever it is available.

### Tests

- Added `tests/test_fast_engine.py` with 44 tests covering the lexer, parser,
  completion, definitions, diagnostics, caching, and incremental re-lexing.
- Added `tests/test_semantic_engine.py` with 16 libclang tests covering
  discovery/version, resource directories, diagnostics, member and STL
  completion, local/cross-file/word-end definitions, reparse, cache/prewarm,
  eviction, graceful failure, universal-header detection, and PCH status. The
  slow PCH build test is opt-in through `CA_TEST_LIBCLANG_PCH=1`.
- The full suite now has 89 tests, 2 skipped by default (libclang tests skip
  when the library is unavailable).
- CI now runs on pushes to `main` as well as `master`, across Python 3.9,
  3.12, and 3.14, and includes a Python 3.3 syntax-compatibility check.

## v1.8.0

Code actions, semantic rename, find references, and range formatting, plus a
fix for clangd diagnostics parsing.

### Features

- **Code actions** (`ca_code_actions`): request `textDocument/codeAction` for
  the cursor or selection and show quick fixes and refactors in a quick panel.
  Supports actions with an immediate `edit`, lazy actions through
  `codeAction/resolve`, and refactor tweaks through `workspace/executeCommand`
  with `workspace/applyEdit`.
- **Rename Symbol** (`ca_rename`): semantic rename through
  `textDocument/rename`, applying the returned `WorkspaceEdit` to open buffers
  and unopened project files.
- **Find References** (`ca_find_references`): list references from
  `textDocument/references` in a quick panel and jump to the selected location.
- **Format Selection** (`ca_format_selection`): format only the selected lines.
  Tries clangd `textDocument/rangeFormatting`, then clang-format `--lines`,
  then the built-in range formatter. Text outside the selection is preserved.
- Added command palette entries, an optional CppAssistant menu, and optional
  key bindings for the new commands.
- Workspace edits containing file create, rename, or delete operations are
  rejected as a whole instead of being applied partially.

### clangd

- Parse `textDocument/publishDiagnostics` from the top-level `uri` field as
  defined by LSP; previously diagnostics sent by clangd were not stored, which
  also prevented quick fixes from receiving their diagnostic context.
- Pass the current document's raw diagnostics in the code-action context, and
  only when the diagnostic version matches the document version.
- Reply to `workspace/applyEdit` with `{"applied": bool}` so clangd tweaks can
  complete instead of failing to decode the reply.
- Skip redundant `didChange` notifications when the text is unchanged, keeping
  diagnostic versions aligned with the document version.
- Added UTF-16 position conversion and TextEdit/WorkspaceEdit helpers for
  applying clangd edits safely.

## v1.7.1

Documentation cleanup and bug fixes. No new user-facing features.

### Documentation

- Moved the full release history out of `README.md` into this file.
- Split documentation into an English-first `README.md` and a Chinese
  `README.zh-CN.md`.
- Corrected the Python host note: older ST4 builds use Python 3.3, while
  current builds such as Sublime Text 4215 use Python 3.14. The plugin source
  remains loadable on both.
- Updated installation instructions now that CppAssistant is in the default
  Package Control channel.
- Updated the settings table and removed stale notes about cache invalidation
  on language changes.

### clangd

- Restart the clangd client when relevant settings change (`clangd_binary`,
  `cxx_standard`, `clangd_args`, compiler settings, and include paths).
  Previously the settings signature was updated but the old process kept
  running.
- Retry clangd discovery every 30 seconds after it is not found, so installing
  clangd while Sublime is running does not require a restart.
- Send the full document to clangd instead of truncating at 400 KB. LSP
  positions are computed from the full text, so truncation could shift
  completions and diagnostics. Files larger than 1 MB now use the built-in
  database instead.
- Apply clangd diagnostics on the UI thread. `sublime.windows()` and view
  access are not thread-safe and were previously called from the clangd reader
  thread.
- Return an array for `workspace/configuration` requests, as required by some
  clangd versions.
- Handle `insertTextFormat` inside `textEdit` and extract multiple `#include`
  lines from one `additionalTextEdit`.
- Keep operator names such as `operator<=>` intact in short completion labels.
- Fix the active signature index when clangd returns empty signature labels.
- Cache hover results and suppress duplicate in-flight hover/signature
  requests.
- Warm up the document on tab activation and after saving.

### Diagnostics

- Attach compiler `note:` lines to the preceding diagnostic instead of
  counting them as separate warnings.
- Escape HTML-sensitive characters and preserve line breaks in inline
  diagnostic phantoms.
- Do not clear clangd diagnostics when the instant basic check sees a new
  source hash; clangd diagnostics are refreshed by clangd itself.
- Include compiler flags and include paths in the PCH signature so a stale PCH
  is not reused after settings change.
- Guard the compilation database with a lock and prune entries for files that
  no longer exist.
- Include `compiler_extra_args` and `include_paths` when probing compiler
  include directories and building the PCH.

### Completion

- Keep completion details bilingual internally and choose the display language
  when rendering, so English mode no longer shows Chinese details for user
  symbols or built-in snippets.
- Remove duplicate built-in snippets that were added by both the analysis
  engine and the static tier sorter.
- Cache compiler discovery for 30 seconds when no compiler is found, so adding
  a compiler to `PATH` does not require a restart.

### Go to definition

- Search `include_paths` directories for local headers. They were previously
  passed to `open()` as if they were files, so F12 never searched them.
- Pass compiler system include paths separately for standard-library fallback,
  avoiding a recursive scan of the whole compiler include tree.
- Keep path comparison case-sensitive on Linux and macOS.

### Formatting and lifecycle

- Run document formatting in a worker thread so clang-format cannot freeze the
  UI. Format-on-save remains synchronous so the buffer is replaced before the
  file is written.
- Cancel an asynchronous format result if the buffer changed or the view was
  closed.
- Clean up debounce timers, diagnostics, and phantoms when the plugin unloads.
- Update settings comments and remove the duplicate Settings entry from the
  package menu.

## v1.7.0

- Made the UI English-first: settings comments, menus, command palette
  captions, completion kind labels, and status messages.
- Changed the default `display_language` to `"en"`.
- Switched settings writes to `sublime.load_settings` /
  `sublime.save_settings` instead of rewriting the user settings file.
- Removed the settings listener; caches validate themselves against a settings
  signature.
- Moved hover, signature help, and completion work off the UI thread.

## v1.6.1

- Show short completion labels (for example `std::vector` and `push_back`)
  while keeping full signatures in the details panel; preserve operator names.
- Fixed menu entries that used `open_file ${packages}` and failed in
  `.sublime-package` installs.
- Documented Sublime's default Enter-to-accept-completion behavior and the
  optional key binding to make Enter always insert a newline.

## v1.6.0

- Simplified completion lists by using clangd's bundled completion style
  instead of expanding every overload.
- Reduced the synchronous clangd wait and kept asynchronous refresh as the
  default.
- Added `lint_engine` with `compiler` and `clangd` modes.
- Stored clangd diagnostics per file in the client.

## v1.5.2

- Fixed completion failure on Sublime Text 4213 by using `getattr` for
  completion flags that do not exist in that build.

## v1.5.1

- Fixed the compilation database key (`file`, not `filename`), which had
  prevented clangd from loading it.
- Suppressed Sublime word completions when clangd results are shown.
- Made compilation database writes atomic.
- Injected compiler include paths into clangd fallback flags.
- Added preamble readiness detection and the completion engine status command.
- Made inline phantoms toggleable at runtime.

## v1.5.0

- Added smart header insertion when accepting a completion.
- Added clangd hover documentation and signature help.
- Preserved function argument placeholders.
- Added `textDocument/hover` and `textDocument/signatureHelp` support to the
  LSP client.

## v1.4.1

- Fixed plugin loading by using relative imports for the `cppassistant`
  subpackage.

## v1.4.0

- Embedded a minimal clangd LSP client for semantic completion.
- Added dynamic `compile_commands.json` generation with `-std=` and compiler
  header paths.
- Added built-in database fallback while clangd is unavailable or preparing.
- Added clangd settings, completion engine commands, and snippet-first
  ordering.

## v1.3.3

- Suppressed Sublime word completions in LSP-clangd completion mode.
- Added the compiler version to the PCH signature and rebuilt stale PCH files.
- Added console logging for completion, basic check, and compiler failures.

## v1.3.2

- Removed `.no-sublime-package`; the package works from a compressed
  `.sublime-package`.

## v1.3.1

- Added the `enable_clangd_style_completion` mode switch and commands.
- Invalidated completion caches when switching modes.
- Cleaned up Package Control review warnings.

## v1.3.0

- Added multi-level caches for lexical state, type environment, completions,
  and diagnostics.
- Removed temporary `.cpp` files by passing source through stdin.
- Terminated stale compiler processes immediately.
- Added PCH mounting through `-include`.
- Expanded diagnostic translations and warning-flag translations.
- Added standard-library fallback for F12.
- Reduced lint debounce and improved stale diagnostic clearing.
- Reworked the package menu.

## v1.2.3

- Fixed Package Control review warnings around `sys.path`,
  `CREATE_NO_WINDOW`, keymap naming, `edit_settings`, and menu entries.

## v1.2.0

- Added instant basic checks, stale-process termination, and content-hash
  caching.
- Warmed PCH earlier at startup.
- Added lint status messages and Chinese UI text.

## v1.1.0

- Added snippet completions, Chinese diagnostics, completion caching, and PCH
  acceleration.

## v1.0.0

- Initial release: C++ completions, Chinese syntax diagnostics, F12
  go-to-definition, and jiangly-style formatting.
