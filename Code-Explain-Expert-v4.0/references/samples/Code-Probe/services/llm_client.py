###################
# LLM Client — 这是大模型与 Embedding 的客户端封装层，项目里所有对 OpenAI 兼容服务的调用都经这里：对话模型、流式回答、Embedding 向量化，把 OpenAI SDK 的同步、异步、向量嵌入全部包装起来；上层业务代码（search_service、chat_stream）不直接 import openai，全部调用本文件函数
# 好处：所有密钥、base_url、模型名称统一在这里处理；后续切换兼容 OpenAI 接口的第三方模型（DeepSeek、Qwen、Ollama）只改这一层
###################

from typing import AsyncGenerator
import openai
from app.config.manager import get_settings

# get_llm_client（同步客户端）：返回同步OpenAI 客户端对象
# 使用场景：普通一次性请求，不需要流式打字机。但是本项目里几乎没用到，更多是备用
def get_llm_client() -> openai.OpenAI:
    
    # key 为空时用 "sk-placeholder" 占位，避免 openai 库在创建时就抛"缺 key"，
    # 把真正的 401 留到实际请求时报，由调用方感知。
    s = get_settings()
    return openai.OpenAI(
        api_key=s["llm_api_key"] or "sk-placeholder",
        base_url=s["llm_base_url"],
    )

# get_async_llm_client（异步客户端）：返回异步大模型客户端，给 SSE 流式问答用（stream_chat流式对话底层就是调用它）。
def get_async_llm_client() -> openai.AsyncOpenAI:

    # AsyncOpenAI：异步版本客户端，async/await，不会阻塞 web 服务
    s = get_settings()
    return openai.AsyncOpenAI(
        api_key=s["llm_api_key"] or "sk-placeholder",
        base_url=s["llm_base_url"],
    )

# get_embedding_client（向量化客户端）：返回同步客户端，生成向量一般用同步就足够，索引仓库的时候批量生成向量
def get_embedding_client() -> openai.OpenAI:

    # 支持阿里云免费向量大模型
    s = get_settings()
    key = s["embedding_api_key"] or s["llm_api_key"] or "sk-placeholder"
    base_url = s["embedding_base_url"] or s["llm_base_url"]
    return openai.OpenAI(api_key=key, base_url=base_url)

# embed_texts（多个文本批量向量化）：在建索引的时候，传入一堆 chunk 代码片段
def embed_texts(texts: list[str]) -> list[list[float]]:
    
    # 返回每段文本对应的向量，供 ChromaDB 存储。
    s = get_settings()
    client = get_embedding_client()
    response = client.embeddings.create(
        input=texts,
        model=s["embedding_model"],
    )
    return [item.embedding for item in response.data]

# embed_query（单条向量化）：给检索用，把用户问题转向量去查库。取返回列表第 0 项，得到单个向量
# 调用位置：search_service.py，用户提问，把问题转为向量做向量检索
def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]

# stream_chat（流式对话）：RAG 问答的"嘴巴"——异步逐 token 吐字，给 chat_service 的 SSE 阶段3 用。
async def stream_chat(messages: list[dict], model: str) -> AsyncGenerator[str, None]:
    
    # 获取异步客户端
    client = get_async_llm_client()

    # stream=True 开启流式模式，大模型会分片返回内容，不是一次性返回完整结果
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
    )
    # OpenAI 流式返回的 chunk 简化结构：
    # {
    #     "choices": [
    #         {
    #         "delta": {"content":"返回的一小段文字"},
    #         "finish_reason": None
    #         }
    #     ]
    # }

    try:
        async for chunk in stream:

            # 有些空分片，直接跳过
            if not chunk.choices:
                continue

            # delta 就是增量新增内容
            delta = chunk.choices[0].delta
            # 只有 content 不为 None，才 yield 吐出字符串
            # 注意：只吐出纯文本 token 字符串，不包字典；上层chat_stream拿到字符串，再包装成{"type":"chunk","content":token}
            if delta.content:
                yield delta.content
    finally:
        # 无论正常结束还是异常报错，强制关闭流连接，释放网络资源，防止连接泄露
        await stream.close()
