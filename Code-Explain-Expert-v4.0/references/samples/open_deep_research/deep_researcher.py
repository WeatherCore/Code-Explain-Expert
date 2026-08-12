"""Main LangGraph implementation for the Deep Research agent."""
# 整个深度研究流程的"总导演"：用户的一句话进来，怎么澄清、怎么立项、怎么派研究员、怎么写报告，全在这张图（Graph）里编排

import asyncio
from typing import Literal
import logging
logger = logging.getLogger(__name__)

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from open_deep_research.configuration import (
    Configuration,
)
from open_deep_research.prompts import (
    clarify_with_user_instructions,
    compress_research_simple_human_message,
    compress_research_system_prompt,
    final_report_generation_prompt,
    lead_researcher_prompt,
    research_system_prompt,
    transform_messages_into_research_topic_prompt,
)
from open_deep_research.state import (
    AgentInputState,
    AgentState,
    ClarifyWithUser,
    ConductResearch,
    ResearchComplete,
    ResearcherOutputState,
    ResearcherState,
    ResearchQuestion,
    SupervisorState,
)
from open_deep_research.utils import (
    anthropic_websearch_called,
    get_all_tools,
    get_api_key_for_model,
    get_model_token_limit,
    get_notes_from_tool_calls,
    get_today_str,
    is_token_limit_exceeded,
    openai_websearch_called,
    remove_up_to_last_ai_message,
    think_tool,
)

# 创建一个“可配置”的聊天模型对象，该对象本身不绑定具体模型，而是暴露三个字段供后续注入
# 后面每个节点都用 .with_config({...}) 动态传入自己想要的模型，从而“实例化”出一个真实的模型实例，相当于一个模具，想印什么印什么
configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key"),
)

###################
# Input Processing
# 输入处理：用户需求进来之后，先看要不要追问，再把提纲立起来
###################

# clarify_with_user（向用户澄清）：整个流程的"开门把关"。用户一句话可能很模糊，这里让 Agent 判断"要不要先问清楚再开工"。如果配置里禁用了追问，就直接溜去写调研提纲
async def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command[Literal["write_research_brief", "__end__"]]:
    """Analyze user messages and ask clarifying questions if the research scope is unclear.
    
    This function determines whether the user's request needs clarification before proceeding
    with research. If clarification is disabled or not needed, it proceeds directly to research.
    
    Args:
        state: Current agent state containing user messages
        config: Runtime configuration with model settings and preferences
        
    Returns:
        Command to either end with a clarifying question or proceed to research brief
    """

    # 读取运行时config，
    configurable = Configuration.from_runnable_config(config)
    # 判断是否允许澄清。如果不允许，就直接跳过澄清环节，去写调研提纲
    if not configurable.allow_clarification:
        return Command(goto="write_research_brief")
    
    # 从自定义拓展状态拿取messages列表，里面是用户的所有消息（HumanMessage）和 Agent 的所有消息（AIMessage），按时间顺序排好
    # 场景 1：首次发起调研（最开始）
    # 场景 2：助手向用户反问，用户再次回复触发澄清
    messages = state["messages"]
    # 设置本次模型参数
    model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # 组装带能力的模型实例
    clarification_model = (
        configurable_model
        .with_structured_output(ClarifyWithUser)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(model_config)
    )

    # 把历史消息拼接进提示词，交给模型判断需求清晰度
    prompt_content = clarify_with_user_instructions.format(
        messages=get_buffer_string(messages), # get_buffer_string() 会把所有消息拼接成一段文本
        date=get_today_str()
    )
    response = await clarification_model.ainvoke([HumanMessage(content=prompt_content)])

    # 模型说"需要追问" → 抛个问题给用户，流程到此打住（回到外部等用户回答）
    if response.need_clarification:
        return Command(
            goto=END, 
            update={"messages": [AIMessage(content=response.question, name="clarify_question")]}
        )
    # 说"不用" → 需求足够清晰，直接前往 write_research_brief，附带一句确认话术存入消息历史
    else:
        return Command(
            goto="write_research_brief", 
            update={"messages": [AIMessage(content=response.verification)]}
        )


