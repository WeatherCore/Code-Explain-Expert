#!/usr/bin/env python3
"""detect_changes.py — 变更检测：用 Git Diff 找出变动文件，只对这些文件重新生成注释。

用于"更新维护"模式（场景 C）：合并分支 / 提交后，定位本次变更涉及的源码文件，
避免全量重扫。

非 Git 仓库时退化为 MD5 快照比对（首次运行返回全部源码文件，后续运行只返回
哈希变动的文件），保证非 Git 项目也能用。

用法:
    python detect_changes.py --root <项目根> [--base <git-ref>] [--out changes.json] [--mode auto|git|hash]
    # 默认 base 为工作区未提交变更；无未提交变更时回退 HEAD~1
    # 可指定 --base origin/main 等任意 ref

输出 JSON:
    {"base": "...", "mode": "git"|"hash", "added": [...], "modified": [...], "deleted": [...], "all_source": [...]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SOURCE_EXT = {
    ".java", ".kt", ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    ".cs", ".php", ".rb", ".swift",
}

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "dist", "build", "target",
    "out", "__pycache__", ".idea", ".vscode", "coverage", ".next", "vendor",
}

HASH_CACHE = ".cc_hash.json"  # 非 git 仓库的 MD5 快照缓存文件名


def is_source(rel: str) -> bool:
    if any(part in SKIP_DIRS for part in Path(rel).parts):
        return False
    p = Path(rel)
    if p.name.startswith("."):
        return False
    if p.name.startswith(("min.", ".min.")):
        return False
    return p.suffix.lower() in SOURCE_EXT


def git(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("[ERROR] 未找到 git 命令", file=sys.stderr)
        sys.exit(1)
    if r.returncode != 0:
        print(f"[ERROR] git {' '.join(args)} 失败: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return r.stdout


def hash_snapshot(root: Path) -> dict[str, str]:
    """对项目内全部源码文件计算 MD5，返回 {相对路径: hash}。"""
    manifest: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in SOURCE_EXT:
            continue
        try:
            h = hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            continue
        manifest[p.relative_to(root).as_posix()] = h
    return manifest


def hash_diff(root: Path) -> dict:
    """基于 MD5 快照比对，返回与上次运行相比的变动文件。

    首次运行（缓存不存在）→ 返回全部源码文件作为 added；
    后续运行 → 新增/哈希变 → added+modified，删除 → deleted。
    """
    cache_path = root / HASH_CACHE
    prev: dict[str, str] = {}
    if cache_path.exists():
        try:
            prev = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}

    manifest = hash_snapshot(root)
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for rel, h in manifest.items():
        if rel not in prev:
            added.append(rel)
        elif prev[rel] != h:
            modified.append(rel)
    for rel in prev:
        if rel not in manifest:
            deleted.append(rel)

    # 落盘新快照
    cache_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "base": f"hash-snapshot@{HASH_CACHE}",
        "mode": "hash",
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "all_source": sorted(set(added) | set(modified)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="变更检测：Git Diff 优先，非 Git 仓库回退 MD5 快照")
    parser.add_argument("--root", required=True, help="项目根目录")
    parser.add_argument("--base", default="HEAD", help="对比基准（默认 HEAD，即工作区未提交变更）")
    parser.add_argument("--out", default="changes.json", help="输出 JSON 路径")
    parser.add_argument("--mode", choices=["auto", "git", "hash"], default="auto",
                        help="auto=git 优先回退 hash；git=强制 git（非 git 仓库报错）；hash=强制 hash 快照")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 根目录不存在: {root}", file=sys.stderr)
        return 1

    use_hash = False
    if args.mode == "hash":
        use_hash = True
    elif args.mode == "git":
        if not (root / ".git").exists():
            print(f"[ERROR] {root} 不是 Git 仓库，且 --mode=git 强制 git 模式", file=sys.stderr)
            return 1
    else:  # auto
        if not (root / ".git").exists():
            print(f"[INFO] {root} 不是 Git 仓库，回退到 MD5 快照模式", file=sys.stderr)
            use_hash = True

    if use_hash:
        result = hash_diff(root)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True) if out.parent != Path(".") else None
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK][hash] 变更清单已写入 {out}")
        print(f"     新增: {len(result['added'])} | 修改: {len(result['modified'])} | 删除: {len(result['deleted'])} | 需注释: {len(result['all_source'])}")
        for rel in result["all_source"][:50]:
            print(f"     - {rel}")
        return 0

    # git 模式
    base = args.base
    if base == "HEAD":
        # 默认看工作区未提交变更；若无变更则回退到 HEAD~1 比较最近一次提交
        status = git(["status", "--porcelain"], root).strip()
        if not status:
            base = "HEAD~1"
            print(f"[INFO] 工作区无未提交变更，对比基准改为 {base}")

    output = git(["diff", "--name-status", f"{base}..."], root) if base != "HEAD" else git(["diff", "--name-status"], root)
    lines = [ln for ln in output.splitlines() if ln.strip()]

    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        status_code, rel = parts[0], parts[1]
        if not is_source(rel):
            continue
        if status_code.startswith("A"):
            added.append(rel)
        elif status_code.startswith("D"):
            deleted.append(rel)
        else:
            modified.append(rel)

    result = {
        "base": base,
        "mode": "git",
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "all_source": added + modified,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True) if out.parent != Path(".") else None
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK][git] 变更清单已写入 {out}")
    print(f"     新增: {len(added)} | 修改: {len(modified)} | 删除: {len(deleted)} | 需注释: {len(result['all_source'])}")
    for rel in result["all_source"]:
        print(f"     - {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
