# 已知限制（Known Limitations）

本文件诚实记录 v4.0 的已知限制，避免用户误以为输出是 100% 精确。所有这些限制都在注释阶段靠阅读真实源码补足，不影响最终注释质量。

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

## 3. 大文件切块（bigfile_split.py）

- 按行数机械切块，**不感知语法边界**：可能在一个方法中间切断
- 相邻块通过 `--overlap` 行重叠保证连续性，默认 40 行
- **v4.0：切块文件不落盘到客户项目**（v3 的 `<file_dir>/.cc_split/<base>.NNN.txt` 已移除）。脚本默认自动落盘到 skill `.work/chunks.json`，切块清单 JSON 只含行号区间（`start_line`/`end_line`，`chunk_file` 始终为 None），LLM 用 Read 工具读 `.work/chunks.json` 拿行号区间，再按 offset/limit 读源文件对应行区间

## 4. 语言覆盖

- **骨架提取**支持 14 种语言（Java/Kotlin/Python/JS/TS/Go/Rust/C/C++/C#/PHP/Ruby/Swift 等）
- **注释语法规范**只对 5 种语言（Java/Kotlin/Python/TS/JS/Go）给出明确示例（见 `language-adaptation.md`）
- 其他语言用通用规则（行注释 `//` 或 `#`、块注释 `/** */`），不强制特定文档注释格式

## 5. 不在 Skill 边界内

以下任务本 Skill 明确不做（应走其他 skill 或普通问答）：

- 修改业务逻辑、修复 bug、重构代码
- 运行项目、调试启动错误
- 单文件脚本/算法题讲解
- 多项目对比、技术选型
- 生成测试用例
- 变更检测 / 增量注释更新（v4.0 已移除，不做 diff 比对）

详见 SKILL.md 的 description 与 Constraints。

## 6. v4.0 客户项目保护红线（强制约束）

以下行为 v4.0 起一律禁止，违反即事故：

- **不执行任何 git 写操作**（commit / push / stash / branch / checkout / tag / reset / add / merge / rebase 等），只允许 git 只读操作（status / diff / log / show / reflog / branch -a / cat-file）
- **不在客户项目目录内造任何缓存/备份/中间文件**（.bak / .bak-<timestamp> / .cc_hash.json / .cc_split/ / skeleton.json / batch.txt / chunks.json 等）。所有中间产物**自动落盘到 skill 目录下 `.work/` 子目录**，不写到客户项目；完成后清理 `.work/`
- **不把客户项目代码上传到任何远端**（GitHub / GitLab / Gitee / 内部 Git 服务器 / 网盘 / 对象存储 / paste 服务）
- **不替用户做备份决策**：备份与版本控制完全由用户自己负责，skill 只告知待改清单并提醒"请自行备份"，不执行任何备份动作
- **回滚由用户主导**：只提示回滚命令，不代为执行任何 git 写命令

## 7. WorkBuddy 沙箱下的 .work/ 清理限制

- **现象**：在 WorkBuddy sandbox 内执行 `shutil.rmtree('.work/')` 会被 safe-delete 拦截，报错 `windows-sandbox-recycle-bin-unavailable`（沙箱内回收站不可用，safe-delete fail-closed 拒绝删除）
- **影响范围**：仅影响 `.work/` 清理步骤，**不影响客户项目安全**（`.work/` 在 skill 目录下，不在客户项目内）
- **为什么不阻塞**：三个脚本默认覆盖写入（`write_text` 覆盖同名文件），`.work/` 里的 skeleton.json / batch.txt / chunks.json 不会积累垃圾，下次运行自动覆盖
- **如需彻底清理**：① 在 Bash 工具中授权命令（escalation）后执行清理命令；② 或提示用户手动删除 `.work/` 目录
- **设计决策**：v4.0 选择"覆盖写入 + 尽力清理"策略，而非"强制清理"——因为 safe-delete 是平台行为，skill 无法绕过；覆盖写入保证了即使 .work/ 残留也不会造成实际问题

## 8. quick_start_files 启发式限制

skeleton.json 的 `quick_start_files` 字段基于 dependency_links 自动算出"快速上手 3 文件"，有以下启发式限制：

- **入口识别依赖命名特征**：算法优先选名字含 Controller/Router/Main/App/Index 等的文件作为入口。若项目用非主流命名（如入口叫 `BizFacade`、`Action`、`View`、`Routes`），可能漏选；退化为入度=0 时可能误选 Impl 等实现类（Spring 等框架里 Controller 依赖接口而非 Impl，静态分析建不出 Controller→Impl 边，Impl 入度=0 是假象）
- **中间节点选入口直接依赖里入度最高的**：可能选到枚举/DTO 等被广泛引用的底层类，而非 Service 等业务中间层。LLM 在 ZHIDAO 里可补充语义说明
- **核心/补充选全局入度排序**：可能是工具类/常量类/枚举而非业务核心。LLM 应结合 `reason` 字段判断
- **依赖 dependency_links 的准确性**：§2 列出的依赖边限制（多态/接口/wildcard import/动态分发）都会影响 quick_start_files 的质量

算法是数据驱动的近似，不保证完美——但比 LLM 凭感觉写阅读路径更可靠。LLM 拿到 quick_start_files 后应结合项目语义在 ZHIDAO 里组织叙述，而非机械照搬。
