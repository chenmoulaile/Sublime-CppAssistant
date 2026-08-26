# CppAssistant —— Sublime Text 4 C++ 辅助插件（jiangly 码风）

为 C++ 提供类似 LSP-clangd 的轻量体验：**智能补全 / 中文语法检查 / F12 跳转定义 / jiangly 码风格式化**。
纯 Python 实现，无外部 Python 依赖，开箱即用。

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

### 2. 实时语法检查（报错信息中文化）
- 后台调用 `g++ -fsyntax-only` 或 `clang++ -fsyntax-only`（自动在 PATH 中查找），
  波浪线标注错误位置，行下方显示中文幽灵提示，状态栏统计 `✖ 错误 ⚠ 警告`
- 内置 136 条 GCC/Clang 报错翻译规则，例如：
  - `expected ';' before 'vector'` → 在 'vector' 之前缺少 ',' 或 ';'
  - `'x' was not declared in this scope` → 标识符 'x' 未在此作用域中声明(检查拼写或是否漏了头文件)
  - `did you mean 'hello'?` → 你是不是想写 'hello'？
- **PCH 预编译头加速**：启动后自动在后台构建 `bits/stdc++.h` 缓存，
  之后含该头文件的检查耗时约从 1.6s 降至 0.35s（约 4~5 倍）
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
1. `Ctrl+Shift+P` → **Package Control: Add Repository**
2. 输入：`https://github.com/chenmoulaile/Sublime-CppAssistant`
3. `Ctrl+Shift+P` → **Package Control: Install Package** → 选择 **CppAssistant**

### 方式二：git clone
```bash
cd "%APPDATA%\Sublime Text\Packages"        # Windows（菜单 Preferences → Browse Packages 可定位）
git clone https://github.com/chenmoulaile/Sublime-CppAssistant CppAssistant
```
> Linux/macOS 目录为 `~/.config/sublime-text/Packages`。目录名建议用 `CppAssistant`。

### 方式三：手动下载
下载本仓库 ZIP，解压到 Packages 目录下并重命名为 `CppAssistant`，重启 Sublime Text。

## 快捷键（仅 C/C++ 文件生效）

| 按键 | 功能 |
| --- | --- |
| `F12` | 跳转到定义 |
| `Shift+Alt+F` / `Shift+Alt+L` | 按 jiangly 码风格式化整个文档 |
| `Ctrl+Alt+D` | 打开中文语法诊断面板 |

命令面板中同样提供以上三条命令（前缀 `CppAssistant:`）。

## 配置

菜单 `Preferences → Package Settings → CppAssistant`（或直接编辑 `CppAssistant.sublime-settings`）：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable_completions` | `true` | 智能补全开关 |
| `enable_linting` | `true` | 实时语法检查开关 |
| `lint_debounce` | `0.4` | 停止输入多少秒后开始检查 |
| `enable_pch` | `true` | PCH 预编译头加速（bits/stdc++.h） |
| `show_phantoms` | `true` | 错误行下方显示中文提示条 |
| `cxx_standard` | `"c++17"` | 语法检查使用的标准 |
| `compiler_path` | `""` | 编译器路径，留空自动查找 g++ / clang++ |
| `compiler_extra_args` | `[]` | 额外编译参数 |
| `include_paths` | `[]` | 额外头文件目录（同时用于跳转定义） |
| `format_on_save` | `false` | 保存时自动格式化 |
| `clang_format_path` | `""` | clang-format 路径，留空自动查找 |
| `indent_width` | `4` | 兜底格式化器缩进宽度 |

## 常见问题

- **状态栏一直显示“✖ 无语法错误”但不检查？** 未找到编译器且基础检查无异常。
  安装 [MinGW-w64](https://www.mingw-w64.org/) 或 LLVM 并加入 PATH，或把完整路径填入 `compiler_path`。
- **F12 没反应？** 可能被其他包或 User 键位设置占用，检查 `Preferences → Key Bindings`。
- **格式化没变化？** 未安装 clang-format 时使用保守的内置格式化器，只做安全子集的整理。

## License

[MIT](LICENSE) © chenmoulaile
