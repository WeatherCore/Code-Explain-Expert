"""Utility functions and helpers for the Deep Research agent."""

import asyncio
import logging
import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

import aiohttp
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    MessageLikeRepresentation,
    filter_messages,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import (
    BaseTool,
    InjectedToolArg,
    StructuredTool,
    ToolException,
    tool,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.config import get_store
from mcp import McpError
from tavily import AsyncTavilyClient

from open_deep_research.configuration import Configuration, SearchAPI
from open_deep_research.prompts import summarize_webpage_prompt
from open_deep_research.state import ResearchComplete, Summary

# 模块级缓存：记录本进程内已经精读并摘要过的 URL，实现「跨搜索调用去重」。
# 同一个网页在不同轮次 / 不同研究员之间会被反复搜出来，如果每次都重新抓取原文 + 重新摘要，
# 就是反复计费。这里让同一个 URL 只精读一次，后续轮次直接跳过。
# 注意：langgraph dev 是单进程常驻，缓存会跨多次研究共享；进程重启后自然清空。
_SEEN_URLS: set = set()
_MAX_SEEN_URLS = 3000

##########################
# Tavily Search Tool Utils tavily_search 联网搜索工具

##########################

# Tavily Search Tool 的 Description
TAVILY_SEARCH_DESCRIPTION = (
    "A search engine optimized for comprehensive, accurate, and trusted results. "
    "Useful for when you need to answer questions about current events."
)

# 这是大模型如果选择执行 Tavily Search 而使用的那个最终函数，他进入tool列表是在第705行
# 研究员调用这个工具 → 执行联网检索、网页抓取、网页摘要、结果格式化，最终把整理好的资料返回给研究员 LLM
@tool(description=TAVILY_SEARCH_DESCRIPTION)
async def tavily_search(
    queries: List[str],  # 支持一次传入多个搜索词批量查询，由 LLM 生成。LLM 看到用户问题“帮我查量子计算和 AI 的结合”，会主动把这个字段填成 ["量子计算 AI 结合", "quantum computing AI"]
    max_results: Annotated[int, InjectedToolArg] = 5,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
    # max_results 和 topic：被 InjectedToolArg 标记，由代码底层自动注入，意味着 LLM 看不见这两个参数，也不会费力去填它们。它们的值来自：函数定义里的默认值（5 和 "general"），或者当你调用 agent.invoke(..., config={"configurable": {"max_results": 10}}) 时，被运行时注入
    config: RunnableConfig = None
) -> str:  # 最终组装好的纯文本，直接放进消息历史给 LLM 阅读
    """Fetch and summarize search results from Tavily search API.
    Args:
        queries: List of search queries to execute
        max_results: Maximum number of results to return per query
        topic: Topic filter for search results (general, news, or finance)
        config: Runtime configuration for API keys and model settings
    Returns:
        Formatted string containing summarized search results
    """
    
    # tavily_search_async：第165行定义，并发执行所有 query
    # search_results 是一个列表，每个元素对应一个 query 的返回结果
    search_results = await tavily_search_async(
        queries,
        max_results=max_results,
        topic=topic,
        include_raw_content=True, # 最重要：开启网页正文抓取；如果关闭，只能拿到简短预览摘要，无法深度调研
        config=config
    )
    
    # URL 去重（工程关键优化点）
    # 同一个网页可能被多个 query 同时搜出来。不去重会造成：重复抓取、重复摘要、浪费 token、大量冗余信息。
    # 第 1 层：本次调用内去重（URL 作为唯一 key）；第 2 层：跨调用去重（_SEEN_URLS 模块级缓存，
    # 已被之前任何一轮 / 任何研究员精读过的 URL 直接跳过，避免同一网页反复抓取 + 反复摘要计费）
    unique_results = {}
    for response in search_results:
        # response['results'] 是 Tavily 返回的字典，包含 title、url、content、raw_content(正文)等字段
        for result in response['results']:
            url = result['url']
            if url in _SEEN_URLS:
                continue  # 之前已精读摘要过，跳过
            if len(_SEEN_URLS) >= _MAX_SEEN_URLS:
                _SEEN_URLS.clear()  # 缓存过大时整体清空兜底
            _SEEN_URLS.add(url)
            unique_results[url] = {**result, "query": response['query']}
            # 效果:
            # unique_results = {
            #     "url_1": {"title": "...", "url": "...", "content": "...", "raw_content": "...", "query": "量子计算"},
            #     "url_2": {"title": "...", "url": "...", "content": "...", "raw_content": "...", "query": "量子计算"}
            # }
    
    # 从运行时配置读取参数，所有模型参数在顶层统一管控，不用硬编码
    configurable = Configuration.from_runnable_config(config)
    max_char_to_include = configurable.max_content_length
    model_api_key = get_api_key_for_model(configurable.summarization_model, config)

    # 初始化“总结专用模型”（打工仔配置）
    summarization_model = init_chat_model(
        model=configurable.summarization_model,
        max_tokens=configurable.summarization_model_max_tokens,
        api_key=model_api_key,
        tags=["langsmith:nostream"] # LangSmith 追踪标签，标记该次模型调用不需要流式输出，用于日志调试区分，优化监控体验
    ).with_structured_output(Summary).with_retry(
        stop_after_attempt=configurable.max_structured_output_retries
    )
    # .with_structured_output(Summary)强制这个模型输出的 JSON 必须符合你之前看的 Summary Pydantic 类（包含 summary 和 key_excerpts）
    # .with_retry(...)：如果总结失败，自动重试，次数由 max_structured_output_retries 决定（默认 3 次）
    
    # noop = no operation，空操作异步函数，用来处理没有网页原文的情况
    async def noop():
        """No-op function for results without raw content."""

        return None

    # 只是一个任务清单，列表里每一项是「将来要执行的摘要任务」，但是还没有真正运行
    summarization_tasks = [
        # 如果网页没有原始正文 raw_content：分配空任务 noop()，直接放一个 None 占位
        noop() if not result.get("raw_content") 
        # 如果存在正文：调用 summarize_webpage，把截断后的正文（[:max_char_to_include]）传进去
        else summarize_webpage( 
            summarization_model, 
            result['raw_content'][:max_char_to_include]
        )
        for result in unique_results.values()
        # 遍历 unique_results 里的每一个结果，只用到了正文字段
    ]
    
    # asyncio.gather：同时发射所有总结任务
    # 返回的 summaries 是一个列表，顺序与 summarization_tasks 一一对应。如果某个任务执行了 noop，对应位置就是 None
    summaries = await asyncio.gather(*summarization_tasks)
    
    # 同时遍历 URL 列表、原始结果字典列表、总结结果列表，构造一个新的字典 summarized_results，Key 是 URL，Value 是 {'title': 标题, 'content': 总结内容（或保底摘要）}
    summarized_results = {
        url: {
            'title': result['title'], 
            'content': result['content'] if summary is None else summary
        }
        for url, result, summary in zip(
            unique_results.keys(), 
            unique_results.values(), 
            summaries
        )
    }
    
    # 空结果兜底：如果去重后一个结果都没有，直接返回一段提示文本
    if not summarized_results:
        return "No valid search results found. Please try different search queries or use a different search API."

    # 组装字符串，把所有结果格式化成纯文本，作为 ToolMessage 的 content，直接插入对话历史，供 LLM 阅读。LLM 最喜欢看这种带编号和分栏的“报告格式”：
    #  1.标题用 --- SOURCE 1: 标题 --- 包裹。
    #  2.加上 URL。
    #  3.把 result['content']（可能是总结，也可能是保底摘要）原样打印。
    #  4.每两个来源之间用 80 个 - 分隔线隔开
    formatted_output = "Search results: \n\n"
    for i, (url, result) in enumerate(summarized_results.items()):
        formatted_output += f"\n\n--- SOURCE {i+1}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "\n\n" + "-" * 80 + "\n"
    
    return formatted_output

# 这个函数只负责“抓取原始数据”，不负责去重、不负责总结、不负责格式化。
# 它把原始数据丢回去给上层（tavily_search 那个总函数）做后续处理
async def tavily_search_async(
    search_queries,    # 搜什么？列表，比如 ["量子计算", "量子计算 应用"]
    max_results: int = 5, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True,    # Tavily 不仅返回摘要，还会抓取网页完整 HTML 文本，后续用来送入summarize_webpage做网页精读
    config: RunnableConfig = None
):
    """Execute multiple Tavily search queries asynchronously.
    
    Args:
        search_queries: List of search query strings to execute
        max_results: Maximum number of results per query
        topic: Topic category for filtering results
        include_raw_content: Whether to include full webpage content
        config: Runtime configuration for API key access
        
    Returns:
        List of search result dictionaries from Tavily API
    """

    # 使用 Tavily 官方 Python SDK 提供的异步客户端类初始化客户端
    tavily_client = AsyncTavilyClient(api_key=get_tavily_api_key(config))
    
    # 遍历 search_queries（比如 ["量子计算", "量子计算 应用"]），为每一个 query 创建一个 “异步任务对象”，但是这里没有真正去执行搜索，只是把“指令”打包成了任务对象，存进 search_tasks 列表里
        # {
        #     "query": "量子计算",
        #     "results": [
        #         {"title": "...", "url": "...", "content": "摘要", "raw_content": "完整HTML..."},
        #         ...
        #     ]
        # }
    search_tasks = [
        tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
            # max_results、topic、include_raw_content 都是上层传来的，对所有查询一视同仁
        )
        for query in search_queries
    ]
    
    # asyncio.gather() 将所有查询同时并发发出，大幅缩短整体搜索耗时
    # 如果没有 gather：你需要写 for query in search_queries: await client.search(query)，那是排队执行，总共耗时 = 3个查询各自耗时之和（比如 10 秒），而 gather 是并行执行，总共耗时 = 3个查询各自耗时中的最大值（比如 3 秒）
    search_results = await asyncio.gather(*search_tasks)
    return search_results

