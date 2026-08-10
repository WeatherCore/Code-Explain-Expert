#!/usr/bin/env python3
"""fetch_sources.py — 精准捞取：按优先级/文件 ID 批量取出完整源码。

LLM 基于 skeleton.json 决策出注释优先级后，用本脚本分批捞取完整源码，
避免一次性将整个项目塞进上下文。

用法:
    python fetch_sources.py --root <项目根> --out batch.txt --files a.py,b.py
    python fetch_sources.py --root <项目根> --out batch.txt --from-skeleton skeleton.json --ids 0,3,5
    python fetch_sources.py --root <项目根> --out batch.txt --from-skeleton skeleton.json --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FILE_HEADER = "\n\n" + "=" * 72 + "\nFILE: {path}\n" + "=" * 72 + "\n"


def resolve_paths(root: Path, args) -> list[str]:
    if args.files:
        return [p.strip() for p in args.files.split(",") if p.strip()]
    if args.from_skeleton:
        sk = json.loads(Path(args.from_skeleton).read_text(encoding="utf-8"))
        files = sk.get("files", [])
        if args.ids:
            return [files[int(i)]["path"] for i in args.ids.split(",") if i.strip()]
        if args.top:
            return [f["path"] for f in files[: args.top]]
        print("[ERROR] 需提供 --ids 或 --top", file=sys.stderr)
        sys.exit(1)
    print("[ERROR] 需提供 --files 或 --from-skeleton", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="按优先级批量捞取完整源码，输出拼接批次文件")
    parser.add_argument("--root", required=True, help="项目根目录")
    parser.add_argument("--out", required=True, help="输出批次文件路径")
    parser.add_argument("--files", default=None, help="逗号分隔的相对路径列表")
    parser.add_argument("--from-skeleton", default=None, help="skeleton.json 路径")
    parser.add_argument("--ids", default=None, help="skeleton.json 中 files 数组的索引，逗号分隔")
    parser.add_argument("--top", type=int, default=None, help="取 files 数组前 N 个")
    parser.add_argument("--max-bytes", type=int, default=60000, help="单个批次最大字节数（默认 60000）")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 根目录不存在: {root}", file=sys.stderr)
        return 1

    paths = resolve_paths(root, args)
    if not paths:
        print("[ERROR] 未解析到任何文件", file=sys.stderr)
        return 1

    chunks: list[str] = []
    chunk_files: list[str] = []  # 与 chunks 一一对应的相对路径，用于打印
    current = 0
    skipped_due_to_size: list[str] = []
    for rel in paths:
        p = root / rel
        if not p.is_file():
            print(f"[WARN] 跳过不存在的文件: {rel}", file=sys.stderr)
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"[WARN] 读取失败 {rel}: {exc}", file=sys.stderr)
            continue
        block = FILE_HEADER.format(path=rel) + content
        block_bytes = len(block.encode("utf-8"))
        if chunks and current + block_bytes > args.max_bytes:
            skipped_due_to_size.append(rel)
            continue  # 超批次上限，跳过本文件（不 break，继续看后续小文件能否塞进）
        chunks.append(block)
        chunk_files.append(rel)
        current += block_bytes

    if not chunks:
        print("[ERROR] 未捞取到任何内容", file=sys.stderr)
        return 1

    if skipped_due_to_size:
        print(f"[INFO] 因 max-bytes={args.max_bytes} 截断，以下文件留到下一批：", file=sys.stderr)
        for f in skipped_due_to_size:
            print(f"         - {f}", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True) if out.parent != Path(".") else None
    out.write_text("".join(chunks), encoding="utf-8")
    print(f"[OK] 批次已写入 {out} | 文件数: {len(chunks)} | 字节: {current}")
    print(f"     已读取: {chunk_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
