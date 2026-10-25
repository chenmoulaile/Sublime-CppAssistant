# CppAssistant

Sublime Text 4 的 C++ 辅助插件：内嵌真实 clangd 引擎，没有 clangd 时自动退回纯 Python 内置数据库，零配置可用。

插件自带一个精简 LSP 客户端，通过 stdio JSON-RPC 直接驱动 `clangd`，提供编译器级语义补全、悬停文档、函数签名提示和实时诊断；也可以用 `g++` / `clang++` 做完整语法检查。格式化优先调用 `clang-format`，没有时使用内置的 jiangly 风格兜底格式化器。

界面与默认提示为英文，中文和双语诊断是可选项。英文文档见 [README.md](README.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- **clangd 语义补全**：函数签名、重载、容器成员、局部变量；用户片段和内置片段排在最前。
- **智能头文件插入**：接受补全时自动补对应的 `#include`，会识别 `bits/stdc++.h` 和已包含的头文件。
- **悬停文档**与**函数签名提示**：由 clangd 提供。
- **实时诊断**：编译器检查（`g++` / `clang++`）或 clangd 的 `publishDiagnostics`，另有毫秒级的括号、全角标点、未闭合字符串基础检查。
- **F12 跳转定义**：当前文件、已打开文件、本地头文件、`include_paths`，最后回退到标准库符号表。
- **格式化**：clang-format 或内置保守格式化器。
- **英文 / 中文 / 中英双语**诊断文本与补全详情。
- **PCH 加速**与重复检查结果缓存。

## 环境要求

- Sublime Text 4（build 4000 或更新）。
- 可选：[clangd](https://clangd.llvm.org/)，用于语义补全、悬停、签名提示和 clangd 诊断。
- 可选：`PATH` 中的 `g++` 或 `clang++`（也可设置 `compiler_path`），用于编译器诊断和 PCH。
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

插件默认不注册快捷键，避免和其他包冲突。`Example.sublime-keymap` 提供了 F12、`Shift+Alt+F`、`Ctrl+Alt+D` 的可选条目，按需复制到用户键位文件即可。

## 命令

| 命令 | 功能 |
| --- | --- |
| `CppAssistant: Format Document (jiangly style)` | 格式化整个文档 |
| `CppAssistant: Go to Definition` | 跳转到定义（F12 风格） |
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
| `CppAssistant: Toggle Inline Error Phantoms` | 显示或隐藏行内提示 |
| `Preferences: CppAssistant Settings` | 编辑默认设置和用户设置 |
| `Preferences: CppAssistant Key Bindings` | 编辑可选快捷键 |

## 设置

通过 **Preferences: CppAssistant Settings** 打开设置，或直接编辑 `Packages/User/CppAssistant.sublime-settings`。用户设置会覆盖默认值。

| 设置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_completions` | `true` | 补全总开关 |
| `enable_clangd_engine` | `true` | 使用 clangd；`false` 使用内置数据库 |
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
| `lint_engine` | `"compiler"` | `compiler` 或 `clangd` |
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

clangd 关闭、未安装或尚未就绪时，使用内置数据库。它包含常用 STL 函数、容器成员、头文件、关键字和用户片段。

### 诊断

- **即时基础检查**：每次编辑后检查括号配平、全角标点和未闭合字符串 / 注释。
- **编译器检查**：后台执行 `g++ -fsyntax-only` 或 `clang++ -fsyntax-only`，结果按源码文本和设置缓存。开启 `enable_pch` 时会在后台构建 PCH。
- **clangd 检查**（`lint_engine: "clangd"`）：直接使用 clangd 推送的诊断，不启动编译器；消息仍会经过插件的本地翻译表。

切换 `display_language` 只改变已有结果的渲染语言，不会重新运行编译器或 clangd。

### 跳转定义

F12 风格查找依次搜索当前文件、其他已打开文件、当前文件包含的头文件、`include_paths` 目录，最后回退到标准库符号表。找到系统头文件路径时可以直接打开真实头文件。

### 格式化

优先使用 clang-format 和 jiangly 风格配置（4 空格缩进、K&R 大括号、100 列限制、不排序 include）。没有 clang-format 时，内置格式化器只做保守整理：缩进归一化、大括号空格、标点空格，不破坏字符串、注释和模板嵌套。

## 常见问题

**找不到 clangd。** 安装 clangd 并加入 `PATH`，或设置 `clangd_binary`。状态栏会提示当前是否使用内置数据库。插件会定期重试发现 clangd，运行中安装 clangd 不必重启 Sublime。

**F12 没反应。** 默认没有注册快捷键。使用命令面板，或把 `Example.sublime-keymap` 里的 F12 条目复制到用户键位文件。

**按回车没有换行，而是接受了补全。** 这是 Sublime 在补全弹窗打开时的默认键位。若希望回车始终换行，把下面条目加入用户键位，并用 Tab 或标点确认补全：

```json
{ "keys": ["enter"], "command": "insert", "args": {"characters": "\n"},
  "context": [{ "key": "auto_complete_visible" }] }
```

**格式化没有变化。** 没有 clang-format 时，内置格式化器只做安全整理。安装 clang-format 或设置 `clang_format_path` 可获得完整格式化。

**更新后诊断突然停了。** 重启一次 Sublime Text。若仍异常，打开 **View -> Show Console**，查看以 `[CppAssistant]` 开头的消息。

## 开发

纯 Python 引擎和 clangd 协议辅助函数可以在 Sublime 之外测试：

```bash
python -m unittest discover -s tests -v
```

测试不会被打进安装包。主要文件：

- `CppAssistant.py`：Sublime 集成、命令、诊断、格式化。
- `cppassistant/ca_engine.py`：补全分析、定义查找、兜底格式化、编译器输出解析。
- `cppassistant/ca_clangd.py`：精简 clangd LSP 客户端。
- `cppassistant/ca_stdlib_data.py`：STL 补全数据与翻译表。
- `cppassistant/ca_user_snippets.py`：用户 `.sublime-snippet` 加载器。

## License

[MIT](LICENSE) © chenmoulaile
