---
name: code-Explain-expert
description: 通读大型项目源码，生成项目导航文档（ZHIDAO.md）、README、Description（中英双版），再批量添加高质量"意图级"代码注释，让新人阅读陌生/自有项目像看带批注和地图的书。Use when the user asks to add intent-level Explains across a whole project. 触发场景：①"帮我通读这个项目，给所有核心业务模块加上中文业务注释"；②"帮我把 XX 包/模块下所有类深度注释一遍"；③"追踪 XX 接口从 Controller 到数据库的完整调用链并加注释"；④"帮我给这个项目写个 README"；⑤"帮我写个项目简介 / Description"。适用于存在标准项目结构（pom.xml / build.gradle / package.json / go.mod / pyproject.toml 等）的多文件工程，兼容 Java/Python/JS/TS 等主流语言。不触发：单段代码讲解、代码重构、运行故障修复、无项目结构的单文件脚本。
---

# Code Explain Expert

## Goal
用「先宏观后微观」的方式让大模型完整理解项目后，产出①结构化导航文档 ZHIDAO.md（项目地图）、②可选的 README.md（项目门面）与 Description.md（中英双版项目名片）、③逐文件意图级注释（代码批注），把陌生项目变成可通读的"带批注实体书"。核心承诺：**只加注释，绝不改逻辑；写入前必先告知用户待改清单并等确认**。

## Workflow
五阶段流水线，**Step 0 是写入前确认护栏，每次必做，先于一切写入**：

### Step 0：写入前确认护栏（告知 + 跳过清单）
- **写入前必须告知用户并等确认**（不替用户做备份决策）：
  - 列出本次将修改的文件清单（含行数估计与意图注释条数预估）。
  - 提醒用户："成熟程序员动项目前请按你自己的方式做好备份/版本控制（git commit、拷贝副本、IDE 本地历史等均可），本 skill 不替你备份、也不动你的 git 状态。"
  - 用户确认后再写。
- **跳过清单**（一律不读不注）：测试（test/tests/spec/__tests__/e2e）、配置、文档、静态资源、构建产物（target/dist/node_modules/.git/__pycache__）。

### Step 1：勘察（Scout）
- 跑 `scripts/extract_skeleton.py --root <项目根>`，提取全仓库骨架 JSON。**自动落盘到 skill 目录下 `.work/skeleton.json`**（不污染客户项目），LLM 用 Read 工具读取。
- **绝不跳过此步直接读单个源码文件**——骨架 JSON 是后续决策的全局上下文。

### Step 2：宏观导航（全量模式必需）
- 基于 skeleton.json（Step 1 读进上下文的），按 `navigation-guide.md`（10 章黄金模板）生成 `ZHIDAO.md`，输出到**目标项目根目录**（这是用户要的产物，不是中间文件）。
- 先输出给用户确认，再进入注释环节。

### Step 2b：README 生成（可选，用户要求时做）
- 基于 skeleton.json + ZHIDAO.md（若已生成），按 `readme-guide.md`（黄金门面模板：居中头部+徽章、mermaid 架构图、emoji 章节头、编号快速开始、折叠收纳长内容，150-400 行）生成或更新 `README.md`，输出到**目标项目根目录**；风格把握不准时对照 `samples/README/README1.md`（产品型）与 `samples/README/README2.md`（学习型）。
- **已有 README 时绝不覆盖**（除非用户明确要求重写）；"更新/补全"模式保留原文，只补缺失章节，新增内容用 `<!-- CC: 新增 -->` 标记。
- 用户没提 README 就不动——README 是用户的地盘，不请自来就是越权。

### Step 2c：Description 生成（可选，用户要求时做）
- 基于 skeleton.json + ZHIDAO.md（若已生成），按 `description-guide.md` 生成 `Description.md`（中英双版，各 300-350 字符），输出到**目标项目根目录**。
- **已有 Description.md 时绝不覆盖**（除非用户明确要求重写）。
- 用户没提 Description 就不动。

### Step 3：精准注释（核心循环）
- 用 skeleton.json（`.work/skeleton.json`）作全局上下文决策优先级 → `scripts/fetch_sources.py --root <项目根> --files <逗号分隔路径>` 按优先级分批捞取完整源码。**自动落盘到 `.work/batch.txt`**，LLM 用 Read 工具读取 → 逐文件生成意图级注释并写回。
- 也可用 `--from-skeleton`（不传路径默认读 `.work/skeleton.json`）+ `--ids` 或 `--top` 按骨架索引捞取。
- **超大文件**（> 500 行）：先跑 `scripts/bigfile_split.py --file <file> --max-lines 800 --overlap 40` 切块。**自动落盘到 `.work/chunks.json`**（仅含行号区间），LLM 用 Read 工具读取，再按 offset/limit 逐块读源文件注释。

