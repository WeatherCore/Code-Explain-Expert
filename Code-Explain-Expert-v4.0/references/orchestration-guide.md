# 五阶段流水线详解（Orchestrator 操作手册）

本文是 SKILL.md 工作流的分支细节。按当前模式读取对应阶段。

## 阶段 0：写入前确认护栏（所有模式的前置，先于一切写入）

**目的**：保证用户知情同意，跳过非源码文件。**v4.0 起不再替用户备份**——备份与版本控制由用户自己负责。

执行步骤：
1. **不执行任何 git 写操作，不造任何 .bak / .cc_hash.json / .cc_split 文件**。备份是用户的事，skill 只告知不替决策。
2. 跳过清单（不读不注）：测试目录（test/tests/spec/__tests__/e2e）、配置、文档、静态资源、构建产物（target/dist/node_modules/.git/__pycache__）。`extract_skeleton.py` 已内置过滤，但 LLM 在定向场景手动指定 `--files` 时需自查。
3. **批量写回前向用户确认**：列出本次将修改的文件清单（含行数估计与意图注释条数预估），并提醒"请按你自己的方式做好备份（git commit、拷贝副本、IDE 本地历史等均可），本 skill 不替你备份、也不动你的 git 状态"。用户确认后再写。

## 阶段 1：勘察（Scout）

**目的**：在不读实现代码的情况下，建立全仓库心智地图。

执行步骤：
1. `python scripts/extract_skeleton.py --root <项目根>`
   - **自动落盘到 skill 目录下 `.work/skeleton.json`**（不污染客户项目），LLM 用 Read 工具读取
   - 产出：模块分布、文件清单、每个文件的类/方法签名/imports/引用类型/依赖边/已有注释率
2. 读项目 README（若存在）与 `.work/skeleton.json` 的 `modules`、`dependency_links`
3. 识别：语言（`language_hint`）、总文件数、核心模块（依赖边入度最高的文件 = 被依赖最多 = 越底层越重要；出度高的 = 编排者）

**优先级决策输入**（给 LLM 的全局上下文 = 骨架 JSON + README）：
- 出度高的文件（Controller/入口）：先注释，读者先看到入口
- 入度高的文件（Service/Domain）：核心业务，优先但可稍后（理解其调用方后再注释更准）
- `existing_Explain_ratio` < 0.1 的文件：注释真空区，值得优先
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
- 每轮处理 3-5 个文件（大文件 > 500 行只处理 1 个）
- 每轮流程：
  1. 从优先级队列取下一批文件（从 `.work/skeleton.json` 的 `files` 数组提取路径）
  2. `python scripts/fetch_sources.py --root <项目根> --files <逗号分隔路径> --max-bytes 60000`（**自动落盘到 `.work/batch.txt`**，LLM 用 Read 工具读取；也可用 `--from-skeleton` + `--ids`/`--top` 按骨架索引捞取）
  3. 读取 `.work/batch.txt` 的源码批次，按 `references/Explain-style-guide.md` + `references/language-adaptation.md` 生成注释
  4. 用编辑工具逐文件写入（只新增注释行，不触碰逻辑行）
  5. 记录该文件已注释，更新进度
- 每完成 5 个文件自查：git 仓库用 `git diff --stat` 确认只有注释行新增、无逻辑变更；非 git 仓库用 `fetch_sources.py` 重新捞原文件比对

**大文件切块**（单文件 > 500 行）：
- 先跑 `python scripts/bigfile_split.py --file <file> --max-lines 800 --overlap 40`（**自动落盘到 `.work/chunks.json`**，不污染客户项目）
- **v4.0：切块清单 JSON 只含行号区间，`chunk_file` 字段始终为 None，不再落盘切块文件到客户项目**（v3 的 `.cc_split/` 已移除）
- 逐块注释：用 Read 工具按 `start_line`/`end_line` 的 offset/limit 读取源文件对应行区间，先类/方法注释（高价值），行内注释第二遍补充；相邻块共享 overlap 行保证连续性
- 写回时按原始行号区间映射，避免错位

**优先级队列维护**：骨架 JSON `files` 数组的索引即 ID。LLM 决策后可用 `--ids` 精确捞取，或将整个数组按重要性重排后 `--top N` 顺序捞取。

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
| fetch_sources 提示"因 max-bytes 截断" | 正常行为，被列出的文件留到下一批；用更大的 `--max-bytes` 或更小的 `--files` 范围 |
| bigfile_split 切块数为 1 | 文件未超 `--max-lines` 阈值，无需切块；正常情况 |
| bigfile_split 输出 chunk_file 为 None | v4.0 正常行为：切块清单自动落盘到 `.work/chunks.json`，`chunk_file` 字段为 None（不再落盘到客户项目），用 Read 工具读 `.work/chunks.json` 拿行号区间，再按 offset/limit 读源文件 |
| 注释写入后 git diff 显示逻辑变更 | 立即停止，**提示用户**执行 `git checkout -- <file>` 或 `git restore <file>`（由用户自己执行，skill 不代为执行），重新只加注释 |
| 用户要求回滚 | **只提示命令，不执行**：git 仓库提示 `git checkout -- <files>` 或 `git restore <files>`（由用户执行）；非 git 仓库提示用户从自己的备份恢复。本 skill 绝不执行任何 git 写命令 |

## 完成后清理

所有注释 / ZHIDAO.md / README.md / Description.md 生成完成后，**建议清理 skill 目录下 `.work/` 目录**（中间产物是一次性的，不保留）：

```bash
python -c "import shutil, os
p = r'<skill目录>/.work'
try:
    shutil.rmtree(p)
    print('cleaned')
except Exception as e:
    print(f'清理被拦截: {e}。.work/ 在 skill 目录下不影响客户项目，下次运行会覆盖写入；如需彻底清理请授权 Bash 命令后重试。')"
```

**⚠️ WorkBuddy 沙箱限制**：`shutil.rmtree` 在 sandbox 下可能被 safe-delete 拦截（报错 `windows-sandbox-recycle-bin-unavailable`），需授权 Bash 命令（escalation）后才能执行。这不影响客户项目安全（`.work/` 在 skill 目录下，不在客户项目内），且三个脚本默认覆盖写入（同名文件不积累垃圾），`.work/` 残留不会造成实际问题。如需彻底清理：① 在 Bash 工具中授权命令后重试；② 或提示用户手动删除 `.work/` 目录。

中间产物（skeleton.json / batch.txt / chunks.json）是一次性的，不保留。`.work/` 目录里没有任何需要持久化的内容——所有最终产物（ZHIDAO.md / README.md / Description.md / 注释）都已写到客户项目根目录或源文件里。
