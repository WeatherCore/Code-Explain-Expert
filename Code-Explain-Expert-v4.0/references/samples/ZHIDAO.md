# 📖 Open Deep Research 项目导读指南

> 本文件是 `open_deep_research` 项目的中文导读，帮助你从零开始理解这个深度研究 Agent 的架构、代码和运行方式。

---

## 目录

1. [这个项目是干什么的？](#1-这个项目是干什么的)
2. [核心概念速览](#2-核心概念速览)
3. [项目目录结构详解](#3-项目目录结构详解)
4. [运行流程全景图](#4-运行流程全景图)
5. [逐文件代码导读](#5-逐文件代码导读)
6. [关键设计模式解析](#6-关键设计模式解析)
7. [配置系统详解](#7-配置系统详解)
8. [如何运行和测试](#8-如何运行和测试)
9. [复刻建议与学习路线](#9-复刻建议与学习路线)
10. [常见问题](#10-常见问题)

---

## 1. 这个项目是干什么的？

**一句话总结**：你给它一个研究问题（比如"对比 OpenAI 和 Anthropic 的 AI 安全策略"），它会自动帮你搜索互联网、整理资料、生成一份带有引用来源的深度研究报告。

**更具体地说**：

```
用户提问 → AI 澄清问题 → 制定研究计划 → 多个子 Agent 并行搜索
    → 压缩研究结果 → 生成最终报告（带引用来源）
```

它不是一个简单的问答机器人，而是一个 **多 Agent 协作系统**，模拟了人类研究员的工作方式：
- 先理解你要研究什么
- 把大课题拆成小课题
- 分头去搜索和阅读
- 汇总所有发现
- 写一份结构化的报告

---

## 2. 核心概念速览

在开始读代码之前，你需要了解以下关键概念：

### 2.1 LangGraph（工作流引擎）

LangGraph 是 LangChain 团队开发的工作流编排框架。你可以把它理解为一个 **"有向图执行引擎"**：
- **节点（Node）**：每个节点是一个函数（如"搜索""压缩""生成报告"）
- **边（Edge）**：定义节点之间的执行顺序（如 `搜索 → 压缩 → 报告`）
- **状态（State）**：在节点之间传递的共享数据

```python
# 伪代码示例
graph = StateGraph(MyState)
graph.add_node("step1", step1_function)
graph.add_node("step2", step2_function)
graph.add_edge(START, "step1")
graph.add_edge("step1", "step2")
graph.add_edge("step2", END)
```

### 2.2 Agent（智能体）

Agent = LLM + 工具（Tools）。LLM 决定调用哪个工具、传什么参数，工具执行后把结果返回给 LLM，LLM 再决定下一步。这就是所谓的 **ReAct 模式**（Reasoning + Acting）。

### 2.3 MCP（Model Context Protocol）

MCP 是一种协议标准，让 Agent 能够连接外部工具和服务。比如你可以配置一个 MCP 服务器，让 Agent 能访问数据库、API 或其他外部资源。

### 2.4 子图（Subgraph）

本项目用了 **嵌套图** 的设计：
- **主图**（deep_researcher）：负责整体流程
- **主管子图**（supervisor_subgraph）：管理研究任务分配
- **研究员子图**（researcher_subgraph）：执行具体搜索

---

## 3. 项目目录结构详解

```
open_deep_research/
│
├── README.md                          # 项目说明
├── ZHIDAO.md                          # 导读文件
├── pyproject.toml                     # Python 项目配置（依赖、元数据）
├── langgraph.json                     # LangGraph 配置文件
├── uv.lock                            # 依赖锁定文件
├── LICENSE                            # MIT 开源许可
├── .env.example                       # 环境变量模板
│
├── src/
│   ├── open_deep_research/            # ⭐ 核心实现（重点阅读）
│   │   ├── deep_researcher.py         # 主图定义（入口文件）
│   │   ├── configuration.py           # 配置管理
│   │   ├── state.py                   # 状态定义
│   │   ├── prompts.py                 # 所有提示词模板
│   │   ├── utils.py                   # 工具函数和辅助方法
│   │   └── files/                     # 研究输出和示例文件
│   │
│   └── security/                      # 🔐 安全模块
│       └── auth.py                    # 认证处理
│
├── tests/                             # 🧪 评估脚本
│   ├── run_evaluate.py                # 主评估脚本
│   ├── evaluators.py                  # 评估函数
│   ├── prompts.py                     # 评估提示词
│   └── ...
│
└── examples/                          # 📋 示例输出
    ├── arxiv.md                       # ArXiv 研究示例
    ├── pubmed.md                      # PubMed 研究示例
    └── inference-market.md            # 推理市场分析
```

---

## 4. 运行流程全景图

整个系统由 **4 个主要阶段** 组成，按顺序执行：

```
┌─────────────────────────────────────────────────────────────────┐
│                    用户输入研究问题                               │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  阶段 1: clarify_with_user（用户澄清）                           │
│  ─────────────────────────────────────                          │
│  • 分析用户的问题是否足够清晰                                      │
│  • 如果不清晰 → 向用户提问 → 等待回答 → 重新开始                    │
│  • 如果清晰 → 进入下一阶段                                        │
│  • 可通过 allow_clarification=False 跳过                         │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  阶段 2: write_research_brief（制定研究计划）                      │
│  ────────────────────────────────────                           │
│  • 将用户的对话转化为结构化的"研究简报"                               │
│  • 初始化主管（Supervisor）的上下文                                │
│  • 输出：research_brief（研究简报字符串）                          │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  阶段 3: research_supervisor（研究主管 - 子图）                    │
│  ───────────────────────────────────────                        │
│                                                                  │
│  ┌──────────────────────────────────────────┐                    │
│  │  supervisor 节点（主管思考）                │                    │
│  │  • 分析研究简报                            │                    │
│  │  • 决定如何分配研究任务                      │                   │
│  │  • 使用 think_tool 进行策略规划             │                    │
│  │  • 调用 ConductResearch 分配任务            │                    │
│  │  • 或调用 ResearchComplete 表示完成          │                   │
│  └────────────────┬─────────────────────────┘                    │
│                   ▼                                              │
│  ┌──────────────────────────────────────────┐                    │
│  │  supervisor_tools 节点（执行工具）           │                   │
│  │  • 处理 ConductResearch 调用               │                    │
│  │    → 并行启动多个研究员子图                   │                   │
│  │    → 每个研究员独立搜索和收集信息             │                  │
│  │  • 处理 think_tool 调用                    │                    │
│  │  • 检查结果，决定是否需要更多研究               │                  │
│  └────────────────┬─────────────────────────┘                    │
│                   │                                              │
│          ┌───────┴───────┐                                       │
│          │ 循环 or 结束？ │                                       │
│          └───┬───────┬───┘                                       │
│         继续研究    完成                                          │
│          │           │                                           │
│          ▼           ▼                                           │
│       supervisor   退出子图                                       │
│                                                                  │
│  ┌──────────────────────────────────────────┐                    │
│  │  研究员子图（researcher_subgraph）           │                   │
│  │  ├── researcher: 搜索、收集信息              │                   │
│  │  ├── researcher_tools: 执行搜索工具          │                   │
│  │  └── compress_research: 压缩整理发现         │                   │
│  └──────────────────────────────────────────┘                    │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  阶段 4: final_report_generation（生成最终报告）                    │
│  ──────────────────────────────────────────                     │
│  • 汇总所有研究员的发现                                            │
│  • 生成结构化的 Markdown 报告                                     │
│  • 包含引用来源                                                   │
│  • 自动检测并使用用户语言（中文提问 → 中文报告）                       │
│  • 支持 token 超限时的自动截断和重试                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 逐文件代码导读

### 5.1 `state.py` — 状态定义（读代码起点）

**文件作用**：定义系统中所有数据结构和状态类型。

**阅读顺序建议**：这是最容易读懂的文件，从这里开始。

**关键数据结构**：

| 类名               | 用途                                       |
| ------------------ | ------------------------------------------ |
| `ConductResearch`  | 主管用来分配研究任务的工具定义             |
| `ResearchComplete` | 标记研究完成的信号工具                     |
| `Summary`          | 搜索结果摘要                               |
| `ClarifyWithUser`  | 用户澄清请求的结构化输出                   |
| `ResearchQuestion` | 研究问题的结构化输出                       |
| `AgentState`       | 主图状态（消息、研究简报、笔记、最终报告） |
| `SupervisorState`  | 主管状态（消息、研究简报、迭代次数）       |
| `ResearcherState`  | 研究员状态（消息、工具调用次数、研究主题） |

**要点**：
- `override_reducer` 是一个自定义的状态合并函数，允许在需要时覆盖而非追加状态值
- 所有状态都继承了 `MessagesState`，意味着它们都包含消息列表

### 5.2 `configuration.py` — 配置管理

**文件作用**：定义所有可配置的参数。

**核心配置项**：

| 配置项                          | 默认值                | 说明                                     |
| ------------------------------- | --------------------- | ---------------------------------------- |
| `search_api`                    | `tavily`              | 搜索 API（Tavily/OpenAI/Anthropic/None） |
| `research_model`                | `openai:gpt-4.1`      | 执行研究的模型                           |
| `summarization_model`           | `openai:gpt-4.1-mini` | 摘要模型                                 |
| `compression_model`             | `openai:gpt-4.1`      | 压缩模型                                 |
| `final_report_model`            | `openai:gpt-4.1`      | 写报告的模型                             |
| `max_researcher_iterations`     | 6                     | 主管最大迭代次数                         |
| `max_react_tool_calls`          | 10                    | 研究员最大工具调用次数                   |
| `max_concurrent_research_units` | 5                     | 最大并行研究员数                         |
| `max_content_length`            | 50000                 | 网页内容最大字符数                       |
| `mcp_config`                    | None                  | MCP 服务器配置                           |

**要点**：
- 每个字段都有 `x_oap_ui_config` 元数据，用于在 LangGraph Studio UI 中自动生成配置界面
- `from_runnable_config` 方法可以从环境变量和运行时配置中加载设置

### 5.3 `prompts.py` — 提示词模板

**文件作用**：存储所有 LLM 提示词。这是影响系统行为最关键的文件。

**提示词清单**：

| 提示词名                                        | 使用位置                | 作用                   |
| ----------------------------------------------- | ----------------------- | ---------------------- |
| `clarify_with_user_instructions`                | clarify_with_user       | 判断是否需要向用户提问 |
| `transform_messages_into_research_topic_prompt` | write_research_brief    | 将对话转为研究简报     |
| `lead_researcher_prompt`                        | supervisor              | 指导主管如何分配任务   |
| `research_system_prompt`                        | researcher              | 指导研究员如何搜索     |
| `compress_research_system_prompt`               | compress_research       | 指导如何压缩研究结果   |
| `final_report_generation_prompt`                | final_report_generation | 指导如何生成最终报告   |
| `summarize_webpage_prompt`                      | utils.py                | 指导如何摘要网页       |

**要点**：
- 提示词中大量使用 XML 标签（如 `<Task>`、`<Instructions>`）来组织结构
- `lead_researcher_prompt` 中定义了详细的"思考-行动-反思"循环规则
- `final_report_generation_prompt` 明确要求报告语言与用户输入语言一致

### 5.4 `deep_researcher.py` — 主图定义（核心文件）

**文件作用**：定义所有节点函数和工作流图结构。

**建议阅读顺序**：

1. **先看图的构建部分**（文件底部，约第 700-718 行）：
   ```python
   deep_researcher_builder = StateGraph(AgentState, input=AgentInputState, config_schema=Configuration)
   deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
   deep_researcher_builder.add_node("write_research_brief", write_research_brief)
   deep_researcher_builder.add_node("research_supervisor", supervisor_subgraph)
   deep_researcher_builder.add_node("final_report_generation", final_report_generation)
   ```

2. **然后按流程顺序读每个节点函数**：
    主图 START
        ↓
    clarify_with_user  用户澄清（Command动态跳转至下一站）
        ↓
    write_research_brief 生成调研纲要
        ↓
    research_supervisor【主管子图 supervisor_subgraph】
        ├─ 拆分多个子调研主题
        ├─ 并发启动 N 个【研究员子图 researcher_subgraph】
        │    ├─ researcher（思考生成工具调用）
        │    ├─ researcher_tools（执行搜索，Command实现React循环）
        │    └─ compress_research（压缩输出compressed_research）→ 子图END
        └─ 收集全部研究员结果，写入主state notes
        ↓  主管子图END，回到主图
    final_report_generation 汇总notes，生成最终报告，处理token截断重试
        ↓
    主图 END

**核心函数说明**：

| 函数                      | 行号 | 功能                               |
| ------------------------- | ---- | ---------------------------------- |
| `clarify_with_user`       | ~60  | 用结构化输出判断是否需要澄清       |
| `write_research_brief`    | ~118 | 生成研究简报，初始化主管           |
| `supervisor`              | ~178 | 主管 LLM 决策（调工具或完成）      |
| `supervisor_tools`        | ~225 | 执行主管的工具调用（含并行研究员） |
| `researcher`              | ~365 | 研究员 LLM 搜索决策                |
| `researcher_tools`        | ~435 | 执行搜索工具                       |
| `compress_research`       | ~511 | 压缩和整理研究发现                 |
| `final_report_generation` | ~607 | 生成最终 Markdown 报告             |

**关键设计点**：
- 第 295-305 行：`asyncio.gather` 实现多个研究员并行搜索
- 第 255 行：三种退出条件（超限/无工具调用/完成信号）
- 第 660-683 行：token 超限时自动截断发现并重试

### 5.5 `utils.py` — 工具函数

**文件作用**：提供搜索、MCP、token 管理等辅助功能。

**核心功能模块**：

| 模块              | 行号     | 功能                        |
| ----------------- | -------- | --------------------------- |
| Tavily 搜索       | ~39-173  | 异步搜索 + 网页摘要         |
| think_tool        | ~218-244 | 策略反思工具                |
| MCP 工具加载      | ~449-524 | 连接 MCP 服务器并加载工具   |
| 搜索工具配置      | ~531-567 | 根据 API 类型返回搜索工具   |
| Token 限制检测    | ~665-785 | 检测各模型的 token 超限错误 |
| 模型 Token 限制表 | ~788-829 | 各模型的上下文窗口大小      |
| API 密钥管理      | ~892-925 | 从环境变量或配置获取密钥    |

**要点**：
- `tavily_search` 工具会自动对搜索结果进行 AI 摘要（使用 `summarization_model`）
- `think_tool` 是一个不执行任何操作的"虚拟工具"，仅用于让 LLM 暂停思考
- `get_all_tools` 组装完整的工具集：研究完成工具 + 思考工具 + 搜索工具 + MCP 工具

---

## 6. 关键设计模式解析

### 6.1 三层 Agent 嵌套架构

```
主图 (deep_researcher)
  └── 主管子图 (supervisor_subgraph)
        └── 研究员子图 (researcher_subgraph) × N（并行）
```

这种设计的好处：
- **关注点分离**：主管只管分配，研究员只管搜索
- **并行能力**：多个研究员同时工作，提高效率
- **独立状态**：每个研究员有自己的消息历史和迭代计数

### 6.2 Command 模式（控制流）

所有节点函数都返回 `Command` 对象，同时指定：
- `goto`：下一个节点
- `update`：要更新的状态

```python
return Command(
    goto="research_supervisor",
    update={"research_brief": response.research_brief}
)
```

这是 LangGraph 的条件路由机制，让执行路径可以动态变化。

### 6.3 结构化输出（Structured Output）

多处使用 Pydantic 模型约束 LLM 输出格式：

```python
clarification_model = configurable_model.with_structured_output(ClarifyWithUser)
response = await clarification_model.ainvoke(...)
# response 是 ClarifyWithUser 实例，有 .need_clarification, .question 等属性
```

这保证了 LLM 的输出可以被程序可靠地解析。

### 6.4 错误恢复机制

系统有多层错误恢复：
- **结构化输出重试**：`with_retry(stop_after_attempt=3)`
- **Token 超限处理**：自动截断内容并重试（最多 3 次，每次减少 10%）
- **工具执行安全**：`execute_tool_safely` 捕获异常返回错误字符串
- **摘要超时保护**：60 秒超时，失败则返回原始内容

---

## 7. 配置系统详解

### 7.1 配置加载优先级

```
环境变量 > 运行时配置（LangGraph Studio UI）> 默认值
```

在 `Configuration.from_runnable_config` 中：
```python
values = {
    field_name: os.environ.get(field_name.upper(), configurable.get(field_name))
    for field_name in field_names
}
```

### 7.2 如何配置搜索 API

```bash
# 使用 Tavily（默认，需要 TAVILY_API_KEY）
SEARCH_API=tavily

# 使用 OpenAI 原生搜索
SEARCH_API=openai

# 使用 Anthropic 原生搜索
SEARCH_API=anthropic

# 不使用搜索（仅 MCP）
SEARCH_API=none
```

---

## 8. 如何运行和测试

### 8.1 环境准备

```bash
# 1. 克隆项目
git clone https://github.com/WeatherCore/My-Open-Deep-Research.git
cd My-Open_Deep_Research

# 2. 创建虚拟环境
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
uv sync

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入：
# - DEEPSEEK_API_KEY（如果使用 DEEPSEEK 模型）
# - TAVILY_API_KEY（如果使用 Tavily 搜索）
```

### 8.2 启动开发服务器

```bash
$env:PYTHONUTF8=1; 
.venv\Scripts\langgraph dev --allow-blocking
```

启动后：
- API 地址：`http://127.0.0.1:2024`
- Studio 界面：`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

### 8.3 运行评估

```bash
# 运行完整评估（注意：可能花费 $20-$100）
python tests/run_evaluate.py
```

---

## 9. 复刻建议与学习路线

### 9.1 推荐阅读顺序

```
第 1 步：读 state.py         → 理解数据结构（约 15 分钟）
第 2 步：读 configuration.py  → 理解可配置项（约 15 分钟）
第 3 步：读 prompts.py        → 理解 AI 如何被引导（约 30 分钟）
第 4 步：读 deep_researcher.py → 理解主流程（约 60 分钟）
第 5 步：读 utils.py          → 理解工具实现（约 30 分钟）
第 6 步：读 tests/            → 理解评估方法（约 20 分钟）
```

### 9.2 复刻路线建议

**阶段 1：最小可行版本（1-2 天）**
- 只实现单 Agent + 单搜索工具
- 去掉主管层，直接一个研究员搜索 + 生成报告
- 使用 Tavily 搜索 + OpenAI 模型

**阶段 2：加入多 Agent（2-3 天）**
- 添加主管子图
- 实现并行研究员
- 添加 think_tool

**阶段 3：完善功能（3-5 天）**
- 添加用户澄清流程
- 实现研究压缩
- 添加引用来源
- 实现 token 超限处理

**阶段 4：高级功能（5-7 天）**
- MCP 工具集成
- 多搜索 API 支持
- 评估系统
- 部署到 LangGraph Platform

### 9.3 关键技术栈学习资源

| 技术       | 学习资源                                                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| LangGraph  | [官方文档](https://langchain-ai.github.io/langgraph/) + [本项目配套课程](https://academy.langchain.com/courses/deep-research-with-langgraph) |
| LangChain  | [Python LangChain 文档](https://python.langchain.com/docs/)                                                                                  |
| Pydantic   | [Pydantic V2 文档](https://docs.pydantic.dev/latest/)                                                                                        |
| asyncio    | [Python 异步编程官方教程](https://docs.python.org/3/library/asyncio.html)                                                                    |
| Tavily API | [Tavily 文档](https://docs.tavily.com/)                                                                                                      |

---

## 10. 常见问题

### Q: `.env` 文件需要哪些环境变量？

至少需要：
```bash
OPENAI_API_KEY=sk-xxx          # OpenAI 模型密钥
TAVILY_API_KEY=tvly-xxx        # Tavily 搜索密钥
```

可选：
```bash
ANTHROPIC_API_KEY=sk-ant-xxx   # Anthropic 模型密钥
GOOGLE_API_KEY=xxx             # Google 模型密钥
```

### Q: 如何不用 Tavily 而用其他搜索？

修改 `.env` 中的 `SEARCH_API` 配置，或在 LangGraph Studio UI 中切换。

### Q: 可以使用本地模型吗？

可以，通过 Ollama 支持。模型字符串格式为 `ollama:model_name`。但注意本地模型可能不支持结构化输出和工具调用。

### Q: 研究一个主题大概花多少钱？

使用默认配置（GPT-4.1），一次完整研究大约 $0.5-$2。评估 100 个主题大约 $45（默认配置）。

### Q: `langgraph.json` 是做什么的？

这是 LangGraph Cloud / LangGraph Studio 的项目配置文件，不是 Python 代码，是 JSON 配置，专门给 LangGraph 工具链读取，它告诉 LangGraph CLI 主图的入口点在哪里：
```json
{
  "graphs": {
    "agent": "./src/open_deep_research/deep_researcher.py:deep_researcher"
  }
}
```

### Q: 如何自定义提示词？

直接修改 `src/open_deep_research/prompts.py` 文件。这是影响系统行为最直接的方式。

### Q: `think_tool` 有什么用？

它是一个"虚拟工具"，让 LLM 可以在工具调用循环中暂停一下，进行策略性思考。比如：
- "我已经找到了 3 个相关来源，是否还需要继续搜索？"
- "这个研究方向似乎没有结果，是否应该换个关键词？"

这模拟了人类研究员的"停下来想想"的行为，被证明可以提高研究质量。

---

## 附录：关键术语对照表

| 英文              | 中文             | 说明                         |
| ----------------- | ---------------- | ---------------------------- |
| Deep Research     | 深度研究         | 自动化多步骤研究             |
| Agent             | 智能体/代理      | 能使用工具的 AI 系统         |
| Supervisor        | 主管             | 管理研究任务分配的 Agent     |
| Researcher        | 研究员           | 执行具体搜索的子 Agent       |
| State             | 状态             | 在节点间传递的共享数据       |
| Node              | 节点             | 工作流图中的一步             |
| Edge              | 边               | 节点间的连接                 |
| Subgraph          | 子图             | 嵌套在主图中的独立工作流     |
| Tool Calling      | 工具调用         | LLM 决定调用外部工具         |
| Structured Output | 结构化输出       | LLM 输出符合特定格式的数据   |
| MCP               | 模型上下文协议   | 连接外部工具和服务的标准协议 |
| ReAct             | 推理-行动循环    | 交替思考和行动的模式         |
| Token Limit       | Token 限制       | 模型上下文窗口大小限制       |
| Compression       | 压缩             | 将研究发现精简为更紧凑的格式 |
| LangGraph Studio  | LangGraph 工作台 | 可视化的 Agent 调试界面      |
