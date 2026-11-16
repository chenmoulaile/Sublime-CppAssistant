# Changelog

All notable changes to CppAssistant are documented here. The project follows
semantic versioning; Package Control installs the newest tag.

## v2.3.5 — 2026-11-16

### Performance

- **The two lint timers were firing far too often.** The instant basic check
  used a 20 ms debounce and the full compiler check 100 ms, so holding a key
  started a fresh g++ process (and a full-file parse) many times per second.
  They are 150 ms and 350 ms now; results are still cached per text hash, and
  saving still lints immediately.
- **No compiler run while a member is half-typed** (`a.pu`), which used to
  produce a red "has no member named 'pu'" line and burn a compile.

### Fixed

- **Console `Parse Error ... Unexpected character`.** The plugin passed
  multi-line completion `details` to Sublime; its details renderer tries to
  parse that string as JSON/HTML, so a snippet body inside the field produced
  a parse error on every completion. Details are now flattened to a single
  line (and capped at 200 chars).
- Every bundled and user `.sublime-snippet`, plus the JSON resources
  (`compile_commands.json`, `templates/index.json`, session files), was
  verified to be well-formed — the parse error came from the completion
  details path, not from a corrupt file on disk.

### Tests

- 240 tests pass.

## v2.3.4 — 2026-11-16

### Fixed

- **User snippets were listed twice.** Sublime Text 4.2 only exposes
  `INHIBIT_WORD_COMPLETIONS`; `INHIBIT_SNIPPET_COMPLETIONS` does not exist, so
  the native snippet source cannot be suppressed. The plugin was adding its own
  copy of every `.sublime-snippet` on top of Sublime's list — which is why
  `ahead` / `aorihash` appeared twice and the inserted text looked wrong (two
  different snippet engines handling the same file). The plugin no longer
  provides user snippets at all: they are only read for *Browse Templates*,
  never copied or rewritten, and Sublime owns their completion entry and
  insertion format. Template-pack snippets work the same way — Sublime indexes
  them natively.
- **`vector` still appeared several times.** With the plugin's own snippet
  copies gone, only the built-in type remains; `_dedupe_by_trigger()` still
  guards STL vs snippet collisions inside a single list.
- **Global completion was too fuzzy.** Typing `ve` also offered `move`,
  `reverse`, `override` through substring matching. When a real prefix match
  exists and the cursor is not in a member access, only prefix matches are
  kept now. Member lists still allow substring matches (`back` →
  `push_back`).
- **Transient compiler error while typing a member.** `a.pu` used to flash
  `has no member named 'pu'`. Compiler checks are skipped while the current
  line ends in `.` / `->` or an unfinished member; saving still lints, so real
  errors are not missed.

### Changed

- **Menu grouped by task.** The 31 flat entries became `Navigate` / `Edit` /
  `Diagnostics` / `Templates` submenus plus the settings block, all translated
  by the Language switch.
- **Cheaper completions.** The snippet path called
  `sublime.find_resources("*.sublime-snippet")` (all packages) on completion
  requests; removing the plugin's snippet copies removes that scan from the
  keystroke path.

### Tests

- 240 tests pass; the menu ids/captions and completion behaviour are unchanged.

## v2.3.3 — 2026-11-15

### Fixed

- **Duplicate top-level menu removed.** `Main.sublime-menu` had two top-level
  nodes: the standard `Preferences > Package Settings > CppAssistant` and a
  standalone `CppAssistant` menu that duplicated Preferences and showed up as
  a second menu bar item. All entries now live in the standard Package
  Settings entry, and their captions follow the Language switch.
- **Duplicate completions from multiple sources.** The same `vector` could
  appear three times (built-in type, user snippet, template pack), and two
  user snippets with the same trigger both appeared. The final list is now
  deduplicated by trigger with a fixed priority: built-in type/function >
  user snippet > template pack.
- **Empty-prefix completion dumped the whole STL.** After `int n = ` the popup
  listed ~200 STL/keyword entries. With an empty prefix and no member access,
  only same-file symbols are returned now; `v.`, `p->` and `std::` still show
  their full lists.