# write_research_brief（写调研提纲）：用户需求澄清完毕（或本就不需要澄清）之后，把一堆杂乱的用户消息提炼成一句正式的 ResearchQuestion 调研提纲，
# 顺便把主管 Agent 的系统提示词和这份提纲塞进 supervisor_messages，相当于给主管"投喂第一口粮食"，组织正式开始运转
async def write_research_brief(state: AgentState, config: RunnableConfig) -> Command[Literal["research_supervisor"]]:
    """Transform user messages into a structured research brief and initialize supervisor.
    
    This function analyzes the user's messages and generates a focused research brief
    that will guide the research supervisor. It also sets up the initial supervisor
    context with appropriate prompts and instructions.
    
    Args:
        state: Current agent state containing user messages
        config: Runtime configuration with model settings
        
    Returns:
        Command to proceed to research supervisor with initialized context
    """

    # 和上一个节点模式高度统一：
    # 读取 Configuration → 拼装 model_config → 基于公共模板configurable_model追加结构化输出 + 重试
    configurable = Configuration.from_runnable_config(config)
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    research_model = (
        configurable_model
        .with_structured_output(ResearchQuestion)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )

    prompt_content = transform_messages_into_research_topic_prompt.format(
        messages=get_buffer_string(state.get("messages", [])),
        date=get_today_str()
    )
    # 模型输出：精简、边界明确的正式调研主题文本 response.research_brief
    response = await research_model.ainvoke([HumanMessage(content=prompt_content)])

    supervisor_system_prompt = lead_researcher_prompt.format(
        date=get_today_str(),
        max_concurrent_research_units=configurable.max_concurrent_research_units,
        max_researcher_iterations=configurable.max_researcher_iterations
    )

    
    # "supervisor_messages"和"research_brief":这是在自定义AgentState里面的参数，
    # 我们自定义了override_reducer 这个 reducer 规约函数，字典 {"type": "override", "value": [...]} 就是专门传给 override_reducer 的协议格式，"override" 用的是 override_reducer 的"覆盖"模式：把主管的对话列表整个清空重写（见 state.py），因为这是主管上任第一秒，还没有任何旧对话，直接覆盖成"系统提示词 + 调研提纲"这两条即可
    return Command(
        goto="research_supervisor",
        update={
            "research_brief": response.research_brief,
            # 比如 "研究量子计算在金融风险建模中的应用，重点关注当前的技术瓶颈和头部公司的工程方案。"
            "supervisor_messages": {
                "type": "override",
                "value": [
                    SystemMessage(content=supervisor_system_prompt),
                    HumanMessage(content=response.research_brief)
                ]
            }
        }
    )
    # 与上一节点不同，它的返回类型只有一个目的地："research_supervisor"。这意味着它一定会去主管节点，不会半路退出。


###################
# Supervisor
# 主管调度：主管 Agent 就像项目经理，负责拆解课题、派发任务、验收成果
###################
# supervisor（主管思考）：整个研究任务的总指挥，它负责拆解整体调研目标、下发子调研任务、判断信息是否充足；不直接联网搜索，只做规划、委派、决策，真正干活在 supervisor_tools
async def supervisor(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor_tools"]]:
    """Lead research supervisor that plans research strategy and delegates to researchers.
    
    The supervisor analyzes the research brief and decides how to break down the research
    into manageable tasks. It can use think_tool for strategic planning, ConductResearch
    to delegate tasks to sub-researchers, or ResearchComplete when satisfied with findings.
    
    Args:
        state: Current supervisor state with messages and research context
        config: Runtime configuration with model settings
        
    Returns:
        Command to proceed to supervisor_tools for tool execution
    """
 
    configurable = Configuration.from_runnable_config(config)
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # 主管仅允许调用 3 个工具（核心决策手段，重点！）：
    #  1.ConductResearch：委派任务，启动子研究员（子 Agent）去执行搜索调研
    #  2.ResearchComplete：哨兵工具，发出指令：调研结束，退出循环，开始撰写最终报告
    #  3.think_tool：反思工具，强制模型显式输出思考过程，优化规划质量
    lead_researcher_tools = [ConductResearch, ResearchComplete, think_tool]
    # 关键点：主管没有搜索工具！主管不会直接调用 tavily 搜索；搜索任务全部委派给子研究员执行。层级划分非常清晰---主管 = 策划指挥官；研究员 = 出去搜集资料的执行者
    
    research_model = (
        configurable_model
        .bind_tools(lead_researcher_tools)  # 绑定上面 3 个可用工具
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )
    
    # 从状态中取出主管专属消息队列 supervisor_messages，存放历史对话、工具返回结果
    supervisor_messages = state.get("supervisor_messages", [])
    # 让主管基于现有上下文做出决策：调用哪个工具
    response = await research_model.ainvoke(supervisor_messages)

    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1    
            # 每轮反思都把 research_iterations 加 1，这是"防无限死循环"的计数器，超过上限就要强制收工
        }
    )
    # 意味着无论模型做了什么决定，这个节点执行完后，永远跳转到 "supervisor_tools" 这个节点，带着老板的指令（response 里的 tool_calls）去找执行部门

