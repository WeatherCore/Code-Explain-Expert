###################
# Index Service — 整个代码仓库 RAG 知识库的「索引核心业务服务层」
# 所有和代码解析、文件遍历、文本滑动切块、向量入库、仓库状态管理、索引数据查询、仓库统计分析相关的业务逻辑，全部封装在这个文件里
# 它是整套系统最核心的业务模块，承接上层接口调用，向下调用文件读写、Chroma 向量库、Embedding 模型、本地 JSON 持久化工具
###################

import json
import os
from app.config.defaults import DATA_DIR
from app.config.manager import get_settings
from app.services import repo_service, llm_client

# 索引服务：RAG 索引侧的核心——分块算法 + 向量化 + 入库。这是"建库"的引擎。
# 三大职责：chunk_text（怎么切）/ index_repo（怎么编排建库）/ get_chunk_context（怎么定位回源码）。
# 数据落盘两份：{repo_id}_chunks.json（分块内容，给前端展示）+ chroma/repo_{repo_id}（向量，给检索用）。

# _chunks_file（分块文件路径）：每个仓库生成一个独立的 chunks.json 完整路径，存全部分块内容
def _chunks_file(repo_id: str) -> str:

    # 最终生成文件示例：data/abc12345_chunks.json
    return os.path.join(DATA_DIR, f"{repo_id}_chunks.json")

# _load_chunks（读分块）：读取某个仓库已缓存的所有文本块
# 用途：重新解析仓库、追加文件、检索知识库时读取历史分块数据
def _load_chunks(repo_id: str) -> list[dict]:
    
    f = _chunks_file(repo_id)

    # 判断文件不存在 → 直接返回空列表，防止FileNotFoundError
    if not os.path.exists(f):
        return []
    # 文件存在，打开把 JSON 文件解析成 Python 字典列表返回（显式 UTF-8，chunk 内容可能含中文/emoji）
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)

# _save_chunks（写分块）：把分块列表整体覆盖持久化写入 JSON
def _save_chunks(repo_id: str, chunks: list[dict]) -> None:

    # 兜底保证data文件夹一定存在，避免写文件报错
    os.makedirs(DATA_DIR, exist_ok=True)

    # 把分块列表整体覆盖持久化写入 JSON
    # 显式 UTF-8 + ensure_ascii=False：代码内容含中文/emoji 时必须 UTF-8，GBK 会抛 UnicodeEncodeError
    with open(_chunks_file(repo_id), "w", encoding="utf-8") as fh:
        # json.dump 把切块字典列表存成 JSON
        json.dump(chunks, fh, indent=2, ensure_ascii=False)


