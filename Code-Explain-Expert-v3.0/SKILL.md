---
name: code-comment-expert
description: 通读大型项目源码，生成项目导航阅读文档（ZHIDAO.md），再批量添加高质量"意图级"代码注释，让新人阅读陌生/自有项目像看带批注和地图的书。Use when the user asks to add intent-level comments across a whole project. 触发场景：①"帮我通读这个项目，给所有核心业务模块加上中文业务注释"；②"帮我把 XX 包/模块下所有类深度注释一遍"；③"合并分支后，帮我把变更涉及的 N 个文件注释更新同步"；④"追踪 XX 接口从 Controller 到数据库的完整调用链并加注释"。适用于存在标准项目结构（pom.xml / build.gradle / package.json / go.mod / pyproject.toml 等）的多文件工程，兼容 Java/Python/JS/TS 等主流语言。不触发：单段代码讲解、代码重构、运行故障修复、无项目结构的单文件脚本。
---

# Code Comment Expert

## Goal
用「先宏观后微观」的方式让大模型完整理解项目后，产出①结构化导航文档 ZHIDAO.md（项目地图）和②逐文件意图级注释（代码批注），把陌生项目变成可通读的"带批注实体书"。核心承诺：**只加注释，绝不改逻辑；写入前必先备份**。

## Workflow
五阶段流水线，**Step 0 是安全护栏，每次必做，先于一切写入**：

### Step 0：安全护栏（备份 + 跳过清单）
- **写入前必须自动备份**：
  - 目标是 git 仓库 → 先 `git -C <root> checkout -b code-comment/<timestamp>` 建分支（推荐，便于整体回滚）；或 `git stash`。
  - 非 git 仓库 → 将每个待改文件复制为 `<file>.bak-<timestamp>`。
- **跳过清单**（一律不读不注）：测试（test/tests/spec/__tests__/e2e）、配置、文档、静态资源、构建产物（target/dist/node_modules/.git/__pycache__）。

### Step 1：勘察（Scout）
- 跑 `scripts/extract_skeleton.py --root <项目根> --out skeleton.json`，提取全仓库骨架 JSON + 读项目 README。
- **绝不跳过此步直接读单个源码文件**——骨架 JSON 是后续决策的全局上下文。

### Step 2：宏观导航（全量模式必需）
- 基于 skeleton.json，按 `navigation-guide.md`（10 章黄金模板）生成 `ZHIDAO.md`，输出到**目标项目根目录**。
- 先输出给用户确认，再进入注释环节。

### Step 3：精准注释（核心循环）
- 用 skeleton.json 作全局上下文决策优先级 → `scripts/fetch_sources.py` 按优先级分批捞取完整源码 → 逐文件生成意图级注释并写回。
- **超大文件**（> 500 行）：先跑 `scripts/bigfile_split.py --file <file> --max-lines 800 --overlap 40 --out chunks.json` 切块，逐块注释。

### Step 4：变更更新（场景 C 专用）
- 跑 `scripts/detect_changes.py --root <项目根> --out changes.json`，只对变动文件重跑 Step 3。
- 非 git 仓库自动回退 MD5 快照模式（首次返回全部源码文件，后续只返回哈希变动的）。

## Decision Tree
- **模式选择**（先分类再行动）：
  - 全量填充（场景 A）→ Step 0→1→2→3 全跑，按模块分批
  - 定向攻坚（场景 B）→ `extract_skeleton.py --root <项目根> --path <目标目录>`，跳过全仓导航，直接 Step 3
  - 更新维护（场景 C）→ 直接 Step 4：`detect_changes.py`，仅注释变动文件
  - 链路追踪（场景 D）→ 先 `extract_skeleton.py`，从 skeleton.json 的 `dependency_links` 沿调用链追踪，仅注释链路节点文件
- **优先级决策**（用 skeleton.json 字段）：
  - 出度高的文件（Controller/入口）→ 先注释，读者先看到入口
  - 入度高的文件（Service/Domain）→ 核心业务，优先
  - `existing_comment_ratio < 0.1` 的文件 → 注释真空区，优先
  - 跳过：配置文件、文档、静态资源、测试（脚本已过滤）