# supervisor_tools（主管执行工具）：supervisor（主管 LLM 决策节点）嘴上说"去查、去收工"，这里才是真动手的地方。
# 它把主管的命令翻译成三件事：记录反思、真正启动研究员子图去查资料、判断要不要收场
async def supervisor_tools(state: SupervisorState, config: RunnableConfig) -> Command[Literal["supervisor", "__end__"]]:
    """Execute tools called by the supervisor, including research delegation and strategic thinking.
    
    This function handles three types of supervisor tool calls:
    1. think_tool - Strategic reflection that continues the conversation
    2. ConductResearch - Delegates research tasks to sub-researchers
    3. ResearchComplete - Signals completion of research phase
    
    Args:
        state: Current supervisor state with messages and iteration count
        config: Runtime configuration with research limits and model settings
        
    Returns:
        Command to either continue supervision loop or end research phase
    """

    configurable = Configuration.from_runnable_config(config)
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    # supervisor_messages[-1]：拿到主管 LLM 刚刚输出的那一条消息，里面携带 tool_calls（模型想要调用哪些工具）
    most_recent_message = supervisor_messages[-1]

        # most_recent_message.tool_calls = [
        #     {
        #         "name": "think_tool",
        #         "id": "call_123",
        #         "args": {"reflection": "目前资料缺少2026量子计算落地案例，需要补充检索"}
        #     },
        #     {
        #         "name": "ConductResearch",
        #         "id": "call_456",
        #         "args": {"research_topic": "量子计算商业落地现状"}
        #     }
        # ]

    # 三种"收工"信号：
    #  1.迭代超限：调研轮次 > 配置最大迭代次数，防止无限循环；
    exceeded_allowed_iterations = research_iterations > configurable.max_researcher_iterations
    #  2.无工具调用：模型没有输出任何 tool_call，代表模型打算直接输出文本，无法继续调研循环
    no_tool_calls = not most_recent_message.tool_calls
    #  3.主动收工：模型调用了 ResearchComplete，代表模型认为已经收集到足够信息，可以收工了
    research_complete_tool_call = any(
        tool_call["name"] == "ResearchComplete" 
        for tool_call in most_recent_message.tool_calls
    )
    
    # 三个“死亡条件”，满足任意一个就直接结束，收工前把收集到的所有笔记 notes 交出去，作为写报告的素材
    if exceeded_allowed_iterations or no_tool_calls or research_complete_tool_call:
        return Command(
            goto=END,
            update={
                "notes": get_notes_from_tool_calls(supervisor_messages), # 不知道
                "research_brief": state.get("research_brief", "")
            }
        )
    
    # 用来存放本次执行产生的所有工具返回消息（ToolMessage）
    all_tool_messages = []
    # 作为最终返回的 update 字典的雏形，后面可能会往里面塞 raw_notes
    update_payload = {"supervisor_messages": []}  
    
    # 从模型全部的工具调用请求里，筛选出名称等于 think_tool 的调用请求
    think_tool_calls = [
        tool_call for tool_call in most_recent_message.tool_calls 
        if tool_call["name"] == "think_tool"
    ]
    
    for tool_call in think_tool_calls:
        # tool_call["args"] = 模型传进来的参数字典
        # reflection 就是模型思考内容，也就是大模型写下的反思、规划文本
        reflection_content = tool_call["args"]["reflection"]
        # 把反思内容记录下来，让老板下次决策时能看到“自己之前写了什么”，形成连续的思考链
        # LangGraph 协议强制要求：每一条 tool_call，必须对应生成一条 ToolMessage 应答，tool_call_id 必须和模型申请里的 id 完全一致，框架依靠 id 匹配「调用请求 ↔ 工具返回结果」
        all_tool_messages.append(ToolMessage(
            content=f"Reflection recorded: {reflection_content}",
            name="think_tool",
            tool_call_id=tool_call["id"]
        ))
    
    # 从模型全部的工具调用请求里，筛选出名称等于 ConductResearch 的调用请求
    conduct_research_calls = [
        tool_call for tool_call in most_recent_message.tool_calls
        if tool_call["name"] == "ConductResearch"
    ]

    if conduct_research_calls:
        try:
            # 主管可能一次想派 10 个研究员，但并发上限只允许 5 个（max_concurrent_research_units），于是"分水岭"一刀切：前 5 个放行去干，后面超出的 5 个扔进 overflow 等着被拒绝
            allowed_conduct_research_calls = conduct_research_calls[:configurable.max_concurrent_research_units]
            overflow_conduct_research_calls = conduct_research_calls[configurable.max_concurrent_research_units:]

            # 给每个放行的子课题，用 researcher_subgraph 单独起一个研究员子图（每个子图都是独立的小世界）
            research_tasks = [
                researcher_subgraph.ainvoke({
                    "researcher_messages": [
                        HumanMessage(content=tool_call["args"]["research_topic"])
                    ],
                    "research_topic": tool_call["args"]["research_topic"]
                }, config) 
                for tool_call in allowed_conduct_research_calls
            ]

            # asyncio.gather 是"并发等待"大师：把所有研究员子图任务扔进去，等它们全部跑完才继续，凉拌的结果一起打包回来
            # 每一个ConductResearch调用，都会启动一套独立调研流水线：子研究员 → Tavily 搜索 → 网页摘要 → 信息压缩 → 返回调研小结
            tool_results = await asyncio.gather(*research_tasks)

            # tool_results 是一个列表，顺序与 research_tasks 一一对应
            # [
            #     # 这是第 1 个研究员返回的状态（对应 "量子计算 金融应用"）
            #     {
            #         "researcher_messages": [ ... ],  # 研究员内部的对话历史（你之前看的 operator.add 累积的那堆消息，会被存到这里）
            #         "tool_call_iterations": 3,       # 这个研究员调用了 3 次工具（搜索 + 反思）
            #         "research_topic": "量子计算 金融应用",  # 老板派给它的原始课题
            #         "compressed_research": "量子计算在金融领域的应用主要集中在风险建模（如蒙特卡洛模拟加速）和投资组合优化。当前头部公司如 IBM 和 Rigetti 已与摩根大通展开试点合作。",  # 核心！经过压缩的精华摘要（几百字）
            #         "raw_notes": [                    # 核心！原始摘录列表
            #             "根据文档 A 所述：量子计算可加速 Monte Carlo 模拟 100 倍。",
            #             "根据文档 B 所述：JPMorgan 正在测试 Rigetti 的量子芯片。"
            #         ]
            #     },
            #   
            #     # 这是第 2 个研究员返回的状态（对应 "量子计算 硬件瓶颈"）
            #     {
            #         "researcher_messages": [ ... ],
            #         "tool_call_iterations": 4,
            #         "research_topic": "量子计算 硬件瓶颈",
            #         "compressed_research": "当前量子计算硬件的主要瓶颈包括量子比特退相干时间短（微秒级）、门保真度不足（低于 99.9%）以及量子纠错开销过大。",
            #         "raw_notes": [
            #             "来源 C：退相干时间目前约 100 微秒。",
            #             "来源 D：表面码纠错需要上千个物理比特。"
            #         ]
            #     }
            # ]
            
            # 把子图返回结果封装成ToolMessage，传回主管状态，让主管知道每个子课题的调研结果
            for observation, tool_call in zip(tool_results, allowed_conduct_research_calls):
                all_tool_messages.append(ToolMessage(
                    content=observation.get("compressed_research", "Error synthesizing research report: Maximum retries exceeded"),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"]
                ))
            
            # 超出的研究员别浪费，也要给主管一个交代：明确告诉它"你派太多了，这些没跑，再试一次少派点"
            for overflow_call in overflow_conduct_research_calls:
                all_tool_messages.append(ToolMessage(
                    content=f"Error: Did not run this research as you have already exceeded the maximum number of concurrent research units. Please try again with {configurable.max_concurrent_research_units} or fewer research units.",
                    name="ConductResearch",
                    tool_call_id=overflow_call["id"]
                ))
            
            # 把所有研究员交回来的原始素材 raw_notes 拼成一大坨，一并塞进主管状态，留给后面写报告用
            raw_notes_concat = "\n".join([
                "\n".join(observation.get("raw_notes", []))
                for observation in tool_results
            ])
            
            if raw_notes_concat:
                update_payload["raw_notes"] = [raw_notes_concat]
                
        except Exception as e:
            # 一旦执行子调研时触发 token 超限，主动终止整个调研循环，不再继续迭代，直接进入报告阶段（使用已收集到的素材生成报告）
            if is_token_limit_exceeded(e, configurable.research_model):
                return Command(
                    goto=END,
                    update={
                        "notes": get_notes_from_tool_calls(supervisor_messages),
                        "research_brief": state.get("research_brief", "")
                    }
                )
            # 其他异常（限流/网络等）：记日志，把错误回给主管继续，而不是静默终止
            logger.exception("research delegation failed: %s", e)
            for tool_call in conduct_research_calls:
                all_tool_messages.append(ToolMessage(
                    content=f"Error running research: {e}",
                    name="ConductResearch",
                    tool_call_id=tool_call["id"]
                ))
    
    # 把所有工具执行产生的ToolMessage写入状态，跳转回 supervisor 主管节点，开启新一轮决策循环
    update_payload["supervisor_messages"] = all_tool_messages
    return Command(
        goto="supervisor",
        update=update_payload
    ) 

