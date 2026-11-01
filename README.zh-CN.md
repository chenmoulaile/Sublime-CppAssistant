# CppAssistant

Sublime Text 4 的 C++ 辅助插件：内嵌真实 clangd 引擎，没有 clangd 时自动退回零依赖的 FastEngine 结构引擎和纯 Python 内置数据库，零配置可用。

插件自带一个精简 LSP 客户端，通过 stdio JSON-RPC 直接驱动 `clangd`，提供编译器级语义补全、悬停文档、函数签名提示、实时诊断、代码操作、语义重命名和查找引用；也可以用 `g++` / `clang++` 做完整语法检查。没有 clangd 时，FastEngine 用增量词法分析和容错结构解析提供同文件局部变量、参数、成员、枚举值等补全，并加速 F12 跳转和即时检查。可选的 **libclang 语义引擎** 通过 `ctypes` 调用已安装的 libclang，提供 PCH 加速诊断、精确的跨文件定义和热缓存语义补全，无需启动编译器进程。格式化优先调用 `clang-format`，没有时使用内置的 jiangly 风格兜底格式化器，并支持只格式化选中的行。

界面与默认提示为英文，中文和双语诊断是可选项。英文文档见 [README.md](README.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- **clangd 语义补全**：函数签名、重载、容器成员、局部变量；用户片段和内置片段排在最前。
- **FastEngine 兜底**：零依赖增量解析器，在 clangd 关闭、未安装或尚未就绪时提供同文件局部变量、参数、类/结构体成员、枚举值、命名空间、别名和宏补全。
- **可选 libclang 语义引擎**：通过 `ctypes` 加载系统 libclang（无需 Python 包），提供 `libclang` 诊断引擎、热缓存语义补全和跨文件 F12 兜底。后台构建的 `bits/stdc++.h` PCH 可把热诊断降到约 200ms，成员补全降到 10–20ms。
- **智能头文件插入**：接受补全时自动补对应的 `#include`，会识别 `bits/stdc++.h` 和已包含的头文件。
- **悬停文档**与**函数签名提示**：由 clangd 提供。
- **代码操作**：clangd 的快速修复、`#include` 建议，以及提取函数等重构。
- **语义重命名**与**查找引用**：跨当前文件和项目头文件。
- **实时诊断**：编译器检查（`g++` / `clang++`）、clangd 的 `publishDiagnostics`，或可选的 libclang 引擎；另有毫秒级的括号、全角标点、未闭合字符串基础检查。
- **F12 跳转定义**：当前文件、已打开文件、本地头文件、`include_paths`，最后回退到标准库符号表。FastEngine 会先按作用域给出同文件候选，再执行文本搜索；libclang 在热缓存时补充精确的跨文件和标准库位置。
- **格式化**：clang-format 或内置保守格式化器，可格式化整个文档或仅格式化选区。
- **英文 / 中文 / 中英双语**诊断文本与补全详情。
- **PCH 加速**与重复检查结果缓存。

## 环境要求

- Sublime Text 4（build 4000 或更新）。
- 可选：[clangd](https://clangd.llvm.org/)，用于语义补全、悬停、签名提示和 clangd 诊断。
- 可选：`PATH` 中的 `g++` 或 `clang++`（也可设置 `compiler_path`），用于编译器诊断和 PCH。
- 可选：`libclang`（`libclang.dll` / `libclang.so` / `libclang.dylib`），用于 libclang 诊断引擎、语义补全和 F12 兜底。插件会自动发现，也可设置 `libclang_path`；libclang 旁边有匹配的 `clang++` 时可启用 PCH 加速。
- 可选：`PATH` 中的 `clang-format`（也可设置 `clang_format_path`），用于完整格式化。

插件源码保持与旧版 ST4 的 Python 3.3 插件宿主兼容；当前版本（例如 Sublime Text 4215）使用 Python 3.14，两种宿主都能加载。

## 安装

### Package Control（推荐）

1. 按 `Ctrl+Shift+P` / `Cmd+Shift+P` 打开命令面板。
2. 执行 **Package Control: Install Package**。
3. 搜索 **CppAssistant** 并安装。

CppAssistant 已收录在 Package Control 默认频道。新版本发布后执行 **Package Control: Upgrade Package** 即可更新。

### git clone

```bash
cd "%APPDATA%\Sublime Text\Packages"
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```

Linux / macOS 的 Packages 目录通常是 `~/.config/sublime-text/Packages`。目录名请保持 `CppAssistant`。

### 手动安装 ZIP

下载仓库 ZIP，解压到 Packages 目录，把文件夹重命名为 `CppAssistant`，然后重启 Sublime Text。

## 快速开始

1. 打开 `.cpp` 或 `.h` 文件。
2. 开始输入。如果安装了 clangd，会得到语义补全；首次弹窗可能先显示内置数据库结果，clangd 的 preamble 准备好后会自动刷新。
3. 在命令面板搜索 `CppAssistant:` 可以查看全部命令。

插件默认不注册快捷键，避免和其他包冲突。`Example.sublime-keymap` 提供了 F12、`Shift+Alt+F`、`Ctrl+Alt+F`、`Ctrl+.`、`Ctrl+Alt+R`、`Shift+F12`、`Ctrl+Alt+D` 的可选条目，按需复制到用户键位文件即可。

## 命令

| 命令 | 功能 |
| --- | --- |
| `CppAssistant: Format Document (jiangly style)` | 格式化整个文档 |
| `CppAssistant: Format Selection` | 只格式化选中的行 |
| `CppAssistant: Go to Definition` | 跳转到定义（F12 风格） |
| `CppAssistant: Code Actions` | 显示 clangd 快速修复与重构 |
| `CppAssistant: Rename Symbol` | 跨项目重命名符号 |
| `CppAssistant: Find References` | 列出光标处符号的引用 |
| `CppAssistant: Show Diagnostics` | 显示诊断面板 |
| `CppAssistant: Diagnostic Language - English` | 英文诊断与补全详情 |
| `CppAssistant: Diagnostic Language - Chinese` | 中文诊断与补全详情 |
| `CppAssistant: Diagnostic Language - Both` | 中英双语 |
| `CppAssistant: Completion Mode - LSP-clangd Style` | 模糊、作用域感知的匹配 |
| `CppAssistant: Completion Mode - Strict Prefix` | 严格前缀匹配 |
| `CppAssistant: Completion Engine - clangd` | 使用 clangd 引擎 |
| `CppAssistant: Completion Engine - Built-in Database` | 使用内置数据库 |
| `CppAssistant: Completion Engine Status` | 查看 clangd / 客户端 / preamble 状态 |
| `CppAssistant: Diagnostic Engine - clangd` | 使用 clangd 诊断 |
| `CppAssistant: Diagnostic Engine - Compiler` | 使用 g++/clang++ 诊断 |
| `CppAssistant: Diagnostic Engine - libclang (PCH)` | 使用可选的 libclang 引擎 |
| `CppAssistant: Toggle Inline Error Phantoms` | 显示或隐藏行内提示 |
| `Preferences: CppAssistant Settings` | 编辑默认设置和用户设置 |
| `Preferences: CppAssistant Key Bindings` | 编辑可选快捷键 |

## 设置

通过 **Preferences: CppAssistant Settings** 打开设置，或直接编辑 `Packages/User/CppAssistant.sublime-settings`。用户设置会覆盖默认值。

| 设置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_completions` | `true` | 补全总开关 |
| `enable_clangd_engine` | `true` | 使用 clangd；`false` 使用内置数据库 |
| `enable_fast_engine` | `true` | 使用 FastEngine 提供同文件符号、即时检查和 F12；`false` 使用旧版正则路径 |
| `enable_libclang_engine` | `true` | 自动发现并使用 libclang；不存在时不做任何事 |
| `libclang_path` | `""` | libclang 库文件或所在目录；留空则自动发现 |
| `libclang_args` | `[]` | 额外 libclang 参数，例如 `["-DDEBUG"]` |
| `libclang_pch` | `true` | 用匹配的 `clang++` 构建并复用 `bits/stdc++.h` PCH |
| `libclang_completion` | `true` | 翻译单元已热缓存时合并 libclang 补全 |
| `libclang_goto` | `true` | 翻译单元已热缓存时用 libclang 提供跨文件 F12 |
| `clangd_binary` | `""` | clangd 可执行文件；留空则在 `PATH` 中查找 |
| `clangd_args` | `[]` | 额外 clangd 命令行参数 |
| `clangd_completion_wait_ms` | `0` | 同步等待 clangd 结果的毫秒数；`0` 不阻塞编辑器 |
| `clangd_extra_fallback_flags` | `[]` | 传给 clangd 的额外 fallback 参数 |
| `clangd_working_dir` | `""` | clangd 项目根目录；留空使用文件所在目录 |
| `enable_clangd_style_completion` | `true` | `true` = LSP-clangd 模糊模式；`false` = 严格前缀 |
| `auto_insert_includes` | `true` | 接受补全时自动插入对应 `#include` |
| `enable_hover` | `true` | 显示 clangd 悬停文档 |
| `enable_signature_help` | `true` | 在函数调用括号内显示签名提示 |
| `enable_linting` | `true` | 实时诊断总开关 |
| `lint_engine` | `"compiler"` | `compiler`、`clangd` 或 `libclang` |
| `instant_basic_check` | `true` | 毫秒级括号 / 全角标点 / 字符串检查 |
| `lint_debounce` | `0.1` | 停止输入后等待多少秒开始编译器检查 |
| `lint_timeout` | `12` | 编译器单次检查超时（秒） |
| `enable_pch` | `true` | 构建并使用 `bits/stdc++.h` PCH 缓存 |
| `show_phantoms` | `true` | 在错误行下方显示提示条 |
| `display_language` | `"en"` | `en` / `zh` / `both`；影响诊断和补全详情 |
| `cxx_standard` | `"c++14"` | 检查和 clangd 使用的 C++ 标准 |
| `compiler_path` | `""` | 编译器路径；留空则在 `PATH` 中查找 |
| `compiler_extra_args` | `[]` | 额外编译参数 |
| `include_paths` | `[]` | 额外头文件目录，用于 clangd、PCH 和 F12 |
| `indent_width` | `4` | 内置格式化器的缩进宽度 |
| `format_on_save` | `false` | 保存前格式化整个文档 |
| `clang_format_path` | `""` | clang-format 路径；留空则在 `PATH` 中查找 |
| `clang_format_style` | jiangly 风格配置 | clang-format 的 style 字符串 |

## 工作原理

### 补全

开启 `enable_clangd_engine` 后，插件会按需启动 clangd，通过 stdio JSON-RPC 通信。它在缓存目录维护 `compile_commands.json`，把当前的 `-std=`、编译参数、`include_paths` 和编译器头文件路径交给 clangd；对于不在编译数据库里的文件，还会提供 `fallbackFlags`。

补全请求默认异步：首次弹窗可以先显示内置数据库结果，clangd 结果到达后自动替换。把 `clangd_completion_wait_ms` 调大可以让首次弹窗短暂等待 clangd，但会增加输入延迟。

clangd 关闭、未安装或尚未就绪时，使用内置数据库。开启 `enable_fast_engine` 时，FastEngine 会先索引当前文件，提供按作用域过滤的局部变量、参数、成员、枚举值和命名空间，再与常用 STL 函数、容器成员、头文件、关键字和用户片段合并。解析结果按版本缓存，只从第一处改动行开始重新词法分析，保证输入时的响应速度。

可选的 libclang 引擎已加载、且当前缓冲区的翻译单元已热缓存时（通常是 `lint_engine` 为 `"libclang"`），libclang 补全会排在 FastEngine 和内置数据库结果之前。冷缓存绝不会阻塞 UI 线程：FastEngine 和内置数据库立即返回，libclang 结果在后续弹窗中加入。

### 诊断

- **即时基础检查**：每次编辑后检查括号配平、全角标点、未闭合字符串 / 注释、未闭合原始字符串、格式错误的 `#include`，以及未配平的 `#if` / `#endif`。
- **编译器检查**：后台执行 `g++ -fsyntax-only` 或 `clang++ -fsyntax-only`，结果按源码文本和设置缓存。开启 `enable_pch` 时会在后台构建 PCH。
- **clangd 检查**（`lint_engine: "clangd"`）：直接使用 clangd 推送的诊断，不启动编译器；消息仍会经过插件的本地翻译表。
- **libclang 检查**（`lint_engine: "libclang"`）：后台通过 libclang C API 解析缓冲区，不启动编译器进程；libclang 无法加载时自动回退到编译器引擎。开启 `libclang_pch` 且有匹配的 `clang++` 时，会在后台构建一次 `bits/stdc++.h` 预编译头并复用，热检查约 200ms，成员补全约 10–20ms。

切换 `display_language` 只改变已有结果的渲染语言，不会重新运行编译器、clangd 或 libclang。

### 跳转定义

F12 风格查找依次搜索当前文件、其他已打开文件、当前文件包含的头文件、`include_paths` 目录，最后回退到标准库符号表。找到系统头文件路径时可以直接打开真实头文件。FastEngine 会先给出作用域感知的同文件候选，因此本地函数、类型、成员和枚举值在文本搜索之前就能命中。libclang 引擎已热缓存时，会以更高优先级补充精确的跨文件和标准库位置。

### 代码操作、重命名与查找引用

这些命令需要文件已保存并运行 clangd。**Code Actions** 对光标或选区发送 `textDocument/codeAction`，用 quick panel 列出快速修复和重构。自带编辑的操作直接应用；延迟计算的操作走 `codeAction/resolve`；提取函数等 tweak 走 `workspace/executeCommand` 和 `workspace/applyEdit`。

**Rename Symbol** 发送 `textDocument/rename`，把返回的 `WorkspaceEdit` 应用到已打开缓冲区和未打开的项目头文件。**Find References** 发送 `textDocument/references`，在 quick panel 中列出结果，选中后跳转。`WorkspaceEdit` 中的文件创建、改名、删除操作暂不支持；遇到这类操作会整体拒绝，避免只应用一半。

### 格式化

优先使用 clang-format 和 jiangly 风格配置（4 空格缩进、K&R 大括号、100 列限制、不排序 include）。没有 clang-format 时，内置格式化器只做保守整理：缩进归一化、大括号空格、标点空格，不破坏字符串、注释和模板嵌套。

**Format Selection** 优先请求 clangd 的 `textDocument/rangeFormatting`；clangd 不可用时执行 clang-format `--lines`，最后使用内置范围格式化器。只修改选区覆盖的完整行，缓冲区其余部分保持不变。

### 可选的 libclang 语义引擎

第二阶段的语义引擎通过 `ctypes` 调用系统 libclang，不需要安装任何 Python 包，也不启动编译器进程。它会从 `LIBCLANG_PATH`、插件的 `libs/` 目录、clangd / clang 所在目录，以及常见的 LLVM / MSYS2 / Xcode / Homebrew 位置发现 libclang，并自动发现 clang resource 目录，因此 `#include <bits/stdc++.h>` 能正确解析内置头文件。

引擎绝不会在 Sublime 的 UI 线程加载或冷解析：诊断在后台线程执行；补全和 F12 只使用已缓存的翻译单元，冷缓存时保留 FastEngine / clangd 的结果不变。引擎用一个小型 LRU 缓存翻译单元，缓冲区变化时做增量重解析。执行 **Diagnostic Engine - libclang (PCH)** 可以预热引擎，**Completion Engine Status** 会显示库路径、resource 目录、PCH 状态和解析 / 补全耗时。

## 常见问题

**找不到 clangd。** 安装 clangd 并加入 `PATH`，或设置 `clangd_binary`。状态栏会提示当前是否使用内置数据库。插件会定期重试发现 clangd，运行中安装 clangd 不必重启 Sublime。

**F12 没反应。** 默认没有注册快捷键。使用命令面板，或把 `Example.sublime-keymap` 里的 F12 条目复制到用户键位文件。

**按回车没有换行，而是接受了补全。** 这是 Sublime 在补全弹窗打开时的默认键位。若希望回车始终换行，把下面条目加入用户键位，并用 Tab 或标点确认补全：

```json
{ "keys": ["enter"], "command": "insert", "args": {"characters": "\n"},
  "context": [{ "key": "auto_complete_visible" }] }
```

**格式化没有变化。** 没有 clang-format 时，内置格式化器只做安全整理。安装 clang-format 或设置 `clang_format_path` 可获得完整格式化。

**代码操作、重命名或查找引用没有反应。** 这些功能依赖 clangd。先保存文件，再用 **CppAssistant: Completion Engine Status** 确认 clangd 已运行。快速修复还需要 clangd 已为当前文档版本推送诊断；编辑后稍等片刻再试。

**更新后诊断突然停了。** 重启一次 Sublime Text。若仍异常，打开 **View -> Show Console**，查看以 `[CppAssistant]` 开头的消息。

**找不到 libclang。** 安装 LLVM（或使用 MSYS2 / Xcode 自带的 libclang），确保库可以被发现，或把 `libclang_path` 设为 `libclang.dll` / `libclang.so` / `libclang.dylib` 或其所在目录。libclang 无法加载时，libclang 诊断引擎会自动回退到编译器引擎；**Completion Engine Status** 会显示发现错误。

**libclang PCH 没有生效。** PCH 加速要求 `clang++` 的主次版本与 libclang 一致，并且位于 libclang 旁边或 `PATH` 中。没有匹配的驱动时诊断仍可用，但每次都会重新解析头文件。首次构建需要一些时间，之后的检查会复用系统临时目录中的 PCH。

## 开发

纯 Python 引擎和 clangd 协议辅助函数可以在 Sublime 之外测试：

```bash
python -m unittest discover -s tests -v
```

找不到 libclang 时，libclang 测试会自动跳过，因此没有 LLVM 的机器也能通过测试。较慢的 PCH 构建测试需要手动开启：

```bash
CA_TEST_LIBCLANG_PCH=1 python -m unittest tests.test_semantic_engine -v
```

测试不会被打进安装包。主要文件：

- `CppAssistant.py`：Sublime 集成、命令、诊断、格式化、代码操作、重命名与查找引用。
- `cppassistant/ca_engine.py`：补全分析、定义查找、整篇与范围兜底格式化、编译器输出解析。
- `cppassistant/ca_fast.py`：零依赖增量词法分析、容错解析、作用域 / 符号索引、补全、定义和即时检查。
- `cppassistant/ca_semantic.py`：可选的 libclang ctypes 引擎，包含发现、resource 目录、诊断、补全、定义和 PCH 加速。
- `cppassistant/ca_clangd.py`：精简 clangd LSP 客户端，包含 WorkspaceEdit 与代码操作辅助函数。
- `cppassistant/ca_stdlib_data.py`：STL 补全数据与翻译表。
- `cppassistant/ca_user_snippets.py`：用户 `.sublime-snippet` 加载器。

## License

[MIT](LICENSE) © chenmoulaile
