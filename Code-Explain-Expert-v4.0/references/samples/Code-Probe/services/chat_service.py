###################
# Chat Service — RAG 对话会话持久化管理模块，用本地 sessions.json JSON 文件存储每一轮用户聊天会话，解决两个核心问题：多轮上下文记忆：同一个对话窗口下，AI 能记住上一轮提问、引用过的代码片段，实现连续追问；会话隔离：不同浏览器 / 不同用户生成独立 session_id，互不干扰，还能绑定固定仓库 repo_id
# 用户每问一句话，这里串起完整流程：调 search_service 找相关代码 → 拼成提示词 →
# 调 llm_client 让模型流式回答 → 把对话存进 sessions.json。
# 按 4 个阶段把过程推给前端（检索到什么 → 提示词长啥样 → 回答逐字出来 → 完成），
# 让 RAG 过程透明可见，这是本项目最大的亮点。
###################

import json
import os
import uuid
from datetime import datetime
from typing import AsyncGenerator
from app.config.defaults import DATA_DIR
from app.config.manager import get_settings
from app.services import search_service, llm_client

# 会话落盘到存储文件 data/sessions.json，assistant 消息带 rag_data 快照（能回放当时的 RAG 过程）。
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

# _load_sessions（读会话）：加载所有会话
def _load_sessions() -> list[dict]:
    
    # 确保数据文件夹一定存在，不存在自动创建，防止第一次运行报路径不存在错误
    os.makedirs(DATA_DIR, exist_ok=True)

    # 判断文件是否存在：
    #  文件还没生成（首次启动）→ 直接返回空列表 []
    #  文件存在 → json.load() 读取全部会话数组返回
    if not os.path.exists(SESSIONS_FILE):
        return []
    # 显式 UTF-8：会话内容含中文/代码，Windows 默认 GBK 读会崩
    with open(SESSIONS_FILE, encoding="utf-8") as f:
        return json.load(f)

# _save_sessions（写会话）：覆盖写入会话文件
def _save_sessions(sessions: list[dict]) -> None:

    # 确保数据文件夹一定存在，不存在自动创建，防止第一次运行报路径不存在错误
    os.makedirs(DATA_DIR, exist_ok=True)
    # 覆盖写入会话文件（显式 UTF-8 + ensure_ascii=False，中文原样保存）
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

# get_sessions（会话列表）：给前端侧边栏展示历史会话用
def get_sessions() -> list[dict]:
    
    return _load_sessions()

# create_session（建会话）：创建新对话会话（前端新建聊天窗口调用）,
# repo_id 允许为 None：用户可以先创建空会话，进入聊天界面后再选择仓库。前端路由设计如此
def create_session(repo_id: str | None = None) -> dict:
    
    session = {
        "session_id": uuid.uuid4().hex[:12],
        "repo_id": repo_id,
        "created_at": datetime.utcnow().isoformat(),
        "messages": [],
    }

    # 加载全部会话 → 追加新会话对象 → 写回 JSON 持久化 → 返回新建的会话给前端
    sessions = _load_sessions()
    sessions.append(session)
    _save_sessions(sessions)
    return session

# get_session（取单个会话）：根据 ID 查询单个会话
# 用途：用户发送提问时，先拉取历史上下文
def get_session(session_id: str) -> dict | None:

    # 遍历会话列表精准匹配 session_id，找到返回会话字典，找不到返回 None
    for s in _load_sessions():
        if s["session_id"] == session_id:
            return s
    return None

# _update_session（更新会话）：更新会话（核心用于追加聊天记录）。给 chat_stream 阶段4 追加消息用
# updates：{"messages": new_messages_list}，最常用场景：往 messages 列表追加用户提问、AI 回答记录
def _update_session(session_id: str, updates: dict) -> dict | None:
    
    sessions = _load_sessions()
    for s in sessions:
        if s["session_id"] == session_id:
            s.update(updates)
            _save_sessions(sessions)
            return s
    return None

# delete_session（删会话）：删除指定会话
# 用途：前端点击清空对话、关闭会话按钮调用
def delete_session(session_id: str) -> bool:
    
    sessions = _load_sessions()
    # 列表推导式过滤：保留所有 session_id 不等于目标 ID 的会话；
    new = [s for s in sessions if s["session_id"] != session_id]
    # 长度没变 → 没找到该会话，返回 False
    if len(new) == len(sessions):
        return False
    # 长度减少 → 删除成功，写入新列表，返回 True
    _save_sessions(new)
    return True

