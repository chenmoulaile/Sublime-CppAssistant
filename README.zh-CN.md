# CppAssistant

[English](README.md) · **简体中文**

[![Package Control](https://img.shields.io/packagecontrol/v/CppAssistant.svg)](https://packagecontrol.io/packages/CppAssistant)
[![Package Control Downloads](https://img.shields.io/packagecontrol/dt/CppAssistant.svg)](https://packagecontrol.io/packages/CppAssistant)
[![Latest release](https://img.shields.io/github/v/release/chenmoulaile/Sublime-CppAssistant.svg)](https://github.com/chenmoulaile/Sublime-CppAssistant/releases)
[![GitHub Actions](https://github.com/chenmoulaile/Sublime-CppAssistant/workflows/tests/badge.svg)](https://github.com/chenmoulaile/Sublime-CppAssistant/actions)
[![License](https://img.shields.io/github/license/chenmoulaile/Sublime-CppAssistant.svg)](LICENSE)
[![Sublime Text](https://img.shields.io/badge/Sublime%20Text-4-blue.svg)](https://www.sublimetext.com/)

> **Sublime Text 4 的 C++ 辅助插件**：内嵌真实 clangd 引擎，clangd 不可用时自动退回
> 零依赖的纯 Python 结构引擎与内置 STL 数据库。**专为竞赛编程（OI / CSP-S / ICPC）调优。**

安装即可用：有 clangd 就是编译器级语义，没有 clangd 也不会哑火——增量词法分析器加
容错解析器撑起同文件补全、跳转和即时检查。不用配索引、不用起后台服务、不用折腾配置文件。

---

## 🆕 2.2.0 更新要点

| 变化 | 为什么重要 |
|------|-----------|
| **PBDS 头文件自动插入** | `bits/stdc++.h` 不含 `ext/pb_ds`；补全 `__gnu_pbds::tree` 会自动补上两个头文件 |
| **`auto` 显示真实类型** | `auto it = v.find(x)` 补全显示 `vector<int>::iterator`，不再是 `auto` |
| **签名带复杂度** | `push_back(int) · O(1)`、`find(const long long&) · O(log n)`、`insert(...) · O(1) avg` |
| **全角标点一键修** | `Ctrl+Alt+,` 只把**代码区**的 `，；：（）` 换成半角，注释和字符串保持原样 |
| **模板浏览器** | 插入前可预览任意片段的前 20 行、来源和行数 |
| **调用/类型层级** | 谁调用了这个函数、它调用了谁、类的继承链 |
| **状态栏提示** | `[clangd] · void solve(int n, vector<int>& a) · arg 2/2: int n` |
| **设置校验** | `c++2x`、`lint_timeout: 0` 这类会被 Sublime 静默忽略的值会明确报出来 |

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 🏆 为什么竞赛选手会用它

| 功能 | 赛场价值 |
|------|---------|
| **`bits/stdc++.h` PCH 加速** | 热诊断 ~200ms，成员补全 ~10–20ms |
| **数组越界检查** | `int a[10]; a[10] = 1` 立刻标红 |
| **模板参数推导** | `vector<int> v; v.` 显示 `push_back(int)` 而不是 `push_back(value_type)` |
| **PBDS 不用记头文件** | `tree` / `gp_hash_table` 补全后自动插入 `ext/pb_ds/*` |
| **竞赛数据库** | `__int128`、`__gnu_pbds`、C++20 `ranges`、C 库 I/O |
| **全角标点修复** | 代码里的 `，` 会变成 `stray '\357'` 报错，一键全改 |
| **诊断一键复制** | `Ctrl+Shift+C` → 直接粘到群里或搜索 |
| **Jiangly 码风格式化** | 4 空格、K&R、100 列，和赛场习惯一致 |
| **模板包** | 几何、图论、数据结构、字符串、数学，随包发布、按需安装 |
| **Snippet Tab Stop** | 每个模板都可以 `$0` `$1` `${1:default}` 跳转 |
| **中英双语诊断** | 中/英/双语随时切换，不用重跑编译器 |
| **菜单本地化** | `CppAssistant: Toggle Menu Language` 只翻译本插件自己的菜单 |

---

## ✨ 功能

### 🎯 补全

- **clangd 语义补全** — 编译器级签名、重载、容器成员、局部变量、宏；preamble
  就绪后异步结果自动替换兜底弹窗。
- **FastEngine 兜底** — 零依赖增量词法分析器 + 容错解析器：同文件符号、作用域、
  结构体/类/枚举成员、数组越界、模板推导，全部纯 Python。
- **可选 libclang 引擎** — ctypes + PCH 加速，只在翻译单元已预热时参与，绝不阻塞输入。
- **模板参数推导** — `vector<int> v; v.push_back(` 提示 `push_back(int)`；
  map/set/string/PBDS 与嵌套容器同样支持。
- **`auto` 类型推导** — `auto it = v.find(x)` → `vector<int>::iterator`，
  `auto value = m.at("k")` → `int`。
- **复杂度提示** — 常用 STL/PBDS 成员标注 `O(1)` / `O(log n)` / `O(1) avg` / `O(n)`。
- **来源标注** — `kmp · 字符串模板`、`total · 局部变量`、`MAXN · 宏`，
  按 Tab 之前就知道这条来自哪里。
- **智能头文件插入** — 识别 `bits/stdc++.h`、跳过已包含头文件、插在最后一个
  `#include` 之后。
- **PBDS 头文件插入** — 即使文件已有万能头，也会补
  `ext/pb_ds/assoc_container.hpp` 与 `tree_policy.hpp`，插入后立刻让 clangd 重新同步。
- **`__gnu_pbds::` 命名空间** — `tree`、`gp_hash_table`、`cc_hash_table`、
  `rb_tree_tag`、`splay_tree_tag`、`ov_tree_tag`、
  `tree_order_statistics_node_update`、`null_type`、扩容策略，插入时带完整模板骨架。
- **C++20 子集** — `ranges::sort`、`views::filter`、`integral<T>` 等，标注 `C++20`，
  在 `cxx_standard` 提到 C++20 之前排在 C++14 档之后；`ranges::` / `views::`
  作用域只列自己的成员。
- **头文件说明** — `#include <bits/stdc++.h>` 会写明“含全部标准头，但**不含** PBDS”。

### 🔍 诊断

- **即时基础检查**（< 10ms）— 括号、全角标点、未闭合字符串/注释、畸形 `#include`、
  不平衡 `#if`。
- **编译器检查** — 后台 `g++` / `clang++ -fsyntax-only`，带 PCH 缓存、防抖、超时、
  编译器自动探测。
- **clangd 检查** — LSP `publishDiagnostics`，带 `relatedInformation` 上下文，
  不启动编译器进程。
- **libclang 检查** — 同样无编译器进程，PCH 加速，跨文件位置精确。
- **数组越界检查** — 定长数组、多维、十六/二/八进制与后缀、作用域 shadow 都照顾到；
  `obj.a[10]` 不会被误判成本地数组。
- **双语消息** — `display_language = en | zh | both`，切语言不重跑检查。
- **内联错误提示** — 报错行下方显示一行简短说明。
- **一键复制** — `Ctrl+Shift+C`，优先复制错误行，保留 warning code。
- **诊断过滤** — `diagnostic_filters` 支持 `-Wfoo`、`Wno-foo`、`foo` 三种写法，
  归一化后同时作用于编译器、clangd fallback flags 和 libclang。
- **默认抑制 unused 警告** — CSP-S / NOIP 风格。
- **全角标点一键修复** — `Ctrl+Alt+,` 只改代码区；注释、字符串/字符字面量、
  原始字符串一律不动。

### 🧭 跳转与导航

- **F12 跳转定义** — 同文件作用域感知 → 其他已打开文件 → 本地头文件递归 →
  libclang 跨文件 → 标准库符号映射。
- **查找引用** — clangd 结果按文件分组，标 `[3/7]`，每项带行内预览。
- **调用层级** — 调用方与被调用方（clangd LSP 3.16）。
- **类型层级** — 基类与派生类。
- **语义重命名** — 全项目原子改名，buffer 变动时自动放弃。
- **状态栏提示** — 补全来源 + 光标所在调用的签名 + 当前第几个实参。

### ⚡ 编辑、代码操作与格式化

- **代码操作**（`Ctrl+.`）— 快速修复、插入 include、优先修复、禁用原因说明。
- **Jiangly 码风格式化** — 内置 clang-format 风格（4 空格、K&R、100 列），
  没有 clang-format 时用内置兜底。
- **格式化文档 / 选区** — 优先 clangd `rangeFormatting`，其次 clang-format `--lines`。

### 📦 模板与片段

- **随包模板** — 几何、图论、数据结构、字符串、数学；安装、启用、禁用、删除都在
  命令面板里。未安装的包不会出现在补全中。
- **模板浏览器** — Quick Panel 内联预览前 20 行、来源和行数，选中即按 snippet 插入。
- **创建 / 导入 / 管理片段** — 从选区创建、从 JSON 导入、按文件打开/重命名/回收。
- **浏览器与补全同源** — 补全里能看到什么，浏览器里就能预览什么。

### 🌐 本地化

- **英文 / 中文 / 中英双语** — 诊断、补全详情、来源标注都跟随 `display_language`。
- **菜单本地化** — 只翻译 CppAssistant 自己的菜单项，Sublime 内置菜单和用户菜单
  永不改动。

---

## 🚀 快速上手

1. 安装插件（Package Control、git clone 或下载 release 里的 `.sublime-package`，
   见下文**安装**一节）。
2. 打开 `.cpp` 文件，直接敲代码，补全立刻可用，无需任何配置。
3. **F12** 跳转定义，**Shift+Alt+F** 格式化文档。
4. 想要编译器级结果：把 `compile_commands.json` 放在项目里，或保证 `PATH` 上有
   `g++` / `clangd`。

`vector<int> v; v.` 的弹窗大致长这样：

```text
push_back     void push_back(int)                 · O(1)     [STL]
at            int at(size_type) const             · O(1)     [STL]
find          iterator find(const int&)           · O(n)     [STL]
```

PBDS 有序集合：

```text
order_of_key      size_type order_of_key(const int&) const   · O(log n)
find_by_order     iterator find_by_order(size_t)             · O(log n)
```

---

## 📦 安装

### Package Control（推荐）

1. `Ctrl+Shift+P` → **Package Control: Install Package**
2. 搜索 **CppAssistant** → 安装

### Git Clone

```bash
# Windows
cd "%APPDATA%\Sublime Text\Packages"
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant

# Linux / macOS
cd ~/.config/sublime-text/Packages
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```

### 手动安装

从 [最新 release](https://github.com/chenmoulaile/Sublime-CppAssistant/releases)
下载 `CppAssistant.sublime-package`，放进 `Packages/`（或 `Installed Packages/`），
重启 Sublime Text。

---

## ⌨️ 命令

### 导航

| 命令 | 功能 |
|------|------|
| `CppAssistant: Go to Definition` | 跳转定义（F12） |
| `CppAssistant: Find References` | 引用列表，按文件分组 |
| `CppAssistant: Call Hierarchy` | 调用方 / 被调用方 |
| `CppAssistant: Type Hierarchy` | 基类与派生类 |
| `CppAssistant: Rename Symbol` | 全项目重命名 |
| `CppAssistant: Code Actions` | 快速修复与重构 |

### 编辑

| 命令 | 功能 |
|------|------|
| `CppAssistant: Format Document (jiangly style)` | 格式化整个文件 |
| `CppAssistant: Format Selection` | 只格式化选中行 |
| `CppAssistant: Fix Full-width Punctuation` | 只把代码区全角标点换成半角 |

### 诊断

| 命令 | 功能 |
|------|------|
| `CppAssistant: Show Diagnostics` | 诊断面板 |
| `CppAssistant: Copy Diagnostic` | 复制当前诊断 |
| `CppAssistant: Diagnostic Language - …` | 中文 / 英文 / 双语 |
| `CppAssistant: Diagnostic Engine - …` | compiler / clangd / libclang |
| `CppAssistant: Toggle Inline Error Phantoms` | 开/关内联错误提示 |
| `CppAssistant: Completion Engine Status` | 当前由哪个引擎回答、为什么 |

### 模板与设置

| 命令 | 功能 |
|------|------|
| `CppAssistant: Browse Templates` | 预览并插入片段 |
| `CppAssistant: Install Template Pack` | 安装模板包 |
| `CppAssistant: Manage Templates` | 启用/禁用已安装的包 |
| `CppAssistant: Create User Snippet` | 从选区创建片段 |
| `CppAssistant: Manage User Snippets` | 打开 / 重命名 / 回收片段 |
| `CppAssistant: Import Snippets` | 从 JSON 导入 |
| `CppAssistant: Validate Settings` | 报告会被 Sublime 忽略的设置 |
| `CppAssistant: Toggle Menu Language` | 菜单中英切换 |
| `Preferences: CppAssistant Settings` | 编辑设置 |

---

## ⌨️ 快捷键

以下快捷键随 `Example.sublime-keymap` 发布；与自己的键位冲突时删掉或在用户键位里覆盖。

| 按键 | 命令 |
|------|------|
| `F12` | 跳转定义 |
| `Shift+F12` | 查找引用 |
| `Shift+Alt+F` | 格式化文档 |
| `Ctrl+Alt+F` | 格式化选区 |
| `Ctrl+.` | 代码操作 |
| `Ctrl+Alt+R` | 重命名符号 |
| `Ctrl+Alt+D` | 显示诊断 |
| `Ctrl+Shift+C` | 复制诊断 |
| `Ctrl+Alt+,` | 修复全角标点 |

---

## ⚙️ 设置

通过 **Preferences: CppAssistant Settings** 打开，或直接编辑
`Packages/User/CppAssistant.sublime-settings`。随包设置文件里每一项都有中英注释；
不确定写得对不对时，跑一次 `CppAssistant: Validate Settings`。

### 引擎

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `enable_completions` | `true` | 补全总开关 |
| `enable_clangd_engine` | `true` | 使用 clangd；`false` = 内置数据库 |
| `enable_fast_engine` | `true` | FastEngine 提供同文件符号、即时检查、F12 |
| `enable_libclang_engine` | `true` | 自动发现并使用 libclang |
| `libclang_path` | `""` | libclang 路径；空 = 自动发现 |
| `libclang_pch` | `true` | 构建并复用 `bits/stdc++.h` PCH |
| `libclang_completion` | `true` | 热缓存时合并 libclang 补全 |
| `libclang_goto` | `true` | 热缓存时用 libclang 跨文件 F12 |
| `clangd_binary` | `""` | clangd 可执行文件；空 = `PATH` |
| `clangd_args` | `[]` | 传给 clangd 的额外参数 |
| `clangd_completion_wait_ms` | `0` | 同步等待 clangd 的毫秒数；`0` = 不阻塞 |
| `enable_clangd_style_completion` | `true` | `true` = LSP 模糊匹配，`false` = 严格前缀 |

### 补全行为

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `auto_insert_includes` | `true` | 接受补全时插入 `#include` |
| `cpp_auto_insert_pbds_includes` | `true` | PBDS 补全后插入 `ext/pb_ds/*` |
| `show_status_hint` | `true` | 状态栏显示引擎、调用签名、第几个实参 |
| `enable_hover` | `true` | clangd 悬停文档 |
| `enable_signature_help` | `true` | 调用括号内显示签名帮助 |

### 诊断

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `enable_linting` | `true` | 诊断总开关 |
| `lint_engine` | `"compiler"` | `compiler` / `clangd` / `libclang` |
| `instant_basic_check` | `true` | 毫秒级括号/标点检查 |
| `lint_debounce` | `0.1` | 输入空闲多少秒后开始编译器检查 |
| `lint_timeout` | `12` | 编译器单次超时（秒） |
| `enable_pch` | `true` | 构建 `bits/stdc++.h` PCH 缓存 |
| `show_phantoms` | `true` | 内联错误提示 |
| `display_language` | `"en"` | `en` / `zh` / `both` |
| `menu_language` | `"en"` | `en` 或 `zh`，跟随 `display_language` |
| `cxx_standard` | `"c++14"` | 检查和 clangd 用的 C++ 标准 |
| `compiler_path` | `""` | 编译器路径；空 = `PATH` |
| `compiler_extra_args` | `[]` | 额外编译参数 |
| `include_paths` | `[]` | 额外头文件目录 |
| `suppress_unused_warnings` | `true` | 默认加 `-Wno-unused-variable` 等 |
| `diagnostic_filters` | `[]` | 要抑制的警告代码 |

### 格式化

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `indent_width` | `4` | 内置格式化器缩进宽度 |
| `format_on_save` | `false` | 保存前格式化整个文档 |
| `clang_format_path` | `""` | clang-format 路径 |
| `clang_format_style` | jiangly 配置 | clang-format style 字符串 |

---

## 🔧 工作原理

### 补全流水线

1. **clangd**（主）— stdio 上的异步 JSON-RPC，按文件生成并缓存
   `compile_commands.json`，fallback flags 来自你的设置。
2. **FastEngine**（兜底）— 增量词法 + 容错解析，作用域符号、模板推导、数组边界；
   毫秒级返回，同时支撑 F12 和即时检查。
3. **libclang**（可选）— ctypes + PCH 加速，只在翻译单元已预热时参与。
4. **内置数据库** — STL 成员、竞赛类型（`__int128`、`__gnu_pbds`）、
   C++20 ranges/views/concepts，中英双语描述。
5. **用户片段与模板包** — 永远排在最前，带 tab stop 和来源标注。

### 诊断引擎

| 引擎 | 速度 | 说明 |
|------|------|------|
| 即时基础检查 | < 10ms | 括号、标点、字符串、`#include`、`#if` |
| 编译器 | ~300ms（PCH） | 完整语法、`-Wall`、PCH 缓存 |
| clangd | ~50ms | 实时 LSP 诊断，无编译器进程 |
| libclang | ~200ms | 跨文件位置精确，无编译器进程 |

### F12 解析顺序

1. FastEngine 同文件（作用域感知排序）
2. 其他已打开文件
3. 包含的头文件 / `include_paths`（递归）
4. libclang 跨文件（热缓存时）
5. 标准库符号 → 头文件映射

---

## 📋 环境要求

- **Sublime Text 4**（build 4000+），Windows / macOS / Linux
- **可选**：[clangd](https://clangd.llvm.org/) — 语义补全、悬停、签名、
  层级、LSP 诊断
- **可选**：`PATH` 上的 `g++` / `clang++`（或设置 `compiler_path`）— 编译器诊断、PCH
- **可选**：`libclang`（`libclang.dll` / `.so` / `.dylib`）— libclang 诊断、
  语义补全、跨文件 F12
- **可选**：`clang-format` — 完整格式化

> 插件源码兼容 Python 3.3（旧 ST4 宿主），当前版本实际运行在 3.14 上。
> 不需要任何第三方 Python 包。

---

## 🧪 开发与测试

```bash
# 全量测试（208 个）
python -m unittest discover -s tests -v

# libclang 测试（可选，需 libclang + clang++）
CA_TEST_LIBCLANG_PCH=1 python -m unittest tests.test_semantic_engine -v

# 与 CI 相同的静态检查
flake8 . --select=E9,F63,F7,F82
mypy --ignore-missing-imports --python-version 3.10 cppassistant/ CppAssistant.py
```

### 模块划分

| 文件 | 职责 |
|------|------|
| `CppAssistant.py` | Sublime 集成：命令、事件、诊断、格式化、层级、设置 UI |
| `cppassistant/ca_clangd.py` | 精简 LSP 客户端：补全/悬停/签名/重命名/代码操作/引用/层级 |
| `cppassistant/ca_fast.py` | FastEngine：增量词法、容错解析、符号索引、调用上下文 |
| `cppassistant/ca_engine.py` | 旧版引擎：类型环境、成员签名、格式化兜底、编译器输出解析 |
| `cppassistant/ca_semantic.py` | 可选 libclang ctypes 引擎，PCH 加速 |
| `cppassistant/ca_stdlib_data.py` | STL/竞赛补全数据、PBDS 命名空间、C++20 子集、翻译表 |
| `cppassistant/ca_template_packs.py` | 模板包目录、XML 生成、安装 |
| `cppassistant/ca_user_snippets.py` | 用户 `.sublime-snippet` 加载器（缓存 + 来源标注） |
| `cppassistant/ca_fixes.py` | 全角标点扫描/替换（只处理代码区） |
| `cppassistant/ca_config.py` | 设置项校验 |
| `cppassistant/ca_filters.py` | 诊断过滤归一化 |
| `cppassistant/ca_menu_language.py` | 菜单翻译覆盖包生成 |

CI 会跑 flake8、mypy、Python 3.9 / 3.10 / 3.12 / 3.14 的单元测试、libclang 测试，
并在打 tag 时构建 `.sublime-package`。

---

## 🐛 常见问题

| 问题 | 解决方式 |
|------|----------|
| **找不到 clangd** | 安装 clangd（`pacman -S mingw-w64-x86_64-clang`、`apt install clangd`、`brew install llvm`）或设置 `clangd_binary` |
| **F12 没反应** | 检查 `Example.sublime-keymap` 里的按键，或直接用命令面板 |
| **回车只是换行** | Sublime 默认行为。用户键位里加 `{ "keys": ["enter"], "command": "insert", "args": {"characters": "\n"}, "context": [{ "key": "auto_complete_visible" }] }` |
| **格式化没变化** | 安装 `clang-format` 或设置 `clang_format_path`；内置兜底只做基础缩进 |
| **代码操作/重命名/层级没反应** | 需要 clangd；先保存文件，再用 **Completion Engine Status** 确认 clangd 在跑 |
| **有万能头还说找不到 PBDS** | 现在会自动插入；若关掉了 `cpp_auto_insert_pbds_includes`，请手动加 `ext/pb_ds/*` |
| **全角修复没改我的注释** | 这是故意的：只改代码区，注释和字符串里的中文标点保留 |
| **诊断突然停了** | 重启 Sublime Text，并在控制台看 `[CppAssistant]` 消息 |
| **找不到 libclang** | 安装 LLVM / MSYS2 / Xcode，或设置 `libclang_path`；失败会自动回退编译器引擎 |
| **libclang PCH 不生效** | 需要与 libclang 主次版本匹配的 `clang++` 在 `PATH` 中 |
| **设置好像没生效** | 跑一次 `CppAssistant: Validate Settings`，非法值会写到输出面板 |

---

## 📄 许可证

[MIT](LICENSE) © chenmoulaile

- 版本历史：[CHANGELOG.md](CHANGELOG.md)
- Package Control 页面：[packagecontrol.io/packages/CppAssistant](https://packagecontrol.io/packages/CppAssistant)

## 🙏 致谢

- [clangd](https://clangd.llvm.org/) — C++ 语言服务器
- [jiangly](https://github.com/jiangly) — 格式化风格参考
- [Sublime Text](https://www.sublimetext.com/) — 编辑器本体
- 所有贡献者和提供反馈的 OIers
