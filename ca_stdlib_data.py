# -*- coding: utf-8 -*-
"""CppAssistant 纯数据模块：STL 补全数据库 + 编译器报错中文翻译表。
不依赖 sublime，可独立测试。

条目格式: (completion, annotation, kind)
kind: f=函数 m=成员函数 v=数据成员 c=常量/宏 t=类型 k=关键字 s=代码片段
"""

# --------------------------------------------------------------------------
# 成员函数数据库（按容器类型分组）
# --------------------------------------------------------------------------

_ITER = [
    ("begin()", "起始迭代器", "m"),
    ("end()", "结束迭代器", "m"),
    ("rbegin()", "反向起始迭代器", "m"),
    ("rend()", "反向结束迭代器", "m"),
]
_SIZE = [
    ("size()", "元素个数", "m"),
    ("empty()", "是否为空", "m"),
]

VECTOR_MEMBERS = [
    ("push_back(${1:x})", "尾部追加元素 O(1)", "m"),
    ("pop_back()", "删除尾部元素", "m"),
    ("emplace_back(${1:args})", "尾部原地构造", "m"),
    ("back()", "尾元素", "m"),
    ("front()", "首元素", "m"),
    ("at(${1:i})", "越界检查访问", "m"),
] + _ITER + _SIZE + [
    ("clear()", "清空", "m"),
    ("resize(${1:n})", "改变大小", "m"),
    ("reserve(${1:n})", "预留容量", "m"),
    ("insert(${1:pos}, ${2:x})", "在迭代器 pos 前插入", "m"),
    ("erase(${1:pos})", "删除迭代器处元素", "m"),
    ("assign(${1:n}, ${2:x})", "重新赋值", "m"),
    ("swap(${1:other})", "交换", "m"),
    ("data()", "底层裸指针", "m"),
    ("capacity()", "当前容量", "m"),
]

DEQUE_MEMBERS = [
    ("push_back(${1:x})", "尾部追加", "m"),
    ("push_front(${1:x})", "头部追加", "m"),
    ("pop_back()", "删尾", "m"),
    ("pop_front()", "删头", "m"),
    ("emplace_back(${1:args})", "尾部原地构造", "m"),
    ("emplace_front(${1:args})", "头部原地构造", "m"),
    ("back()", "尾元素", "m"),
    ("front()", "首元素", "m"),
    ("at(${1:i})", "随机访问", "m"),
] + _ITER + _SIZE + [
    ("clear()", "清空", "m"),
    ("insert(${1:pos}, ${2:x})", "插入", "m"),
    ("erase(${1:pos})", "删除", "m"),
    ("resize(${1:n})", "改变大小", "m"),
]

STRING_MEMBERS = [
    ("substr(${1:pos}${2:, len})", "取子串", "m"),
    ("find(${1:s})", "查找子串/字符, 返回下标或 npos", "m"),
    ("rfind(${1:s})", "从后往前找", "m"),
    ("find_first_of(${1:s})", "首次出现任一字符的位置", "m"),
    ("find_last_of(${1:s})", "最后出现任一字符的位置", "m"),
    ("append(${1:s})", "拼接", "m"),
    ("push_back(${1:c})", "追加字符", "m"),
    ("pop_back()", "删除末字符", "m"),
    ("insert(${1:pos}, ${2:s})", "插入", "m"),
    ("erase(${1:pos}${2:, len})", "删除子串", "m"),
    ("replace(${1:pos}, ${2:len}, ${3:s})", "替换子串", "m"),
    ("compare(${1:s})", "字典序比较", "m"),
    ("c_str()", "const char* 裸字符串", "m"),
    ("data()", "裸字符数组", "m"),
    ("length()", "长度", "m"),
    ("back()", "末字符", "m"),
    ("front()", "首字符", "m"),
    ("at(${1:i})", "越界检查访问", "m"),
    ("npos", "find 失败时的返回值", "c"),
    ("resize(${1:n})", "改变长度", "m"),
] + _ITER + _SIZE + [("clear()", "清空", "m")]

_ASSOC_CORE = [
    ("insert(${1:x})", "插入元素", "m"),
    ("emplace(${1:args})", "原地构造插入", "m"),
    ("erase(${1:key})", "按键/值删除", "m"),
    ("find(${1:key})", "查找, 返回迭代器", "m"),
    ("count(${1:key})", "键出现次数(0/1)", "m"),
    ("lower_bound(${1:key})", "第一个 >= key 的迭代器", "m"),
    ("upper_bound(${1:key})", "第一个 > key 的迭代器", "m"),
    ("equal_range(${1:key})", "等价区间", "m"),
] + _ITER + _SIZE + [("clear()", "清空", "m")]

SET_MEMBERS = list(_ASSOC_CORE)

MAP_MEMBERS = [
    ("insert({${1:k}, ${2:v}})", "插入键值对", "m"),
    ("emplace(${1:k}, ${2:v})", "原地构造插入", "m"),
    ("at(${1:k})", "越界检查取值", "m"),
    ("erase(${1:key})", "按键删除", "m"),
    ("find(${1:key})", "查找, 返回迭代器", "m"),
    ("count(${1:key})", "键是否存在", "m"),
    ("lower_bound(${1:key})", "第一个 >= key", "m"),
    ("upper_bound(${1:key})", "第一个 > key", "m"),
] + _ITER + _SIZE + [("clear()", "清空", "m")]

QUEUE_MEMBERS = [
    ("push(${1:x})", "入队", "m"),
    ("pop()", "出队(不返回值)", "m"),
    ("front()", "队首元素", "m"),
    ("back()", "队尾元素", "m"),
    ("emplace(${1:args})", "原地构造入队", "m"),
] + _SIZE