# 网页原始长文本压缩专用函数
# Tavily 返回网页全文过长，直接丢给大模型会极度消耗 token，调用本函数提炼网页核心信息
async def summarize_webpage(model: BaseChatModel, webpage_content: str) -> str:
    """Summarize webpage content using AI model with timeout protection.
    
    Args:
        model: The chat model configured for summarization
        webpage_content: Raw webpage content to be summarized
        
    Returns:
        Formatted summary with key excerpts, or original content if summarization fails
    """

    try:
        # 读取全局定义好的网页摘要提示词模板，填入网页正文、当前日期
        prompt_content = summarize_webpage_prompt.format(
            webpage_content=webpage_content, 
            date=get_today_str()
        )
        
        # asyncio.wait_for 强制超时保护，如果模型调用（model.ainvoke）超过 60 秒还没返回，wait_for 就会抛出一个 TimeoutError ，因为总结一篇超长网页可能耗时很久。如果模型卡住了（网络超时、服务器过载），你的 Agent 会一直卡死。60 秒超时保证了即使模型挂了，程序也能继续往下走
        summary = await asyncio.wait_for(
            # 异步调用大模型，不阻塞事件循环
            model.ainvoke([HumanMessage(content=prompt_content)]),
            timeout=60.0  # 60 second timeout for summarization
        )
        
        # 这个 model 已经被 .with_structured_output(Summary) 包装过了。所以 model.ainvoke 返回的不再是纯字符串，而是一个 Pydantic 对象，自动拥有 summary 和 key_excerpts 属性
        # 输出格式：用 XML 标签包裹，使输出结构化，便于后续解析
        formatted_summary = (
            f"<summary>\n{summary.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
        )
        
        return formatted_summary

    # 设计哲学：宁可用“未经总结的原文”凑合回答，也不要让整个 Agent 因为单个网页总结失败而崩溃。这叫 “静默降级”
    # 第一层兜底（超时）：如果 60 秒内没总结完，返回原始网页内容（webpage_content）
    except asyncio.TimeoutError:
        # Timeout during summarization - return original content
        logging.warning("Summarization timed out after 60 seconds, returning original content")
        return webpage_content
    # 第二层兜底（其他异常）：如果模型突然崩溃或网络断掉，同样返回原始内容
    except Exception as e:
        # Other errors during summarization - log and return original content
        logging.warning(f"Summarization failed with error: {str(e)}, returning original content")
        return webpage_content

##########################
# Reflection Tool Utils
##########################