### 完成后清理
- 所有注释完成后，**建议**清理 skill 目录下 `.work/` 目录（中间产物是一次性的，不保留）。三个脚本默认覆盖写入，`.work/` 残留不会积累垃圾，也不影响客户项目安全。
- 清理命令（用 Python；WorkBuddy sandbox 下 `shutil.rmtree` 可能被 safe-delete 拦截，需授权 Bash 命令后执行）：

  ```bash
  python -c "import shutil, os
  p = r'<skill目录>/.work'
  try:
      shutil.rmtree(p)
      print('cleaned')
  except Exception as e:
      print(f'清理被拦截: {e}。.work/ 在 skill 目录下不影响客户项目，下次运行会覆盖；如需彻底清理请授权 Bash 命令后重试。')"
  ```

## Decision Tree
- **模式选择**（先分类再行动）：
  - 全量填充（场景 A）→ Step 0→1→2→（2b 可选）→3 全跑，按模块分批
  - 定向攻坚（场景 B）→ `extract_skeleton.py --root <项目根> --path <目标目录>`，跳过全仓导航，直接 Step 3
  - 链路追踪（场景 C）→ 先 `extract_skeleton.py`，从 skeleton.json 的 `dependency_links` 沿调用链追踪，仅注释链路节点文件
  - README 生成（场景 D）→ 先 `extract_skeleton.py`，基于 skeleton.json 按 `readme-guide.md` 生成/更新 README.md（黄金门面模板：徽章+mermaid+emoji 章节+折叠，150-400 行；已有 README 绝不覆盖，除非用户要求重写）
  - Description 生成（场景 E）→ 先 `extract_skeleton.py`，基于 skeleton.json 按 `description-guide.md` 生成 Description.md（中英双版各 300-350 字符，已有绝不覆盖）
- **优先级决策**（用 skeleton.json 字段）：
  - 出度高的文件（Controller/入口）→ 先注释，读者先看到入口
  - 入度高的文件（Service/Domain）→ 核心业务，优先
  - `existing_Explain_ratio < 0.1` 的文件 → 注释真空区，优先
  - 跳过：配置文件、文档、静态资源、测试（脚本已过滤）
- **语言分支**：Java / Python / JS / TS / Go → 读 `language-adaptation.md` 对应章节
- **导航文档**：生成 ZHIDAO.md 前读 `navigation-guide.md`（唯一权威：10 章黄金模板 + 风格特征 + 验收清单）
- **README**：生成 README.md 前读 `readme-guide.md`（黄金门面模板 + 两类场景 + 三产物定位区别 + 已有 README 绝不覆盖策略），风格对照 `samples/README/`
- **Description**：生成 Description.md 前读 `description-guide.md`（300-350 字符中英双版 + 4 句话结构 + 纯文本无 markdown）
- **注释风格**：生成注释前读 `comment-style-guide.md`（唯一权威：用户黄金风格 + 意图级底线 + 正反例速查 + 防编造规则）；风格把握不准时对照三套黄金样例 `samples/Gewu-Deep-Research/`（Agent 编排型）、`samples/Code-Probe/`（业务服务型）与 `samples/Mall-Order/`（Java Spring Boot 业务服务型：Controller/Service/Mapper 分层、幂等/防超卖/状态机 CAS、接口与实现注释分工），导航文档对照 `samples/ZHIDAO.md`
- **超大项目**（skeleton.json `total_files > 50` 或源码总量 > 1MB）→ 读 `orchestration-guide.md` 的批处理策略，分阶段执行，每轮只处理 3-5 个文件
- **大文件**（单文件 > 500 行）→ 跑 `scripts/bigfile_split.py` 切块，逐块注释（先类/方法注释，行内注释第二遍补）
- **流水线失败** → 查 `orchestration-guide.md` 末尾"失败排查表"

## Constraints
红线规则（不可违反）：

**v4.0 客户项目保护红线（最高优先级，违反即事故）**：
- **绝不执行任何 git 写操作**：`git commit` / `git push` / `git stash` / `git branch` / `git checkout` / `git tag` / `git reset` / `git add` / `git merge` / `git rebase` 等一切会修改 git 状态或历史的命令，一律禁止。只允许 git 只读操作（`git status` / `git diff` / `git log` / `git show` / `git reflog` / `git branch -a` / `git cat-file`），仅用于变更检测与自查。
- **绝不在客户项目目录内造任何缓存/备份/中间文件**：禁止造 `.bak` / `.bak-<timestamp>` / `.cc_hash.json` / `.cc_split/` 等任何文件。所有中间产物（skeleton.json / batch.txt / chunks.json）**自动落盘到 skill 目录下 `.work/` 子目录**，不写到客户项目；完成后清理 `.work/`。
- **绝不把客户项目代码上传到任何远端**：禁止 push 到 GitHub / GitLab / Gitee / 内部 Git 服务器，禁止上传到网盘 / 对象存储 / paste 服务。客户项目代码只在本机处理。
- **备份与版本控制完全由用户自己负责**：成熟程序员动项目前自有备份方式（git commit、拷贝副本、IDE 本地历史、外部快照等），skill 不替用户做这个决策、不替用户执行备份。Step 0 只**告知**用户即将修改的文件清单并提醒"请自行备份"，不执行任何备份动作。