STACK_MEMBERS = [
    ("push(${1:x})", "入栈", "m"),
    ("pop()", "出栈(不返回值)", "m"),
    ("top()", "栈顶元素", "m"),
    ("emplace(${1:args})", "原地构造入栈", "m"),
] + _SIZE

PQ_MEMBERS = [
    ("push(${1:x})", "入堆 O(log n)", "m"),
    ("pop()", "弹出堆顶(不返回值)", "m"),
    ("top()", "堆顶元素", "m"),
    ("emplace(${1:args})", "原地构造", "m"),
] + _SIZE

LIST_MEMBERS = [
    ("push_back(${1:x})", "尾部追加", "m"),
    ("push_front(${1:x})", "头部追加", "m"),
    ("pop_back()", "删尾", "m"),
    ("pop_front()", "删头", "m"),
    ("remove(${1:x})", "删除所有等于 x 的元素", "m"),
    ("unique()", "去除相邻重复", "m"),
    ("sort()", "链表排序", "m"),
    ("reverse()", "翻转", "m"),
    ("merge(${1:other})", "归并另一有序链表", "m"),
    ("front()", "首元素", "m"),
    ("back()", "尾元素", "m"),
    ("insert(${1:pos}, ${2:x})", "插入", "m"),
    ("erase(${1:pos})", "删除", "m"),
] + _ITER + _SIZE + [("clear()", "清空", "m")]

ARRAY_MEMBERS = [
    ("fill(${1:x})", "全部填充", "m"),
    ("swap(${1:other})", "交换", "m"),
    ("at(${1:i})", "越界检查访问", "m"),
    ("front()", "首元素", "m"),
    ("back()", "尾元素", "m"),
    ("data()", "底层裸指针", "m"),
] + _ITER + _SIZE

PAIR_MEMBERS = [
    ("first", "第一个成员", "v"),
    ("second", "第二个成员", "v"),
]

BITSET_MEMBERS = [
    ("set(${1:i})", "置 1", "m"),
    ("reset(${1:i})", "清 0", "m"),
    ("flip(${1:i})", "取反", "m"),
    ("test(${1:i})", "检查某位", "m"),
    ("count()", "1 的个数", "m"),
    ("any()", "是否有 1", "m"),
    ("none()", "是否全 0", "m"),
    ("all()", "是否全 1", "m"),
    ("to_ulong()", "转 unsigned long", "m"),
    ("to_ullong()", "转 unsigned long long", "m"),
    ("to_string()", "转字符串", "m"),
    ("size()", "位数", "m"),
]

COMPLEX_MEMBERS = [
    ("real()", "实部", "m"),
    ("imag()", "虚部", "m"),
]

STREAM_IN = [
    ("getline(${1:buf}, ${2:n})", "读一行到 char 数组", "m"),
    ("get(${1:c})", "读取单字符", "m"),
    ("ignore(${1:n}, ${2:d})", "跳过字符", "m"),
    ("peek()", "预览下一字符", "m"),
    ("eof()", "是否到达末尾", "m"),
    ("fail()", "是否失败", "m"),
    ("good()", "流状态是否正常", "m"),
    ("clear()", "清除错误状态", "m"),
    ("gcount()", "上次未格式化读取的字符数", "m"),
]

STREAM_FILE_IN = STREAM_IN + [
    ("open(${1:\"file.txt\"})", "打开文件", "m"),
    ("close()", "关闭文件", "m"),
    ("is_open()", "是否已打开", "m"),
    ("read(${1:buf}, ${2:n})", "二进制读取", "m"),
    ("seekg(${1:pos})", "移动读指针", "m"),
    ("tellg()", "读指针位置", "m"),
]

STREAM_OUT = [
    ("flush()", "刷新缓冲区", "m"),
    ("put(${1:c})", "写入单字符", "m"),
    ("write(${1:buf}, ${2:n})", "二进制写入", "m"),
    ("precision(${1:n})", "浮点精度", "m"),
    ("width(${1:n})", "字段宽度", "m"),
    ("fill(${1:c})", "填充字符", "m"),
    ("fail()", "是否失败", "m"),
]

STREAM_FILE_OUT = STREAM_OUT + [
    ("open(${1:\"file.txt\"})", "打开文件", "m"),
    ("close()", "关闭文件", "m"),
    ("is_open()", "是否已打开", "m"),
    ("seekp(${1:pos})", "移动写指针", "m"),
    ("tellp()", "写指针位置", "m"),
]

STRSTREAM_MEMBERS = [
    ("str()", "取得内部字符串副本", "m"),
    ("str(${1:s})", "设置内容", "m"),
    ("clear()", "清除状态", "m"),
]

# 未知对象时的通用成员集合
GENERIC_MEMBERS = [
    ("push_back(${1:x})", "", "m"),
    ("pop_back()", "", "m"),
    ("push(${1:x})", "", "m"),
    ("pop()", "", "m"),
    ("front()", "", "m"),
    ("back()", "", "m"),
    ("top()", "", "m"),
    ("insert(${1:x})", "", "m"),
    ("erase(${1:key})", "", "m"),
    ("find(${1:key})", "", "m"),
    ("count(${1:key})", "", "m"),
    ("lower_bound(${1:key})", "", "m"),
    ("first", "", "v"),
    ("second", "", "v"),
] + _ITER + _SIZE + [("clear()", "", "m")]

