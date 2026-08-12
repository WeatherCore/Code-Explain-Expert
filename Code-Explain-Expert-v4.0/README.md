# Code-Explain-Expert

> 一个 WorkBuddy skill：通读大型项目源码，生成项目导航文档（ZHIDAO.md）、README、Description（中英双版），再批量添加「意图级」代码注释——把陌生项目变成可通读的"带批注实体书"。

**当前版本：v4.0（正式版）** ｜ 纯 Python 标准库实现，无外部依赖

---

## 功能特性

- **五阶段流水线**：勘察 → 宏观导航 → （可选 README / Description）→ 精准注释，先宏观后微观
- **三个产物**：`ZHIDAO.md`（项目地图，给开发者通读代码）+ `README.md`（项目门面，给访客 30 秒决策）+ `Description.md`（项目名片，中英双版各 300-350 字符）
- **意图级注释**：解释"为什么 / 业务语义 / 隐藏约束"，拒绝流水账式复述代码；保留原英文 docstring 不动
- **14 种语言骨架提取**：Java/Kotlin/Python/JS/TS/Go/Rust/C/C++/C#/PHP/Ruby/Swift（Python 用 ast 解析，其余正则）
- **v4.0 客户项目保护红线**：不 git 写、不污染客户项目、不上传远端、不替用户备份、不覆盖已有 README/Description

## 快速开始

### 安装

把整个目录放到 WorkBuddy 的 skills 路径下：

```bash
# 用户级（所有项目可用）
cp -r Code-Explain-Expert-v4.0 ~/.workbuddy/skills/code-comment-expert

# 或项目级（仅当前项目）
cp -r Code-Explain-Expert-v4.0 <项目>/.workbuddy/skills/code-comment-expert
```

### 触发

在 WorkBuddy 对话里直接说人话，skill 会自动匹配：

```
帮我把这个项目通读一遍，给所有核心业务模块加上中文意图级注释
追踪 XX 接口从 Controller 到数据库的完整调用链并加注释
帮我把 src/payment 包下所有类深度注释一遍
帮我给这个项目写个 README
帮我写个项目简介 / Description
```

### 验证安装

```bash
python tests/test_smoke.py    # 25 项冒烟测试应全过
```

## 项目结构

```
Code-Explain-Expert-v4.0/
├── SKILL.md                    # 控制层：工作流 / 决策树 / 红线 / 验收
├── agents/openai.yaml          # OpenAI agent 路由配置
├── scripts/                    # 执行层（纯标准库）
│   ├── extract_skeleton.py     #   提取项目骨架 JSON（类/方法/import/依赖边）
│   ├── fetch_sources.py        #   按优先级批量捞取完整源码
│   └── bigfile_split.py        #   超大文件行级切块
├── references/                 # 知识层（按需加载，不进主上下文）
│   ├── navigation-guide.md     #   ZHIDAO.md 10 章黄金模板
│   ├── readme-guide.md         #   README 7 章模板
│   ├── description-guide.md    #   Description 300-350 字符规范
│   ├── comment-style-guide.md  #   意图级注释风格 + 正反例
│   ├── language-adaptation.md  #   各语言注释语法
│   ├── orchestration-guide.md  #   流水线细节 + 失败排查
│   ├── limitations.md          #   已知限制
│   └── samples/                #   人工认可的黄金样例
└── tests/                      # 冒烟测试 + 样例 fixtures
```

## 技术栈

| 维度 | 选型 |
|---|---|
| 实现语言 | Python 3（仅标准库：`ast` / `argparse` / `json` / `re` / `pathlib`） |
| 外部依赖 | **无**（不需要 pip install 任何包） |
| 支持语言 | Java / Kotlin / Python / JS / TS / Go / Rust / C / C++ / C# / PHP / Ruby / Swift |
| 运行环境 | WorkBuddy（提供 LLM + 文件读写工具） |
| 中间产物落盘 | skill 目录下 `.work/`（完成后清理，不污染客户项目） |

## 版本演进：v3 → v4.0

v4.0 是在 v3 发生"客户项目被误传远端仓库"事故后的**安全加固正式版**。核心改造围绕"客户项目保护红线"展开。

| 维度 | v3（旧） | v4.0（正式版） |
|---|---|---|
| **git 操作** | 自动 `git checkout -b` 建分支、`git stash` | **绝不执行任何 git 写操作**，仅允许只读（status/diff/log） |
| **客户项目污染** | 往客户项目造 `.bak-<ts>` / `.cc_hash.json` / `.cc_split/` | **中间产物全部落 skill `.work/`**，完成后清理，客户项目零新增文件 |
| **备份策略** | skill 自动备份（替用户决策） | **备份与版本控制由用户自己负责**，skill 只告知待改清单 + 提醒"请自行备份" |
| **回滚** | skill 执行回滚命令 | **只提示命令，绝不代为执行** git 写命令 |
| **变更检测** | `detect_changes.py` 支持 git + 非 git（hash 快照） | **删除整个变更检测场景**（场景 C），skill 彻底不碰 git |
| **大文件切块** | 落盘切块文件到客户项目 `.cc_split/` | **切块清单只含行号区间**，`chunk_file` 始为 None，LLM 用 Read offset/limit 读源文件 |
| **远端上传** | 无明确禁止 | **明确禁止**上传到 GitHub/GitLab/Gitee/网盘/对象存储等任何远端 |
| **产物** | ZHIDAO.md + 注释 | ZHIDAO.md + **README.md** + **Description.md**（中英双版）+ 注释 |
| **场景数** | 4 个（A 全量 / B 定向 / C 变更 / D README） | 5 个（A 全量 / B 定向 / C 链路追踪 / D README / E Description） |
| **脚本数** | 4 个（含 detect_changes） | 3 个（删除 detect_changes） |
| **已有 README** | 可能覆盖 | **绝不覆盖**（除非用户明确要求重写） |

> 完整事故复盘与改造记录见 `.workbuddy/memory/2026-08-12.md`。

## 客户项目保护红线（v4.0 强制约束）

以下行为 v4.0 起一律禁止，违反即事故：

1. **不执行任何 git 写操作**（commit / push / stash / branch / checkout / tag / reset / add / merge / rebase 等）
2. **不在客户项目目录内造任何缓存/备份/中间文件**（`.bak` / `.cc_hash.json` / `.cc_split/` / `skeleton.json` / `batch.txt` / `chunks.json` 等，全部落 skill `.work/`）
3. **不把客户项目代码上传到任何远端**（GitHub / GitLab / Gitee / 内部 Git / 网盘 / 对象存储）
4. **不替用户做备份决策**（备份与版本控制完全由用户自己负责）
5. **回滚由用户主导**（只提示命令，不代为执行）
6. **已有 README / Description 绝不覆盖**（除非用户明确要求重写）

红线在 `SKILL.md` / `orchestration-guide.md` / `limitations.md` / `agents/openai.yaml` 四处一致声明，任一文件改动需同步其余三处。

## 验证与测试

```bash
# 冒烟测试：三脚本可执行性 + 客户项目零污染 + .work/ 生命周期
python tests/test_smoke.py

# 单脚本语法检查
python -m py_compile scripts/extract_skeleton.py scripts/fetch_sources.py scripts/bigfile_split.py
```

详细测试说明见 [tests/README.md](tests/README.md)。
