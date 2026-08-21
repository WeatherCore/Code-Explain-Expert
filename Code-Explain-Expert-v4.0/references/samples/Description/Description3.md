# Description

## 中文版

Code Explain Expert 是一套以客户项目零污染为最高原则的 AI 代码解读流水线，用于在严格不改动源码的前提下为大型多文件工程生成可读性产物。它通过写入前确认护栏、骨架提取、宏观导航、可选 README/Description 与精准注释五阶段工作，所有注释均为非破坏性追加，写入前必先告知待改清单并等待确认。工程由 extract_skeleton.py、fetch_sources.py、bigfile_split.py 三个 Python 脚本构成，约束 git 只读、缓存落盘 skill 目录、源码不出本机，兼容 Java、Python、JS、TS、Go。目标用户为对代码安全与可追溯性有强要求的工程团队，适用于接手、复盘、交接与审计等需在不触碰逻辑的情况下理解项目的场景。

## English

Code Explain Expert is an AI pipeline that adds readable docs without ever touching your source logic. Five stages—write-confirmation, skeleton extraction, macro nav, optional README/Description, comments—append only after confirm. Three Python scripts enforce git-read-only and local caching, supporting Java, Python, JS, TS, Go. It fits teams with strict safety needs for onboarding, review, audit.