# estimate_tokens（token 粗估）：粗略估算 token 数量，
# 仅给前端展示"提示词大概多大"参考
def estimate_tokens(text: str) -> int:

    # 1 token ≈ 4 字符。极不精确（中文严重低估）
    return len(text) // 4

# build_prompt（RAG 核心提示词组装函数）：接收用户提问、会话 ID、仓库 ID，自动完成三件事：
#  1.调用向量检索 search_service.search 召回相关代码片段；
#  2.把系统角色指令 + 检索到的代码上下文 + 历史聊天记录 + 当前用户问题，规整打包成结构化 Prompt 模块；
#  3.粗略估算整段 Prompt 总 Token 长度，方便控制大模型上下文窗口上限。
#  4.它是检索 → 拼接上下文 → 送入 LLM中间最关键的胶水层
# build_prompt 是给预览接口 /chat/prompt-preview 使用的，只做组装，不调用大模型、不流式输出。前端点「预览 Prompt」，直接把拼接好的上下文、检索结果返回给页面看，用于调试 RAG 效果，不真正跑 LLM
def build_prompt(message: str, session_id: str | None, repo_id: str, top_k: int | None = None) -> dict:
    
    # 读取全局配置里给 AI 设定的角色指令
    settings = get_settings()
    system_prompt = settings["system_prompt"]
    # 优先使用接口传入的 top_k，否则用配置默认的召回条数
    k = top_k if top_k is not None else settings["top_k"]

    # 调用向量检索 search_service.search 在指定仓库做语义查询，拿到所有相似度命中的代码块、距离、分数、文件路径、行号等信息
    retrieval = search_service.search(message, repo_id, k)

    # 把检索到的多条代码片段格式化拼接成上下文字符串
    context_parts = []
    for r in retrieval["results"]:
        # context 格式：📄 文件路径 (L起-止)\n内容
        # 文件路径+行号让 LLM 能在回答里引用具体位置（配合 system_prompt 的要求）。
        context_parts.append(
            f"📄 {r['file_path']} (L{r['line_start']}-{r['line_end']})\n{r['content']}"
        )
        # 拼接出来这一条就是：
        # 📄 src/service/login.py (L26-L38)
        # def verify_password(raw_pwd: str, hash_pwd: str) -> bool:
        #     """校验用户密码"""
        #     import bcrypt
        #     return bcrypt.checkpw(raw_pwd.encode("utf-8"), hash_pwd.encode("utf-8"))

    # 多条结果之间用 \n\n---\n\n 分割线隔开，结构清晰，大模型更容易区分不同文件、不同代码块
    # context_str 最终就是一整块完整的参考知识库上下文
    context_str = "\n\n---\n\n".join(context_parts)

    # 读取历史对话记录（多轮记忆）
    history = []

    # 只有传入了 session_id 才去会话文件查找；后续拼入 Prompt，实现连续追问、上下文记忆
    if session_id:
        session = get_session(session_id)
        if session:
            history = session.get("messages", [])
            # [
            #     {"role":"user", "content":"用户上一轮问题"},
            #     {"role":"assistant", "content":"AI上一轮回答"}
            # ]

    # 把构成最终提示词的四大组件分开存储，方便：
    # 上层打印日志调试；
    # 按需裁剪（比如超长时截断 history）；
    # 传给 LLM 时自由组装成 OpenAI 兼容的 messages 数组
    prompt_parts = {
        "system": system_prompt,
        "context": context_str,
        "history": history,
        "user_message": message,
    }

    # 把系统提示、检索上下文、当前问题、所有历史回答全部拼接成超长字符串，用于粗略估算总 Token 数量
    total_text = system_prompt + context_str + message
    for m in history:
        total_text += m.get("content", "")

    return {
        "prompt_parts": prompt_parts,
        "total_tokens_estimate": estimate_tokens(total_text),
        "retrieval_results": retrieval["results"],
    }
    # 返回三层关键数据：
    # - prompt_parts：拆分好的提示词四大组成模块，供 LLM 调用层拼装请求体；
    # - total_tokens_estimate：预估总 Token，用于窗口限制判断；
    # - retrieval_results：原始检索命中列表，方便前端展示引用的代码片段、相似度分数、文件行号，但这些内容只在前端本地展示（RagDrawer 抽屉），不会通过 API 的 SSE 流推送到前端（SSE 只推 token）。这保证了代码内容不会在流式传输中被截获泄露