MEMBERS_DB = {
    "vector": VECTOR_MEMBERS,
    "deque": DEQUE_MEMBERS,
    "string": STRING_MEMBERS,
    "basic_string": STRING_MEMBERS,
    "set": SET_MEMBERS,
    "multiset": SET_MEMBERS,
    "unordered_set": SET_MEMBERS,
    "unordered_multiset": SET_MEMBERS,
    "map": MAP_MEMBERS,
    "multimap": MAP_MEMBERS,
    "unordered_map": MAP_MEMBERS,
    "unordered_multimap": MAP_MEMBERS,
    "queue": QUEUE_MEMBERS,
    "stack": STACK_MEMBERS,
    "priority_queue": PQ_MEMBERS,
    "list": LIST_MEMBERS,
    "array": ARRAY_MEMBERS,
    "pair": PAIR_MEMBERS,
    "bitset": BITSET_MEMBERS,
    "complex": COMPLEX_MEMBERS,
    "ifstream": STREAM_FILE_IN,
    "istringstream": STREAM_IN,
    "istream": STREAM_IN,
    "cin": STREAM_IN,
    "ofstream": STREAM_FILE_OUT,
    "ostringstream": STREAM_OUT,
    "ostream": STREAM_OUT,
    "cout": STREAM_OUT,
    "cerr": STREAM_OUT,
    "fstream": STREAM_FILE_IN + [i for i in STREAM_FILE_OUT if i not in STREAM_FILE_IN],
    "stringstream": STRSTREAM_MEMBERS + STREAM_IN,
}

# 元素类型推断表: 容器 -> 元素类型描述(用占位 T/K/V 表示模板参数)
_ELEM_RULES = {
    "vector": "{T}", "deque": "{T}", "list": "{T}", "array": "{T}",
    "set": "{T}", "multiset": "{T}", "unordered_set": "{T}",
    "unordered_multiset": "{T}",
    "string": "char", "basic_string": "char",
    "map": "pair<const {K}, {V}>", "multimap": "pair<const {K}, {V}>",
    "unordered_map": "pair<const {K}, {V}>",
    "unordered_multimap": "pair<const {K}, {V}>",
    "queue": "{T}", "stack": "{T}", "priority_queue": "{T}",
}

# --------------------------------------------------------------------------
# 自由函数 / 全局符号
# --------------------------------------------------------------------------

