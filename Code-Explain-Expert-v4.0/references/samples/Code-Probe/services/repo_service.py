###################
# Repo Service — 代码仓库数据源管理模块，专门管理用户上传 / 本地选择的“仓库注册表” repos.json
# 记录每个库的路径、名称、索引状态，把仓库信息持久化存在 repos.json，提供增、查、从 Zip 压缩包解压导入仓库全套能力，是 RAG 系统中「知识库数据源」的管理层
# 两种入库方式——本地路径直连（引用原目录，不拷贝）和 zip 上传（解压到 uploads/ 管理）。
# 索引建库前先用 scan_files 扫一遍，找出这个库里所有要处理的 .py 文件。
# 它的上游是 api/repos.py（提供 HTTP 接口），下游是 index_service.py（索引时要用 get_repo 获取仓库路径去扫文件）。它绝不包含任何向量检索或 LLM 调用的代码——这是单一职责原则的体现
###################

import json
import os
import uuid
import zipfile
import shutil
from datetime import datetime
from app.config.defaults import DATA_DIR

# 仓库服务：代码库的注册、查询、删除、zip 解压、文件扫描。落盘到 data/repos.json。
# 两种导入方式：add_repo（本地路径，引用不拷贝）/ add_repo_from_upload（zip 解压到 uploads/）。

# 仓库元数据持久化JSON文件路径：data/repos.json。
REPOS_FILE = os.path.join(DATA_DIR, "repos.json")
# 前端上传zip解压后的根目录：data/uploads/。
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

# _load_repos（读仓库列表）：所有查询、新增仓库都会先调用它加载最新数据，避免内存旧数据问题
def _load_repos() -> list[dict]:

    # 先确保data目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    # 如果repos.json还没创建过，直接返回空列表
    if not os.path.exists(REPOS_FILE):
        return []
    # 存在就读取并返回仓库列表数组（显式 UTF-8，避免 Windows 默认 GBK 读中文内容报错）
    with open(REPOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# _save_repos（写仓库列表）：配套保存函数，新增仓库后调用，将内存里修改后的列表落盘到本地文件
def _save_repos(repos: list[dict]) -> None:

    # 先确保data目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    # 把更新后的仓库列表w模式全覆盖写入repos.json，indent=2格式化方便查看
    # 因为每次调用前都会先_load_repos拿取所有数据再添加再写入，所以这里直接覆盖写不会重复写入
    # 显式 UTF-8 + ensure_ascii=False：中文路径/文件名原样落盘，GBK 写 emoji 等字符会崩
    with open(REPOS_FILE, "w", encoding="utf-8") as f:
        json.dump(repos, f, indent=2, ensure_ascii=False)

# add_repo（本地路径导入比如 D:/project/demo）：把一个已存在的本地目录注册为代码库，不拷贝文件（直接引用原路径）
def add_repo(path: str, name: str | None = None) -> dict:

    # 转为绝对路径，防止相对路径带来重复判断BUG
    path = os.path.abspath(path)
    # 校验：路径必须是真实存在的文件夹，因为用户可能输入 ./my_project 或 ../repo，如果不转，存到 repos.json 里的路径就是相对的。一旦后端进程切换工作目录，这些相对路径就全废了
    if not os.path.isdir(path):
        raise ValueError(f"Path does not exist or is not a directory: {path}")
    
    # 先加载全部已有仓库
    repos = _load_repos()
    # 去重校验：同一个物理文件夹不能重复添加
    for r in repos:
        if r["path"] == path:
            raise ValueError(f"Repo already exists: {path}")

    # 组装仓库元数据字典，repo_id随机生成，name默认取文件夹名，status默认"pending"待索引
    repo = {
        "repo_id": uuid.uuid4().hex[:8],
        "name": name or os.path.basename(path),
        "path": path,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "file_count": 0,
        "chunk_count": 0,
        "error_msg": None,
    }
    # 追加进列表并持久化保存
    repos.append(repo)
    _save_repos(repos)
    return repo

# add_repo_from_upload（zip 上传导入）：解压到 data/uploads/{repo_id}/ 并注册
def add_repo_from_upload(zip_path: str, name: str | None = None) -> dict:
    
    # 比 add_repo 更通用（不依赖服务器本地文件系统），是前端默认导入方式。
    # 单顶层文件夹处理：很多 zip 里包一层目录（如 project-main/），自动剥到真正的代码根。

    # 先用repo_id作为解压文件夹名，隔离不同上传包
    repo_id = uuid.uuid4().hex[:8]
    # data/uploads/八位随机ID，每个上传的压缩包都解压到独立文件夹，防止不同项目代码互相覆盖冲突
    extract_dir = os.path.join(UPLOADS_DIR, repo_id)
    # 创建这个解压目录，如果目录已经存在也不会报错
    os.makedirs(extract_dir, exist_ok=True)

    try:
        # 以只读模式打开上传的 zip 文件，把压缩包里所有文件、所有子文件夹全部解压到上面创建的 extract_dir 目录
        # metadata_encoding="utf-8"：zip 内中文文件名按 UTF-8 解码（Python 3.11+），
        # 避免 Windows 默认 cp437/GBK 把中文文件名解成乱码
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir, metadata_encoding="utf-8")
    # 如果上传的文件不是有效的 zip 压缩包，抛出异常并删除临时解压目录
    except zipfile.BadZipFile:
        # 递归删除刚才创建的解压文件夹，清理无效空目录，避免磁盘垃圾堆积
        shutil.rmtree(extract_dir, ignore_errors=True)
        # 抛出中文错误，给前端返回提示，告诉用户包有问题
        raise ValueError("上传的文件不是有效的 zip 压缩包")
    finally:
        # finally 删临时 zip：前端上传的原始 zip 只是临时中转文件，解压完成（或者解压失败）都直接删掉原压缩包，只保留解压后的源码文件夹，节约服务器磁盘空间
        os.remove(zip_path)

    # 关于压缩可能会出现两种情况的文件夹
    # 这里自动抹平压缩包嵌套层级问题，RAG 后续遍历文件时路径永远是纯净的代码根目录，不会读取多余层级
    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        repo_path = os.path.join(extract_dir, entries[0])
    else:
        repo_path = extract_dir

    # 前端传了自定义名字就用自定义名字，没传就取代码文件夹的名字当做仓库名
    default_name = name or os.path.basename(repo_path)
    # 组装完整 repo 字典
    repo = {
        "repo_id": repo_id,
        "name": default_name,
        "path": repo_path,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "file_count": 0,
        "chunk_count": 0,
        "error_msg": None,
        "source": "upload",
    }
    # 入 repos.json 持久化保存并返回
    repos = _load_repos()
    repos.append(repo)
    _save_repos(repos)
    return repo