# 这个函数本身没有任何业务计算逻辑，函数体仅仅是把传入的 reflection 字符串原样包装返回
# 它真正的作用：强制模型进行显式思考，参数名是 reflection（反思）。LLM 调用这个工具时，必须传入一段文字，内容是关于当前研究进度的“内心独白”，文档里写得很清楚，这份“独白”必须包含四点：
#  1.分析当前发现（Analysis of current findings）：我具体找到了什么？
#  2.差距评估（Gap assessment）：我还缺什么关键信息？
#  3.质量评价（Quality evaluation）：证据够不够充分？
#  4.策略决策（Strategic decision）：我是继续搜还是直接回答？
@tool(description="Strategic reflection tool for research planning")
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """

    # 只是把 LLM 传进来的 reflection 原样套了个前缀，然后返回，这个返回值会作为 ToolMessage（工具执行结果）进入对话历史
    return f"Reflection recorded: {reflection}"


##########################
# MCP Utils
# MCP（Model Context Protocol）工具的鉴权辅助代码，这是给部署到线上时用的，比如 Open Agent Platform，另外一处在用户登录认证（src/security/auth.py），本地跑的话这些代码根本不会执行到。如果你以后想部署到线上让用户登录使用，那时候再了解也不迟
##########################

# get_mcp_access_token() 作用：OAuth 令牌交换
# 用户持有 supabase_token（前端登录后的身份凭证），MCP 服务器不认这个 Supabase 令牌；需要向后端发起一次请求，把 Supabase 登录的 token 兑换成 MCP 服务认可的访问令牌，后续调用 MCP 工具时带上这个新 token 鉴权，然后把令牌存起来，到期自动扔掉，另外一个参数是 MCP 服务器的地址，比如 https://mcp.example.com
async def get_mcp_access_token(
    supabase_token: str,
    base_mcp_url: str,
) -> Optional[Dict[str, Any]]:
    """Exchange Supabase token for MCP access token using OAuth token exchange.
    
    Args:
        supabase_token: Valid Supabase authentication token
        base_mcp_url: Base URL of the MCP server
        
    Returns:
        Token data dictionary if successful, None if failed
    """

    try:
        # Prepare OAuth token exchange request data
        form_data = {
            "client_id": "mcp_default",
            "subject_token": supabase_token,  
            # 传入用户原始凭证（supabase token）
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", 
            # 标准 OAuth 协议的【令牌交换模式】，告诉服务器：“我不是来登录的，我是来拿我的 Supabase 凭证换取一张临时 MCP 入场券的。”
            "resource": base_mcp_url.rstrip("/") + "/mcp",  
            # 你要访问的目标 MCP 资源地址，方便服务器按最小权限原则发放令牌
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", 
            # 声明你给出的是访问令牌类型
        }
        
        # aiohttp.ClientSession()：aiohttp 的异步会话对象，类比 requests 的 Session
        async with aiohttp.ClientSession() as session:
            # MCP 服务器的 OAuth 2.0 的标准令牌交换端点，通常是 /oauth/token
            token_url = base_mcp_url.rstrip("/") + "/oauth/token"

            # 发起 POST 异步请求
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            async with session.post(token_url, headers=headers, data=form_data) as response:
                # 成功返回一个 JSON 对象，里面至少包含 access_token（临时令牌）和 expires_in（多少秒后过期）；失败打印日志返回 None

                if response.status == 200:
                    # Successfully obtained token
                    token_data = await response.json()
                    return token_data
                else:
                    # Log error details for debugging
                    response_text = await response.text()
                    logging.error(f"Token exchange failed: {response_text}")
                    
    except Exception as e:
        logging.error(f"Error during token exchange: {e}")
    
    return None

# get_tokens(config) ：从长期记忆(持久化存储)里读取缓存的 MCP 令牌，并且自动校验过期
async def get_tokens(config: RunnableConfig):
    """Retrieve stored authentication tokens with expiration validation.
    
    Args:
        config: Runtime configuration containing thread and user identifiers
        
    Returns:
        Token dictionary if valid and not expired, None otherwise
    """

    store = get_store()
    
    # 对话线程 ID
    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return None
    # 当前操作人 ID（token 是按用户隔离的，不同用户不能互通）
    user_id = config.get("metadata", {}).get("owner")
    if not user_id:
        return None
    
    # 以 user_id 作为 key，去存储里读取该用户缓存的 MCP 令牌信息
    tokens = await store.aget((user_id, "tokens"), "data")
    if not tokens:
        return None
    
    # 拿到令牌创建时间 + 有效时长，算出到期时间，对比当前 UTC 时间
    expires_in = tokens.value.get("expires_in")  # seconds until expiration
    created_at = tokens.created_at  # datetime of token creation
    current_time = datetime.now(timezone.utc)
    expiration_time = created_at + timedelta(seconds=expires_in)

    # 令牌过期 → 直接删除缓存，返回 None，上层代码会重新触发令牌交换流程
    if current_time > expiration_time:
        await store.adelete((user_id, "tokens"), "data")
        return None

    # 令牌有效 → 直接复用，返回令牌字典
    return tokens.value

# set_tokens()：把兑换成功后的 MCP 访问令牌按用户存入 LangGraph 全局 KV 存储
async def set_tokens(config: RunnableConfig, tokens: dict[str, Any]):
    """Store authentication tokens in the configuration store.
    
    Args:
        config: Runtime configuration containing thread and user identifiers
        tokens: Token dictionary to store
    """

    store = get_store()
    
    # 对话线程 ID
    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return
    
    # 当前操作人 ID
    user_id = config.get("metadata", {}).get("owner")
    if not user_id:
        return
    
    # 异步方法，把 tokens 字典写进持久化存储
    await store.aput((user_id, "tokens"), "data", tokens)

# 整条 MCP 令牌获取的入口逻辑，所有要调用 MCP 远程工具的地方，优先调用 fetch_tokens 获取有效令牌
# 调用者（如 MCP 工具）不需要关心令牌是否过期，只管调用 fetch_tokens(config)，它总能保证返回一个可用的令牌。如果底层检测到过期，它会悄无声息地重新换票，整个过程对调用者透明
async def fetch_tokens(config: RunnableConfig) -> dict[str, Any]:
    """Fetch and refresh MCP tokens, obtaining new ones if needed.
    
    Args:
        config: Runtime configuration with authentication details
        
    Returns:
        Valid token dictionary, or None if unable to obtain tokens
    """

    # 1.先尝试读取缓存里现有的有效token
    current_tokens = await get_tokens(config)
    if current_tokens:
        # 缓存里有有效令牌，直接返回
        return current_tokens
    
    # 2.没取到（没有令牌或已过期），准备换新令牌
    #  从运行时config取出supabase登录令牌、MCP服务地址
    supabase_token = config.get("configurable", {}).get("x-supabase-access-token")
    if not supabase_token:
        return None
    mcp_config = config.get("configurable", {}).get("mcp_config")
    if not mcp_config or not mcp_config.get("url"):
        return None
    
    #  调用之前的 get_mcp_access_token() 执行OAuth令牌交换
    mcp_tokens = await get_mcp_access_token(supabase_token, mcp_config.get("url"))
    if not mcp_tokens:
        return None

    #  兑换成功 → 存入存储，并且返回新token
    await set_tokens(config, mcp_tokens)
    return mcp_tokens

# 给每一个远程加载的 MCP 工具包一层异常捕获外壳的函数：接收一个工具，返回一个增强后的工具，没有改变工具的“功能”，只改变了“出错时的反应”。LangGraph Agent 能正常处理 ToolException，会把异常文本返回给用户；如果直接抛出原生 McpError，框架识别不了，直接中断整个调研流程、崩溃
def wrap_mcp_authenticate_tool(tool: StructuredTool) -> StructuredTool:
    """Wrap MCP tool with comprehensive authentication and error handling.
    
    Args:
        tool: The MCP structured tool to wrap
        
    Returns:
        Enhanced tool with authentication error handling
    """

    # 备份当前工具原始的异步执行函数，后续包装后替换，异步函数特有属性.coroutine
    original_coroutine = tool.coroutine

    # 定义新函数-未来 Agent 调用这个 MCP 工具时，不再执行原始函数，而是执行这个 authentication_wrapper，因为我们下面写了tool.coroutine = authentication_wrapper，**kwargs：透传 Agent 传入工具的所有参数
    async def authentication_wrapper(**kwargs):
        """Enhanced coroutine with MCP error handling and user-friendly messages."""

        # 内部递归工具函数，作用：深挖异常链，找到最底层的 MCP 错误对象
        # 普通 try except 只能抓到最外层异常，MCP 真正报错会被包裹在 ExceptionGroup（并发异常组）里面
        def _find_mcp_error_in_exception_chain(exc: BaseException) -> McpError | None:
            """Recursively search for MCP errors in exception chains."""

            # 如果当前异常本身就是 McpError → 直接返回
            if isinstance(exc, McpError):
                return exc
            
            # 如果异常带有 .exceptions 属性（异常组），循环递归遍历里面每一个子异常
            if hasattr(exc, 'exceptions'):
                for sub_exception in exc.exceptions:
                    if found_error := _find_mcp_error_in_exception_chain(sub_exception):
                        # 找到第一个 McpError 就返回；找不到返回 None
                        return found_error
            return None
        
        try:
            # 执行原始 MCP 远程调用，把所有参数原样传给原始函数
            # 正常成功：直接返回结果，后面异常代码全部跳过
            return await original_coroutine(**kwargs)

        # 捕获所有异常
        except BaseException as original_error:
            # 调用递归函数查找异常链里有没有 MCP 协议异常
            mcp_error = _find_mcp_error_in_exception_chain(original_error)
            # 如果不是 McpError：原样抛出，我们不干预，交给上层原有错误处理
            if not mcp_error:
                # Not an MCP error, re-raise the original exception
                raise original_error
            
            # 如果找到 McpError：继续往下处理
            error_details = mcp_error.error
            # 从 MCP 异常对象提取标准协议字段：
            # code：MCP 标准错误码，data：错误附带的自定义载荷（字典）
            error_code = getattr(error_details, "code", None)
            error_data = getattr(error_details, "data", None) or {}
            
            # MCP 协议规定：-32003 = 需要用户人工交互授权
            if error_code == -32003:  
                # 从 error_data 里提取 message（用户可读的提示）和 url（用户可点击的链接）
                message_payload = error_data.get("message", {})
                error_message = "Required interaction"
                
                # 拼接成人类可读消息，比如“需要用户交互，请访问 https://... 完成授权”）
                if isinstance(message_payload, dict):
                    error_message = message_payload.get("text") or error_message
                if url := error_data.get("url"):
                    error_message = f"{error_message} {url}"

                # 最重要的转换动作：原生抛出的是 McpError，Agent 看不懂。我们把它转换成 LangChain 标准 ToolException，from original_error：保留原始异常堆栈，方便日志排查
                raise ToolException(error_message) from original_error
            
            # 其余所有 MCP 错误码（非 - 32003），不做转换，原样抛出
            raise original_error
    
    # 覆盖替换，把工具对象原本的异步函数，替换成我们写好的包装函数，外部调用工具时，自动先走我们的异常捕获逻辑
    tool.coroutine = authentication_wrapper
    # 返回改造完成的工具对象
    return tool

# 它把外部 MCP 服务器提供的工具，经过“身份验证 → 配置校验 → 去重 → 白名单过滤 → 异常包装”五道工序，最终输出一份可以直接塞进 Agent 工具箱的合规工具列表
async def load_mcp_tools(
    config: RunnableConfig,
    existing_tool_names: set[str], # 已经加载完毕的本地tools工具名称集合，避免重复加载
) -> list[BaseTool]:
    """Load and configure MCP (Model Context Protocol) tools with authentication.
    
    Args:
        config: Runtime configuration containing MCP server details
        existing_tool_names: Set of tool names already in use to avoid conflicts
        
    Returns:
        List of configured MCP tools ready for use
    """

    configurable = Configuration.from_runnable_config(config)
    
    # 如果用户配置里 auth_required == True（即 MCP 服务器需要登录），就调用 fetch_tokens(config) 去换令牌。如果不需要认证，直接设 mcp_tokens = None
    if configurable.mcp_config and configurable.mcp_config.auth_required:
        mcp_tokens = await fetch_tokens(config)
    else:
        mcp_tokens = None
    
    # 满足全部条件才算配置合法：
    # 1.存在 mcp 配置
    # 2.填写了 MCP 服务 url
    # 3.配置了允许加载的工具名称列表 tools
    # 4.【鉴权规则】要么拿到令牌，要么服务不需要鉴权
    config_valid = (
        configurable.mcp_config and 
        configurable.mcp_config.url and 
        configurable.mcp_config.tools and 
        (mcp_tokens or not configurable.mcp_config.auth_required)
    )

    # 任意条件不满足 → 直接返回空列表，不加载任何 MCP 工具，因为可能用户根本没想用 MCP
    if not config_valid:
        return []
    # 合法 → 继续执行
    
    # 拼接 MCP URL
    server_url = configurable.mcp_config.url.rstrip("/") + "/mcp"
    # 构造请求头（如果拿到了令牌，加上 Authorization: Bearer <access_token>）
    auth_headers = None
    if mcp_tokens:
        auth_headers = {"Authorization": f"Bearer {mcp_tokens['access_token']}"}

    # MultiServerMCPClient 期望一个字典，Key 是服务器别名，Value 是连接参数
    mcp_server_config = {
        "server_1": {
            "url": server_url,
            "headers": auth_headers,
            "transport": "streamable_http"  # MCP 协议的传输方式，基于 HTTP 流式通信
        }
    }
    # TODO: When Multi-MCP Server support is merged in OAP, update this code
    # 目前代码只支持单 MCP 服务器，未来会扩展成多个，所以这里用单元素字典，写死了 "server_1"
    
    try:
        # 创建 MCP 客户端(这是 MCP SDK 提供的客户端类)，和远程 MCP 服务建立连接
        client = MultiServerMCPClient(mcp_server_config)
        # .get_tools()：向 MCP 服务请求，获取该服务对外暴露的全部工具列表
        available_mcp_tools = await client.get_tools()
    except Exception:
        # 捕获异常：连不上服务、网络报错、服务宕机 → 直接返回空数组，不导致整个 Agent 程序崩溃
        return []
    
    # Step 5: Filter and configure tools
    configured_tools = []
    # 遍历 MCP 服务返回的工具列表，逐个检查：
    for mcp_tool in available_mcp_tools:
        # 重名检测：如果 MCP 工具名字和本地已有工具（比如 web_search）重名 → 跳过，打印警告日志
        if mcp_tool.name in existing_tool_names:
            warnings.warn(
                f"MCP tool '{mcp_tool.name}' conflicts with existing tool name - skipping"
            )
            continue
        
        # 白名单过滤：MCP 服务可能暴露几十种工具，我们只启用配置文件填写的指定工具，最小权限原则
        if mcp_tool.name not in set(configurable.mcp_config.tools):
            continue
        
        # 工具增强：通过 wrap_mcp_authenticate_tool() 给每个 MCP 工具套异常捕获外壳，处理 -32003 用户交互授权错误
        enhanced_tool = wrap_mcp_authenticate_tool(mcp_tool)
        # 过滤、包装完成的工具，存入列表最终返回，回到上层 get_all_tools()，并入全局工具集合
        configured_tools.append(enhanced_tool)
    
    return configured_tools


##########################
# Tool Utils
# 整个 Deep Research Agent 的 “工具箱组装流水线”
##########################

# get_search_tool：根据配置的search_api枚举，返回对应格式的搜索工具定义
#  ANTHROPIC / OPENAI 返回的是结构化字典（模型原生内置搜索声明）
#  TAVILY 返回的是Python 函数工具（LangChain @tool 装饰器定义的本地工具）
#  不管走原生搜索、还是 Tavily，对外工具名称统一叫web_search，上层 Agent 逻辑不用修改！Agent 始终只知道一个工具名 web_search，底层实现自动切换
async def get_search_tool(search_api: SearchAPI):
    """Configure and return search tools based on the specified API provider.
    
    Args:
        search_api: The search API provider to use (Anthropic, OpenAI, Tavily, or None)
        
    Returns:
        List of configured search tool objects for the specified provider
    """
    # 这里入参和形参都写错了，应该是 search_provider 而不是 search_api

    # 返回字典格式，直接传给 Anthropic 接口：启用 Claude 原生服务端搜索
    if search_api == SearchAPI.ANTHROPIC:
        return [{
            "type": "web_search_20250305", 
            "name": "web_search", 
            "max_uses": 5  # 限制本轮对话最多调用 5 次搜索
        }]

    # 同样只是接口声明字典，启用 GPT 原生服务端联网搜索
    elif search_api == SearchAPI.OPENAI:
        return [{"type": "web_search_preview"}]

    # 
    elif search_api == SearchAPI.TAVILY:
        # 来自第50行定义的一个 tool，这里这里不是复制函数，只是引用赋值，二者指向同一个工具对象
        search_tool = tavily_search
        # 原始工具自带：metadata = {}，这里再额外补充字段，.metadata 里不会有 type 和 name，这里强制加上，确保 Agent 识别为 web_search 工具
        search_tool.metadata = {
            **(search_tool.metadata or {}), 
            "type": "search", 
            "name": "web_search"
        }
        return [search_tool]

    # 用户明确关闭了联网搜索，返回空列表
    elif search_api == SearchAPI.NONE:
        # No search functionality configured
        return []
        
    # Default fallback for unknown search API types
    return []

# get_all_tools：把“核心工具”、“搜索扳手”、“第三方插件（MCP）”全部装进一个工具箱，返回给 Agent
async def get_all_tools(config: RunnableConfig):
    """Assemble complete toolkit including research, search, and MCP tools.
    
    Args:
        config: Runtime configuration specifying search API and MCP settings
        
    Returns:
        List of all configured and available tools for research operations
    """

    # 1. 基础工具（硬编码）
    # think_tool：定义在 271 行
    # ResearchComplete：定义在 state.py 里的一个空类，作为一个哨兵工具，告诉 Agent 检索任务完成了
    tools = [tool(ResearchComplete), think_tool]

    # 2. 从配置里读取用户选的搜索引擎，塞进去
    # from_runnable_config方法在configuration.py的第256行，把 config 转换成 Configuration 对象，方便后续代码调用
    configurable = Configuration.from_runnable_config(config)
    # 获得搜索服务商，SearchAPI 枚举类型，值是 tavily / openai
    search_api = SearchAPI(get_config_value(configurable.search_api))
    # get_search_tool在第638行那里，根据 search_api 返回不同格式的搜索工具定义
    search_tools = await get_search_tool(search_api)
    # Python 内置列表（list）原生自带方法，把传入列表里所有元素逐个追加到原列表里
    tools.extend(search_tools)
    
    # 3. 收集已经加载好的所有工具名称，存入集合 existing_tool_names
    existing_tool_names = {
        tool.name if hasattr(tool, "name") else tool.get("name", "web_search") 
        for tool in tools
    }
    
    # 4. 加载 MCP（第三方插件）工具，并传入已有工具名字列表，防止重名覆盖
    mcp_tools = await load_mcp_tools(config, existing_tool_names)
    tools.extend(mcp_tools)
    
    return tools

# get_notes_from_tool_calls：事后从对话历史里，把工具执行后吐出来的“笔记”全部抽出来
def get_notes_from_tool_calls(messages: list[MessageLikeRepresentation]):
    """Extract notes from tool call messages, excluding reflections and error placeholders."""
    error_markers = (
        "Error: Did not run this research",
        "Error synthesizing research report",
        "Error running research",
    )
    notes = []
    for tool_msg in filter_messages(messages, include_types="tool"):
        name = getattr(tool_msg, "name", "") or ""
        content = str(tool_msg.content or "")
        if name == "think_tool":
            continue  # 反思文本不进入报告素材
        if content.startswith(error_markers):
            continue  # 错误占位符不进入报告素材
        notes.append(content)
    return notes


##########################
# Model Provider Native Websearch Utils
# 模块定位：检测厂商原生内置联网搜索是否被触发，因为搜索动作在厂商服务端内部执行，我们的代码感知不到搜索过程
##########################

# 检测本次Claude接口响应中，是否调用了Anthropic官方原生服务器联网搜索工具
def anthropic_websearch_called(response):
    """Detect if Anthropic's native web search was used in the response.
    
    Args:
        response: The response object from Anthropic's API
        
    Returns:
        True if web search was called, False otherwise
    """

    # Anthropic 把是否联网搜索的信息藏得非常深。路径是：response → response_metadata → usage → server_tool_use → web_search_requests，每一层都做判空保护：用 if not usage 这种写法，只要中间某一层不存在，就直接返回 False（没搜过）。这防止了因 API 返回结构变动导致的程序崩溃（AttributeError）， Anthropic 把“联网搜索”视为一种特殊的“服务端工具调用（Server-side Tool Use）”，它会消耗额外的 Token，并在 usage 字段里单独计费。所以它把信息藏在计费明细里
    try:
        # Navigate through the response metadata structure
        usage = response.response_metadata.get("usage")
        if not usage:
            return False
        
        # Check for server-side tool usage information
        server_tool_use = usage.get("server_tool_use")
        if not server_tool_use:
            return False
        
        # Look for web search request count
        web_search_requests = server_tool_use.get("web_search_requests")
        if web_search_requests is None:
            return False
        
        # Return True if any web search requests were made
        # 如果成功取到了 web_search_requests 这个数字，就看它是否 大于 0。只要大于 0，说明模型确实发出了联网请求
        return web_search_requests > 0

    # 异常兜底：字段层级缺失、对象不存在属性时，捕获异常返回False，防止接口返回结构变动直接让整个调研流程崩溃
    except (AttributeError, TypeError):
        return False

# 检测本次OpenAI接口响应中，是否调用了OpenAI官方原生服务器联网搜索工具
def openai_websearch_called(response):
    """Detect if OpenAI's web search functionality was used in the response.
    
    Args:
        response: The response object from OpenAI's API
        
    Returns:
        True if web search was called, False otherwise
    """

    # Check for tool outputs in the response metadata
    tool_outputs = response.additional_kwargs.get("tool_outputs")
    if not tool_outputs:
        return False
    
    # Look for web search calls in the tool outputs
    for tool_output in tool_outputs:
        if tool_output.get("type") == "web_search_call":
            return True
    
    return False


##########################
# Token Limit Exceeded Utils
# Token 超限检测工具，结合项目背景：多次搜索、不断追加网页摘要，消息列表持续膨胀，极易触发 上下文窗口超限（Context Window Exceeded。一旦识别命中，程序就可以触发自动压缩历史消息（就是源码里的 compress_research 逻辑）、删减冗余内容，再重试调用大模型，而不是直接崩溃退出
##########################

# 输入：一个报错对象 exception，以及你正在使用的模型名（比如 "openai/gpt-4o"）。
# 逻辑：从模型名里截取前缀（openai:、anthropic:），判断是哪个厂商的模型。然后交给对应的“专科检查函数”去诊断。如果模型名是 None，则依次用所有厂商的“专科医生”去检查一遍。
def is_token_limit_exceeded(exception: Exception, model_name: str = None) -> bool:
    """Determine if an exception indicates a token/context limit was exceeded.
    
    Args:
        exception: The exception to analyze
        model_name: Optional model name to optimize provider detection
        
    Returns:
        True if the exception indicates a token limit was exceeded, False otherwise
    """

    error_str = str(exception).lower()
    
    # Step 1: Determine provider from model name if available
    # 步骤 1：根据模型名识别供应商
    provider = None
    if model_name:
        model_str = str(model_name).lower()
        if model_str.startswith('openai:'):
            provider = 'openai'
        elif model_str.startswith('anthropic:'):
            provider = 'anthropic'
        elif model_str.startswith('gemini:') or model_str.startswith('google:'):
            provider = 'gemini'
        elif model_str.startswith('dashscope:'):
            provider = 'dashscope'
    
    # Step 2: Check provider-specific token limit patterns
    # 步骤 2：根据供应商，派发到对应的“专科医生”
    if provider == 'openai':
        return _check_openai_token_limit(exception, error_str)
    elif provider == 'anthropic':
        return _check_anthropic_token_limit(exception, error_str)
    elif provider == 'gemini':
        return _check_gemini_token_limit(exception, error_str)
    elif provider == 'dashscope':
        return _check_dashscope_token_limit(exception, error_str)
    
    # Step 3: If provider unknown, check all providers
    # 步骤 3：如果不知道供应商（或者模型名传了个 None），就轮番用所有检测方法试一遍
    return (
        _check_openai_token_limit(exception, error_str) or
        _check_anthropic_token_limit(exception, error_str) or
        _check_gemini_token_limit(exception, error_str) or
        _check_dashscope_token_limit(exception, error_str)
    )

# OpenAI 系列检测
# OpenAI 的报错体系相对规范，代码设计了多层校验，防止误判
# 三层过滤机制：
#   1.先查户口（是不是 OpenAI 的异常类）；
#   2.再看长相（异常类型名是不是 BadRequestError 或 InvalidRequestError）；
#   3.最后翻内容（错误文本里有没有 token、context、length 这些关键词）。
#  额外保险：如果异常对象自带 code 属性，且值是 context_length_exceeded，直接确诊
def _check_openai_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates OpenAI token limit exceeded."""
    
    # Analyze exception metadata
    # 1.扒开异常的“外衣”，看它是不是 OpenAI 家出产的
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    is_openai_exception = (
        'openai' in exception_type.lower() or 
        'openai' in module_name.lower()
    )
    
    # Check for typical OpenAI token limit error types
    # 2. 看异常的类型名，是不是典型的“请求参数错误”类
    is_request_error = class_name in ['BadRequestError', 'InvalidRequestError']

    # 3. 如果是 OpenAI 的异常，且属于请求参数错误，再检查错误消息里有没有“token/context/length”等关键词
    if is_openai_exception and is_request_error:
        # Look for token-related keywords in error message
        token_keywords = ['token', 'context', 'length', 'maximum context', 'reduce']
        if any(keyword in error_str for keyword in token_keywords):
            return True
    
    # Check for specific OpenAI error codes
    # 4. 还有些 OpenAI 异常自带 error.code 属性，比如 code = 'context_length_exceeded'
    if hasattr(exception, 'code') and hasattr(exception, 'type'):
        error_code = getattr(exception, 'code', '')
        error_type = getattr(exception, 'type', '')
        
        if (error_code == 'context_length_exceeded' or
            error_type == 'invalid_request_error'):
            return True
    
    return False