- **Members of a typedef'd container disappeared.** `typedef std::vector<int>
  VI; VI a; a.` produced no members because alias-following only handled
  user-defined types. Typedef/using aliases are now resolved to the real
  container (and `_container_type_args` normalises the token-joined
  `std :: vector < int >`), so `a.` lists `push_back`, `size`, `begin` and the
  rest, with the actual template argument substituted.

### Tests

- 240 tests (+6): menu merge, cross-source dedupe, typedef/using member
  completion, and empty-prefix behaviour.

## v2.3.2 — 2026-11-15

### Fixed

- **Every completion raised `AttributeError: sublime.KIND_ID_CONSTANT`.**
  That constant has never existed in the Sublime API, so `_make_item()`
  failed for every candidate: the popup never appeared and the console
  filled with the same traceback — which is also why typing felt much
  slower. All kind ids now go through a guarded lookup with an AMBIGUOUS
  fallback, and `_make_item()` has its own safety net so one bad icon can
  never kill a whole completion batch.

### Changed

- **Template placeholders no longer leak into popup labels.**
  `vector<${1:int}>` used to be the trigger, so the list showed raw snippet
  text and the same type looked like several entries. Triggers are now just
  the qualified name (`vector`), which also removes those duplicates.
- **Shorter detail lines.** `_clean_signature()` resolves `${1:x}` → `x` and
  strips the `std::` prefix, so the details pane shows `vector<int>` /
  `push_back(x)` instead of the raw snippet text.
- **Less fuzzy noise.** When a real prefix match exists, pure subsequence
  matches are dropped (typing `vec` no longer offers `views::common`),
  while substring matches still work (`back` → `push_back` in member lists).
- **Bilingual annotations are complete.** English annotations are backfilled
  from `en_detail()` for every STL/PBDS entry, so English mode no longer
  shows Chinese descriptions (and the Chinese mode covers everything,
  including `scanf`, `vector` members and algorithms).

### Added

- **One language switch for everything**: `CppAssistant > Language`
  (简体中文 / English / English + Chinese) sets the menu bar, the completion
  popup and diagnostics at once. The same three entries exist in the command
  palette. The old separate "Diagnostic language" and "Toggle Menu Language"
  menu entries are replaced by this single switch.

### Tests

- 234 tests (+10). New `tests/test_completion_display.py` reproduces the
  missing `KIND_ID_CONSTANT`, checks trigger hygiene (no `$`/`<`), the
  placeholder cleanup, the language command and bilingual annotations.

## v2.3.1 — 2026-11-15

Fixes "the plugin installed but does nothing" on current Sublime Text builds,
and makes the default completion engine pure Python.

### Fixed

- **Menus greyed out / no commands registered on ST builds that disabled the
  3.3 plugin host.** 2.2.1 shipped without a `.python-version` file, so
  Sublime placed it on the deprecated Python 3.3 host. On builds such as
  4215 that host is disabled (`plugin host 3.3 is disabled` in the console),
  so the Python side never loaded while the static menu entries still
  appeared — every `ca_*` command looked disabled. The package now ships
  `.python-version` (`3.8`), and a test asserts it is present.

### Changed

- **Pure-local engine is the default.** `completion_engine` replaces the
  old `enable_clangd_engine` boolean and accepts three values:
  - `builtin` (default) — no clangd process, no libclang. Completion comes
    from FastEngine (same-file locals, parameters, members, enums,
    namespaces), the bundled STL/PBDS database and user snippets. Memory
    stays at normal Python-plugin level, which matters on laptops and when
    several C++ projects are open.
  - `auto` — use clangd when it is on `PATH`, fall back to `builtin`
    silently when it is missing (no status-bar nagging).
  - `clangd` — previous behaviour: always use the semantic engine and
    explain in the status bar when it cannot be found.
  Switching is immediate: the old client is stopped and libclang is
  released when moving to `builtin`. The menu and the command palette
  expose all three modes.
- The legacy boolean is still honoured when `completion_engine` is absent,
  so existing configurations keep their explicit choice.
- `builtin` also skips libclang for diagnostics and F12 unless
  `builtin_uses_libclang` is enabled, so "pure local" really is pure
  Python.
- Signature hints now work without clangd: `builtin` uses FastEngine's
  same-file signature and shows `arg 2/2: vector<int>& a`, matching the
  clangd popup layout.
- F12, diagnostics, formatting and `#include` insertion keep their local
  fallbacks in `builtin` mode.

### Tests

- 224 unit tests (+13). New `tests/test_engine_modes.py` covers the three
  modes, legacy-key compatibility, libclang gating, the switch command, and
  pure-local completion/signature behaviour.

## v2.3.0 — 2026-11-09

A verification-driven bugfix release: every fix below was independently
reproduced before patching (isolated probes against the real modules, real
`g++ -std=c++14` compiles for templates, and a live clangd wire test), and the
whole batch was re-verified afterwards (213 tests, `compileall`, plus new
integrity gates).

### Fixed — commands and completions

