# CppAssistant

C++ assistance for Sublime Text 4, built around a real clangd engine with a
zero-configuration fallback.

CppAssistant ships a small LSP client that talks to `clangd` directly for
compiler-grade completions, hover documentation, signature help, live
diagnostics, code actions, semantic rename, and find references. When clangd is
not installed it falls back to a pure-Python STL database, so completion still
works out of the box. The compiler can also be used for full syntax checks, and
`clang-format` (or a built-in formatter) is used for jiangly-style formatting,
including formatting only the selected lines.

The UI and default messages are English. Chinese and bilingual diagnostic
text are available as an option. Chinese documentation is in
[README.zh-CN.md](README.zh-CN.md); release history is in
[CHANGELOG.md](CHANGELOG.md).

## Features

- **clangd semantic completion** with signatures, overloads, members, and
  local variables; user snippets and built-in snippets are listed first.
- **Smart `#include` insertion**: accepting a completion can add the matching
  header, while respecting `bits/stdc++.h` and headers already present.
- **Hover documentation** and **signature help** powered by clangd.
- **Code actions** from clangd: quick fixes, `#include` suggestions, and
  refactors such as extracting a function.
- **Semantic rename** and **find references** across the current file and
  project headers.
- **Live diagnostics** from either the compiler (`g++` / `clang++`) or
  clangd's `publishDiagnostics`, plus instant bracket/punctuation/string
  checks that do not wait for a compiler.
- **F12 go-to-definition** across the current file, other open files, local
  headers, `include_paths`, and a standard-library fallback.
- **Formatting** with clang-format or a conservative built-in fallback, for
  the whole document or only the selected lines.
- **English, Chinese, or bilingual** diagnostic text and completion details.
- **PCH acceleration** for compiler checks and a cache for repeated results.

## Requirements