# wants_std=True 的条目在缺少 using namespace std; 时自动补 std:: 前缀
STD_FUNCTIONS = [
    # <algorithm>
    ("sort(${1:v.begin()}, ${2:v.end()})", "排序(升序) O(n log n)", "f", True),
    ("stable_sort(${1:first}, ${2:last})", "稳定排序", "f", True),
    ("nth_element(${1:first}, ${2:first+k}, ${3:last})", "使第 k 位元素就位 O(n)", "f", True),
    ("lower_bound(${1:first}, ${2:last}, ${3:x})", "第一个 >= x 的迭代器", "f", True),
    ("upper_bound(${1:first}, ${2:last}, ${3:x})", "第一个 > x 的迭代器", "f", True),
    ("equal_range(${1:first}, ${2:last}, ${3:x})", "等价区间 pair", "f", True),
    ("binary_search(${1:first}, ${2:last}, ${3:x})", "二分查找存在性", "f", True),
    ("min(${1:a}, ${2:b})", "最小值", "f", True),
    ("max(${1:a}, ${2:b})", "最大值", "f", True),
    ("minmax(${1:a}, ${2:b})", "pair{min,max}", "f", True),
    ("min_element(${1:first}, ${2:last})", "最小值迭代器", "f", True),
    ("max_element(${1:first}, ${2:last})", "最大值迭代器", "f", True),
    ("clamp(${1:v}, ${2:lo}, ${3:hi})", "夹到区间内 (C++17)", "f", True),
    ("reverse(${1:first}, ${2:last})", "区间翻转", "f", True),
    ("unique(${1:first}, ${2:last})", "相邻去重, 返回新尾", "f", True),
    ("rotate(${1:first}, ${2:mid}, ${3:last})", "循环左移", "f", True),
    ("next_permutation(${1:first}, ${2:last})", "下一排列", "f", True),
    ("prev_permutation(${1:first}, ${2:last})", "上一排列", "f", True),
    ("partition(${1:first}, ${2:last}, ${3:p})", "按谓词划分", "f", True),
    ("fill(${1:first}, ${2:last}, ${3:x})", "区间赋值", "f", True),
    ("fill_n(${1:first}, ${2:n}, ${3:x})", "前 n 个赋值", "f", True),
    ("count(${1:first}, ${2:last}, ${3:x})", "计数", "f", True),
    ("count_if(${1:first}, ${2:last}, ${3:p})", "条件计数", "f", True),
    ("find(${1:first}, ${2:last}, ${3:x})", "线性查找", "f", True),
    ("find_if(${1:first}, ${2:last}, ${3:p})", "条件查找", "f", True),
    ("all_of(${1:first}, ${2:last}, ${3:p})", "全部满足?", "f", True),
    ("any_of(${1:first}, ${2:last}, ${3:p})", "存在满足?", "f", True),
    ("none_of(${1:first}, ${2:last}, ${3:p})", "全不满足?", "f", True),
    ("transform(${1:in_first}, ${2:in_last}, ${3:out}, ${4:op})", "变换", "f", True),
    ("for_each(${1:first}, ${2:last}, ${3:f})", "逐个处理", "f", True),
    ("copy(${1:first}, ${2:last}, ${3:out})", "复制", "f", True),
    ("copy_n(${1:first}, ${2:n}, ${3:out})", "复制前 n 个", "f", True),
    ("merge(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end}, ${5:out})", "归并两个有序区间", "f", True),
    ("inplace_merge(${1:first}, ${2:mid}, ${3:last})", "原地归并", "f", True),
    ("is_sorted(${1:first}, ${2:last})", "是否有序", "f", True),
    ("set_intersection(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end}, ${5:out})", "交集", "f", True),
    ("set_union(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end}, ${5:out})", "并集", "f", True),
    ("set_difference(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end}, ${5:out})", "差集 A-B", "f", True),
    ("set_symmetric_difference(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end}, ${5:out})", "对称差", "f", True),
    ("includes(${1:a}, ${2:a_end}, ${3:b}, ${4:b_end})", "是否包含子序列", "f", True),
    ("push_heap(${1:first}, ${2:last})", "上滤建堆", "f", True),
    ("pop_heap(${1:first}, ${2:last})", "弹出堆顶到尾部", "f", True),
    ("make_heap(${1:first}, ${2:last})", "建堆 O(n)", "f", True),
    ("sort_heap(${1:first}, ${2:last})", "堆排序", "f", True),
    # <numeric>
    ("accumulate(${1:first}, ${2:last}, ${3:init})", "累加/自定义折叠", "f", True),
    ("iota(${1:first}, ${2:last}, ${3:start})", "递增填充", "f", True),
    ("inner_product(${1:a}, ${2:a_end}, ${3:b}, ${4:init})", "内积", "f", True),
    ("gcd(${1:a}, ${2:b})", "最大公约数 (C++17)", "f", True),
    ("lcm(${1:a}, ${2:b})", "最小公倍数 (C++17)", "f", True),
    ("__gcd(${1:a}, ${2:b})", "GCC 内置 gcd", "f", False),
    # 迭代器
    ("distance(${1:first}, ${2:last})", "两点距离", "f", True),
    ("advance(${1:it}, ${2:n})", "移动迭代器", "f", True),
    ("prev(${1:it})", "前一迭代器", "f", True),
    ("next(${1:it})", "后一迭代器", "f", True),
    # 工具
    ("swap(${1:a}, ${2:b})", "交换", "f", True),
    ("make_pair(${1:a}, ${2:b})", "构造 pair", "f", True),
    ("make_tuple(${1:args})", "构造 tuple", "f", True),
    ("tie(${1:a}, ${2:b})", "解包 tuple", "f", True),
    ("get(${1:t})", "取 tuple 第 i 个元素", "f", True),
    ("move(${1:x})", "转右值", "f", True),
    ("to_string(${1:x})", "数字转字符串", "f", True),
    ("stoi(${1:s})", "字符串转 int", "f", True),
    ("stoll(${1:s})", "字符串转 long long", "f", True),
    ("stod(${1:s})", "字符串转 double", "f", True),
    ("numeric_limits<int>::max()", "int 最大值", "c", True),
    ("numeric_limits<int>::min()", "int 最小值", "c", True),
    ("numeric_limits<long long>::max()", "long long 最大值", "c", True),
    # IO
    ("cin", "标准输入流", "v", True),
    ("cout", "标准输出流", "v", True),
    ("cerr", "标准错误流(无缓冲)", "v", True),
    ("endl", "换行+刷新缓冲", "c", True),
    ("fixed", "定点小数操纵符", "c", True),
    ("left", "左对齐操纵符", "c", True),
    ("right", "右对齐操纵符", "c", True),
    ("getline(${1:cin}, ${2:s})", "读一行到 string", "f", True),
    ("setw(${1:n})", "设置域宽 (iomanip)", "f", True),
    ("setprecision(${1:n})", "设置精度 (iomanip)", "f", True),
    ("setfill(${1:c})", "设置填充符 (iomanip)", "f", True),
    # <cmath>
    ("abs(${1:x})", "绝对值", "f", True),
    ("fabs(${1:x})", "浮点绝对值", "f", True),
    ("sqrt(${1:x})", "平方根", "f", True),
    ("pow(${1:x}, ${2:y})", "幂", "f", True),
    ("floor(${1:x})", "向下取整", "f", True),
    ("ceil(${1:x})", "向上取整", "f", True),
    ("round(${1:x})", "四舍五入", "f", True),
    ("trunc(${1:x})", "向零截断", "f", True),
    ("log(${1:x})", "自然对数", "f", True),
    ("log2(${1:x})", "以 2 为底对数", "f", True),
    ("log10(${1:x})", "以 10 为底对数", "f", True),
    ("exp(${1:x})", "e^x", "f", True),
    ("sin(${1:x})", "正弦", "f", True),
    ("cos(${1:x})", "余弦", "f", True),
    ("tan(${1:x})", "正切", "f", True),
    ("asin(${1:x})", "反正弦", "f", True),
    ("acos(${1:x})", "反余弦", "f", True),
    ("atan(${1:x})", "反正切", "f", True),
    ("atan2(${1:y}, ${2:x})", "极角", "f", True),
    ("fmod(${1:x}, ${2:y})", "浮点取模", "f", True),
    ("hypot(${1:x}, ${2:y})", "sqrt(x^2+y^2)", "f", True),
    ("cbrt(${1:x})", "立方根", "f", True),
    ("isnan(${1:x})", "是否 NaN", "f", True),
    ("isfinite(${1:x})", "是否有限", "f", True),
    # <cctype>
    ("tolower(${1:c})", "转小写", "f", True),
    ("toupper(${1:c})", "转大写", "f", True),
    ("isalpha(${1:c})", "是否字母", "f", True),
    ("isdigit(${1:c})", "是否数字", "f", True),
    ("isalnum(${1:c})", "是否字母或数字", "f", True),
    ("isspace(${1:c})", "是否空白", "f", True),
    ("isupper(${1:c})", "是否大写", "f", True),
    ("islower(${1:c})", "是否小写", "f", True),
    # <cstdlib> / <cstring>
    ("memset(${1:buf}, ${2:0}, ${3:sizeof buf})", "内存填充(多用于清零/初始化 INF)", "f", False),
    ("memcpy(${1:dst}, ${2:src}, ${3:n})", "内存复制", "f", False),
    ("strlen(${1:s})", "C 字符串长度", "f", False),
    ("atoi(${1:s.c_str()})", "字符串转 int", "f", False),
    ("abs(${1:x})", "整数绝对值", "f", False),
    ("rand()", "伪随机数 [0,RAND_MAX]", "f", False),
    ("srand(${1:seed})", "设置随机种子", "f", False),
    ("clock()", "处理器时钟", "f", False),
    # <cstdio>
    ("printf(${1:\"%d\\n\", x})", "格式化输出", "f", False),
    ("scanf(${1:\"%d\", &x})", "格式化输入", "f", False),
    ("puts(${1:s})", "输出一行", "f", False),
    ("getchar()", "读单字符", "f", False),
    ("putchar(${1:c})", "写单字符", "f", False),
    ("freopen(${1:\"in.txt\"}, ${2:\"r\"}, ${3:stdin})", "重定向输入", "f", False),
    # 内建
    ("__builtin_popcount(${1:x})", "二进制中 1 的个数", "f", False),
    ("__builtin_popcountll(${1:x})", "long long 版本", "f", False),
    ("__builtin_ctz(${1:x})", "末尾 0 的个数", "f", False),
    ("__builtin_clz(${1:x})", "前导 0 的个数", "f", False),
    ("__builtin_ffs(${1:x})", "最低位 1 的位置(1 起)", "f", False),
]