# list_repos（列出仓库）：前端"仓库管理"页展示所有仓库及索引状态
def list_repos() -> list[dict]:
    
    return _load_repos()

# get_repo（取单个仓库）：线性查找（repos.json 小，无需索引）。
def get_repo(repo_id: str) -> dict | None:
    
    for r in _load_repos():
        if r["repo_id"] == repo_id:
            return r
    return None

# update_repo（更新仓库元数据）：专门用来修改仓库 JSON 元数据的通用更新工具函数
def update_repo(repo_id: str, updates: dict) -> dict | None:
    
    repos = _load_repos()
    for r in repos:
        if r["repo_id"] == repo_id:
            # 匹配到立刻返回该仓库完整字典
            r.update(updates)
            _save_repos(repos)
            return r
    return None

# delete_repo（删仓库）：移除记录 + 清 chunks.json + 清 uploads 目录。
def delete_repo(repo_id: str) -> bool:
    
    # [注意] 已知缺陷：未清 ChromaDB collection（data/chroma/repo_{repo_id}），
    # 会留孤儿向量数据。复刻时建议补 client.delete_collection。见 ZHIDAO.md Q3。

    # 第一步：加载所有仓库，过滤掉要删除的那条
    repos = _load_repos()
    target = None
    new_repos = []

    # 遍历所有仓库，找到要删除的仓库对象，并记录下来，同时把其他仓库对象放到新列表里
    for r in repos:
        if r["repo_id"] == repo_id:
            target = r 
        else:
            new_repos.append(r)

    # 仓库不存在直接返回删除失败
    if not target:
        return False
    # 仓库存在，删除 repos.json 里面的记录并保存
    _save_repos(new_repos)

    # 第二步：删除该仓库对应的切块缓存文件 xxx_chunks.json
    chunks_file = os.path.join(DATA_DIR, f"{repo_id}_chunks.json")
    if os.path.exists(chunks_file):
        os.remove(chunks_file)

    # 第三步：如果是上传zip创建的仓库，删除服务器解压的源码文件夹，代码存在 data/uploads/八位ID 文件夹里，而本地路径添加的仓库（source 不存在）不会执行这一步，因为那是用户服务器原有项目，程序无权删除
    upload_dir = os.path.join(UPLOADS_DIR, repo_id)
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)

    return True

# scan_files（扫描代码文件）：只收 .py 文件——这是"只支持 Python 代码库"的根源
def scan_files(repo_path: str) -> list[str]:
    
    py_files = []

    # os.walk 深度递归遍历整个仓库目录
    # root：当前正在遍历的文件夹绝对路径、dirs：当前文件夹下所有子目录列表、files：当前文件夹下所有文件列表
    for root, dirs, files in os.walk(repo_path):
        # 跳过 .开头目录(隐藏/git) + __pycache__/node_modules/.venv/venv(依赖/缓存) + 其他常见干扰目录，减少扫描开销
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".venv", "venv")]

        for f in files:
            # 遍历当前目录下所有文件，只保留 .py 结尾的文件
            # 要支持其他语言，改这里的扩展名过滤即可，分块/索引逻辑语言无关。
            if f.endswith(".py"):
                #  /data/uploads/xxx/main.py 完整绝对路径
                py_files.append(os.path.join(root, f))
                
    return py_files