def _check_anthropic_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates Anthropic token limit exceeded."""
    # Analyze exception metadata
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    # Check if this is an Anthropic exception
    is_anthropic_exception = (
        'anthropic' in exception_type.lower() or 
        'anthropic' in module_name.lower()
    )
    
    # Check for Anthropic-specific error patterns
    is_bad_request = class_name == 'BadRequestError'
    
    if is_anthropic_exception and is_bad_request:
        # Anthropic uses specific error messages for token limits
        if 'prompt is too long' in error_str:
            return True
    
    return False

def _check_gemini_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates Google/Gemini token limit exceeded."""
    
    # Analyze exception metadata
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')
    
    # Check if this is a Google/Gemini exception
    is_google_exception = (
        'google' in exception_type.lower() or 
        'google' in module_name.lower()
    )
    
    # Check for Google-specific resource exhaustion errors
    is_resource_exhausted = class_name in [
        'ResourceExhausted', 
        'GoogleGenerativeAIFetchError'
    ]
    
    if is_google_exception and is_resource_exhausted:
        return True
    
    # Check for specific Google API resource exhaustion patterns
    if 'google.api_core.exceptions.resourceexhausted' in exception_type.lower():
        return True
    
    return False

def _check_dashscope_token_limit(exception: Exception, error_str: str) -> bool:
    """Check if exception indicates DashScope(Qwen) token limit exceeded."""
    exception_type = str(type(exception))
    class_name = exception.__class__.__name__
    module_name = getattr(exception.__class__, '__module__', '')

    # 判断是否来自dashscope库异常
    is_dashscope_exception = (
        'dashscope' in exception_type.lower() or
        'dashscope' in module_name.lower()
    )

    if is_dashscope_exception:
        # 阿里云百炼 上下文超限典型报错关键词
        token_keywords = ["token length", "context length", "exceed maximum", "超过最大上下文长度"]
        if any(keyword in error_str for keyword in token_keywords):
            return True
    return False

