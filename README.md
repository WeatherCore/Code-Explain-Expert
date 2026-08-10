# 📖 Code-Explain-Expert

> 让 AI 通读大型项目源码，生成 `ZHIDAO.md` 项目导航文档，再批量添加高质量「意图级」代码注释——把陌生/自有代码库变成一本带「批注」和「地图」的实体书。


> A WorkBuddy skill that reads through large codebases, generates a ZHIDAO.md navigation guide, and adds intent-level comments in batch — turning unfamiliar repos into an annotated book with a map.

## 它解决什么问题

大型单体/遗留仓库的常见痛点：新人拿到代码库不知道该从哪读起，单看某个文件不知道它为什么存在、业务意图是什么、有什么隐藏约束。

本 skill 用「先宏观后微观」的方式解决：

1. **宏观**：扫描全仓库骨架（类/方法签名、import、依赖关系，不含实现代码），生成 `ZHIDAO.md` 项目导航文档——技术栈、目录树、模块职责、依赖流向、推荐阅读路径，像一张项目地图。
2. **微观**：以骨架 JSON 为全局上下文决策注释优先级，分批捞取完整源码，为类/方法/关键逻辑行添加**意图级注释**——解释"为什么存在 / 解决什么业务问题 / 有什么约束与坑"，而不是流水账复述代码。

**核心承诺**：只加注释，绝不改逻辑；写入前必先备份；读不懂就标 `// 待确认`，绝不编造。

## 版本演进

| 版本 | 状态 | 说明 |
|---|---|---|
| `Code-Comment-Expert-v1.0` | 归档（可对比） | 首版：Java/Python 双语言、备份护栏、hash 兜底，但结构缺 Decision Tree/Validation、无黄金样例、无 openai.yaml |
| `Code-Comment-Expert-v2.0` | 归档（可对比） | 大改进：14 语言骨架、依赖图、黄金风格样例、教科书式六段结构；但丢失 v1 的备份护栏、bigfile_split、hash 兜底，Python 解析退化为正则 |
| **`Code-Explain-Expert-v3.0`** | **✅ 推荐使用** | 在 v2 基础上找回 v1 全部 5 处工程补强，修复 v2 的 9 个 bug，新增 `limitations.md` 诚实限制记录。通过 skill-creator-pro review：**0 high / 0 medium / 0 low** |

> v1.0 / v2.0 保留仅作版本对比与演进参考，实际使用请用 v3.0。

## 快速上手

### 1. 安装（二选一）

打开你正在用的 agent（Claude Code、Codex、Cursor、OpenClaw、Hermes、CodeBuddy、Workbuddy、Gemini CLI、OpenCode 等），告诉它：

```
帮我安装这个 skill：https://github.com/WeatherCore/Code-Comment-Expert
```

或者用通用 CLI 安装器（[WeatherCore/Code-Explain-Expert](https://github.com/WeatherCore/Code-Explain-Expert)，支持 55+ runtime）：

```bash
npx skills add WeatherCore/Code-Explain-Expert
```

它会自动识别你当前的 runtime 并把 skill 放到正确目录。需要指定时加 `-a claude-code` / `-a codex` / `-a cursor` / `-a openclaw` 等参数。


### 2. 触发（四种模式）

| 场景 | 用户怎么说 | Skill 做什么 |
|---|---|---|
| 全量填充 | 「通读这个项目，给核心业务模块加上中文业务注释」 | 勘察 → 生成 ZHIDAO.md → 分批注释全仓 |
| 定向攻坚 | 「把 payment 包下所有类深度注释一遍」 | 只扫目标目录 → 直接注释 |
| 更新维护 | 「合并了 feature/refactor，更新这 20 个文件的注释」 | Git diff / MD5 快照检测变更 → 只重注变更文件 |
| 链路追踪 | 「追踪用户下单接口从 Controller 到 DB 的完整调用链并加注释」 | 沿 dependency_links 追踪 → 只注释链路节点 |

### 3. 不触发（走普通问答）

- 单段代码讲解（无完整项目结构）
- 代码重构 / 改业务实现
- 运行故障修复 / 调试
- 多项目对比 / 技术选型

## 工作流概览（v3.0）

```
Step 0 安全护栏   写入前 git 建分支 / .bak 备份；跳过测试/配置/构建产物
   ↓
Step 1 勘察       extract_skeleton.py → skeleton.json（类/方法/import/依赖/注释率）
   ↓
Step 2 宏观导航   navigation-guide.md（10 章黄金模板）→ ZHIDAO.md，用户确认
   ↓
Step 3 精准注释   fetch_sources.py 分批捞源码 → 意图级注释写回（大文件先切块）
   ↓
Step 4 变更更新   detect_changes.py（git 优先，非 git 回退 MD5）→ 只重注变更
```

## 目录结构（v3.0）

```
Code-Explain-Expert-v3.0/
├── SKILL.md                      # 控制层：触发/工作流/决策树/约束/验证/资源
├── agents/openai.yaml            # UI 元数据
├── scripts/                      # 4 个确定性脚本（零外部依赖，纯标准库）
│   ├── extract_skeleton.py       #   骨架提取（14 语言，Python 用 ast 精准解析）
│   ├── fetch_sources.py          #   按优先级分批捞取完整源码
│   ├── bigfile_split.py          #   超大文件行级切块（800 行/块、40 行重叠）
│   └── detect_changes.py         #   变更检测（git diff / MD5 快照双模式）
├── references/                   # 按需加载的规范与样例
│   ├── navigation-guide.md       #   ZHIDAO.md 10 章黄金模板 + 验收清单
│   ├── comment-style-guide.md    #   意图级注释规范 + 正反例 + 防编造规则
│   ├── language-adaptation.md    #   5 语言注释语法
│   ├── orchestration-guide.md    #   流水线细节 + 批处理策略 + 失败排查表
│   ├── limitations.md            #   已知限制（诚实记录）
│   └── samples/                  #   用户认可的黄金样例
│       ├── ZHIDAO.md             #   29KB 完整 10 章导航样例
│       └── open_deep_research/   #   带拟人化比喻风格注释的源码样例
└── tests/                        # 验证用 fixtures 与样例输出
    ├── fixtures/                 #   sample-java + sample-py 最小工程
    └── out/ZHIDAO.example.md     #   10 章黄金模板示例输出
```

## 验证与质量

- **结构性校验**：`skill-creator-pro/scripts/review_skill.py` 三版实测——v1.0 = 2 high / 3 medium，v2.0 = 0/0/0，v3.0 = **0/0/0**
- **脚本端到端**：4 个脚本在 `tests/fixtures/` 上全部跑通（Java 3 文件 2 依赖边；Python 2 文件 1 依赖边，测试文件正确跳过）
- **Python 解析精度**：v3.0 用 `ast`（编译器级）替代 v2.0 的正则，在黄金样例上精准识别 10 个类与跨文件依赖

## 已知限制

骨架提取为结构推断（依赖图为启发式匹配，非精确调用图）；Java 泛型/Lombok/内部类、JS 箭头函数、动态分发/反射调用等无法 100% 精确。完整清单见 `references/limitations.md`。

## License

[MIT](LICENSE) © 2026 Weather-Report
