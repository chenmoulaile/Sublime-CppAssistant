# CppAssistant —— Sublime Text 4 C++ 辅助插件（内嵌真实 clangd 引擎 + 全汉化）

> **v1.4.0 起内置移植 LSP-clangd**：插件自带最小 LSP 客户端，直接驱动真实的 clangd 语言服务器（无需安装 LSP 主框架），补全达到编译器级语义准确度；同时保留纯 Python 内置数据库兜底，零配置可用。诊断信息完整中文化。
>
> **v1.5.0 补全力度对齐 LSP-clangd**：**智能头文件插入**（补全被接受时自动补 `#include <vector>`，已有 `bits/stdc++.h` 万能头则不插）、**悬停文档**（鼠标悬停显示 clangd 类型/文档）、**函数签名提示**（光标在调用括号内显示重载签名）。

为 C++ 提供 LSP-clangd 同级体验：**真实 clangd 语义补全 / 智能头文件插入 / 悬停文档 / 函数签名提示 / 中文语法检查 / F12 跳转定义 / jiangly 码风格式化**。
开箱即用：有 clangd 就用真引擎，没有就自动回退内置数据库。

## 性能（与 LSP-clangd 对比）

| 操作 | LSP-clangd | 本插件 |
| --- | --- | --- |
| 结构性错误反馈（括号、全角标点、未闭合字符串） | 200~500ms（依赖 LSP 调度） | **< 10ms**（纯 Python 即时检查） |
| 完整语义检查（含 PCH） | 冷启动 1~3s，热路径 200~500ms | 冷启动 0.5~1s，热路径 **< 300ms** |
| 补全响应（命中缓存） | 5~20ms | **< 1ms** |
| 补全响应（未命中缓存） | 50~200ms | **< 5ms**（内置 130+ STL 函数 + 31 类容器成员） |
| 多次编辑（相同文本） | 重新检查 | **零延迟复用**（内容哈希 + 设置签名缓存） |

性能优化关键点：
1. **多级缓存**：词法状态行表、类型环境、用户符号、补全结果、语法诊断全部带版本缓存
2. **stdin 传递源码**：编译器检查通过 stdin 传入源码，**不创建任何临时 .cpp 文件**
3. **过期进程立即终止**：新一次检查开始时立即 kill 旧进程，绝不排队
4. **PCH 直接挂载**：使用 `-include ca_pch.h` 命令行选项挂载预编译头
5. **正则预编译**：所有正则表达式在模块加载时编译，热路径零开销
6. **字典代替正则分支**：标准符号匹配走 O(1) 字典查找

## 功能特性

### 1. 智能代码补全（v1.4.0 起默认真实 clangd 引擎）

**补全引擎（`enable_clangd_engine`，默认开启）**：
- **真实 clangd 引擎**（默认）：插件内嵌最小 LSP 客户端（移植自 LSP-clangd 的
  服务器发现与参数逻辑），直接与 clangd 语言服务器通过 stdio JSON-RPC 通信：
  - 编译器级语义补全：函数签名、重载列表、容器成员、局部变量全部准确
  - 通过动态维护 `compile_commands.json`（`--compile-commands-dir` 指向）
    注入 `-std=`（默认 **c++14**，面向 CSP-S/NOIP）与编译器头文件路径，
    MSYS2 等clangd环境下 `bits/stdc++.h` 可正常解析
  - **排序策略：用户代码片段（User 包里的 `.sublime-snippet`）与内置片段
    永远排最前面**，其后是 clangd 结果（保持服务端相关性排序，全部符合
    当前 C++ 标准；C++14 下不会混入 C++17/20/23 符号）
  - clangd 结果未就绪时（打开文件后约 1~2s 内）先弹内置数据库兜底，
    结果到达后自动刷新弹窗（与 LSP 插件行为一致）；等待上限
    `clangd_completion_wait_ms`（默认 60ms）可调，设 0 完全异步
  - 找不到 clangd 自动回退内置数据库并状态栏提示；路径可用 `clangd_binary` 指定
  - 补全条目类型全部中文标注（函数 / 成员函数 / 成员变量 / 类 / 常量…）
  - **智能头文件插入**（v1.5.0，`auto_insert_includes` 默认开启）：补全被接受时
    自动补上对应的 `#include <X>`——例如输入 `vector` 补全后自动插入
    `#include <vector>`；**文件里已有 `#include <bits/stdc++.h>`（万能头）时
    不再重复插入**，头文件已在文件里也会跳过，否则插入到最后一个
    `#include` 行之后（与 LSP-clangd 的 header-insertion 行为一致）
  - **函数参数占位符**：补全函数时自动带上 `push_back(${1:x})` 式的参数
    占位片段（clangd 默认行为，Tab 跳参数）