# NOTE: 模型厂商经常升级上下文长度，如果不维护字典，会出现预估不准，这就是我们之前聊过的静默降级风险
# 全局常量字典，维护「模型全名 → 最大上下文窗口」映射，
# 统一格式：厂商:模型名称，和项目里 get_api_key_for_model 的模型命名规则完全对齐
MODEL_TOKEN_LIMITS = {
    "openai:gpt-4.1-mini": 1047576,
    "openai:gpt-4.1-nano": 1047576,
    "openai:gpt-4.1": 1047576,
    "openai:gpt-4o-mini": 128000,
    "openai:gpt-4o": 128000,
    "openai:o4-mini": 200000,
    "openai:o3-mini": 200000,
    "openai:o3": 200000,
    "openai:o3-pro": 200000,
    "openai:o1": 200000,
    "openai:o1-pro": 200000,
    "anthropic:claude-opus-4": 200000,
    "anthropic:claude-sonnet-4": 200000,
    "anthropic:claude-3-7-sonnet": 200000,
    "anthropic:claude-3-5-sonnet": 200000,
    "anthropic:claude-3-5-haiku": 200000,
    "google:gemini-1.5-pro": 2097152,
    "google:gemini-1.5-flash": 1048576,
    "google:gemini-pro": 32768,
    "cohere:command-r-plus": 128000,
    "cohere:command-r": 128000,
    "cohere:command-light": 4096,
    "cohere:command": 4096,
    "mistral:mistral-large": 32768,
    "mistral:mistral-medium": 32768,
    "mistral:mistral-small": 32768,
    "mistral:mistral-7b-instruct": 32768,
    "ollama:codellama": 16384,
    "ollama:llama2:70b": 4096,
    "ollama:llama2:13b": 4096,
    "ollama:llama2": 4096,
    "ollama:mistral": 32768,
    "bedrock:us.amazon.nova-premier-v1:0": 1000000,
    "bedrock:us.amazon.nova-pro-v1:0": 300000,
    "bedrock:us.amazon.nova-lite-v1:0": 300000,
    "bedrock:us.amazon.nova-micro-v1:0": 128000,
    "bedrock:us.anthropic.claude-3-7-sonnet-20250219-v1:0": 200000,
    "bedrock:us.anthropic.claude-sonnet-4-20250514-v1:0": 200000,
    "bedrock:us.anthropic.claude-opus-4-20250514-v1:0": 200000,
    "anthropic.claude-opus-4-1-20250805-v1:0": 200000,
    "dashscope:qwen3.5-ocr": 128000,
    "dashscope:qwen-plus": 128000,
    "dashscope:qwen-max": 128000,
    "dashscope:qwen3.7-plus-2026-05-26": 131072,
    "deepseek:deepseek-chat": 65536,
}

