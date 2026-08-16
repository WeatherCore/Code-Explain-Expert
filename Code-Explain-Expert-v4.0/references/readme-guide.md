# README 写作规范——唯一权威

> 生成或更新项目 README 时**只读本文件**，风格把握不准时对照 `samples/README/README1.md`（产品型黄金案例：透明 AI Agent 系统）与 `samples/README/README2.md`（学习型黄金案例：12306 高并发购票）。
> README 与 ZHIDAO.md 是配套产物但定位不同：README 是"项目门面"（给访客看，30 秒决定要不要深入了解），ZHIDAO.md 是"项目地图"（给开发者看，通读代码用）。

## 1. 定位区别（必须分清）

| 维度 | README.md | ZHIDAO.md | Description.md |
|---|---|---|---|
| 读者 | 项目访客 / GitHub 浏览者 / 接手评估者 | 要通读源码的开发者 | 列表浏览者 / 搜索者 |
| 篇幅 | 150-400 行（长内容用折叠收纳） | 300-800 行（详尽） | 300-350 字符（中英各一版） |
| 深度 | 是什么 / 怎么用 / 亮点在哪 | 为什么 / 机制 / 逐文件导读 | 是什么 + 技术栈（3-5 句） |
| 场景 | GitHub 首页 / 项目门面 | 开发者通读代码 | GitHub About / 卡片 / 简历 / 搜索摘要 |
| 格式 | Markdown（徽章 + mermaid + emoji 章节） | Markdown | 纯文本（无 markdown 语法） |
| 位置 | 项目根目录（覆盖或追加） | 项目根目录（新建） | 项目根目录（新建） |

> Description.md 的详细规范见 `description-guide.md`。

## 2. 黄金案例的两种场景（先分类再套模板）

| | README1.md（产品型） | README2.md（学习型） |
|---|---|---|
| 适用 | 有明确定位主张的工具/系统 | 教学价值高的重构/高并发/架构项目 |
| 头部亮点 | 定位宣言 blockquote（"它不做平台，只做你的数字副手"） | 双架构形态对比表 + 推荐学习路径 |
| 核心章节 | 三大设计支柱表、核心机制深度解释（ASCII 流程）、技能编写指南 | mermaid 时序图拆解核心链路、自研中间件能力表、学习路线建议 |
| 共同骨架 | 居中头部+徽章 / 功能全景 / mermaid 架构图 / 技术栈表 / 注释目录树 / 编号快速开始 / Roadmap / 居中尾部 | 同左 |

判断依据：项目有"设计哲学/产品主张"→ 偏 README1；项目价值在"架构与机制教学"→ 偏 README2。两者骨架一致，只是核心章节侧重不同。

## 3. 结构模板（按黄金案例骨架，板块可裁剪）

| # | 板块 | 内容要点 | 必需 |
|---|---|---|---|
| 1 | 居中头部 | `<div align="center">` 包裹：emoji 项目名（H1）→ 加粗一句话定位 → 副标题（英文 slogan 或技术栈一行）→ shields.io 徽章行 → 锚点导航行（快速开始 · 架构 · 技术亮点 · 项目结构）→ `</div>` + `---` | ✅ |
| 2 | 项目简介 / 定位宣言 | 2-4 段说清"这是什么、解决什么问题、最大特色"；产品型加 blockquote 宣言；多形态项目加对比表 + 💡 推荐路径 | ✅ |
| 3 | 设计支柱 / 核心亮点 | 产品型：2-4 列支柱对比表；学习型：mermaid `sequenceDiagram` 拆解核心链路 + 亮点 bullet（**加粗术语** — 机制 + 具体文件名） | ✅ |
| 4 | 架构总览 | mermaid `flowchart TB`（subgraph 分层：前端/后端/中间件/文件系统，节点标注端口与职责，连线标注协议） | ✅ |
| 5 | 功能全景 | emoji bullet：`- 💬 **流式对话** — SSE 逐 Token 输出，工具调用全程事件化`，每条 = 加结名词 + 一句能力说明 | ✅ |
| 6 | 技术栈 | 分类表格（语言/框架、存储、缓存、消息、监控…），或单行罗列放架构图下 | ✅ |
| 7 | 项目结构 | 带 emoji 分区注释的目录树（二级深度，逐行中文职责）；更细的内容（完整目录、API 一览、各服务核心包）用 `<details><summary>` 折叠 | ✅ |
| 8 | 快速开始 | 0️⃣-5️⃣ 编号 emoji 小节：环境要求表（组件-版本-默认地址）→ 初始化（命令 100% 可复制，Windows 差异注明）→ 修改配置 → 编译 → 启动（多方式用 `<details>`）→ 体验核心链路（编号调用真实接口） | ✅ |
| 9 | 配置说明 | 环境变量表（变量-必填-默认值-说明），末尾加 💡 最低可用配置提示 | ⬜ |
| 10 | 深入章节 | 核心机制深度解释（ASCII 流程图）/ 内置能力表 / 扩展编写指南（给 Agent 看的示例） | ⬜ |
| 11 | Roadmap | `- [x]` / `- [ ]` checkbox 列表，已完成 ✅ 待规划 ⬜ | ⬜ |
| 12 | 居中尾部 | `<div align="center">`：参与贡献（Fork → Branch → PR）+ License + 致谢/Star 号召 | ✅ |