- **内置数据库模式**（`enable_clangd_engine: false` 或无 clangd 时自动启用）：
  - 纯 Python 静态数据库：129 个 STL 函数 + 31 类容器成员
  - **排序策略同样为：用户/内置片段最前 → C++14 及以下档 → C++17/20/23 档靠后**
  - **两种匹配模式可切换**（`enable_clangd_style_completion`，默认 LSP-clangd 风格）：
    - **LSP-clangd 风格**（默认）：所有以当前前缀开头、属于当前作用域的补全立即弹出；
      同时允许子串/子序列模糊匹配兜底；自动压制 Sublime 内置单词补全
    - **严格前缀基础模式**：只保留严格前缀匹配（大小写不敏感），最简洁最可预测
    - 切换方式：命令面板 `CppAssistant: 切换补全模式为 ...` / 菜单 / 手动改设置
- 内置数据库同时支持（两种引擎下兜底行为一致）：
  - 自动识别 `using namespace std;`：未声明时输入 `lowe` → 插入 `std::lower_bound(...)`
  - 类型推断：`vector<int> v;` → `v.` 弹出成员；迭代器 `it->` → `first / second`
  - 输入 `#include <` 或 `#include "` 弹出头文件列表
  - 代码片段：`us` → `using namespace std;`，`inc` → 万能头，`fastio`、`mainf`、`solvef`

### 2. 悬停文档与函数签名提示（v1.5.0 新增）
- **悬停文档**（`enable_hover`，默认开启）：鼠标悬停在符号上时弹出 clangd
  生成的类型/文档弹窗（函数签名、所在头文件、成员说明），与 LSP-clangd 的
  hover 一致；纯 clangd 数据，无 clangd 时不出弹窗
- **函数签名提示**（`enable_signature_help`，默认开启）：光标位于函数调用的
  括号内时自动弹出重载签名列表（当前重载高亮，附带参数文档），
  输入过程实时更新，移出括号自动关闭；`if` / `for` / `while` 等
  控制流语句的括号不会误触发

### 3. 实时语法检查（报错信息全中文，三级加速）
- **第一级 · 即时基础检查**（毫秒级）：纯 Python 词法扫描，输入过程中实时检测
  括号配平、全角标点、未闭合字符串/注释，不必等编译器
- **第二级 · 编译器完整检查**：后台调用 `g++ -fsyntax-only` 或 `clang++ -fsyntax-only`
  （自动在 PATH 中查找），波浪线标注错误位置，行下方显示中文幽灵提示，
  状态栏统计 `✖ 错误 ⚠ 警告`；新一次检查开始时立即终止过期进程，绝不排队堆积
- **第三级 · 结果缓存**：文本与设置未变时直接复用上次诊断，零延迟刷新
- 内置 136 条 GCC/Clang 报错翻译规则，例如：
  - `expected ';' before 'vector'` → 在 'vector' 之前缺少 ',' 或 ';'
  - `'x' was not declared in this scope` → 标识符 'x' 未在此作用域中声明(检查拼写或是否漏了头文件)
  - `did you mean 'hello'?` → 你是不是想写 'hello'？