# 根据模型标识字符串，查询该模型支持的最大上下文token上限。
def get_model_token_limit(model_string):
    """Look up the token limit for a specific model.
    
    Args:
        model_string: The model identifier string to look up
        
    Returns:
        Token limit as integer if found, None if model not in lookup table
    """

    # Search through known model token limits
    # 遍历字典
    for model_key, token_limit in MODEL_TOKEN_LIMITS.items():
        # 模糊匹配：传入 openai:gpt-4o-2025-08-01，字典 key 是 openai:gpt-4o，in 判断命中，成功返回 128000
        if model_key in model_string:
            return token_limit
    
    # Model not found in lookup table
    # 上层代码收到 None，代表未知模型，可以设置兜底阈值（比如默认 32768）
    return None

# 从后往前查找最后一条 AI 输出消息，删掉这条 AI 消息以及它之后所有内容，返回剩余消息列表
# 为什么这么设计？open_deep_research 循环流程：LLM (主管) → 调用 ConductResearch 工具 → 工具返回信息 → 再次交给主管思考，在 Agent 的 TAO 循环中，Token 爆炸通常是因为：AI 产生了一条“太长”的思考链，或者 AI 调用了工具后，工具返回了超长的网页原文（Observation）。如果直接切掉最后一条 UserMessage，可能会破坏用户原始意图。但切掉 AI 自己生成的废话+工具返回的超长原文，相当于告诉 Agent：“你刚才想了啥、搜了啥全忘了，重头再想，但记住用户最初的提问还在。”这是最安全、最小程度伤害上下文语义的截断方式
def remove_up_to_last_ai_message(messages: list[MessageLikeRepresentation]) -> list[MessageLikeRepresentation]:
    """Truncate message history by removing up to the last AI message.
    
    This is useful for handling token limit exceeded errors by removing recent context.
    
    Args:
        messages: List of message objects to truncate
        
    Returns:
        Truncated message list up to (but not including) the last AI message
    """

    # Search backwards through messages to find the last AI message
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            # Return everything up to (but not including) the last AI message
            return messages[:i]
    
    # No AI messages found, return original list
    return messages