- Sublime Text 4 (build 4000 or newer).
- Optional: [clangd](https://clangd.llvm.org/) for semantic completion, hover,
  signature help, and clangd diagnostics.
- Optional: `g++` or `clang++` on `PATH` (or set `compiler_path`) for compiler
  diagnostics and PCH support.
- Optional: `clang-format` on `PATH` (or set `clang_format_path`) for
  formatting.

The plugin source uses syntax that loads on both older ST4 builds with a
Python 3.3 plugin host and current builds; Sublime Text 4215 uses Python 3.14.

## Installation

### Package Control (recommended)

1. Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`).
2. Run **Package Control: Install Package**.
3. Search for **CppAssistant** and install it.

CppAssistant is available from the default Package Control channel. After a
new release, run **Package Control: Upgrade Package** to update.

### Git clone

```bash
cd "%APPDATA%\Sublime Text\Packages"
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```

On Linux and macOS the Packages directory is usually
`~/.config/sublime-text/Packages`. Keep the directory name `CppAssistant`.

### Manual ZIP install

Download the repository ZIP, extract it into the Packages directory, rename
the folder to `CppAssistant`, and restart Sublime Text.

## Quick start

1. Open a `.cpp` or `.h` file.
2. Start typing. If clangd is available, semantic completions appear; the
   first popup may briefly show built-in results while clangd prepares its
   preamble, then refresh automatically.
3. Use the Command Palette and type `CppAssistant:` to see all commands.

No key bindings are registered by default, to avoid conflicts with other
packages. `Example.sublime-keymap` contains optional entries for F12,
`Shift+Alt+F`, `Ctrl+Alt+F`, `Ctrl+.`, `Ctrl+Alt+R`, `Shift+F12`, and
`Ctrl+Alt+D`; copy the ones you want into your user keymap.

## Commands

| Command | Purpose |
| --- | --- |
| `CppAssistant: Format Document (jiangly style)` | Format the whole file |
| `CppAssistant: Format Selection` | Format only the selected lines |
| `CppAssistant: Go to Definition` | Jump to the symbol definition (F12-style) |
| `CppAssistant: Code Actions` | Show clangd quick fixes and refactors |
| `CppAssistant: Rename Symbol` | Rename a symbol across the project |
| `CppAssistant: Find References` | List references to the symbol at the cursor |
| `CppAssistant: Show Diagnostics` | Show the diagnostics panel |
| `CppAssistant: Diagnostic Language - English` | English diagnostics and completion details |
| `CppAssistant: Diagnostic Language - Chinese` | Chinese diagnostics and completion details |
| `CppAssistant: Diagnostic Language - Both` | English with Chinese appended |
| `CppAssistant: Completion Mode - LSP-clangd Style` | Fuzzy, scope-aware matching |
| `CppAssistant: Completion Mode - Strict Prefix` | Strict prefix-only matching |
| `CppAssistant: Completion Engine - clangd` | Use the clangd engine |
| `CppAssistant: Completion Engine - Built-in Database` | Use the static database |
| `CppAssistant: Completion Engine Status` | Show clangd/client/preamble status |
| `CppAssistant: Diagnostic Engine - clangd` | Use clangd diagnostics |
| `CppAssistant: Diagnostic Engine - Compiler` | Use g++/clang++ diagnostics |
| `CppAssistant: Toggle Inline Error Phantoms` | Show or hide inline messages |
| `Preferences: CppAssistant Settings` | Edit default and user settings |
| `Preferences: CppAssistant Key Bindings` | Edit the optional key bindings |

## Settings

Open settings with **Preferences: CppAssistant Settings**, or edit
`Packages/User/CppAssistant.sublime-settings` directly. User settings override
the defaults.

| Setting | Default | Description |
| --- | --- | --- |
| `enable_completions` | `true` | Master switch for completions |
| `enable_clangd_engine` | `true` | Use clangd; `false` uses the built-in database |
| `clangd_binary` | `""` | clangd executable; empty means search `PATH` |
| `clangd_args` | `[]` | Extra clangd command-line arguments |
| `clangd_completion_wait_ms` | `0` | Synchronous wait for clangd results; `0` never blocks the editor |
| `clangd_extra_fallback_flags` | `[]` | Extra fallback flags passed to clangd |
| `clangd_working_dir` | `""` | clangd project root; empty uses the file's directory |
| `enable_clangd_style_completion` | `true` | `true` = LSP-clangd fuzzy mode; `false` = strict prefix |
| `auto_insert_includes` | `true` | Insert the matching `#include` when accepting a completion |
| `enable_hover` | `true` | Show clangd hover documentation |
| `enable_signature_help` | `true` | Show function signature help inside call parentheses |
| `enable_linting` | `true` | Master switch for live diagnostics |
| `lint_engine` | `"compiler"` | `compiler` or `clangd` |
| `instant_basic_check` | `true` | Millisecond bracket/punctuation/string checks |
| `lint_debounce` | `0.1` | Seconds to wait after typing before a compiler check |
| `lint_timeout` | `12` | Compiler timeout in seconds |
| `enable_pch` | `true` | Build and use a `bits/stdc++.h` PCH cache |
| `show_phantoms` | `true` | Show inline diagnostic messages |
| `display_language` | `"en"` | `en`, `zh`, or `both`; affects diagnostics and completion details |
| `cxx_standard` | `"c++14"` | Standard for checks and clangd |
| `compiler_path` | `""` | Compiler executable; empty means search `PATH` |
| `compiler_extra_args` | `[]` | Extra compiler arguments |
| `include_paths` | `[]` | Extra header directories for clangd, PCH, and F12 |
| `indent_width` | `4` | Indent width for the built-in formatter |
| `format_on_save` | `false` | Format the document before saving |
| `clang_format_path` | `""` | clang-format executable; empty means search `PATH` |
| `clang_format_style` | jiangly-style config | clang-format style string |

## How it works

### Completion

With `enable_clangd_engine` enabled, CppAssistant starts clangd lazily and
communicates over stdio JSON-RPC. It maintains a `compile_commands.json` in a
cache directory so clangd receives the active `-std=`, compiler arguments,
`include_paths`, and compiler header paths. The client also supplies
`fallbackFlags` for files that are not in the compilation database.

Completion requests are asynchronous by default: the first popup can show
built-in results immediately, and clangd results replace them when they
arrive. Setting `clangd_completion_wait_ms` above zero makes the first popup
wait briefly for clangd at the cost of some typing latency.

The built-in database is used when clangd is disabled, unavailable, or not
ready. It recognizes common STL functions, container members, headers,
keywords, and user snippets.

### Diagnostics

- **Instant basic check** runs on every edit and catches unbalanced brackets,
  full-width punctuation, and unclosed strings/comments.
- **Compiler check** runs `g++ -fsyntax-only` or `clang++ -fsyntax-only` in
  the background. Results are cached by source text and settings. PCH files
  are built in the background when `enable_pch` is on.
- **clangd check** (`lint_engine: "clangd"`) uses the diagnostics pushed by
  clangd, with no compiler process. clangd messages are still translated by
  the plugin's local translation table.

Changing `display_language` only changes how existing results are rendered; it
does not rerun the compiler or clangd.

### Go to definition

F12-style lookup searches the current file, other open files, headers included
by the current file, `include_paths` directories, and finally the standard
library symbol table. If a system header can be found, the command can open
the real header file.

### Code actions, rename, and references

These commands require a saved file and a running clangd. **Code Actions**
requests `textDocument/codeAction` for the cursor or selection and shows the
available quick fixes and refactors in a quick panel. Actions that carry an
edit are applied directly; actions that clangd resolves lazily use
`codeAction/resolve`, and refactor tweaks use `workspace/executeCommand` plus
`workspace/applyEdit`.

**Rename Symbol** sends `textDocument/rename` and applies the returned
`WorkspaceEdit` to open buffers and to project headers that are not open.
**Find References** sends `textDocument/references` and lists the results in a
quick panel; selecting an entry jumps to that location. File creation, rename,
and deletion operations in a `WorkspaceEdit` are not applied; the command
reports the action as unsupported instead of applying half of it.

### Formatting

CppAssistant runs clang-format with a jiangly-style configuration (4-space
indent, K&R braces, 100-column limit, no include sorting). If clang-format is
missing, a conservative built-in formatter normalizes indentation, brace
spacing, and punctuation without changing strings, comments, or template
nesting.

**Format Selection** first asks clangd for `textDocument/rangeFormatting`.
When clangd is unavailable it runs clang-format with `--lines`, and finally
uses the built-in range formatter. Only the lines touched by the selection are
changed; the rest of the buffer is preserved.

## Troubleshooting

**clangd is not found.** Install clangd and make sure it is on `PATH`, or set
`clangd_binary`. The status bar reports when the built-in database is in use.
The plugin retries clangd discovery periodically, so installing clangd while
Sublime is running does not require a restart.

**F12 does nothing.** No key binding is installed by default. Use the Command
Palette command, or copy the F12 entry from `Example.sublime-keymap` into your
user keymap.

**Enter accepts a completion instead of inserting a newline.** This is
Sublime's default behavior whenever the completion popup is open. To make
Enter always insert a newline, add this to your user keymap and accept
completions with Tab or punctuation instead:

```json
{ "keys": ["enter"], "command": "insert", "args": {"characters": "\n"},
  "context": [{ "key": "auto_complete_visible" }] }
```

**Formatting does not change anything.** Without clang-format the built-in
formatter only performs safe normalizations. Install clang-format or set
`clang_format_path` for full formatting.

**Code actions, rename, or references do nothing.** These features require
clangd. Save the file first and check **CppAssistant: Completion Engine
Status** to confirm that clangd is running. Quick fixes also need clangd to
have published diagnostics for the current document version; wait a moment
after the last edit and try again.

**Diagnostics stopped after an update.** Restart Sublime Text once. If the
problem persists, open **View -> Show Console** and look for messages starting
with `[CppAssistant]`.

## Development

The pure-Python engine and clangd protocol helpers can be tested outside
Sublime Text:

```bash
python -m unittest discover -s tests -v
```

Tests are not included in the installed package. The main files are:

- `CppAssistant.py`: Sublime integration, commands, diagnostics, formatting,
  code actions, rename, and references.
- `cppassistant/ca_engine.py`: completion analysis, definition lookup,
  whole-file and range formatting fallbacks, and compiler output parsing.
- `cppassistant/ca_clangd.py`: minimal LSP client for clangd, including
  WorkspaceEdit and code-action helpers.
- `cppassistant/ca_stdlib_data.py`: STL completion data and translations.
- `cppassistant/ca_user_snippets.py`: user `.sublime-snippet` loader.

## License

[MIT](LICENSE) © chenmoulaile
