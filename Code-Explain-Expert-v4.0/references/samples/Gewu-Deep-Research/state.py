"""Graph state definitions and data structures for the Deep Research agent."""

import operator
from typing import Annotated, Optional

from langchain_core.messages import MessageLikeRepresentation
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


###################
# Structured Outputs
# Pydantic 模型 → 给 LLM 结构化输出（强制模型输出规范 JSON，工具调用、需求澄清、研究指令）
###################

# ConductResearch（执行研究）：当 Agent 决定“我要查资料”时，它必须调用这个模型，主管 Agent 下发任务时，强制输出调研子课题，主管拆分大需求，生成多个子调研主题，用这个格式传递给研究员子 Agent
class ConductResearch(BaseModel):
    """Call this tool to conduct research on a specific topic."""
    research_topic: str = Field(
        description="The topic to research. Should be a single topic, and should be described in high detail (at least a paragraph).",
    )

# 在utils.py中被定义为一个tool
# ResearchComplete（研究完成）：这是一个空模型（没有字段）。它在编程中叫哨兵（Sentinel），相当于 Agent 举起一面旗子喊：“报告长官，我的所有检索任务都做完了，可以写总结了！”系统看到这个类被调用，就知道信息搜集足够，可以停止循环检索，进入写报告阶段
class ResearchComplete(BaseModel):
    """Call this tool to indicate that the research is complete."""

# Summary（总结）：它强制研究员完成一轮搜索后，输出摘要 + 关键原文片段，把网页检索结果压缩成结构化笔记，存入状态
class Summary(BaseModel):
    """Research summary with key findings."""
    
    summary: str
    key_excerpts: str

# ClarifyWithUser（向用户澄清）：这是人机协同（Human-in-the-loop）的关键。如果用户的问题太模糊（比如“帮我写个东西”），Agent 不会瞎猜，而是输出这个模型。系统会截断流程，把这个 question 抛回给用户，等用户补充信息后，再继续往下走。这是防止 AI 胡编乱造的兜底机制，对应 HITL 人在回路
class ClarifyWithUser(BaseModel):
    """Model for user clarification requests."""

    # true/false，要不要向用户提问
    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    # 向用户发出澄清问题
    question: str = Field(
        description="A question to ask the user to clarify the report scope",
    )
    # 用户回答之后，启动调研的提示文案，也就是项目开头「主动追问用户明确调研范围」的核心实现
    verification: str = Field(
        description="Verify message that we will start research after the user has provided the necessary information.",
    )

# ResearchQuestion（研究问题）：这是一个内部指令卡，用户需求澄清完毕，Agent 整理一份正式调研提纲，作为后续所有检索、研究的统一目标
class ResearchQuestion(BaseModel):
    """Research question and brief for guiding research."""
    
    research_brief: str = Field(
        description="A research question that will be used to guide the research.",
    )


###################
# State Definitions
# 各类State（TypedDict / MessagesState）→ LangGraph 图运行时的全局状态
###################
# 核心黑科技：它是整个状态更新的“交通规则”
def override_reducer(current_value, new_value):
    """Reducer function that allows overriding values in state."""
    
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)  # 强制覆盖
    else:
        return operator.add(current_value, new_value) # 默认追加（列表相加）
    ###################
    # 常规操作（追加）：当你要在 notes 列表里加一条新笔记时，直接 return "新笔记"，它会自动 append 到历史列表里，不会丢失旧数据。和 operator.add 一样，列表追加（old + new）
    # 特殊操作（覆盖）：当你需要重置某个字段时（比如研究员要清空旧的 raw_notes 重新写），传入 {"type": "override", "value": "全新内容"}，它就会把旧值彻底替换掉。这在清理历史上下文时极其有用
    ###################
    
# 只保留对话消息列表， 对外入口接收用户消息的最简状态
class AgentInputState(MessagesState):
    """InputState is only 'messages'."""

# 整个主流程图的根状态，贯穿全程，包含对话消息和所有研究数据
class AgentState(MessagesState):
    """Main agent state containing messages and research data."""
    
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer] # 主管 Agent 内部消息队列，规则意味着默认追加，特殊情况可以整体覆盖
    # Annotated 的语法定义 Annotated[类型, 附加元数据1, 附加元数据2, ...] ：第一个参数是标准数据类型，后面可以放任意数量的「附加元信息」。它不会改变变量本身的 Python 类型，只是挂上额外信息，供第三方库读取，在 TypedDict / MessagesState 的字段中，如果 Annotated 第二个参数是一个函数，这个函数就被识别为 Reducer（状态归约函数）
    research_brief: Optional[str] # 澄清完毕后生成的正式调研提纲（全局统一目标）
    raw_notes: Annotated[list[str], override_reducer] = [] # 各个研究员传回的原始素材-原始未整理
    notes: Annotated[list[str], override_reducer] = [] # 汇总、去重、压缩后的有效笔记信息
    final_report: str # 最后生成的 markdown 报告

# 【主管子图专用状态】 它是 AgentState 的“精简子集”加了个计数
class SupervisorState(TypedDict):
    """State for the supervisor that manages research tasks."""
    
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: str
    notes: Annotated[list[str], override_reducer] = []
    research_iterations: int = 0 # 用来限制调研迭代轮次，防止无限检索！
    raw_notes: Annotated[list[str], override_reducer] = []

# 【单个研究员子图状态】 每一个并行运行的研究员，都会拥有一份独立的 ResearcherState
class ResearcherState(TypedDict):
    """State for individual researchers conducting research."""
    
    researcher_messages: Annotated[list[MessageLikeRepresentation], operator.add] # 研究员自己的思考过程、工具返回结果。为什么不用 override_reducer？ 因为研究员干活时上下文千万不能断，必须一条一条全贴上去，绝不能覆盖
    tool_call_iterations: int = 0 # 当前这个研究员搜索调用次数
    research_topic: str # 当前分配给他的子课题
    compressed_research: str # 本轮调研压缩摘要
    raw_notes: Annotated[list[str], override_reducer] = []

# 研究员任务完成后向外输出结构
# 为什么要单独写一个？ 因为研究员干完活，不能直接把数据塞回主管的硬盘里（怕写乱），而是用这个“快递箱”统一打包，由 LangGraph 的路由逻辑再决定怎么拆箱，它属于内部流程文件（State），不属于对外宣传海报（LLM Structured Output）
class ResearcherOutputState(BaseModel):
    """Output state from individual researchers."""
    
    compressed_research: str # 研究员要把压缩版总结交出去
    raw_notes: Annotated[list[str], override_reducer] = []  # 研究员要把原始摘录也交出去