- **语言分支**：Java / Python / JS / TS / Go → 读 `language-adaptation.md` 对应章节
- **导航文档**：生成 ZHIDAO.md 前读 `navigation-guide.md`（唯一权威：10 章黄金模板 + 风格特征 + 验收清单）
- **注释风格**：生成注释前读 `comment-style-guide.md`（唯一权威：用户黄金风格 + 意图级底线 + 正反例速查 + 防编造规则）；风格把握不准时对照 `samples/open_deep_research/`（用户认可的原始黄金样例源码）与 `samples/ZHIDAO.md`（用户认可的黄金导航样例）
- **超大项目**（skeleton.json `total_files > 50` 或源码总量 > 1MB）→ 读 `orchestration-guide.md` 的批处理策略，分阶段执行，每轮只处理 3-5 个文件
- **大文件**（单文件 > 500 行）→ 跑 `scripts/bigfile_split.py` 切块，逐块注释（先类/方法注释，行内注释第二遍补）
- **流水线失败** → 查 `orchestration-guide.md` 末尾"失败排查表"

## Constraints
红线规则（不可违反）：
- **写入前必先备份**（Step 0）：git 仓库建分支 / 非 git 仓库 .bak 文件；未备份不得写入。
- **批量写回前向用户确认**：告知将修改的文件清单与风险；用户未确认不得批量写。本 skill 默认直接写入源文件，因此确认环节不可省。
- **只加注释，永不修改业务逻辑代码**；发现疑似 bug 只写注释标注（如 `// [注意] 潜在NPE：...`），禁止顺手修复。
- **不覆盖已有有效注释**：原文件已存在的注释（含 TODO、说明性注释、英文 docstring）一律保留；`extract_skeleton.py` 输出的 `existing_comment_ratio` 用于判断是否需要补充。已有注释质量差时保留原文，下方追加意图级注释并用 `[补充]` 前缀标注。
- **不编造**：遇到读不懂的代码，注释写 `// 待确认：此处逻辑疑似 X，但未找到对应业务文档，需与负责人核实`，而非臆测业务意图。
- **业务意图优先**：注释解释"为什么这么做、业务上是什么、隐藏约束是什么"，拒绝流水账式复述代码。
- **先宏观后微观**：必须先生成/确认 ZHIDAO.md，再开始逐文件注释；未生成导航文档不得直接进入注释环节（定向攻坚模式除外，但需先说明目标模块在项目中的位置）。
- **跳过非源码文件**：配置文件、文档、静态资源、测试代码、构建产物一律不加注释（识别逻辑内置在 `extract_skeleton.py`）。
- **上下文预算**：每轮注释最多 3-5 个文件，防止信息过载；超大项目分阶段执行并报告进度。
- **回滚保护**：若用户要求回滚，提示 `git checkout -- <files>`（git 仓库）或还原 `.bak` 文件（非 git）；本 skill 不执行 git 回滚命令（只读保护，避免误操作）。

## Validation
- **Step 0 后**：备份已建立（git 分支已切 / .bak 文件已生成），用户已确认待写入清单
- **Step 1 后**：`extract_skeleton.py` 退出码 0，skeleton.json 可解析，`total_files > 0`，`files[]` 不含测试/构建产物路径
- **Step 2 后**：ZHIDAO.md 含核心章节（第 1/3/4/5/9 章不可省），且按 `navigation-guide.md` 的 10 章黄金模板与风格特征生成
- **Step 3 后**：抽查 2-3 个已注释文件，确认
  ①注释为意图级非流水账（对照 `comment-style-guide.md` §5 正反例速查）
  ②原逻辑代码零改动（`git diff --stat` 只显示注释行新增）
  ③已有注释（含英文 docstring）未被覆盖
  ④无 `// 待确认` 之外的臆测性业务描述（防编造自检）
- **Step 4 后**：变动文件列表与 `detect_changes.py` 输出一致，无遗漏
- **完成标准**：所有目标文件注释完成，ZHIDAO.md 已生成，向用户报告修改文件清单与遗留风险（含 `// 待确认` 标记的待核实点）

## Resources
- **scripts/**：`extract_skeleton.py`（Step 1）/ `fetch_sources.py`（Step 3）/ `bigfile_split.py`（大文件）/ `detect_changes.py`（Step 4）
- **references/**：`navigation-guide.md`（Step 2 必读）、`comment-style-guide.md`（Step 3 必读）、`language-adaptation.md`（语言分支）、`orchestration-guide.md`（流水线细节 + 失败排查）、`limitations.md`（已知限制）
- **samples/**：`references/samples/ZHIDAO.md` 与 `references/samples/open_deep_research/` 是用户认可的黄金样例，风格把握不准时对照