# 构建一个 LangGraph 的子图（Subgraph），用于管理研究任务的分发与协调（即 Supervisor 的工作流）
# 整个大项目不是一张大图全部写在一起，而是拆成很多小块，这一块就是其中一块独立的小工作单元，专门负责：思考要不要搜索、调用工具、判断调研是否结束
supervisor_builder = StateGraph(SupervisorState, context_schema=Configuration)

supervisor_builder.add_node("supervisor", supervisor)           # Main supervisor logic
supervisor_builder.add_node("supervisor_tools", supervisor_tools)  # Tool execution handler

supervisor_builder.add_edge(START, "supervisor")  # Entry point to supervisor

# 这里从大局上来看，研究员子图 researcher_subgraph 并没有作为一个 node 添加到主管子图 supervisor_subgraph 里面去，它没有在构建图（build time）就写死的节点，而是运行时动态调用的子图，在主管的节点函数内部，代码里面直接去 invoke researcher_subgraph，具体在 P347 代码里面有体现
supervisor_subgraph = supervisor_builder.compile()

###################
# Researcher
# 研究员执行：每个研究员是一线小兵，被派到具体子课题上，抱着一堆工具去查资料
###################
# 这是研究员子图里面的思考节点。主管 supervisor 给它分配一个细分调研主题，这个节点负责让大模型思考、产出工具调用，然后跳转去执行工具 researcher_tools 节点
async def researcher(state: ResearcherState, config: RunnableConfig) -> Command[Literal["researcher_tools"]]:
    """Individual researcher that conducts focused research on specific topics.
    
    This researcher is given a specific research topic by the supervisor and uses
    available tools (search, think_tool, MCP tools) to gather comprehensive information.
    It can use think_tool for strategic planning between searches.
    
    Args:
        state: Current researcher state with messages and topic context
        config: Runtime configuration with model settings and tool availability
        
    Returns:
        Command to proceed to researcher_tools for tool execution
    """

    configurable = Configuration.from_runnable_config(config)
    # 是这个研究员自己独立的对话历史，不和主管消息混淆
    researcher_messages = state.get("researcher_messages", [])
    
    # 从配置里把工具拼齐；如果一样工具都没有，那研究员就是赤手空拳，直接报错罢工
    tools = await get_all_tools(config)
    if len(tools) == 0:
        raise ValueError(
            "No tools found to conduct research: Please configure either your "
            "search API or add MCP tools to your configuration."
        )
    
    research_model_config = {
        "model": configurable.research_model,
        "max_tokens": configurable.research_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.research_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # 生成研究员专用的系统提示词（research_system_prompt）
    researcher_prompt = research_system_prompt.format(
        mcp_prompt=configurable.mcp_prompt or "", 
        date=get_today_str()
    )
    
    research_model = (
        configurable_model
        .bind_tools(tools)
        .with_retry(stop_after_attempt=configurable.max_structured_output_retries)
        .with_config(research_model_config)
    )
    
    # 消息构造：系统提示词 + 这个研究员的历史对话
    messages = [SystemMessage(content=researcher_prompt)] + researcher_messages
    # 这一步只是拿到模型想要调用什么工具里面携带 tool_call，或者没有使用工具的想法，还没有真正执行搜索
    response = await research_model.ainvoke(messages)
    
    # 到下一个 node 去执行工具，研究员的状态里多了一个新消息：模型刚刚输出的那条消息（里面可能有多个工具调用请求）
    return Command(
        goto="researcher_tools",
        update={
            "researcher_messages": [response],
            "tool_call_iterations": state.get("tool_call_iterations", 0) + 1
        }
    )

# 辅助函数：execute_tool_safely，捕获全部异常，不会让整个 Agent 崩溃
async def execute_tool_safely(tool, args, config):
    """Safely execute a tool with error handling."""
    try:
        return await tool.ainvoke(args, config)
    except Exception as e:
        return f"Error executing tool: {str(e)}"


# 这是研究员子图的工具执行节点。
# 上一步researcher节点让大模型生成工具调用请求，本节点真正把工具跑起来：执行搜索、think_tool、MCP 工具；做完之后通过Command决定：要么跳回researcher继续循环调研，要么跳到compress_research结束本轮调研
async def researcher_tools(state: ResearcherState, config: RunnableConfig) -> Command[Literal["researcher", "compress_research"]]:
    """Execute tools called by the researcher, including search tools and strategic thinking.
    
    This function handles various types of researcher tool calls:
    1. think_tool - Strategic reflection that continues the research conversation
    2. Search tools (tavily_search, web_search) - Information gathering
    3. MCP tools - External tool integrations
    4. ResearchComplete - Signals completion of individual research task
    
    Args:
        state: Current researcher state with messages and iteration count
        config: Runtime configuration with research limits and tool settings
        
    Returns:
        Command to either continue research loop or proceed to compression
    """
    
    configurable = Configuration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])
    # 取最新一条消息，这条消息就是上一步researcher节点模型输出，里面携带tool_calls
    most_recent_message = researcher_messages[-1]

    # 判断模型有没有输出普通工具调用
    has_tool_calls = bool(most_recent_message.tool_calls)
    # 断是否是 OpenAI / Anthropic模型内置的服务端联网搜索
    has_native_search = (
        openai_websearch_called(most_recent_message) or
        anthropic_websearch_called(most_recent_message)
    )
    # 模型既没有调用普通工具，也没有调用厂商原生搜索 → 调研直接结束，跳转compress_research做信息压缩
    if not has_tool_calls and not has_native_search:
        return Command(goto="compress_research")

    # 获取全部可用工具
    tools = await get_all_tools(config)
    # 工具既有"对象"形态（Tavily）也有"字典"形态（OpenAI/Anthropic 原生搜索），这里建立一个“名字 → 工具对象”的查找表（tools_by_name）
    tools_by_name = {
        tool.name if hasattr(tool, "name") else tool.get("name", "web_search"): tool
        for tool in tools
    }

    # 根据模型输出的tool_calls批量构造协程任务列表，每个任务执行一个工具
    tool_calls = most_recent_message.tool_calls
    tool_execution_tasks = [
        execute_tool_safely(tools_by_name[tool_call["name"]], tool_call["args"], config) 
        for tool_call in tool_calls
    ]
    observations = await asyncio.gather(*tool_execution_tasks)
    
    # 把每一个工具返回结果，包装成ToolMessage，这是 LangChain 标准消息结构，必须带上tool_call_id，大模型才能把结果和之前的工具调用一一对应上，后面通过update写入 state，交给add_messages归约器追加进researcher_messages里
    tool_outputs = [
        ToolMessage(
            content=observation,
            name=tool_call["name"],
            tool_call_id=tool_call["id"]
        ) 
        for observation, tool_call in zip(observations, tool_calls)
    ]
    
    # 工具跑完之后再判一次：是不是搜太多次了（超过 max_react_tool_calls）？
    exceeded_iterations = state.get("tool_call_iterations", 0) >= configurable.max_react_tool_calls
    # 或者研究员主动喊了收工？
    research_complete_called = any(
        tool_call["name"] == "ResearchComplete" 
        for tool_call in most_recent_message.tool_calls
    )

    # 如果搜太多次了，或者研究员喊了收工，就结束本轮调研，跳转compress_research做信息压缩
    if exceeded_iterations or research_complete_called:
        # End research and proceed to compression
        return Command(
            goto="compress_research",
            update={"researcher_messages": tool_outputs}
        )
    
    # 否则继续调研，跳回researcher节点，让大模型继续思考、调用工具
    return Command(
        goto="researcher",
        update={"researcher_messages": tool_outputs}
    )