- **Hover popups and signature help had never worked**: the `CaHoverText` /
  `CaSignaturePopupText` classes did not end in `Command`, so Sublime never
  registered them and `run_command("ca_hover" / "ca_signature_popup")` was a
  silent no-op. Renamed to `CaHoverCommand` / `CaSignaturePopupCommand`
  (call sites unchanged). A new `tests/test_command_integrity.py` asserts
  that every internal `run_command("ca_...")` string has a matching
  `*Command` class — exactly the blind spot that let this ship.
- The lexer merged `]]` / `[[` into single operator tokens, so nested
  subscripts (`cnt[a[i]]`) produced two false "Unclosed bracket" errors, the
  array-bounds check was skipped entirely, and attribute handling broke.
  Multi-character bracket tokens are gone.
- `std::ranges::` / `std::views::` completion fell back to the whole library
  and inserted wrong code: the receiver parser returned an empty name for
  nested qualified scopes. Now resolved, including `std::__gnu_pbds::`.
- `stringstream ss; ss.` offered no members in either engine even though the
  member table existed; stream types are now recognized declaration kinds.
- The bare `std::` popup was truncated at 200 entries, which hid exactly the
  new `__gnu_pbds` skeletons; the cap is now 256 (the library has 204).
- The legacy engine (`enable_fast_engine: false`) did not infer method return
  types for `auto` (`auto value = m.at("k")` stayed `auto`); it now shares
  the fast engine's inference rules.
- `diagnostic_filters` normalized a bare `W` into warning name `w` and passed
  `-Wno-w` to the compiler; single-letter inputs are now rejected, as the
  module documentation always promised.

### Fixed — diagnostics and formatting

- clangd diagnostics only rendered for the non-standard `textDocument.uri`
  notification form. Real clangd sends the LSP-spec top-level `params.uri`
  (verified against a live clangd), so clangd diagnostics never rendered at
  all, and `diagnostic_filters` / context translation never applied on that
  path. Both forms render now.
- Compiler driver and linker errors (`g++: error:`, `cc1plus:`, `collect2:`,
  `ld: ...`) were dropped by the diagnostic regex, so a mistyped
  `compiler_extra_args` failed silently. They are parsed now, and output that
  matches no rule surfaces as a fallback diagnostic instead of nothing.
- Compilers running under a Chinese locale emit `错误：`/`警告：`, which also
  produced zero diagnostics; compile commands now force `LC_ALL=C`/`LANG=C`.
- gcc ≥ 10 curly quotes in `In function 'main'` context lines never matched;
  quotes are normalized before context extraction.
- The built-in formatter rewrote string literal contents (TAB expansion and
  the protection mask were applied to string/comment segments, and the mask
  desynchronized on deeply indented lines). Segments are length-synced and
  preserved verbatim now.