##########################
# Misc Utils
# 这一组是通用工具函数，不属于检索、不属于 Agent 节点，是支撑整个系统的底层小工具，负责：日期格式化、配置值解析、各类 API 密钥读取
##########################

# 生成人类可读英文日期，直接注入所有系统 Prompt（你之前看到所有 prompt 里的 {date} 占位符来源），联网调研类任务时效性极强，把当前日期告诉大模型，模型能区分信息新旧，避免拿过时资料当做有效结论
def get_today_str() -> str:
    """Get current date formatted for display in prompts and outputs.
    
    Returns:
        Human-readable date string in format like 'Mon Jan 15, 2024'
    """
    # 一串可读的日期时间文本 2026-08-03 15:30:45.123456，但是在内存里依然是个对象，不是字符串
    now = datetime.now() 
    # 格式化成可读的日期时间文本（纯字符串）
    return f"{now:%a} {now:%b} {now.day}, {now:%Y}"

# 统一读取配置字段，专门处理枚举 Enum 类型（还记得配置文件里的 SearchAPI(Enum) 吗）, Configuration 配置类里存在枚举字段，序列化为消息、传给工具时，统一调用这个函数抹平类型差异，防止报错
def get_config_value(value):
    """Extract value from configuration, handling enums and None values."""

    if value is None:
        return None
    if isinstance(value, str):  # 字符串 → 直接返回
        return value
    elif isinstance(value, dict):
        return value
    else:  # 其他类型（主要是 Enum 实例） → 返回 .value
        return value.value