# compress_research（压缩研究）：研究员查了一堆资料、留下一长串对话，这个节点调用专门的压缩模型，把杂乱的对话、网页搜索结果提炼成一段结构化摘要
# 这个节点不返回 Command 对象，直接返回普通字典：compressed_research（精炼总结，交回给主管）+ raw_notes（原始素材，留着写报告时引用），字典内容交给 LangGraph，自动合并更新到子图 researcher_subgraph 状态中。执行完这个节点，研究员子图整个就结束，压缩后的 compressed_research 会回传给外层主管 supervisor 子图
async def compress_research(state: ResearcherState, config: RunnableConfig):
    """Compress and synthesize research findings into a concise, structured summary.
    
    This function takes all the research findings, tool outputs, and AI messages from
    a researcher's work and distills them into a clean, comprehensive summary while
    preserving all important information and findings.
    
    Args:
        state: Current researcher state with accumulated research messages
        config: Runtime configuration with compression model settings
        
    Returns:
        Dictionary containing compressed research summary and raw notes
    """

    configurable = Configuration.from_runnable_config(config)
    synthesizer_model = configurable_model.with_config({
        "model": configurable.compression_model,
        "max_tokens": configurable.compression_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.compression_model, config),
        "tags": ["langsmith:nostream"]
    })
    
    # 这里是研究员子图内部全部历史，包含多轮 AI 思考、Tavily 搜索返回的 ToolMessage。是一长串的对话，token 量很高
    researcher_messages = state.get("researcher_messages", [])
    
    # 追加一条 HumanMessage：给模型下达指令：停止搜索，现在你的任务是把全部资料整理成摘要
    researcher_messages.append(HumanMessage(content=compress_research_simple_human_message))

    # 压缩可能失败（资料太多撑爆 token），所以最多重试 3 次，每次失败就砍掉一段旧消息再试
    synthesis_attempts = 0
    max_attempts = 3
    
    while synthesis_attempts < max_attempts:
        try:
            # 组装消息：系统提示词(压缩任务) + 研究员全部历史消息
            compression_prompt = compress_research_system_prompt.format(date=get_today_str())
            messages = [SystemMessage(content=compression_prompt)] + researcher_messages
            
            # 调用模型生成摘要
            response = await synthesizer_model.ainvoke(messages)
            
            # filter_messages工具函数：只提取大模型消息、Tool 消息，过滤 System/Human，拼接成原始原始素材raw_notes_content
            raw_notes_content = "\n".join([
                str(message.content) 
                for message in filter_messages(researcher_messages, include_types=["tool", "ai"])
            ])
            
            # Return successful compression result
            return {
                # 提炼之后精简摘要，交给主管 supervisor 阅读
                "compressed_research": str(response.content), 
                # 完整原始素材留档，方便后续排查问题、生成最终报告
                "raw_notes": [raw_notes_content]
            }
            
        except Exception as e:
            synthesis_attempts += 1
            
            # 如果报错的原因是 “消息太长，超过了模型的 Token 上限”（is_token_limit_exceeded），则调用 remove_up_to_last_ai_message，从消息列表的末尾往前找，砍掉最近的一条 AI 消息及其之后的内容（相当于“删除最近的一次无用对话”），强行给上下文窗口腾出空间，然后用缩短后的消息列表继续重试
            if is_token_limit_exceeded(e, configurable.research_model):
                researcher_messages = remove_up_to_last_ai_message(researcher_messages)
                continue
            
            # 非 token 超限错误（限流/网络/密钥）：不盲目重试烧钱，走兜底
            logger.warning("Compression failed with non-token error: %s", e)
            break
    
    # 如果 3 次重试都失败了（不管是 Token 超限裁了也救不回来，还是网络错误），绝不抛异常中断程序，而是返回一个带错误信息的 compressed_research，以及能救多少就救多少的 raw_notes 原始素材
    raw_notes_content = "\n".join([
        str(message.content) 
        for message in filter_messages(researcher_messages, include_types=["tool", "ai"])
    ])
    return {
        "compressed_research": "Error synthesizing research report: Maximum retries exceeded",
        "raw_notes": [raw_notes_content]
    }