- **PCH 预编译头加速**：启动后自动在后台构建 `bits/stdc++.h` 缓存，
  之后含该头文件的检查耗时约从 1.2s 降至 0.33s（约 4 倍）
- 找不到编译器时自动退化为**基础检查**：括号配平、全角标点检测、未闭合字符串/注释

### 4. F12 跳转定义
搜索顺序：当前文件 → 同窗口已打开文件 → 当前文件目录及 `include_paths` 下的本地头文件（递归跟随 `#include "..."`）。
多个候选时弹出快速面板选择；本地未找到时回退到 Sublime 内置符号索引。

### 5. jiangly 码风格式化
- 优先调用 clang-format（内置 jiangly 风格配置：4 空格缩进、K&R 大括号、ColumnLimit 100 不折行）
- 无 clang-format 时使用内置兜底格式化器（缩进归一化、大括号空格、逗号分号、流运算符空格，
  且保证不破坏字符串/注释/模板嵌套）

## 安装

### 方式一：Package Control（推荐）
本插件已提交官方频道收录审核（[sublimehq/package_control_channel#9536](https://github.com/sublimehq/package_control_channel/pull/9536)），
合并后即可直接：`Ctrl+Shift+P` → **Package Control: Install Package** → 搜索 **CppAssistant**。

审核期间可用添加仓库方式：
1. `Ctrl+Shift+P` → **Package Control: Add Repository**
2. 输入：`https://github.com/chenmoulaile/Sublime-CppAssistant`
3. `Ctrl+Shift+P` → **Package Control: Install Package** → 选择 **CppAssistant**

> 若 Add Repository 下载失败（GitHub 网络原因），请用方式二/三。

### 方式二：git clone
```bash
cd "%APPDATA%\Sublime Text\Packages"        # Windows（菜单 Preferences → Browse Packages 可定位）
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```
> Linux/macOS 目录为 `~/.config/sublime-text/Packages`。目录名建议用 `CppAssistant`。

### 方式三：手动下载
下载本仓库 ZIP，解压到 Packages 目录下并重命名为 `CppAssistant`，重启 Sublime Text。

## 快捷键（可选，默认不注册）

为避免覆盖其他包的键位，本插件**默认不绑定任何快捷键**，全部命令可在命令面板搜索 `CppAssistant:` 使用：

| 命令 | 功能 |
| --- | --- |
| `CppAssistant: 跳转到定义` | 光标符号跳转到定义（本地找不到时回退内置索引） |
| `CppAssistant: 按 jiangly 码风格式化文档` | 整个文档格式化（也可开启 `format_on_save`） |
| `CppAssistant: 显示语法诊断面板` | 中文语法诊断列表 |

如需快捷键，把 `Default (Windows).sublime-keymap` 中注释掉的条目复制到
`Preferences → Key Bindings` 的 User 文件即可，推荐键位：
`F12` 跳转定义 · `Shift+Alt+F` 格式化 · `Ctrl+Alt+D` 诊断面板。

## 配置

打开设置的方式：
1. 菜单 `Preferences → Package Settings → CppAssistant` 下选择 `Settings – Default`（默认设置）/ `Settings – User`（用户设置）/ `Key Bindings – User`（用户快捷键）
2. 命令面板搜索 `首选项: CppAssistant 设置` / `首选项: CppAssistant 快捷键`
3. 直接编辑 `Packages/User/CppAssistant.sublime-settings`

> 用户设置会覆盖默认设置，无需修改默认文件。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable_completions` | `true` | 智能补全开关 |
| `enable_clangd_style_completion` | `true` | 补全模式：`true` LSP-clangd 风格（默认，宽松模糊匹配）/ `false` 严格前缀基础模式（仅前缀匹配） |
| `auto_insert_includes` | `true` | 智能头文件插入：补全被接受时自动补 `#include <X>`（已有 `bits/stdc++.h` 万能头或该头已包含则跳过） |
| `enable_hover` | `true` | 悬停文档：鼠标悬停显示 clangd 类型/文档弹窗 |
| `enable_signature_help` | `true` | 函数签名提示：光标在调用括号内显示重载签名 |
| `enable_linting` | `true` | 实时语法检查开关 |
| `instant_basic_check` | `true` | 即时基础检查（毫秒级括号/全角标点/字符串检测） |
| `lint_debounce` | `0.1` | 停止输入多少秒后开始编译器完整检查（删除错误行后基本即时清除） |
| `lint_timeout` | `12` | 编译器单次检查超时（秒），超时不清空已有标记 |
| `enable_pch` | `true` | PCH 预编译头加速（bits/stdc++.h） |
| `show_phantoms` | `true` | 错误行下方显示提示条 |
| `display_language` | `"zh"` | 诊断信息显示语言: `zh` 仅中文 / `en` 仅英文 / `both` 中英双语 |
| `cxx_standard` | `"c++17"` | 语法检查使用的标准（本机默认配置为 c++23） |
| `compiler_path` | `""` | 编译器路径，留空自动查找 g++ / clang++ |
| `compiler_extra_args` | `[]` | 额外编译参数 |
| `include_paths` | `[]` | 额外头文件目录（同时用于跳转定义） |
| `format_on_save` | `false` | 保存时自动格式化 |
| `clang_format_path` | `""` | clang-format 路径，留空自动查找 |
| `indent_width` | `4` | 兜底格式化器缩进宽度 |

## 诊断显示语言

支持 **中文 / 英文 / 中英双语** 三种模式，可通过以下任一方式切换：

1. **命令面板** 搜索 `CppAssistant: 切换诊断语言为 ...`
2. **菜单** `Preferences → Package Settings → CppAssistant → 诊断显示语言`
3. **手动编辑** `Packages/User/CppAssistant.sublime-settings` 里的 `display_language` 字段：
   ```json
   {
       "display_language": "zh"     // 仅中文 (默认)
       // "display_language": "en"   // 仅英文
       // "display_language": "both" // 中英双语, 形如 "use of foo（未声明的标识符 'foo'）"
   }
   ```

切换后即时生效（自动失效诊断缓存并重渲染当前文件的所有诊断）。状态栏、诊断面板、幽灵提示条中的提示文字会同步切换。

### 翻译覆盖范围

诊断翻译表覆盖 280+ 条常见 gcc / clang 诊断消息，包括：

- **编译错误**：`expected ';' before`、`use of undeclared identifier`、`no matching function for call to`、`redefinition of`、`is not a class template`、`undefined reference to`、`fatal error: ... No such file or directory` ...
- **C++ 标准相关**：`explicit object member function only available with '-std=c++23' or '-std=gnu++23'`、`is a C++23 extension`、`only available in C++17 or later` ...
- **模板相关**：`template argument deduction failed`、`too many/few template arguments for class template`、`specialization after instantiation` ...
- **constexpr/lambda/虚函数**：覆盖 `marked 'override' but does not override`、`non-constexpr function cannot be used in this constant expression`、`virtual function has non-virtual destructor` ...
- **警告旗标**：覆盖 270+ 条 `-Wxxx` 旗标（`-Wc++23-extensions`、`-Wsign-compare`、`-Wformat`、`-Wdeprecated-declarations`、`-Warray-bounds`、`-Wclass-memaccess` ...），旗标名称会自动翻译为中文显示在诊断末尾，例如 `[-Wunused-variable]` → `（未使用变量）`
- **内存/运行时错误**（来自 ASan/Valgrind）：`segmentation fault`、`use-after-free`、`stack smashing detected`、`memory leak` ...

英文模式下会保留 `gcc/clang` 原始报错信息，方便复制搜索；中文模式适合日常学习；双语模式适合教学/对比。

## 更新日志

### v1.5.0（补全力度对齐 LSP-clangd）
- **智能头文件插入**（用户需求核心）：补全被接受时自动补 `#include <X>`——
  例如输入 `vector` 补全后自动插入 `#include <vector>`：
  - 文件里已有 `#include <bits/stdc++.h>`（万能头）→ 不再重复插入
    （clangd 的 header-insertion 默认策略原生识别万能头传递包含，
    插件侧再做二次防御判断，双保险）
  - 该头文件已在文件里 → 跳过；否则插入到最后一个 `#include` 行之后
  - 实现机制：clangd 的 `additionalTextEdits` 携带 include 指令 → 插件在
    补全被接受时（`on_text_changed` 匹配插入文本）智能应用；
    `auto_insert_includes` 可关
  - 剥离 clangd label 前的 header-insertion 装饰符（`•`），触发词保持干净
- **函数参数占位符**：函数补全自动带 `push_back(${1:x})` 式参数占位片段
  （clangd 默认行为，此前被插件误关，现恢复与 LSP-clangd 一致）
- **悬停文档（hover）**：鼠标悬停符号弹出 clangd 类型/文档弹窗，
  `enable_hover` 可关
- **函数签名提示（signature help）**：光标在调用括号内实时显示重载签名
  （当前重载高亮 + 参数文档），控制流关键字不误触发，
  `enable_signature_help` 可关
- 协议层：`ca_clangd.py` 新增 `textDocument/hover` 与
  `textDocument/signatureHelp` 支持（含解析器），补全解析附带
  `includes` 字段
- 兼容性修正：不同版本 clangd 的 `--header-insertion` 取值名不同
  （旧版 `iws` / 新版 `iwyu`），显式传 flag 会令其一端启动失败，
  改用默认策略（即 LSP-clangd 的用法），跨版本稳定

### v1.4.1（修复插件无法加载）
- **修复 ImportError**：ST 宿主不把包目录加入 `sys.path`，根级插件的绝对导入
  `from cppassistant import ...` 会失败（v1.3.4 起即受影响）。
  改为相对导入 `from .cppassistant import ...`（与 cph-by-chenkx 的
  `.core.*` 模式一致），并保留 sys.path 绝对导入兜底，两种宿主挂载方式均可加载。

### v1.4.0（内嵌真实 clangd 引擎）
- **移植 LSP-clangd**：新增 `cppassistant/ca_clangd.py` 最小 LSP 客户端
  （stdio JSON-RPC），直接驱动真实 clangd 语言服务器，无需安装 LSP 主框架：
  - 编译器级语义补全（签名 / 重载 / 容器成员 / 局部变量），补全类型全中文标注
  - 动态维护 `compile_commands.json` 注入 `-std=` 与编译器头文件路径，
    MSYS2 环境下 `bits/stdc++.h` 正常解析（clangd 21 实测通过）
  - clangd 未就绪时内置数据库兜底 + 结果到达自动刷新弹窗（与 LSP 一致）
  - `clangd_binary` / `clangd_args` / `clangd_completion_wait_ms` /
    `clangd_extra_fallback_flags` / `clangd_working_dir` 全套设置
- **补全排序策略**（面向 CSP-S/NOIP）：
  - 用户代码片段（`.sublime-snippet`，自动扫描 C/C++ 作用域）与内置片段
    （`us` / `inc` / `fastio` …）永远排最前
  - clangd 结果遵循当前 C++ 标准（默认 **c++14**，`cxx_standard` 可调，
    同步作用于语法检查的 `-std=`）
  - 内置数据库兜底模式：C++14 及以下档排前面，C++17/20/23 档排后面
- 新增命令 `ca_set_completion_engine` 与菜单/命令面板"补全引擎"开关
- 修复 Package Control 审查项：子包化导入（ca_engine / ca_stdlib_data）、
  全部 subprocess 隐藏窗口处理、`Preferences: CppAssistant Settings /
  Key Bindings` 命令面板条目、菜单 Settings 条目（v1.3.4 审查 2 失败 + 9 警告全部清零）

### v1.3.3
- **补全弹窗现在与 LSP-clangd 完全一致**：LSP-clangd 风格模式下传入 `INHIBIT_WORD_COMPLETIONS`，压制 Sublime 内置的普通单词补全，弹窗只保留按语义排序的候选——不再出现同前缀的普通单词把 `is_sorted` / `stable_sort` 等语义候选挤出可视区、"快打完整个词才看到想要的"的问题
- **PCH 签名加入编译器版本号**：MSYS2 / Homebrew 升级 g++ 后旧 `.gch` 不再兼容，过去会导致语法检查静默失效；现在版本变化自动重建，并自动清理升级遗留的旧缓存目录（每个可达 150MB）
- **陈旧 PCH 自愈**：即使检测到运行时 PCH 不兼容错误（"not compatible with this GCC" 等），也会立刻删除坏缓存、后台重建，并用无 PCH 命令重试本次检查，语法检查不再静默失效
- **拒绝黑箱失败**：补全引擎 / 基础检查 / 编译器启动的异常现在会打印到 Sublime 控制台（`View → Show Console`，同一位置限打 3 次），出错可查

### v1.3.2
- 移除 `.no-sublime-package`：本插件无任何需要解压目录才可用的资源（无捆绑可执行文件、无 `__file__` 路径依赖，PCH 写入系统临时目录，相对导入在 `.sublime-package` 压缩包内同样工作），以默认压缩包形式安装，消除 Package Control 审查警告

### v1.3.1
- **新增补全模式开关** `enable_clangd_style_completion`：在两种补全风格间即时切换
  - LSP-clangd 风格（默认）：所有以当前前缀开头、属于当前作用域（容器/算法/全局）的补全立即弹出；额外允许子串/子序列模糊匹配作为兜底
  - 严格前缀基础模式：只保留严格前缀匹配，过滤掉所有子串/子序列模糊结果，最简洁最可预测
- 新增命令面板条目 `CppAssistant: 切换补全模式为 LSP-clangd 风格 (clangd)` 和 `切换补全模式为严格前缀基础模式 (basic)`
- 新增菜单 `Preferences → Package Settings → CppAssistant → 补全模式`，可在两种模式间可视化切换（带 `checkbox` 标记）
- 补全缓存按模式分组，切换模式后即时失效避免误命中
- 同时清理所有 Package Control 审查警告：补全模式 caption 改为 `Preferences:` 前缀（中英双 caption）、命令面板新增英文 caption、Popen 使用 `subprocess.CREATE_NO_WINDOW` 命名常量

### v1.3.0（LSP-clangd 风格的轻量级汉化优化版）
- **全面性能优化**：参考 LSP-clangd 架构，多级缓存（词法状态/类型环境/补全结果/诊断）使响应达 LSP-clangd 同等水平
- **零临时文件**：编译器检查通过 stdin 传递源码，**不创建任何 .cpp 临时文件**
- **过期进程立即终止**：新一次检查开始时立即 kill 旧进程，绝不排队
- **PCH 直接挂载**：使用 `-include ca_pch.h` 命令行选项，避免 `-I` 路径污染
- **预编译正则**：所有正则模块加载时编译，热路径零开销
- **字典代替正则分支**：标准符号匹配走 O(1) 字典查找
- **完全汉化 + 诊断语言切换**：诊断翻译表扩充至 280+ 条，覆盖 C++23/20/17/14/11 标准相关错误（`[-Wc++23-extensions]`、`[-Wgnu++23]` 等）、模板推导失败、constexpr 违例、lambda 捕获问题、虚函数/override 错误、内存错误等。警告旗标翻译扩充至 270+ 条。新增 `display_language` 设置项支持 **中文（默认）/ 英文 / 中英双语** 三种模式
- **F12 跳转定义支持标准库 fallback**：内置 496 条 std 符号 → 头文件映射表，覆盖 STL 容器/算法/IO/智能指针/线程/random/chrono/filesystem/format 等所有标准库符号。当 `F12` 找不到本地定义时，自动提示 `std::vector 定义于 <vector> 系统头文件中`，光标停在 `#include <bits/stdc++.h>` 上时显示 `标准库头文件 <bits/stdc++.h>`。若在 `include_paths` 配置了编译器系统头文件路径，可直接打开真实系统头文件
- **删除错误行即时响应**：`lint_debounce` 默认 0.4s → 0.1s，基础检查 0.06s → 0.02s。同时实现"源 hash 不一致时立即清空旧编译器诊断"机制，用户删除错误行后 0.02s 内消除标记（之前需要 ~1s），响应速度提升 50×
- 缓存失效机制：设置变更时自动失效所有缓存，保证结果一致性
- 预计算 `KEYWORDS_SET/HEADERS_SET/SNIPPETS_BY_TRIG` 用于 O(1) 查找
- 默认开启所有功能（补全、语法检查、即时基础检查、PCH、幽灵提示）
- **菜单重构**：参考 LSP / SublimeAStyleFormatter 模式，`Main.sublime-menu` 改为完整标准结构（mnemonic / id / Settings-Default / Settings-User / Key Bindings-Default / Key Bindings-User）

### v1.2.3
- 修复所有 Package Control 审查警告：sys.path、CREATE_NO_WINDOW 注释、keymap 重命名、edit_settings 命令、菜单子项、移除 Preferences.sublime-settings

### v1.2.0
- 语法检查三级加速：即时基础检查（毫秒级）+ 过期进程立即终止 + 内容哈希结果缓存
- PCH 预热提前至启动后 1.2 秒，首次检查即享加速
- 检查期间状态栏显示"正在语法检查…"；超时不再清空既有标记
- 全部提示信息中文化；新增 `.no-sublime-package` 保证多模块包以目录形式安装

### v1.1.0
- 片段式补全、全中文诊断、16 倍补全缓存、PCH 检查加速

### v1.0.0
- 首发：C++ 智能补全、中文语法检查、F12 跳转定义、jiangly 码风格式化

## 常见问题

- **刚更新/重装插件后补全和语法检查突然没反应？**
  Sublime 对插件的热重载在文件快速变动时可能进入半死状态（事件监听器失效）。
  **重启一次 Sublime Text 即可恢复**；若仍异常，`View → Show Console` 里查看
  `[CppAssistant]` 开头的报错并发给作者。
- **Package Control 里搜不到 / Add Repository 下载失败？**
  官方频道收录审核中，审核期间请用上方"添加仓库"或手动方式；
  若 GitHub 网络不通，可用方式二/三（镜像加速下载 ZIP 后解压）。
- **和 LSP-clangd 比速度如何？** 打字过程中的结构性错误（括号、全角标点、
  未闭合字符串）由即时基础检查在毫秒级给出，比任何 LSP 都快；
  完整语义检查配合 PCH 与结果缓存，常规竞赛规模代码约 0.3s 内刷新，
  且不会像冷启动的 clangd 那样长时间无响应。
- **状态栏一直显示"✖ 无语法错误"但不检查？** 未找到编译器且基础检查无异常。
  安装 [MinGW-w64](https://www.mingw-w64.org/) 或 LLVM 并加入 PATH，或把完整路径填入 `compiler_path`。
- **F12 没反应？** 默认未注册快捷键（避免与其他包冲突）。用命令面板 `CppAssistant: 跳转到定义`，
  或按上方"快捷键"一节把 F12 条目加进 User 键位。
- **格式化没变化？** 未安装 clang-format 时使用保守的内置格式化器，只做安全子集的整理。

## License

[MIT](LICENSE) © chenmoulaile