# chunk_text（分块算法）：本项目最值得读的函数。字符数驱动 + 行边界对齐 + overlap 重叠。
# 核心思想：按 chunk_size 字符定大致位置，再把 end_char 向后扩到下一个 \n——保证块不切在行中间。
# 记录 line_start/line_end 行号，让回答能定位回源码位置
def chunk_text(content: str,         # 单个.py文件读取出来的完整源码字符串
               file_path: str,       # 该文件的绝对路径，用于记录归属文件
               repo_id: str,         # 所属仓库 ID，做唯一标识
               chunk_size: int,      # 单个分块最大字符长度（配置默认 1000）
               overlap: int          # 相邻两个块之间重叠多少字符（默认 200）
               ) -> list[dict]:      # 返回：当前文件拆分完成后的所有 chunk 字典数组

    # 把整个源码按换行符 \n 切割，keepends=True 保留每行末尾的换行符，保证切片后的文本和原文件完全一致
    # 分割后 ["a = 1\n", "b = 2"]
    lines = content.splitlines(keepends=True)

    # 空列表，用来存放最终拆分好的所有块
    chunks = []

    # 记录每一行在整个文件字符串中开始的字符偏移量，用于快速定位行号
    line_starts = []
    pos = 0

    # 遍历每一行，记录每行的起始字符偏移量
    # 第一个必是0，最后一个必是文件总长，[0,8,14]
    for line in lines:
        line_starts.append(pos)
        pos += len(line)
    line_starts.append(pos)  # 循环结束后手动追加最后总长度作为 sentinel，方便边界判断
    # 为什么要有哨兵：下面的 char_to_line 函数查行号时，必须有一个“末尾值”来防止索引越界

    # char_to_line（字符偏移 → 代码行号转换器）：线性扫描 line_starts 找第一个大于 offset 的位置。
    def char_to_line(char_offset: int) -> int:
        # 入参 char_offset：某个字符在全文中的字符偏移位置
        # 出参：该字符所在代码行的行号（1-based，从1开始计数）

        for i, ls in enumerate(line_starts):
            # 找到第一个大于 char_offset 的下标，返回它的索引 i，就是所在行号
            if ls > char_offset:
                return i
            
        # 全部遍历完没找到，说明超出最后一行，直接返回总行数
        return len(lines)

    # 当前文件内切块序号，从 0 自增，用来拼接唯一 chunk_id
    chunk_index = 0
    # 本轮切块起始字符下标，初始从文件开头 0 位置开始
    start_char = 0

    while start_char < len(content):

        # 理想结束位置：起始位置 + 单块最大字符数
        # min 防止最后一块超出文件总长度，避免字符串切片越界
        end_char = min(start_char + chunk_size, len(content))

        # 智能对齐到换行符，不把一行代码从中间劈开（非常关键的优化）
        if end_char < len(content):
            # 从 end_char 位置往后找第一个换行符
            newline_pos = content.find("\n", end_char)

            if newline_pos != -1:
                # 找到换行，就把结束位置拉到换行下一位，保证切块永远完整包裹整行代码
                # 避免出现半行函数、半行注释被切分开，破坏代码语义
                end_char = newline_pos + 1

        # 字符串切片 content[start_char:end_char] 拿到本轮最终切块的代码文本
        chunk_content = content[start_char:end_char]

        # 记录当前块的起始行和结束行，用于回答时定位回源码位置
        line_start = char_to_line(start_char)
        line_end = char_to_line(end_char - 1)

        # overlap markers
        # overlap 行号记录：从第二块起，标记与上一块重叠的行区间，给前端展示"这块和上块重叠多少"。
        overlap_start = None
        overlap_end = None

        # 前两块及以后才存在重叠，chunk_index>0 跳过第 0 块
        if chunk_index > 0:
            # 当前块开头位置往后走 overlap 个字符，其实就是重叠区域的结束字符位置，
            overlap_end_char = start_char + overlap
            # 重叠区域的起始行天然就等于整个切块的起始行
            overlap_start = line_start
            # 重叠区域的结束行：从重叠结束字符位置，找到它所在行号
            # min 作用：防止重叠长度超出当前块本身的结尾
            overlap_end = char_to_line(min(overlap_end_char, end_char) - 1)

        # 用 / 分隔仓库 ID 和文件路径
        # 用 # 分隔文件路径和当前文件内的块序号
        # 作用：全局唯一主键，向量库存 Embedding 时绑定这个 ID，检索到向量可以反向查到完整代码块
        chunk_id = f"{repo_id}/{file_path}#{chunk_index}"
        # 最终：a2f9d371//uploads/a2f9d371/main.py#0

        chunks.append({
            "chunk_id": chunk_id,                    # 全局唯一主键
            "repo_id": repo_id,                      # 归属哪个仓库，批量清理、按仓库过滤用
            "content": chunk_content,                # 切割好的代码文本（给 LLM 做上下文）
            "file_path": file_path,                  # 原始文件路径，回答时展示文件位置
            "line_start": line_start,                # 当前块在原文件第几行到第几行，前端跳转代码行用
            "line_end": line_end,                    # 当前块在原文件第几行到第几行，前端跳转代码行用
            "char_count": len(chunk_content),        # 当前块字符长度，做统计面板展示
            "chunk_index": chunk_index,              # 单个文件内部的块编号
            "overlap_start": overlap_start,          # 和上一块重叠内容对应的起止行，可用于展开上下文
            "overlap_end": overlap_end,              # 和上一块重叠内容对应的起止行，可用于展开上下文
        })
        # LLM 回答用户问题时，会命中某一段代码块，前端会渲染这段代码，并且高亮标注和上一块重复的重叠行
        # 作用：
        # 1.让使用的人一眼看懂代码分段规则；
        # 2.知道哪部分是重复冗余内容，不用反复阅读相同代码；
        # 3.提升知识库阅读体验，降低代码阅读混淆度

        # 如果结束下标已经等于或超过整个源码字符串总长度，说明已经切完最后一块，直接跳出外层 while 循环，不再生成下一个块
        if end_char >= len(content):
            break

        # 重叠滑动窗口核心逻辑：下一块 chunk_content 回退 overlap 字符，与当前块重叠，防语义在边界断裂。
        next_start = end_char - overlap

        # 防死循环：overlap 过大时 next_start 可能 ≤ start_char（块没前进），
        # 强制前进 max(1, chunk_size-overlap) 字符，保证每块至少前进 1 字符。
        if next_start <= start_char:
            next_start = start_char + max(1, chunk_size - overlap)

        # 更新下一轮的起始位置，继续切下一块
        start_char = next_start
        # 块序号自增
        chunk_index += 1

    return chunks