# 【研究员子图】：每个研究员跑的都是同一套模板 —— researcher 思考 → researcher_tools 调工具 → 回 researcher 继续……直到该压缩了 → compress_research 收尾交差
# output=ResearcherOutputState 意味着：子图内部状态再多，对外只露 compressed_research 和 raw_notes 这两样（见 state.py 的"快递箱"），返回给调用它的外层主管子图
researcher_builder = StateGraph(
    ResearcherState,
    output=ResearcherOutputState,
    config_schema=Configuration
)

# 三节点小流水线：思考、动手、收尾压缩
researcher_builder.add_node("researcher", researcher)                 # Main researcher logic
researcher_builder.add_node("researcher_tools", researcher_tools)     # Tool execution handler
researcher_builder.add_node("compress_research", compress_research)   # Research compression

# 入口从"思考"进；中间的往返靠 Command 路由；只有"压缩"是确定性地走向出口 END
researcher_builder.add_edge(START, "researcher")           # Entry point to researcher
researcher_builder.add_edge("compress_research", END)      # Exit point after compression

# 编译成可被主管并行调用的"标准件"：主管一次 gather N 个，就是 N 个这样的子图同时在跑
researcher_subgraph = researcher_builder.compile()

###################
# Report Generation
# 报告生成：所有研究员都交差了，把零散笔记汇总成一份完整的 Markdown 报告
# prompt 里面写死指令：You must output complete report using valid Markdown syntax, use # ## for headings, tables, bullet points etc，大模型收到这个 prompt，输出的final_report字符串本身就是 markdown 文本，不是代码把普通字符串 “转换” 成 md，是 LLM 直接生成 md 格式的文本内容
###################
# final_report_generation（最终报告生成）：整个流程的"压轴戏"。主管收集完所有研究员产出的compressed_research，汇总全部调研笔记存入notes；喂给专门的写作模型，让它写出最终报告。token 撑爆时会按"砍 10% 再试"的渐进策略兜底
# 同样是普通节点，返回字典，不是Command；执行完本节点，整个 Agent 主流程结束
async def final_report_generation(state: AgentState, config: RunnableConfig):
    """Generate the final comprehensive research report with retry logic for token limits.
    
    This function takes all collected research findings and synthesizes them into a 
    well-structured, comprehensive final report using the configured report generation model.
    
    Args:
        state: Agent state containing research findings and context
        config: Runtime configuration with model settings and API keys
        
    Returns:
        Dictionary containing the final report and cleared state
    """

    # notes 是所有研究员压缩后交回来的笔记；
    notes = state.get("notes", [])
    # cleared_state 用 override 把 notes 清空，因为报告写完这些素材就完成使命了，免得占着状态
    cleared_state = {"notes": {"type": "override", "value": []}}
    # 把全部摘要拼接成一大段文本，作为报告的原始材料
    findings = "\n".join(notes)
    
    configurable = Configuration.from_runnable_config(config)
    writer_model_config = {
        "model": configurable.final_report_model,
        "max_tokens": configurable.final_report_model_max_tokens,
        "api_key": get_api_key_for_model(configurable.final_report_model, config),
        "tags": ["langsmith:nostream"]
    }
    
    # Step 3: Attempt report generation with token limit retry logic
    max_retries = 3
    current_retry = 0
    findings_token_limit = None
    
    while current_retry < max_retries:
        try:
            # 组装报告提示词，输入包含四块内容：研究简报、研究员对话记录、提炼后的笔记、当前日期
            final_report_prompt = final_report_generation_prompt.format(
                research_brief=state.get("research_brief", ""),
                messages=get_buffer_string(state.get("messages", [])),
                findings=findings,
                date=get_today_str()
            )
            
            # 调用模型，生成完整报告
            final_report = await configurable_model.with_config(writer_model_config).ainvoke([
                HumanMessage(content=final_report_prompt)
            ])
            # Return successful report generation
            return {
                "final_report": final_report.content, 
                "messages": [final_report],
                **cleared_state
            }
            
        except Exception as e:
            # 笔记太多塞爆模型：第一轮先按"模型 token 上限 ×4"估算字符数砍一刀（粗略 1 token≈4 字符）， 之后每轮再砍 10%，砍到能塞进去为止——宁可丢点尾巴也要把报告写出来，保证至少能产出一份报告，而不是直接崩溃
            if is_token_limit_exceeded(e, configurable.final_report_model):
                current_retry += 1

                # 第一次触发超限：调用get_model_token_limit，从MODEL_TOKEN_LIMITS拿到该模型最大上下文；如果拿不到，说明MODEL_TOKEN_LIMITS里没有这个模型，直接返回错误
                if current_retry == 1:
                    # First retry: determine initial truncation limit
                    model_token_limit = get_model_token_limit(configurable.final_report_model)
                    if not model_token_limit:
                        return {
                            "final_report": f"Error generating final report: Token limit exceeded, however, we could not determine the model's maximum context length. Please update the model map in deep_researcher/utils.py with this information. {e}",
                            "messages": [AIMessage(content="Report generation failed due to token limits")],
                            **cleared_state
                        }
                    # 对拼接好的调研文本做字符串截断，缩短输入，直接进入下一轮循环重试
                    findings_token_limit = model_token_limit * 4
                else:
                    # 后续每一次重试：把允许的字符上限再打 9 折，不断砍掉一部分原始调研素材
                    findings_token_limit = int(findings_token_limit * 0.9)

                # Truncate findings and retry
                findings = findings[:findings_token_limit]
                continue
            else:
                # Non-token-limit error: return error immediately
                return {
                    "final_report": f"Error generating final report: {e}",
                    "messages": [AIMessage(content="Report generation failed due to an error")],
                    **cleared_state
                }
    
    # 耗尽全部重试次数兜底：返回错误信息，报告生成失败
    return {
        "final_report": "Error generating final report: Maximum retries exceeded",
        "messages": [AIMessage(content="Report generation failed after maximum retries")],
        **cleared_state
    }

