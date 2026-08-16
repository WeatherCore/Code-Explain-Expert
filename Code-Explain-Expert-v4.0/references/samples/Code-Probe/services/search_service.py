###################
# Search Service — 这是RAG 系统核心检索查询入口，前面 index_service.py 是建库写入向量，这个函数就是查表语义召回
# 负责"找"：把用户的问题转成向量，去 ChromaDB 里按余弦相似度捞最像的几段代码，算出相似度分数返回。聊天和搜索试验场都调它，是项目里复用最多的核心函数之一。
# 完整链路：用户输入自然语言问题 → 生成 Query 向量 → 在 Chroma 对应仓库集合做余弦相似度检索 → 换算相似度分数 → 格式化返回命中的代码片段、文件路径、行号、置信度，最终送给大模型做上下文回答
###################

import os
from app.config.defaults import DATA_DIR
from app.config.manager import get_settings
from app.services import llm_client, index_service

# search（语义检索）：问题向量化 → 取 collection → 校验 cosine → 查 Top-K → 算 score。
# 返回的 hits 给 LLM 当上下文，score 给前端按相似度排序展示。
#  query: str：用户的自然语言提问，例如：用户登录逻辑在哪写的
#  repo_id: str：限定只在指定这一个仓库里检索，多仓库数据物理隔离在不同 Collection
#  top_k：可选，本次要返回多少条最相似结果；不传就用系统配置默认值
def search(query: str, repo_id: str, top_k: int | None = None) -> dict:

    # 读配置文件
    settings = get_settings()
    # 如果调用方手动传了top_k（比如前端指定返回 5 条），优先用传入值
    k = top_k if top_k is not None else settings["top_k"]

    # 专门处理单条文本生成向量的方法
    query_embedding = llm_client.embed_query(query)
    # 留前 10 维给前端展示"问题向量长什么样"，是 RAG 可视化的细节（非检索用途）。
    query_embedding_preview = query_embedding[:10]

    # 连接本地持久化 Chroma 数据库
    import chromadb
    chroma_path = os.path.join(DATA_DIR, "chroma")
    client = chromadb.PersistentClient(path=chroma_path)

    try:
        # 获取向量集合，校验仓库是否已经完成索引（关键容错）
        collection = client.get_collection(f"repo_{repo_id}")
    except Exception:
        # 获取失败直接抛异常：告诉前端该仓库还没执行索引，无法检索，必须先跑一遍index_repo建库
        raise ValueError(f"No index found for repo: {repo_id}. Please index first.")

    # Chroma 创建集合时指定了 metadata={"hnsw:space": "cosine"}，检索时校验一下，防止用户误传 L2 库，因为后面有一句核心公式：score = 1 - distance，这个公式只适用于余弦距离
    col_meta = collection.metadata or {}
    if col_meta.get("hnsw:space") != "cosine":
        raise ValueError(
            f"Repo {repo_id} was indexed with L2 distance. "
            "Please re-index to enable cosine similarity scores."
        )

    # 获取集合总向量条数，防止查询条数超限，还可以返回给前端用作展示统计信息
    total_searched = collection.count()
    # n_results 取 min(k, total)：k 可能超过库容量（小仓库），直接传 k 会让 ChromaDB 报错。
    results = collection.query(
        query_embeddings=[query_embedding],
        # 注意必须是二维列表，支持批量多条提问检索；这里只传 1 条，包一层列表
        n_results=min(k, total_searched),
        # 保护逻辑：比如配置默认top_k=10，但这个仓库只索引出 3 个 chunk，min(10,3)只查 3 条，避免 Chroma 报参数越界错误
        include=["documents", "metadatas", "distances"],
        # 指定查询要带回的数据
    )
    # query 返回的数据结构（嵌套列表）：
    #  {
    #     "ids": [["chunk_1", "chunk_2"]],
    #     "documents": [["代码片段1", "代码片段2"]],
    #     "metadatas": [[{}, {}]],
    #     "distances": [[0.123, 0.456]]
    #  }
    # 外层多一层列表是为了兼容批量 query，所以下面代码都取[0]剥离外层

    hits = []
    ids = results["ids"][0]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    # zip并行遍历 4 个等长数组，一一对应每一条检索结果：chunk 唯一 ID、代码内容、元数据字典、余弦距离值。
    for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):

        # 余弦距离（cosine distance）的定义是 1 - cosine_similarity。范围是 [0, 2]
        # 为什么要套 max(0.0, ...)？ 因为 dist 大于 1 时（即向量方向相反），分数会变成负数。对于搜索引擎来说，“完全不相关”和“负分”在视觉上没有区别，统一裁切到 0，让分数范围固定在 [0, 1] 之间，前端展示更友好，前端还会直接乘以 100 展示：相似度 92.34%，非常直观
        score = max(0.0, 1.0 - dist)

        # 组装单条 hit 结构体
        hits.append({
            "chunk_id": chunk_id,
            "content": doc,
            "file_path": meta.get("file_path", ""),
            "line_start": meta.get("line_start", 0),
            "line_end": meta.get("line_end", 0),
            "score": round(score, 4),
            "distance": round(dist, 6),
        })
        # 循环结束就是排序好的 TopK 相似代码块列表（按相似度从高到低）

    return {
        "query_embedding_preview": [round(v, 6) for v in query_embedding_preview],
        "results": hits,
        "total_searched": total_searched,
    }