# index_repo(repo_id) 是仓库完整向量化索引入口主函数，做一整套流水线：
#  1.根据仓库 repo_id 查到仓库信息
#  2.扫描仓库下所有.py文件
#  3.逐个读取源码、调用上面chunk_text做文本切块
#  4.把所有切块存入本地repo_id_chunks.json备份
#  5.连接 Chroma 向量数据库，清空旧数据，批量生成 Embedding 向量入库
#  6.更新仓库状态为indexed、回填文件总数、切块总数
#  7.全局异常捕获，出错就标记仓库为error并记录报错信息
def index_repo(repo_id: str) -> None:

    # 去 repos.json 查这条仓库记录
    repo = repo_service.get_repo(repo_id)
    # 如果返回None（仓库不存在），直接 return 终止执行，不往下跑避免报错
    if not repo:
        return
    
    try:
        # 读取全局配置，拿到两个核心切块参数：chunk_size、chunk_overlap
        settings = get_settings()
        # 拿到仓库真实本地文件夹路径，调用scan_files递归过滤无效目录，返回所有.py文件绝对路径列表
        files = repo_service.scan_files(repo["path"])
        # 大容器：存放当前仓库所有文件拆分出来的全部切块，最后统一存 JSON、统一向量化
        all_chunks = []

        for abs_path in files:
            # abs_path：/data/uploads/xxx/main.py 完整绝对路径
            # repo["path"]：仓库根目录 /data/uploads/xxx
            # relpath 计算出相对仓库根目录的路径，结果：main.py，作用：存到 chunk 里不用存一长串绝对路径，前端展示简洁、迁移服务器路径不会失效
            rel_path = os.path.relpath(abs_path, repo["path"])

            try:
                # 读取文件内容，忽略编码错误（如文件是二进制文件）
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                # 单文件读失败跳过（如权限/编码问题），不中断整个仓库索引
                continue

            # 拿到文件内容后，调用 chunk_text 对单个文件源码滑动窗口切块
            file_chunks = chunk_text(
                content, rel_path, repo_id,
                settings["chunk_size"], settings["chunk_overlap"]
            )
            # 把当前文件的所有切块追加到总池子
            all_chunks.extend(file_chunks)
            # 循环跑完所有文件后，all_chunks就是整个仓库完整结构化切块数据

        # 把所有切块数据存入本地 repo_id_chunks.json，方便调试和前端展示
        _save_chunks(repo_id, all_chunks)
        # 1.向量数据库只存向量，原始文本、行号、文件路径、重叠信息不全部存在向量库，用 JSON 做冷备份
        # 2.后续重新索引、调试切块规则、前端查询单个 chunk 详情，直接读这个文件即可
        # 3.向量库损坏可以依靠 JSON 重新批量生成向量，不用再次解析代码文件

        # 导入 chromadb 向量库
        import chromadb

        # chroma_path 拼接持久化向量库目录：data/chroma
        chroma_path = os.path.join(DATA_DIR, "chroma")
        # PersistentClient 持久化客户端，向量存在磁盘，程序重启不会丢失
        client = chromadb.PersistentClient(path=chroma_path)
        # 每个项目单独一个集合 repo_xxxxxx，隔离不同项目的向量，查询时互不干扰
        collection_name = f"repo_{repo_id}"

        try:
            # 先 delete 再 create：重新索引仓库前，先删除该仓库旧的向量集合，避免脏数据残留。
            client.delete_collection(collection_name)
        except Exception:
            pass

        # 创建全新空集合
        collection = client.create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"},
            # 指定向量相似度计算方式：余弦相似度，文本 Embedding 标准度量方式，越接近 1 语义越相似
        )

        # 每一批只处理 6 个切块，避免一次性请求过多 token 触发 Embedding API 限制。
        batch_size = 6
        # range(起始, 总长度, 步长) 循环切片，
        # 例：总共有 23 个块 → 0-6，6-12，12-18，18-24 四批处理，循环 i 会依次取值：0, 6, 12，18
        for i in range(0, len(all_chunks), batch_size):

            # 当前循环要处理的6 个（或不足 6 个）切块组成的小列表
            batch = all_chunks[i:i + batch_size]

            # 循环遍历当前批次每一个切块 c，只取出 content 字段（也就是切割好的代码原文）
            # 得到：["代码片段1", "代码片段2", ...]
            texts = [c["content"] for c in batch]

            # 取出每个块全局唯一的 chunk_id
            # 作用：Chroma 向量库的主键，后续检索到向量，通过 id 反向找到完整切块信息，必须全局唯一
            ids = [c["chunk_id"] for c in batch]

            # 遍历每个切块，打包成一个字典，装进列表
            metadatas = [{
                "file_path": c["file_path"],
                "line_start": c["line_start"],
                "line_end": c["line_end"],
                "chunk_index": c["chunk_index"],
            } for c in batch]

            # 把刚才提取的一批纯代码文本 texts 丢给嵌入模型函数，返回结果 embeddings 是二维浮点数组，结构：[[0.12,0.35,...], [0.22,0.61,...], ...]
            embeddings = llm_client.embed_texts(texts)

            # 批量插入向量数据库
            collection.add(ids=ids, embeddings=embeddings,
                           documents=texts, metadatas=metadatas)

        # 调用仓库更新方法，修改repos.json里这条仓库三条字段
        repo_service.update_repo(repo_id, {
            "status": "indexed",                         # 状态改为已完成索引，前端显示绿色完成标识
            "file_count": len(files),                    # 本次一共扫描了多少个 py 文件
            "chunk_count": len(all_chunks),              # 一共拆分生成了多少个文本块，前端做统计面板展示
        })

    except Exception as e:
        # 失败兜底：
        #  1.仓库状态改成 error 失败状态
        #  2.把异常堆栈字符串存入error_msg
        # 前端可以查看报错原因，排查索引失败问题，不会让程序直接崩溃退出
        repo_service.update_repo(repo_id, {"status": "error", "error_msg": str(e)})