GLOBAL_CONSTANTS = [
    ("INT_MAX", "int 最大值 2147483647", "c", False),
    ("INT_MIN", "int 最小值", "c", False),
    ("LLONG_MAX", "long long 最大值 9223372036854775807", "c", False),
    ("LLONG_MIN", "long long 最小值", "c", False),
    ("ULLONG_MAX", "unsigned long long 最大值", "c", False),
    ("UINT_MAX", "unsigned int 最大值", "c", False),
    ("M_PI", "圆周率 π", "c", False),
    ("M_E", "自然常数 e", "c", False),
    ("INFINITY", "正无穷", "c", False),
    ("NAN", "非数", "c", False),
    ("RAND_MAX", "rand() 上限", "c", False),
    ("EOF", "文件结束标记(-1)", "c", False),
]

STD_TYPES = [
    ("vector<${1:int}>", "动态数组", "t", True),
    ("pair<${1:int}, ${2:int}>", "二元组", "t", True),
    ("map<${1:int}, ${2:int}>", "有序映射(红黑树)", "t", True),
    ("set<${1:int}>", "有序集合", "t", True),
    ("multiset<${1:int}>", "可重复有序集合", "t", True),
    ("unordered_map<${1:int}, ${2:int}>", "哈希映射", "t", True),
    ("unordered_set<${1:int}>", "哈希集合", "t", True),
    ("queue<${1:int}>", "队列", "t", True),
    ("stack<${1:int}>", "栈", "t", True),
    ("priority_queue<${1:int}>", "大根堆", "t", True),
    ("deque<${1:int}>", "双端队列", "t", True),
    ("list<${1:int}>", "双向链表", "t", True),
    ("array<${1:int}, ${2:N}>", "定长数组", "t", True),
    ("tuple<${1:int}, ${2:int}>", "多元组", "t", True),
    ("string", "字符串", "t", True),
    ("bitset<${1:N}>", "位集", "t", True),
    ("complex<${1:double}>", "复数", "t", True),
    ("mt19937", "32 位梅森旋转随机引擎 (random)", "t", True),
    ("function<${1:void(int)}>", "可调用对象包装", "t", True),
]

KEYWORDS = [
    "alignas", "auto", "bool", "break", "case", "catch", "char", "class",
    "const", "constexpr", "const_cast", "continue", "decltype", "default",
    "delete", "do", "double", "dynamic_cast", "else", "enum", "explicit",
    "export", "extern", "false", "final", "float", "for", "friend", "goto",
    "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept",
    "nullptr", "operator", "override", "private", "protected", "public",
    "register", "reinterpret_cast", "return", "short", "signed", "sizeof",
    "static", "static_assert", "static_cast", "struct", "switch", "template",
    "this", "throw", "true", "try", "typedef", "typeid", "typename", "union",
    "unsigned", "using", "virtual", "void", "volatile", "wchar_t", "while",
]

SNIPPETS = [
    ("us", "using namespace std;", "命名空间声明", "s"),
    ("inc", "#include <bits/stdc++.h>", "万能头文件", "s"),
    ("fastio", "ios::sync_with_stdio(false);\ncin.tie(nullptr);$0", "关闭同步流加速", "s"),
    (
        "mainf",
        "int main() {\n"
        "    ios::sync_with_stdio(false);\n"
        "    cin.tie(nullptr);\n"
        "\n"
        "    int tt = 1;\n"
        "    // cin >> tt;\n"
        "    while (tt--) {\n"
        "        solve();\n"
        "    }\n"
        "\n"
        "    return 0;\n"
        "}",
        "jiangly 码风主函数模板",
        "s",
    ),
    (
        "solvef",
        "void solve() {\n"
        "    $0\n"
        "}",
        "jiangly 码风 solve 函数",
        "s",
    ),
    ("pii", "pair<int, int>", "pair<int,int> 简写", "s"),
]

HEADERS = [
    "bits/stdc++.h", "algorithm", "iostream", "vector", "string", "cmath",
    "cstdio", "cstdlib", "cstring", "map", "set", "unordered_map",
    "unordered_set", "queue", "stack", "deque", "utility", "functional",
    "numeric", "iomanip", "sstream", "fstream", "bitset", "tuple",
    "climits", "cctype", "ctime", "chrono", "random", "complex", "array",
    "list", "iterator", "memory", "cassert",
]

# --------------------------------------------------------------------------
# 编译器报错 -> 中文翻译表 (顺序敏感, 先长后短)
# --------------------------------------------------------------------------

SEVERITY_MAP = {
    "error": "错误",
    "fatal error": "致命错误",
    "warning": "警告",
    "note": "提示",
}