# chat_stream（流式问答，SSE 4 阶段）：整个 RAG 系统最终对外输出的流式问答顶层接口，基于 Python AsyncGenerator 异步生成器实现 SSE 流式打字机效果
# 完整执行流水线：校验会话 → 向量检索代码 → 向前端推送检索结果 → 拼装 LLM 完整消息体 → 调用大模型流式逐字返回回答 → 对话结束自动持久化本轮问答到会话 JSON → 推送结束标识
# chat_stream 是真正聊天主逻辑，要做流式 SSE 输出，要yield分段返回数据，还要保存会话记录
async def chat_stream(
    message: str,                   # 用户当前提问文本
    session_id: str,                # 必填会话 ID，用来读取历史对话、最后更新会话记录
    repo_id: str                    # 必填仓库 ID，限定在该仓库向量库检索代码
) -> AsyncGenerator[dict, None]:    # 异步生成器，持续 yield 字典数据包，前端通过 EventSource/SSE 逐条接收渲染
    
    # 读取全局配置，拿到 AI 的角色指令
    settings = get_settings()
    system_prompt = settings["system_prompt"]

    # 根据 session_id 查询会话，会话不存在直接抛异常阻断流程，防止脏数据
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")

    # 在指定仓库做语义召回代码块，拿到所有相似度命中的代码块、距离、分数、文件路径、行号等信息
    retrieval = search_service.search(message, repo_id, settings["top_k"])
    yield {"type": "retrieval", "results": retrieval["results"]}

    # Step 2: build prompt
    context_parts = []
    for r in retrieval["results"]:
        context_parts.append(
            f"📄 {r['file_path']} (L{r['line_start']}-{r['line_end']})\n{r['content']}"
        )
    context_str = "\n\n---\n\n".join(context_parts)

    history = session.get("messages", [])

    prompt_parts = {
        "system": system_prompt,
        "context": context_str,
        "history": history,
        "user_message": message,
    }
    total_text = system_prompt + context_str + message
    for m in history:
        total_text += m.get("content", "")

    # yield 推送 type: prompt 数据包：
    # 前端可用于调试展示完整提示词结构、上下文长度，生产环境可隐藏
    yield {
        "type": "prompt",
        "prompt_parts": prompt_parts,
        "total_tokens_estimate": estimate_tokens(total_text),
    }

    # 组装符合 OpenAI 规范的 messages 请求数组（核心 LLM 入参）
    messages = [{"role": "system", "content": system_prompt}]
    if context_str:
        messages.append({"role": "system", "content": f"代码上下文:\n{context_str}"})
    for m in history:
        messages.append(m)
    messages.append({"role": "user", "content": message})
    # 最终数组结构示例：
    # [
    #     {"role":"system", "content":"你是资深代码专家..."},
    #     {"role":"system", "content":"代码上下文：📄 main.py(L10-L20) ..."},
    #     # 循环插入历史对话
    #     {"role":"user", "content":"上一轮问题"},
    #     {"role":"assistant", "content":"上一轮回答"},
    #     # 当前最新提问
    #     {"role":"user", "content":"本次用户提问"}
    # ]
    # 小细节：把检索到的代码上下文塞进第二条 system 角色，约束力更强，大模型更容易遵循「只参考给定代码回答」的规则。

    # full_response 字符串全局拼接完整回答，用于最后存入会话
    full_response = ""
    try:
        # 异步迭代大模型返回的每一小段字符追加到 full_response 字符串
        async for token in llm_client.stream_chat(messages, settings["llm_model"]):
            full_response += token
            # 前端持续接收 chunk 类型数据，追加到回答框，实现打字机动态输出；
            yield {"type": "chunk", "content": token}
    except Exception as e:
        # LLM 调用超时、接口报错、密钥错误等，直接推送 type: error 给前端展示错误信息，并终止函数
        yield {"type": "error", "message": str(e)}
        return

    # 对话结束，更新会话持久化历史记录（RAG 记忆闭环）
    # 在原有历史 history 基础上追加两条记录：用户本轮提问 + AI 完整回复；
    new_messages = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": full_response, "rag_data": {
            "retrieval": retrieval["results"],
            "prompt_parts": prompt_parts,
            "total_tokens_estimate": estimate_tokens(total_text),
        }},
    ]
    # 重点扩展：assistant 消息里额外挂载了 rag_data 元数据，把本次检索结果、Prompt 结构、Token 消耗一起存在 sessions.json；用处：后续回看历史对话时，可以还原当时引用了哪些代码片段，溯源 RAG 上下文

    # 调用 _update_session 写回本地 JSON 文件，完成会话持久化
    _update_session(session_id, {"messages": new_messages})

    # 推送最终结束标志
    yield {"type": "done", "session_id": session_id}