# 这个函数让 Deep Research 系统可以在同一个程序中，根据配置动态切换调用 OpenAI、Anthropic 或 Google 的模型，并且自动去找对应的正确 API Key，互不干扰
def get_api_key_for_model(model_name: str, config: RunnableConfig):
    """Get API key for a specific model from environment or config."""

    # “上帝开关”：这个环境变量是个总指挥。如果设为 true，走分支 A；设为 false（默认），走分支 B
    should_get_from_config = os.getenv("GET_API_KEYS_FROM_CONFIG", "false")
    # 统一小写模型名称，消除大小写干扰，项目里模型命名规范：openai:gpt-4o、anthropic:claude-3-5-sonnet
    model_name = model_name.lower()

    # 分支 A：从运行时配置里拿（前端传入密钥场景）
    if should_get_from_config.lower() == "true":
        api_keys = config.get("configurable", {}).get("apiKeys", {})
        if not api_keys:
            return None
        if model_name.startswith("openai:"):
            return api_keys.get("OPENAI_API_KEY")
        elif model_name.startswith("anthropic:"):
            return api_keys.get("ANTHROPIC_API_KEY")
        elif model_name.startswith("google") or model_name.startswith("gemini:"):
            return api_keys.get("GOOGLE_API_KEY")
        elif model_name.startswith("dashscope:"):
            return api_keys.get("DASHSCOPE_API_KEY")
        elif model_name.startswith("deepseek:"):
            return api_keys.get("DEEPSEEK_API_KEY")
        return None
    
    # 分支 B：从服务器的环境变量里拿（传统 .env 文件）
    else:
        if model_name.startswith("openai:"): 
            return os.getenv("OPENAI_API_KEY")
        elif model_name.startswith("anthropic:"):
            return os.getenv("ANTHROPIC_API_KEY")
        elif model_name.startswith("google") or model_name.startswith("gemini:"):
            return os.getenv("GOOGLE_API_KEY")
        elif model_name.startswith("dashscope:"):
            return os.getenv("DASHSCOPE_API_KEY")
        elif model_name.startswith("deepseek:"):
            return os.getenv("DEEPSEEK_API_KEY")
        return None

# 逻辑和 get_api_key_for_model 完全同源，只是专门用来获取Tavily 联网搜索密钥
def get_tavily_api_key(config: RunnableConfig):
    """Get Tavily API key from environment or config."""

    should_get_from_config = os.getenv("GET_API_KEYS_FROM_CONFIG", "false")
    if should_get_from_config.lower() == "true":
        api_keys = config.get("configurable", {}).get("apiKeys", {})
        if not api_keys:
            return None
        return api_keys.get("TAVILY_API_KEY")
    else:
        return os.getenv("TAVILY_API_KEY")