_TRANSLATION_SRC = [
    (r"expected\s+';'(?!\s+(?:before|at))", u"缺少分号 ';'"),
    (r"expected\s+';' before '(.+?)'", r"在 '\g<1>' 之前缺少分号 ';'"),
    (r"expected\s+',' or ';'? before '(.+?)'", r"在 '\g<1>' 之前缺少 ',' 或 ';'"),
    (r"expected\s+',' or ';'? before", u"此处缺少 ',' 或 ';'"),
    (r"expected\s+'\)' before '(.+?)'", r"在 '\g<1>' 之前缺少右括号 ')'"),
    (r"expected\s+'\(' before '(.+?)'", r"在 '\g<1>' 之前缺少左括号 '('"),
    (r"expected\s+'}' at end of input", u"输入结束时缺少右花括号 '}'"),
    (r"expected\s+'\{' at end of input", u"输入结束时缺少左花括号 '{'"),
    (r"expected\s+'\)' at end of input", u"输入结束时缺少右括号 ')'"),
    (r"expected\s+expression before '(.+?)'", r"在 '\g<1>' 之前应为表达式"),
    (r"expected\s+primary-expression before '(.+?)'", r"在 '\g<1>' 之前缺少表达式"),
    (r"expected\s+unqualified-id before '(.+?)'", r"在 '\g<1>' 之前应有合法的标识符或声明"),
    (r"expected\s+declaration before '(.+?)'", r"在 '\g<1>' 之前应为一条声明"),
    (r"expected\s+initializer before '(.+?)'", r"在 '\g<1>' 之前缺少初始化"),
    (r"expected\s+initializer", u"缺少初始化"),
    (r"expected\s+constructor(?:,\s*destructor)?\s+or\s+type conversion before '(.+?)'",
     r"在 '\g<1>' 之前应为构造/析构函数或类型转换"),
    (r"expected\s+type-specifier before '(.+?)'", r"在 '\g<1>' 之前缺少类型说明"),
    (r"expected\s+nested-name-specifier", u"此处应为 '名字空间::名字' 形式"),
    (r"expected\s+class-name before '(.+?)'", r"在 '\g<1>' 处应为类名"),
    (r"expected\s+template-name before", u"此处应为模板名"),
    (r"expected\s+parameter declarator", u"缺少参数声明"),
    (r"expected\s+body of function", u"缺少函数体"),
    (r"expected\s+';' or '\{'", u"此处需要 ';' 或 '{'"),
    (r"'(.+?)'\s+was not declared in this scope", r"标识符 '\g<1>' 未在此作用域中声明(检查拼写/是否漏了头文件)"),
    (r"was not declared in this scope", u"标识符未在此作用域中声明"),
    (r"Did you mean '(.+?)'\?", r"你是不是想写 '\g<1>' ？"),
    (r"did you forget to '#include <(.+?)>'\?", r"是不是忘记 #include <\g<1>> 了？"),
    (r"'(.+?)' does not name a type(?! in)", r"'\g<1>' 不是有效的类型名"),
    (r"does not name a type", u"不是有效的类型名"),
    (r"unknown type name '(.+?)'", r"未知的类型名 '\g<1>'"),
    (r"redefinition of '(.+?)'", r"重复定义了 '\g<1>'"),
    (r"previously defined here", u"先前定义于此"),
    (r"previous (?:definition|declaration) of '(.+?)'", r"'\g<1>' 的先前定义/声明位置"),
    (r"conflicting declaration of '(.+?)'", r"'\g<1>' 的声明相互冲突"),
    (r"invalid conversion from '(.+?)' to '(.+?)'", r"无法把类型 '\g<1>' 转换为 '\g<2>'"),
    (r"cannot convert '(.+?)' to '(.+?)'(?! in)", r"无法把 '\g<1>' 转换成 '\g<2>'"),
    (r"no viable conversion from '(.+?)' to '(.+?)'", r"无法从 '\g<1>' 转换为 '\g<2>'"),
    (r"no match for '(.+?)' \(operand types are '(.+?)' and '(.+?)'\)",
     r"运算符 \g<1> 没有匹配的重载(操作数类型为 '\g<2>' 和 '\g<3>')"),
    (r"no match for '(.+?)'", r"\g<1> 没有匹配的运算符重载"),
    (r"no matching function for call to '(.+?)'", r"调用 '\g<1>' 时没有匹配的重载函数"),
    (r"call of overloaded '(.+?)' is ambiguous", r"重载调用 '\g<1>' 有歧义"),
    (r"too few arguments to function '(.+?)'", r"调用函数 '\g<1>' 的实参太少"),
    (r"too many arguments to function '(.+?)'", r"调用函数 '\g<1>' 的实参太多"),
    (r"invalid operands of types '(.+?)' and '(.+?)' to binary '(.+?)'",
     r"二元运算符 \g<3> 的操作数类型无效('\g<1>' 与 '\g<2>')"),
    (r"'(.+?)' in '(.+?)' does not name a type", r"'\g<2>' 中没有名为 '\g<1>' 的有效类型"),
    (r"(?:class|struct|type)?\s*'(.+?)' has no member named '(.+?)'",
     r"类型 '\g<1>' 没有名为 '\g<2>' 的成员"),
    (r"request for member '(.+?)' in '(.+?)', which is of non-class type[^,\n]*",
     r"'\g<2>' 不是类类型，无法访问其成员 '\g<1>'"),
    (r"request for member '(.+?)' in something not a structure or union",
     r"向非结构体/联合体请求成员 '\g<1>'"),
    (r"invalid use of incomplete type '(.+?)'", r"使用了不完整类型 '\g<1>'(可能缺头文件或前置声明未定义)"),
    (r"aggregate '(.+?)' has incomplete type", r"对象 '\g<1>' 的类型不完整"),
    (r"variable-sized object '(.+?)' may not be initialized", r"变长数组 '\g<1>' 不能初始化"),
    (r"array must be initialized with a brace-enclosed initializer", u"数组必须用花括号初始化"),
    (r"too many initializers for '(.+?)'", r"'\g<1>' 的初始值太多"),
    (r"division by zero", u"除数为零"),
    (r"divide by zero", u"除数为零"),
    (r"floating point exception", u"浮点异常"),
    (r"array subscript out of bound", u"数组下标越界"),
    (r"lvalue required as (.+?) operand", r"作为 \g<1> 操作数必须是左值(可修改的表达式)"),
    (r"assignment(?:\s+\((.+?)\))? used as truth value", u"条件判断中疑似误用了 '='，若确要赋值请加双重括号"),
    (r"suggest parentheses around assignment used as truth value", u"条件判断中疑似误用 '='，建议改用 '==' 或加括号"),
    (r"comparison of integer expressions of different signedness: '(.+?)' and '(.+?)'",
     r"有符号与无符号整数比较('\g<1>' 与 '\g<2>')，可能导致死循环/逻辑错误"),
    (r"ordered comparison of pointer with integer", u"指针与整数的比较无意义"),
    (r"unused variable '(.+?)'", r"变量 '\g<1>' 已定义但未被使用"),
    (r"unused parameter '(.+?)'", r"参数 '\g<1>' 未被使用"),
    (r"unused-but-set variable '(.+?)'", r"变量 '\g<1>' 被赋值但从未读取"),
    (r"unused local typedef", u"本地 typedef 未被使用"),
    (r"control reaches end of non-void function", u"非 void 函数执行到结尾仍未 return 返回值"),
    (r"return-statement with no value, in a function returning '(.+?)'",
     r"返回类型为 '\g<1>' 的函数中出现了不带值的 return"),
    (r"return-statement with a value, in a function returning 'void'", u"void 函数不能 return 一个值"),
    (r"'main' must return 'int'", u"main 函数必须返回 int"),
    (r"break statement not within loop or switch", u"break 不在循环或 switch 语句中"),
    (r"continue statement not within a loop", u"continue 不在循环语句中"),
    (r"case label '(.+?)' not within a switch statement", u"case 标签不在 switch 中"),
    (r"'default' label not within a switch statement", u"default 标签不在 switch 中"),
    (r"duplicate case value", u"case 值重复"),
    (r"ISO C\+\+ forbids declaration of '(.+?)' with no type", r"'\g<1>' 缺少类型声明(C++ 要求显式类型)"),
    (r"declaration of '(.+?)' shadows a (?:global declaration|parameter)",
     r"'\g<1>' 遮蔽了外层的同名变量/参数"),
    (r"operation on '(.+?)' may be undefined", r"对 '\g<1>' 的操作结果可能未定义(如同一表达式中多次修改)"),
    (r"statement has no effect", u"该语句没有任何效果"),
    (r"left operand of comma operator has no effect", u"逗号运算符左侧表达式无效果"),
    (r"value computed is not used", u"计算出的值没有被使用"),
    (r"stray '\\d+' in program", u"程序中有非法字符(常见原因：中文标点、全角空格)"),
    (r"in expansion of macro '(.+?)'", r"展开宏 '\g<1>' 时"),
    (r"required from here", u"由这里的代码实例化引发"),
    (r"in definition of macro '(.+?)'", r"宏 '\g<1>' 定义于此外"),
    (r"macro \"(.+?)\" (?:passed|requires) (\d+) arguments?, but takes just (\d+)",
     r"宏 '\g<1>' 参数个数不对(给了 \g<2> 个, 需要 \g<3> 个)"),
    (r"#include expects \"FILENAME\" or <FILENAME>", u"#include 后应跟 \"文件名\" 或 <文件名>"),
    (r"(.+?): No such file or directory", r"找不到文件 '\g<1>'(检查路径/是否安装该库)"),
    (r"use of deleted function", u"调用了已被删除(=delete)的函数"),
    (r"initializing argument (\d+) of '(.+?)'", r"对应函数 '\g<2>' 的第 \g<1> 个参数"),
    (r"initializing: '?(\w+)?'?", u"初始化时"),
    (r"cannot bind non-const lvalue reference of type '(.+?)' to an rvalue of type '(.+?)'",
     r"非常量左值引用 '\g<1>' 不能绑定到右值('\g<2>')"),
    (r"invalid initialization of (?:non-const )?reference of type '(.+?)' from an rvalue of type '(.+?)'",
     r"引用 '\g<1>' 不能绑定到右值 '\g<2>'"),
    (r"expected '(.+?)' before numeric constant", r"数字常量之前缺少 '\g<1>'"),
    (r"a function-definition is not allowed here", u"此处不允许嵌套定义函数(多半是上面少了大括号)"),
    (r"expected declarations? before '(.+?)' token", r"在 '\g<1>' 之前应为声明"),
    (r"new types may not be defined in a return type", u"不能在函数返回类型处定义新类型"),
    (r"two or more data types in declaration specifiers", u"声明中出现了两个以上的类型说明符(如 int long 写重)"),
    (r"storage class specified for ", u"此处的存储类别说明符不合法"),
    (r"undeclared \(first use in this function\)", u"未声明(首次在此使用)"),
    (r"suggest explicit braces to avoid ambiguous", u"建议显式加花括号以避免 else 归属歧义"),
    (r"overflow in implicit constant conversion", u"常量隐式转换溢出"),
    (r"integer overflow in expression", u"表达式发生整数溢出"),
    (r"unused variable", u"存在未使用的变量"),
    (r"-Wuninitialized", u"变量可能未初始化就被使用"),
    (r"may be used uninitialized in this function", u"变量可能未初始化即被使用"),
    (r"is used uninitialized", u"变量未初始化就被使用"),
    (r"'(.+?)' is static but used in inline function", r"静态 '\g<1>' 被内联函数使用"),
    (r"invalid suffix \"(.+?)\" on integer constant", r"整数常量带有非法后缀 '\g<1>'"),
    (r"missing terminating ' character", u"字符常量缺少结尾的单引号"),
    (r"missing terminating \" character", u"字符串缺少结尾的双引号"),
    (r"null character\(s\) preserved", u"保留了空字符"),
    (r"'auto' deduced as '(.+?)' in declaration of '(.+?)' and '(.+?)' in declaration of '(.+?)'",
     r"auto 推导冲突: '\g<2>' 为 '\g<1>', 而 '\g<4>' 为 '\g<3>'"),
    (r"use of 'auto' before deduction", u"在推导出类型之前就使用了 auto 变量"),
    (r"templates? must be declared? before", u"模板必须先声明再使用"),
    (r"specialization of '(.+?)' after instantiation", r"'\g<1>' 的特化出现在实例化之后"),
    (r"redefinition of default argument", u"默认实参重复给出"),
    (r"default argument given for parameter (\d+) of '(.+?)'", r"函数 '\g<2>' 第 \g<1> 个参数之后的参数也需要默认值"),
    (r"declaration of C function '(.+?)' conflicts with", r"C 函数 '\g<1>' 的声明与已有版本冲突"),
    (r"ambiguous overload for '(.+?)'", r"'\g<1>' 的重载有歧义"),
    (r"candidate is:", u"候选函数为："),
    (r"candidates? are:", u"候选重载为："),
    (r"note: neither the existing declaration nor the new declaration matched",
     u"提示: 新旧声明都不匹配"),
    # 末尾清理
    (r"\s+token\b", u""),
    (r";\s*(你是不是想写|是不是忘记)", r"，\g<1>"),
    (r"\s{2,}", u" "),
]

