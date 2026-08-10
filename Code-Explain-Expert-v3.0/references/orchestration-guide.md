# 五阶段流水线详解（Orchestrator 操作手册）

本文是 SKILL.md 工作流的分支细节。按当前模式读取对应阶段。

## 阶段 0：安全护栏（所有模式的前置，先于一切写入）

**目的**：保证写入可回滚，跳过非源码文件。

执行步骤：
1. 判断目标根是否 git 仓库：
   - 是 → `git -C <root> checkout -b code-comment/<timestamp>`（推荐，整体回滚一行命令）；或 `git stash`
   - 否 → 在批量写回前，将每个待改文件复制为 `<file>.bak-<timestamp>`
2. 跳过清单（不读不注）：测试目录（test/tests/spec/__tests__/e2e）、配置、文档、静态资源、构建产物（target/dist/node_modules/.git/__pycache__）。`extract_skeleton.py` 已内置过滤，但 LLM 在定向场景手动指定 `--files` 时需自查。
3. **批量写回前向用户确认**：列出本次将修改的文件清单（含行数估计与意图注释条数预估），用户确认后再写。

## 阶段 1：勘察（Scout）

**目的**：在不读实现代码的情况下，建立全仓库心智地图。

执行步骤：
1. `python scripts/extract_skeleton.py --root <项目根> --out skeleton.json`
   - 产出：模块分布、文件清单、每个文件的类/方法签名/imports/引用类型/依赖边/已有注释率
2. 读项目 README（若存在）与骨架 JSON 的 `modules`、`dependency_links`
3. 识别：语言（`language_hint`）、总文件数、核心模块（依赖边入度最高的文件 = 被依赖最多 = 越底层越重要；出度高的 = 编排者）

**优先级决策输入**（给 LLM 的全局上下文 = 骨架 JSON + README）：
- 出度高的文件（Controller/入口）：先注释，读者先看到入口
- 入度高的文件（Service/Domain）：核心业务，优先但可稍后（理解其调用方后再注释更准）
- `existing_comment_ratio` < 0.1 的文件：注释真空区，值得优先
- 跳过：配置文件、文档、静态资源、测试（脚本已过滤）

**定向攻坚模式**（场景 B）：`--path <目标目录>` 只扫目标；同时读一次全仓 `modules` 概要定位目标模块在整体中的位置（先宏观定位，再微观精读）。

## 阶段 2：生成 ZHIDAO.md（全量模式必需）

1. 按 `references/navigation-guide.md`（唯一权威：10 章黄金模板 + 风格特征 + 验收清单）生成；风格把握不准时对照 `references/samples/ZHIDAO.md`（用户认可的黄金样例）
2. 五个通用板块缺一不可：技术栈识别 / 目录树 / 模块职责 / 依赖流向 / 推荐阅读路径（在 10 章模板中的落点见 navigation-guide.md §1.2）
3. 依赖流向数据来源：`dependency_links` + 模块推断，可手绘 ASCII 图或 Mermaid
4. 推荐阅读路径：从低依赖到高依赖（先工具/领域模型，后 Service，最后 Controller/入口），标出"快速上手 3 文件"
5. 输出 `ZHIDAO.md` 到项目根目录，交付用户确认后再进入阶段 3

## 阶段 3：精准注释（核心循环）

**批处理协议（上下文预算红线）**：
- 每轮只处理 1-3 个文件（小方法/小文件）或 1 个文件（大文件 > 500 行）
- 每轮流程：
  1. 从优先级队列取下一批文件
  2. `python scripts/fetch_sources.py --root <项目根> --from-skeleton skeleton.json --ids <索引> --out batch.txt`
     （或 `--files a.java,b.java` 直接指定；带 `--max-bytes 60000` 控制批次字节上限）
  3. 读取 batch.txt，按 `references/comment-style-guide.md` + `references/language-adaptation.md` 生成注释
  4. 用编辑工具逐文件写入（只新增注释行，不触碰逻辑行）
  5. 记录该文件已注释，更新进度
