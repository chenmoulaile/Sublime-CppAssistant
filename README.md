# CppAssistant —— Sublime Text 4 C++ 辅助插件（LSP-clangd 风格的轻量级汉化优化版）

> 这是 **LSP-clangd 架构的汉化与性能优化版本**。在保持纯 Python 实现、零外部依赖的同时，参考 LSP-clangd 的多级缓存与异步调度思路，将补全响应、语法检查速度优化至 LSP-clangd 同等水平，并提供完整的中文本地化。

为 C++ 提供类似 LSP-clangd 的轻量体验：**智能补全 / 中文语法检查 / F12 跳转定义 / jiangly 码风格式化**。
纯 Python 实现，无外部 Python 依赖，开箱即用。

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

### 1. 智能代码补全
- 内置 129 个 STL 函数与 31 类容器成员数据库（算法/容器/流/cmath/cctype/内建函数…）
- 自动识别 `using namespace std;`：
  - 未声明时，输入 `lowe` → 补全插入 `std::lower_bound(...)`
  - 已声明时，输入 `lowe` → 只补 `lower_bound(...)`，不会重复加前缀
- 类型推断：识别变量声明后按 `.` 弹出对应成员
  - `vector<int> v;` → `v.` 弹出 `push_back / pop_back / size / ...`
  - map 迭代器 `it->` → 弹出 `first / second`
  - `cin.` / `cout.` 弹出流成员
- 输入 `#include <` 或 `#include "` 弹出头文件列表
- 代码片段：`us` → `using namespace std;`，`inc` → 万能头，`fastio`、`mainf`、`solvef`

### 2. 实时语法检查（报错信息全中文，三级加速）
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

### 3. F12 跳转定义
搜索顺序：当前文件 → 同窗口已打开文件 → 当前文件目录及 `include_paths` 下的本地头文件（递归跟随 `#include "..."`）。
多个候选时弹出快速面板选择；本地未找到时回退到 Sublime 内置符号索引。

### 4. jiangly 码风格式化
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

三种方式打开设置：
1. 菜单 `Preferences → Package Settings → CppAssistant → Settings`
2. 命令面板搜索 `CppAssistant: 打开设置`
3. 直接编辑 `CppAssistant.sublime-settings`

> 新增菜单项需要重启 Sublime Text 后才会显示。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable_completions` | `true` | 智能补全开关 |
| `enable_linting` | `true` | 实时语法检查开关 |
| `instant_basic_check` | `true` | 即时基础检查（毫秒级括号/全角标点/字符串检测） |
| `lint_debounce` | `0.4` | 停止输入多少秒后开始编译器完整检查 |
| `lint_timeout` | `12` | 编译器单次检查超时（秒），超时不清空已有标记 |
| `enable_pch` | `true` | PCH 预编译头加速（bits/stdc++.h） |
| `show_phantoms` | `true` | 错误行下方显示中文提示条 |
| `cxx_standard` | `"c++17"` | 语法检查使用的标准（本机默认配置为 c++23） |
| `compiler_path` | `""` | 编译器路径，留空自动查找 g++ / clang++ |
| `compiler_extra_args` | `[]` | 额外编译参数 |
| `include_paths` | `[]` | 额外头文件目录（同时用于跳转定义） |
| `format_on_save` | `false` | 保存时自动格式化 |
| `clang_format_path` | `""` | clang-format 路径，留空自动查找 |
| `indent_width` | `4` | 兜底格式化器缩进宽度 |

## 更新日志

### v1.3.0（LSP-clangd 风格的轻量级汉化优化版）
- **全面性能优化**：参考 LSP-clangd 架构，多级缓存（词法状态/类型环境/补全结果/诊断）使响应达 LSP-clangd 同等水平
- **零临时文件**：编译器检查通过 stdin 传递源码，**不创建任何 .cpp 临时文件**
- **过期进程立即终止**：新一次检查开始时立即 kill 旧进程，绝不排队
- **PCH 直接挂载**：使用 `-include ca_pch.h` 命令行选项，避免 `-I` 路径污染
- **预编译正则**：所有正则模块加载时编译，热路径零开销
- **字典代替正则分支**：标准符号匹配走 O(1) 字典查找
- **缓存失效机制**：设置变更时自动失效所有缓存，保证结果一致性
- **全面汉化**：所有用户可见提示（状态栏、菜单、命令、诊断、警告旗标）全中文
- 预计算 `KEYWORDS_SET/HEADERS_SET/SNIPPETS_BY_TRIG` 用于 O(1) 查找
- 默认开启所有功能（补全、语法检查、即时基础检查、PCH、幽灵提示）

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