import re as _re

TRANSLATIONS = []
for _pat, _rep in _TRANSLATION_SRC:
    try:
        TRANSLATIONS.append((_re.compile(_pat, _re.I | _re.S), _rep))
    except Exception:
        pass

# clang++ 常见报错风格补充
_CLANG_EXTRA = [
    (r"use of undeclared identifier '(.+?)'",
     r"未声明的标识符 '\g<1>'(检查拼写或是否漏了头文件)"),
    (r"no matching member function for call to '(.+?)'",
     r"调用成员函数 '\g<1>' 时没有匹配的重载"),
    (r"member reference type '(.+?)' is not a pointer",
     r"成员访问类型 '\g<1>' 不是指针，应使用 '.' 而不是 '->'"),
    (r"member reference type '(.+?)' is a pointer; did you mean to use '->'\?",
     r"'\g<1>' 是指针，应使用 '->' 而不是 '.'"),
    (r"expected '\)'", u"缺少右括号 ')'"),
    (r"expected '\}'", u"缺少右花括号 '}'"),
    (r"expected '\{'", u"缺少左花括号 '{'"),
    (r"expected expression", u"此处应为表达式"),
    (r"expected a type", u"此处应为类型名"),
    (r"too many arguments provided to function-like macro invocation",
     u"类函数宏调用时实参过多"),
]
for _pat, _rep in _CLANG_EXTRA:
    try:
        TRANSLATIONS.append((_re.compile(_pat, _re.I | _re.S), _rep))
    except Exception:
        pass