- 每完成 5 个文件做一次 `git diff --stat` 自查：确认只有注释行新增、无逻辑变更

**大文件切块**（单文件 > 500 行）：
- 先跑 `python scripts/bigfile_split.py --file <file> --max-lines 800 --overlap 40 --out chunks.json`
- 切块清单 JSON 每项含 `chunk_file` 路径（落盘到 `<file_dir>/.cc_split/<base>.NNN.txt`）
- 逐块注释：先类/方法注释（高价值），行内注释第二遍补充；相邻块共享 overlap 行保证连续性
- 写回时按原始行号区间映射，避免错位

**优先级队列维护**：骨架 JSON `files` 数组的索引即 ID。LLM 决策后可用 `--ids` 精确捞取，或将整个数组按重要性重排后 `--top N` 顺序捞取。

## 阶段 4：变更更新（场景 C）

1. `python scripts/detect_changes.py --root <项目根> --out changes.json`
   - git 仓库：默认对比工作区未提交变更；无变更时自动回退 `HEAD~1`；可指定 `--base origin/main` 等任意 ref
   - 非 git 仓库：自动回退 MD5 快照模式（首次运行返回全部源码文件，后续运行只返回哈希变动的）；缓存文件 `.cc_hash.json` 落在项目根
   - 可用 `--mode git|hash|auto` 强制指定模式
2. 对 `all_source` 列表中的每个文件：
   - 新增（added）/ 修改（modified）：重新生成注释（保留原有注释，只补新逻辑的意图注释）
   - 删除（deleted）：无需处理
3. 若变更文件较多（> 10），同样走批处理协议
4. 更新 ZHIDAO.md 中受影响模块的描述（如模块职责变化）

## 超大项目策略（骨架 > 50 文件 或 源码 > 1MB）

- 分批全量：每轮处理 3-5 个文件，完成一轮向用户报告进度（如"已完成 3/42 个核心文件"）
- 分阶段：先核心模块（依赖边入度/出度 Top 20），后边缘模块；边缘模块可在用户确认后跳过
- 每轮开始前重读骨架 JSON 的 `modules` 概览（上下文可丢弃已处理文件的源码，保留骨架）
- 单文件 > 500 行：跑 `bigfile_split.py` 切块（默认 800 行/块、40 行重叠），先类注释+方法注释（高价值），行内注释第二遍补充

## 失败排查

| 现象 | 排查 |
|---|---|
| extract_skeleton 输出 0 文件 | 项目无标准构建标志 / 源码扩展名不在支持列表 / 目录被 SKIP_DIRS 命中 |
| 依赖边为 0 | 项目类型名提取失败（少见语言），依赖图降级为模块级（模块职责仍可用） |
| fetch_sources 捞取为空 | 路径写错（用骨架 JSON 的 `path` 字段，不是绝对路径）；或所有文件都超过 `--max-bytes` 上限，需调大该值 |
| fetch_sources 提示"因 max-bytes 截断" | 正常行为，被列出的文件留到下一批；用更大的 `--max-bytes` 或更小的 `--ids` 范围 |
| bigfile_split 切块数为 1 | 文件未超 `--max-lines` 阈值，无需切块；正常情况 |
| detect_changes 报"回退到 MD5 快照模式" | 项目非 git 仓库；首次运行返回全部源码文件（added 列表）属正常，第二次起才返回真实变动 |
| detect_changes 报"非 Git 仓库且 --mode=git" | 用 `--mode auto` 或 `--mode hash` 即可 |
| 注释写入后 git diff 显示逻辑变更 | 立即停止，回滚该文件（`git checkout -- <file>` 需用户确认），重新只加注释 |
| 用户要求回滚 | git 仓库：提示 `git checkout -- <files>` 或 `git checkout code-comment/<timestamp>^`；非 git：还原 `.bak-<timestamp>` 文件；本 skill 不执行回滚命令 |