# get_chunks（取全部分块）：给"分块探查"页用，用于调试、前端批量查看拆分结果
def get_chunks(repo_id: str) -> list[dict]:
    return _load_chunks(repo_id)

# 根据仓库 ID + 唯一 chunk_id，精准查询单条切块详情
# 使用场景：RAG 检索到向量 ID 后，后端调用这个接口取出完整代码、行号、重叠信息，组装上下文发给大模型回答用户问题
def get_chunk(repo_id: str, chunk_id: str) -> dict | None:
    # get_chunk（取单个块）：线性查找，给 get_chunk_context 用。
    for c in _load_chunks(repo_id):
        if c["chunk_id"] == chunk_id:
            return c
    return None

# get_chunk_context（块上下文定位）：根据 仓库ID + chunk唯一ID，加载原始完整源码文件，并标记当前切块需要高亮的行号，专门给前端代码预览页面用的接口
# 比如 RAG 检索命中某一个代码块，前端要打开完整文件，自动高亮这一段代码，全靠这个函数返回数据
def get_chunk_context(repo_id: str, chunk_id: str) -> dict:
    
    # 从 xxx_chunks.json 里取出这个块的所有信息（起止行、文件路径、重叠信息等）
    chunk = get_chunk(repo_id, chunk_id)
    if not chunk:
        # 找不到直接抛异常，上层接口捕获后返回前端 404
        raise ValueError(f"Chunk not found: {chunk_id}")

    # 校验仓库是否存在
    # 双重兜底：防止仓库已经被删除，但 chunk 记录还残留的脏数据
    repo = repo_service.get_repo(repo_id)
    if not repo:
        raise ValueError(f"Repo not found: {repo_id}")

    # repo["path"]：仓库在服务器本地的根文件夹
    # chunk["file_path"]：切块里存的相对路径（比如 utils/db.py）
    # 拼接出磁盘上真实文件地址，用来读取原始源码
    abs_path = os.path.join(repo["path"], chunk["file_path"])

    try:
        # 读取完整源文件全文
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            file_content = f.read()
    except Exception as e:
        raise ValueError(f"Cannot read file: {e}")

    # 计算整个文件总行数
    # splitlines() 按换行符切割成列表，列表长度 = 文件一共有多少行，前端展示「文件共 XX 行」用
    total_lines = len(file_content.splitlines())

    # 组装返回给前端的大字典
    return {
        "chunk": chunk,
        "file_content": file_content,
        "file_path": chunk["file_path"],
        "total_lines": total_lines,
        "highlight_start": chunk["line_start"],
        "highlight_end": chunk["line_end"],
    }
    # 前端拿到数据后做什么？
    # 1.渲染完整代码编辑器；
    # 2.自动从 highlight_start 到 highlight_end 背景标黄；
    # 3.展示该切块的重叠上下文范围、块序号；
    # 4.展示文件总共有多少行