# 编译器警告旗标 -> 中文标签（显示时替换形如 [-Wunused-variable] 的尾巴）
WARNING_FLAG_ZH = {
    "-Wunused-variable": u"未使用变量",
    "-Wunused-but-set-variable": u"已赋值但未读取",
    "-Wunused-parameter": u"未使用参数",
    "-Wunused-function": u"未使用函数",
    "-Wunused-value": u"无效运算",
    "-Wdiv-by-zero": u"除数为零",
    "-Wsign-compare": u"有符号/无符号比较",
    "-Wreturn-type": u"返回类型问题",
    "-Wparentheses": u"建议加括号",
    "-Wuninitialized": u"可能未初始化",
    "-Wmaybe-uninitialized": u"可能未初始化",
    "-Woverflow": u"整数溢出",
    "-Wnarrowing": u"隐式收窄转换",
    "-Wreorder": u"成员初始化顺序与声明不一致",
    "-Wwrite-strings": u"字符串常量写入风险",
    "-Wformat": u"printf 参数不匹配",
    "-Wswitch": u"switch 未覆盖所有枚举值",
}

# 引号统一替换表(gcc 高版本会用弯引号)
QUOTE_NORMALIZE = ((u"\u2018", "'"), (u"\u2019", "'"), (u"\u201c", '"'), (u"\u201d", '"'))

# --------------------------------------------------------------------------
# 预计算补全条目（避免每次按键重复解析元组）
# --------------------------------------------------------------------------


def _entry(comp, ann, kind, want_std=False):
    return {
        "trigger": comp.split("(")[0],
        "insert": comp,
        "annotation": ann or "",
        "kind": kind,
        "want_std": want_std,
    }


MEMBERS_DB_FAST = dict(
    (key, [_entry(c, a, k) for (c, a, k) in members])
    for key, members in MEMBERS_DB.items()
)
GENERIC_MEMBERS_FAST = [_entry(c, a, k) for (c, a, k) in GENERIC_MEMBERS]

_STD_COMBINED = STD_FUNCTIONS + STD_TYPES + GLOBAL_CONSTANTS
STD_ITEMS_ALL = [_entry(c, a, k, w) for (c, a, k, w) in _STD_COMBINED]