**注释质量红线**：
- **批量写回前向用户确认**：告知将修改的文件清单与风险；用户未确认不得批量写。本 skill 默认直接写入源文件，因此确认环节不可省。
- **只加注释，永不修改业务逻辑代码**；发现疑似 bug 只写注释标注（如 `// [注意] 潜在NPE：...`），禁止顺手修复。
- **不覆盖已有有效注释**：原文件已存在的注释（含 TODO、说明性注释、英文 docstring）一律保留；`extract_skeleton.py` 输出的 `existing_Explain_ratio` 用于判断是否需要补充。已有注释质量差时保留原文，下方追加意图级注释并用 `[补充]` 前缀标注。
- **不编造**：遇到读不懂的代码，注释写 `// 待确认：此处逻辑疑似 X，但未找到对应业务文档，需与负责人核实`，而非臆测业务意图。
- **业务意图优先**：注释解释"为什么这么做、业务上是什么、隐藏约束是什么"，拒绝流水账式复述代码。
- **先宏观后微观**：必须先生成/确认 ZHIDAO.md，再开始逐文件注释；未生成导航文档不得直接进入注释环节（定向攻坚模式除外，但需先说明目标模块在项目中的位置）。
- **跳过非源码文件**：配置文件、文档、静态资源、测试代码、构建产物一律不加注释（识别逻辑内置在 `extract_skeleton.py`）。
- **上下文预算**：每轮注释最多 3-5 个文件，防止信息过载；超大项目分阶段执行并报告进度。
- **回滚由用户主导**：若用户要求回滚，**只提示**命令（git 仓库：`git checkout -- <files>` 或 `git restore <files>`，由用户自己执行），本 skill **绝不执行**任何 git 回滚命令；非 git 仓库提示用户从自己的备份恢复。

## Validation
- **Step 0 后**：已向用户告知待写入文件清单与"请自行备份"提醒，用户已确认（**不验证任何备份产物**——备份是用户的事，skill 不创建也不依赖）
- **Step 1 后**：`extract_skeleton.py` 退出码 0，skeleton.json 可解析，`total_files > 0`，`files[]` 不含测试/构建产物路径
- **Step 2 后**：ZHIDAO.md 含核心章节（第 1/3/4/5/9 章不可省），且按 `navigation-guide.md` 的 10 章黄金模板与风格特征生成
- **Step 2b 后**（若执行）：README.md 居中头部齐备（emoji 项目名 + 一句话定位 + 徽章行），至少 1 张可渲染的 mermaid 架构图，快速开始命令可复制即用，长内容已用 `<details>` 折叠，篇幅 150-400 行，项目结构章节指向 ZHIDAO.md，已有 README 未被覆盖（对照 `readme-guide.md` §8 验收清单）
- **Step 2c 后**（若执行）：Description.md 含中英双版，各 300-350 字符，4 句话结构齐备，纯文本无 markdown 语法（对照 `description-guide.md` §8 验收清单）
- **Step 3 后**：抽查 2-3 个已注释文件，确认
  ①注释为意图级非流水账（对照 `comment-style-guide.md` §5 正反例速查）
  ②原逻辑代码零改动（`git diff --stat` 只显示注释行新增；非 git 仓库用 `fetch_sources.py` 重新捞原文件比对）
  ③已有注释（含英文 docstring）未被覆盖
  ④无 `// 待确认` 之外的臆测性业务描述（防编造自检）
- **完成标准**：所有目标文件注释完成，ZHIDAO.md（与 README.md / Description.md，如用户需要）已生成，`.work/` 目录已清理，向用户报告修改文件清单与遗留风险（含 `// 待确认` 标记的待核实点）

## Resources
- **scripts/**：`extract_skeleton.py`（Step 1）/ `fetch_sources.py`（Step 3）/ `bigfile_split.py`（大文件）。三个脚本均默认自动落盘到 skill `.work/` 目录，不污染客户项目；完成后清理 `.work/`。
- **references/**：`navigation-guide.md`（Step 2 必读）、`readme-guide.md`（Step 2b 必读）、`description-guide.md`（Step 2c 必读）、`comment-style-guide.md`（Step 3 必读）、`language-adaptation.md`（语言分支）、`orchestration-guide.md`（流水线细节 + 失败排查）、`limitations.md`（已知限制）
- **samples/**：用户认可的黄金样例，风格把握不准时对照——注释三套：`references/samples/Gewu-Deep-Research/`（Agent 编排型）、`references/samples/Code-Probe/`（业务服务型）与 `references/samples/Mall-Order/`（Java Spring Boot 业务服务型）；README 两套：`references/samples/README/README1.md`（产品型）与 `README2.md`（学习型）；导航：`references/samples/ZHIDAO.md`