# get_stats（索引统计）：给后台 / 前端做仓库索引数据大盘统计，可视化展示切块分布、向量维度、切块配置参数，属于管理后台的统计接口
def get_stats(repo_id: str) -> dict:

    # 当前仓库全部拆分好的块列表
    chunks = _load_chunks(repo_id)
    # 读取系统配置文件,拿到全局配置 chunk_size、chunk_overlap
    settings = get_settings()

    # 初始化空字典 file_dist：
    # key：文件相对路径 file_path（比如 src/service/user.py）
    # value：该文件一共拆分出多少个代码块
    file_dist: dict[str, int] = {}
    for c in chunks:
        # 遍历每一个切块，统计每个文件一共拆分了多少个块
        #  - 如果字典里已经存在这个文件路径，取出已有的计数；
        #  - 不存在默认返回 0，然后 +1 计数累加
        file_dist[c["file_path"]] = file_dist.get(c["file_path"], 0) + 1
        # {
        #     "main.py": 2,
        #     "db/connect.py": 1
        # }

    # 兜底默认值：仓库未索引、向量库损坏、集合不存在时，最终返回 0，前端可以显示「未生成向量」
    embedding_dim = 0

    try:
        # 连接本地持久化 Chroma 向量数据库
        import chromadb
        chroma_path = os.path.join(DATA_DIR, "chroma")
        client = chromadb.PersistentClient(path=chroma_path)

        # 获取当前仓库专属向量集合
        col = client.get_collection(f"repo_{repo_id}")

        # 只取 1 条数据，提取向量（极致性能优化）
        #  limit=1：只查询第一条向量数据，不用拉取成千上万条向量，极大减少 IO 和内存开销；
        #  include=["embeddings"]：Chroma 默认不返回向量，必须手动声明要读取 embeddings 字段
        result = col.get(limit=1, include=["embeddings"])
        embs = result["embeddings"]

        # embs[0] 是第一条数据的浮点型向量数组，数组长度 = Embedding 模型输出维度
        if embs is not None and len(embs) > 0:
            embedding_dim = len(embs[0])

    except Exception:
        # 哪些情况会触发异常直接跳过，保持embedding_dim=0：
        # 1.仓库还没执行索引，Chroma 里没有这个集合；
        # 2.Chroma 数据库文件损坏、磁盘丢失；
        # 3.集合被手动删除；
        # 4.向量集合为空，查不到任何 embedding
        pass

    # 组装最终返回给前端的完整统计 JSON
    return {
        "total_chunks": len(chunks),
        "total_files": len(file_dist),
        "embedding_dim": embedding_dim,
        "chunk_size": settings["chunk_size"],
        "chunk_overlap": settings["chunk_overlap"],
        "file_distribution": [
            {"file_path": fp, "chunk_count": cnt}
            for fp, cnt in sorted(file_dist.items(), key=lambda x: -x[1])
        ],
    }