- Unclosed string/char literals at EOF (no trailing newline) went unreported,
  and `\`-continuations inside strings shifted all later line numbers.
- `REP(i, 1, n) {` macro-call lines were misread as function definitions and
  hijacked F12; three-level nested template arguments
  (`vector<vector<vector<int>>>`) broke declaration parsing entirely.
- FastEngine: editing a multi-line `#define` continuation corrupted the
  incremental token stream until the next full rescan; `sizeof(a[10])` and
  the legal one-past-end `&a[10]` were flagged as errors.
- Array-bounds diagnostics existed in FastEngine but no code path ever
  delivered them to the UI. They are now merged into the basic lint tier
  (deduplicated against parser diagnostics), so `int a[10]; a[10] = 1;`
  is flagged instantly as documented.

### Fixed — templates (each verified by real `g++ -std=c++14` compiles)

- `math/fft` referenced an undefined `PI` and failed to compile standalone.
- `math/lucas` called ntt's `mod_pow`, which is hardcoded to 998244353 — any
  other modulus silently returned wrong answers (verified numerically).
  `lucas` and `math/berlekamp-massey` now carry their own `mod_pow(a, e, m)`.
- `math/crt` used Fermat inverses (wrong for composite moduli, infinite loop
  when the modulus repeated); it now uses the extended Euclidean algorithm.
- `graph/dijkstra` used C++17 structured bindings under the project default
  of `c++14` (fails with `-pedantic-errors` and on older judges).
- `graph/dfs` / `graph/scc` recursed per node (stack overflow around 1e5
  nodes) and left global arrays uninitialized; `graph/mst` could build a
  linear-depth DSU chain; `ds/treap` wrote out of bounds past its capacity
  (ASAN-confirmed). All rewritten or guarded.

### Fixed — libclang engine

- The periodic PCH sweep ran with `keep_sig=None` every 30 minutes and
  deleted the PCH that was in use, forcing a cold rebuild each cycle; it now
  sweeps with the engine's active signature and never touches in-flight
  builds.
- Each settings reload started another unkillable 30-minute sweep timer
  chain; chains are now single-instance and generation-guarded.
- A failed PCH build was retried on every 0.1 s debounce; builds now back
  off. PCH build timeouts kill the child process instead of leaking it, and
  using an engine after `close()` raises instead of handing a NULL index to
  libclang.
- Completion requests that timed out raced the reply and lost it; the
  callback is registered before waiting now.
- `_compiler_include_dirs()` could run a 15 s compiler subprocess on the UI
  thread in the completion path; the completion path now uses the warm cache
  only, with a background thread refreshing it.

### Fixed — template management and snippets

- Creating/renaming a user snippet with a path-like trigger (`../escape`)
  silently sanitized the filename while writing the raw trigger into the XML
  and deleting the original file; such triggers are rejected now.
- A URL pack whose archive contains no `.sublime-snippet` files left an
  empty directory that then showed up as an enabled pack; the directory is
  only created once a snippet is actually copied.

### Changed

- Added `.python-version` (`3.8`) so current ST4 builds run the plugin on
  Python 3.14 instead of the deprecated 3.3 host (removal scheduled for
  2026); pre-4205 builds get real 3.8 and older ones stay on 3.3, which the
  source remains compatible with. README host statements updated.
- `Ctrl+Shift+C` (Copy Diagnostic) now ships in `Default.sublime-keymap` —
  the binding previously existed only in `Example.sublime-keymap`, which
  Sublime never loads automatically, despite the changelog calling it a
  default.
- README test counts, host notes, keybinding docs and the troubleshooting
  table (including a new "all menu items greyed out → check
  `ignored_packages`" row) were refreshed.

### Tests and packaging

- The suite grew to 213 tests, including the new command-integrity
  regression test. A template compile smoke gate (extract snippet CDATA,
  strip placeholders, `g++ -std=c++14 -fsyntax-only`, per-file and per-pack)
  was used to verify this release.

## v2.2.1 — 2026-11-06

Packaging hotfix. **Package Control could not install or upgrade CppAssistant**
on any platform; Windows failed hardest with an uncaught
`OSError: [Errno 22] Invalid argument` in the install worker thread.

### Fixed

- `messages.json` stored the full release-note text as the value of every
  version key. Package Control expects a **path to a text file inside the
  package**: `print_messages()` does
  `open(os.path.join(package_dir, value))`. With a whole paragraph as the
  "path" (newlines, colons, `<`, `>`, quotes), Windows rejected it with
  errno 22. Only `FileNotFoundError` is caught there, so the install thread
  died and the package was rolled back. This had been latent since 2.1.1 and
  became visible as soon as 2.2.0 was installed through Package Control.
- Release notes now live in `messages/install.txt`, `messages/2.1.1.txt`,
  `messages/2.2.0.txt` and `messages/2.2.1.txt`; `messages.json` maps only
  `install` / version keys to those paths.
- Fixed a typo in the install message (`/n` instead of a line break).

### Tests and packaging

- `test_messages_json_is_valid` now asserts that every value is a safe,
  existing in-package text file (no newlines, no absolute paths, no `..`,
  no Windows-invalid characters).
- `test_package_control_can_read_every_message` reproduces Package Control's
  `read_message()` open path, and `test_build_does_not_exclude_message_files`
  makes sure the `.sublime-package` build keeps `messages/`.

No functional changes to completion, diagnostics, navigation or commands.

## v2.2.0 — 2026-11-05

2.2.0 is the "make the popup tell you more, and stop making you retype things"
release. Competitive-programming specifics that were easy to forget are now
automatic: PBDS headers are inserted for you, `auto` variables show their real
type, `bits/stdc++.h` is explained instead of silently ignored, and one
keystroke fixes full-width punctuation. It also adds the navigation and
template workflow commands: call/type hierarchy, a template browser with
inline preview, snippet management, grouped references and status-bar hints.

### Features

- **PBDS header insertion**: completing `__gnu_pbds::tree`,
  `gp_hash_table` or `order_of_key` inserts
  `#include <ext/pb_ds/assoc_container.hpp>` and
  `#include <ext/pb_ds/tree_policy.hpp>` automatically. `bits/stdc++.h`
  does **not** cover PBDS, so these two headers are still inserted when
  the universal header is present. New setting:
  `cpp_auto_insert_pbds_includes` (default `true`). After the insertion
  clangd re-syncs the document immediately, so the next completion is
  already based on the new include.
- **`__gnu_pbds::` namespace completion**: `tree`, `gp_hash_table`,
  `cc_hash_table`, `rb_tree_tag`, `splay_tree_tag`, `ov_tree_tag`,
  `tree_order_statistics_node_update`, `null_type` and the resize
  policies, with the full template skeleton in the insert text.
- **C++20 subset**: `std::ranges` algorithms, `std::views` adaptors and
  common `std::concepts` are in the database, tagged `C++20`. Typing
  `ranges::` or `views::` lists only that sub-namespace and drops the
  prefix, so `std::ranges::s` completes to `sort`, not to `std::ranges::sort`
  inside an already-qualified name.
- **Template browser with preview**: `CppAssistant: Browse Templates`
  shows every installed snippet in a quick panel with a preview of the
  first 20 lines, its origin and its line count; selecting an entry
  inserts it as a real snippet (`$0` / `${1:default}` tab stops work).
- **Snippet management**: `CppAssistant: Manage User Snippets` lists user
  and template-pack snippets with open / rename-trigger / move-to-trash
  actions. Trashed files go to `Packages/User/CppAssistantSnippetTrash`
  with a `.off` extension, so they stop appearing in completion but can
  be restored.
- **Full-width punctuation fix**: `CppAssistant: Fix Full-width
  Punctuation` (`Ctrl+Alt+,`) replaces `，；：（）` and friends with their
  half-width forms **only in code** — comments, string/char literals and
  raw strings keep their Chinese punctuation. Works on the selection when
  one exists, asks for confirmation, and reports how many characters it
  changed.
- **Call / type hierarchy**: `CppAssistant: Call Hierarchy` asks clangd
  for incoming and outgoing calls of the function under the cursor;
  `CppAssistant: Type Hierarchy` lists base classes (supertypes) and
  derived classes (subtypes). Both open the result in a quick panel and
  jump on selection.
- **Status bar hints** (`show_status_hint`, default `true`): shows the
  active completion source and the call under the cursor, including which
  argument is being typed, e.g.
  `[clangd] · void solve(int n, vector<int>& a) · arg 2/2: vector<int>& a`.
  A lightweight stand-in for CodeLens.
- **Settings validation**: `CppAssistant: Validate Settings` (also run at
  startup) reports values Sublime would silently ignore, such as
  `cxx_standard: "c++2x"`, `lint_timeout: 0` or
  `include_paths: "D:/inc"`, in the console, the status bar and an output
  panel.
- **Grouped find references**: results are sorted by file, numbered
  `[3/7]` per file, and keep the inline preview line.

### Completion display

- Local variables, parameters, functions, structs, enums, macros and
  aliases now carry concrete details in the builtin fallback:
  `total` becomes `int`, `add` becomes `int add(int x, int y)`, and
  parameters are labelled `parameter`.
- Completion ranking prefers local symbols first, then user-defined
  functions and types, then the STL database, then keywords.
- STL and `__gnu_pbds` member signatures now show complexity hints such as
  `push_back(int) · O(1)`, `find(const long long&) · O(log n)`, and
  `insert(...) · O(1) avg` for hash tables.
- User snippets and installed template packs are annotated with their
  origin, for example `kmp · 字符串模板` / `kmp · Strings template`,
  so the popup no longer hides where an entry came from.
- Macros use their own kind (`macro`, variable icon) instead of being
  grouped with functions, and `#include` completions explain what the
  header contains (`bits/stdc++.h` → all standard headers, **not** PBDS).
- `auto` variables are inferred at completion time: `auto it = v.find(x)`
  shows `vector<int>::iterator`, `auto value = m.at("k")` shows `int`.
- Duplicate triggers inside one container (for example `stringstream`,
  which inherits both stream directions) no longer appear twice.
- `std::ranges` / `std::views` / `std::concepts` entries are tagged
  `C++20`, so they rank after the C++14 database until the standard is
  raised.

### Removed

- The bundled `Basic` template pack (`us`, `inc`, `fastio`, `mainf`,
  `solvef`, `pii`) is gone; the plugin now ships Geometry, Graph, DS,
  Strings and Math packs only.

### Tests and packaging

- Test suite grows from 134 to 208 cases: full-width fixer, settings
  validation, call context, PBDS/ranges data, completion-database
  integrity (duplicate triggers, missing English details), package
  integrity (commands / menu / keymap / messages must match the classes
  that exist) and an import smoke test that loads the whole plugin against
  a stubbed `sublime` module.
- New modules: `cppassistant/ca_fixes.py` (punctuation scanner/rewriter)
  and `cppassistant/ca_config.py` (settings schema validation). Both are
  pure Python and unit-tested without Sublime Text.

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