## 4. 居中头部模板

```markdown
<div align="center">

# 🦞 {{项目名}}

**{{一句话定位：是什么 + 最大特色}}**

*{{英文 slogan（可选）}}* / {{核心技术栈一行}}

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-D4AF37?style=flat-square)](./LICENSE)

[快速开始](#-快速开始) · [架构总览](#-架构总览) · [技术亮点](#-核心技术亮点) · [项目结构](#-项目结构)

</div>

---
```

- 徽章选 4-8 个：语言、主框架、关键中间件、License（有 PRD/文档链亦可加）
- 无 License 文件时不放 License 徽章，尾部 License 行同步省略

## 5. 风格铁律（必须遵守）

1. **首屏 30 秒原则**：居中头部 + 简介 + 亮点必须让访客 30 秒内回答"这是什么、有什么特别的、怎么跑起来"；主体可以长，靠折叠收纳——**禁止**把长内容裸摊在主体里
2. **命令 100% 可复制**：快速开始每条命令直接粘贴可用（含 `pip install -r requirements.txt`、建库 SQL、`mysql < xx.sql`）；Windows 差异写清（`.venv\Scripts\activate`）
3. **亮点必须落点到文件/组件**：`Lua 脚本令牌桶限流（ticket_availability_token_bucket.lua）`——没有具体落点的亮点是空话，删
4. **mermaid 是架构标配**：模块关系用 `flowchart TB` + subgraph 分层；核心业务链路用 `sequenceDiagram` + autonumber + alt 分支；节点内标注端口/职责
5. **表格是信息密度担当**：对比、环境、变量、API、技术栈一律表格化，不用大段 prose
6. **长内容一律 `<details>`**：`<details><summary><b>📁 完整目录结构</b>（点击展开）</summary>…</details>`
7. **emoji 章节头**：`## ✨ 功能全景`、`## 🏗️ 架构总览`、`## 🚀 快速开始`——全文统一，导航锚点与章节标题严格对应
8. **中文为主，术语中英对照**：与 ZHIDAO.md 风格一致，`Agent（智能体）`
9. **项目结构章末指向 ZHIDAO.md**：必须有"逐文件深度导读见 [ZHIDAO.md](ZHIDAO.md)"
10. **篇幅 150-400 行**：低于 150 说明亮点没展开（补 mermaid/表格/折叠区）；超过 400 说明该折叠的没折叠（砍）

## 6. 已有 README 的处理策略（重要）

目标项目已有 README 时，**绝不覆盖**：

| 情况 | 处理 |
|---|---|
| 用户明确要"重写 README" | 先告知将覆盖的内容，用户确认后按本指南整体重写 |
| 用户要"更新 README" / "补全 README" | 保留原有内容，只补缺失板块、更新过时信息；新增内容用 `<!-- CC: 新增 -->` 标记；原 README 风格与本指南冲突时**尊重原文风格**，仅在其风格内补充 |
| 已有 README 且用户没提 README | 不动，只生成 ZHIDAO.md |

**红线**：绝不在用户没要求的情况下擅自改 README.md。

## 7. 数据来源（skeleton.json → README 板块）

| README 板块 | skeleton.json 来源 |
|---|---|
| 项目名 / 一句话定位 | `project_root` + 已有 README + ZHIDAO.md（若已生成）综合判断 |
| 徽章行 / 技术栈表 | `language_hint` + 构建标志文件（pom.xml / package.json / requirements.txt / go.mod）中的依赖与版本 |
| 架构 mermaid 图 | `modules` 分层 + `dependency_links` 出入度（高频被依赖 → 中间层；高出度 → 入口层） |
| 功能全景 / 核心亮点 | 入度最高的文件（核心业务）+ ZHIDAO.md 的架构章节 |
| 项目结构目录树 | `modules`（二级深度 + emoji 分区注释） |
| 快速开始 | 构建标志文件推断安装/编译/启动命令；`entry_points` 推断启动类与端口 |
| 配置说明表 | 配置文件（application.yaml / .env.example / config.py）中的配置项 |

## 8. 质量验收（生成后自检）

- [ ] 居中头部齐备：emoji 项目名 + 一句话定位 + 徽章行（+ 锚点导航）
- [ ] 简介能让外行 30 秒听懂"这是什么、有什么特别的"
- [ ] 至少 1 张 mermaid 架构图（`flowchart TB`），语法可渲染（subgraph / 连线标注正确）
- [ ] 每条功能/亮点 bullet 有 emoji + 加粗名词 + 落点说明；核心亮点落到具体文件/组件名
- [ ] 技术栈、配置、环境要求均已表格化
- [ ] 快速开始命令 100% 可复制，编号步骤完整（环境 → 初始化 → 配置 → 编译 → 启动 → 体验链路）
- [ ] 长内容（完整目录 / API 一览 / 各模块明细）用 `<details>` 折叠
- [ ] 目录树带 emoji 分区注释；项目结构章末指向 ZHIDAO.md
- [ ] Roadmap（如有）用 checkbox；尾部居中含贡献 + License + Star 号召
- [ ] 篇幅 150-400 行
- [ ] 已有 README 时未被覆盖（除非用户明确要求重写）
