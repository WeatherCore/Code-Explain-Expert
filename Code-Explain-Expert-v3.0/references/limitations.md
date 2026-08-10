# 已知限制（Known Limitations）

本文件诚实记录 v3.0 的已知限制，避免用户误以为输出是 100% 精确。所有这些限制都在注释阶段靠阅读真实源码补足，不影响最终注释质量。

## 1. 骨架提取（extract_skeleton.py）

### 1.1 Java/Kotlin/C# 类似语言（正则解析）

- **泛型参数**：`class Foo<T extends Comparable<T>>` 的多层泛型可能被截断，类名能识别但泛型边界丢失
- **嵌套/内部类**：正则只匹配顶层 `class/interface/enum` 声明，内部类不会单独出现在 `classes[]` 里（但会包含在外层类的 body 范围内）
- **Lombok 注解**：`@Data`、`@Builder` 等生成的合成方法不在 `methods[]` 中，但 Lombok 注解本身会被捕获到 `annotations[]`
- **跨行注解**：`@PostMapping(\n  value = "/pay"\n)` 这种跨行注解参数可能解析不全，方法签名会带"残留括号"
- **方法签名精度**：返回类型与参数列表用宽正则匹配，复杂签名（含泛型、可变参数、注解）可能略有偏移；**行号准确**，签名仅供 LLM 决策优先级用

### 1.2 Python（ast 解析，精度高）

- **Python 用 ast 模块**，类/方法/装饰器/继承精度等同于编译器
- **唯一限制**：`ast.parse` 在含语法错误的源文件上会失败（返回空结果），不会抛出。LLM 在注释阶段读真实源码时会发现语法错误
- **类型注解**：保留为字符串形式（`ast.unparse`），不解析为静态类型

### 1.3 JavaScript / TypeScript（正则解析）

- **类**：`class A extends B implements C` 能识别；mixin、trait、abstract 不在识别范围
- **顶层函数**：`function foo()` 与 `export function foo()` 能识别；箭头函数赋值 `const foo = () => {}` 不识别为顶层函数（这是已知限制）
- **装饰器**：TS 装饰器能识别但参数可能不全

### 1.4 Go（正则解析）

- **结构体方法**：`func (s *Service) Method()` 能识别为结构体方法；接口方法声明（`interface { ... }` 内的）不识别
- **导出/未导出**：注释规范要求导出标识符以标识符名开头，但 extract 不强制

## 2. 依赖关系（dependency_links）

- **构建逻辑**：基于"项目内类型名/函数名出现在其他文件文本中"的启发式匹配，不是真实的调用图
- **多态/接口**：`interface` 的实现类不会自动建立 implements 边
- **动态分发**：反射调用、字符串拼装的类名、依赖注入容器解析的调用关系都不在 dependency_links 内
- **wildcard import**：`import com.foo.*` 只能捕获 `*` 短名，依赖边可能漏建（这是 Java wildcard import 的固有限制）

## 3. 变更检测（detect_changes.py）

### 3.1 git 模式

- 默认对比工作区未提交变更；无未提交变更时回退 `HEAD~1`（最近一次提交）。可指定 `--base origin/main` 等任意 ref
- **重命名/移动**：git 的 `R`（rename）状态会被归入 `modified`，文件路径为新路径

### 3.2 hash 模式（非 git 仓库兜底）

- 基于 MD5 快照比对，缓存文件 `.cc_hash.json` 落在项目根
- 首次运行返回全部源码文件作为 `added`（无对比基准）
- **只检测内容变化**，不区分 added/modified 的语义（首次后新增和修改都按"哈希不同"判为 modified）
- 文件移动会被识别为"旧路径删除 + 新路径新增"，无法识别为 rename

## 4. 大文件切块（bigfile_split.py）

- 按行数机械切块，**不感知语法边界**：可能在一个方法中间切断
- 相邻块通过 `--overlap` 行重叠保证连续性，默认 40 行
- 切块文件落盘到 `<file_dir>/.cc_split/<base>.NNN.txt`，需在注释完成后清理

## 5. 语言覆盖

- **骨架提取**支持 14 种语言（Java/Kotlin/Python/JS/TS/Go/Rust/C/C++/C#/PHP/Ruby/Swift 等）
- **注释语法规范**只对 5 种语言（Java/Kotlin/Python/TS/JS/Go）给出明确示例（见 `language-adaptation.md`）
- 其他语言用通用规则（行注释 `//` 或 `#`、块注释 `/** */`），不强制特定文档注释格式

## 6. 不在 Skill 边界内

以下任务本 Skill 明确不做（应走其他 skill 或普通问答）：

- 修改业务逻辑、修复 bug、重构代码
- 运行项目、调试启动错误
- 单文件脚本/算法题讲解
- 多项目对比、技术选型
- 生成测试用例

详见 SKILL.md 的 description 与 Constraints。