# 【主图】：把前面所有零件串成一条完整流水线——澄清 → 立项 → 主管调度（内含研究员子图）→ 写报告。
# input=AgentInputState 表示对外只接收 messages 这一个口子，内部状态对外不可见
deep_researcher_builder = StateGraph(
    AgentState,
    input=AgentInputState,
    config_schema=Configuration
)

# clarify_with_user 用户澄清节点用户
# 提问模糊、信息不足时，Agent 会反问用户，收集更多背景、约束、调研侧重点
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user) 
# write_research_brief 撰写调研纲要
# 把用户需求整理一份正式调研方案：明确调研目标、需要拆分哪几个子问题。输出存入 state 的research_brief，给后面主管子图使用
deep_researcher_builder.add_node("write_research_brief", write_research_brief) 
# 主管子图 supervisor_subgraph
# 直接把子图对象当做节点注册进主图，主管内部：拆分子主题，并行启动多个研究员子图，收集每一个研究员输出的compressed_research，汇总写入主 state 的notes
deep_researcher_builder.add_node("research_supervisor", supervisor_subgraph)
# final_report_generation 最终报告生成节点
# 读取notes全部调研素材，生成完整报告，处理 token 超限、截断重试，流程终点
deep_researcher_builder.add_node("final_report_generation", final_report_generation)

# 整个项目入口，运行图第一站
deep_researcher_builder.add_edge(START, "clarify_with_user")
# 主管子图全部执行完毕（走到子图内部 END）之后，主图才往下走到报告生成
deep_researcher_builder.add_edge("research_supervisor", "final_report_generation")
# 主图结束，整个 Deep‑Research 流程全部终止
deep_researcher_builder.add_edge("final_report_generation", END)

# 编译出最终的 deep_researcher，这就是 langgraph.json 里配置的入口图
deep_researcher = deep_researcher_builder.compile()


# 完整全局执行链路（从顶层到底层嵌套）：
    # 主图 START
    #     ↓
    # clarify_with_user  用户澄清（Command动态跳转至下一站）
    #     ↓
    # write_research_brief 生成调研纲要
    #     ↓
    # research_supervisor【主管子图 supervisor_subgraph】
    #     ├─ 拆分多个子调研主题
    #     ├─ 并发启动 N 个【研究员子图 researcher_subgraph】
    #     │    ├─ researcher（思考生成工具调用）
    #     │    ├─ researcher_tools（执行搜索，Command实现React循环）
    #     │    └─ compress_research（压缩输出compressed_research）→ 子图END
    #     └─ 收集全部研究员结果，写入主state notes
    #     ↓  主管子图END，回到主图
    # final_report_generation 汇总notes，生成最终报告，处理token截断重试
    #     ↓
    # 主图 END