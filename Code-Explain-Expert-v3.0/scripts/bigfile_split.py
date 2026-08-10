#!/usr/bin/env python3
"""bigfile_split.py — 对超大源文件做行级切块，避免单文件超出 LLM 上下文。

把文件按 max_lines 切片，相邻块保留 overlap 行重叠，便于跨块连续性。
输出每块的文件路径与行区间到 JSON，供注释阶段逐批投喂。

用于场景：单文件 > 500 行（orchestration-guide.md 规定的"大文件"阈值）时，
先切块再注释，避免一次性把整个大文件塞进上下文。

用法:
    python bigfile_split.py --file <源文件> [--max-lines 800] [--overlap 40] --out chunks.json
    # 旧式位置参数也兼容：python bigfile_split.py <源文件> [-o chunks.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_MAX = 800
DEFAULT_OVERLAP = 40


def split_file(path: Path, max_lines: int = DEFAULT_MAX, overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """切分大文件。未超阈值时返回单块描述但不落盘切块文件。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[ERROR] 读取失败 {path}: {exc}", file=sys.stderr)
        return []
    lines = text.splitlines()
    total = len(lines)
    if total <= max_lines:
        return [{
            "index": 0,
            "start_line": 1,
            "end_line": total,
            "path": path.as_posix(),
            "chunk_file": None,
            "note": "未切块（未超阈值）",
        }]

    # 超阈值：落盘切块到 <file_dir>/.cc_split/<basename>.NNN.txt
    out_dir = path.parent / ".cc_split"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = path.stem
    chunks: list[dict] = []
    i, idx = 0, 0
    while i < total:
        end = min(i + max_lines, total)
        chunk_lines = lines[i:end]
        chunk_path = out_dir / f"{base}.{idx:03d}.txt"
        chunk_path.write_text("\n".join(chunk_lines), encoding="utf-8")
        chunks.append({
            "index": idx,
            "start_line": i + 1,
            "end_line": end,
            "path": path.as_posix(),
            "chunk_file": chunk_path.as_posix(),
            "note": f"切块 {idx}（行 {i+1}-{end}）",
        })
        if end >= total:
            break
        i = end - overlap
        idx += 1
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="超大源文件行级切块，输出切块清单 JSON")
    parser.add_argument("--file", help="待切分的源文件路径")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX, help=f"单块最大行数（默认 {DEFAULT_MAX}）")
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP, help=f"相邻块重叠行数（默认 {DEFAULT_OVERLAP}）")
    parser.add_argument("--out", "-o", default="chunks.json", help="输出 JSON 路径")
    # 兼容旧式位置参数：python bigfile_split.py <file> [-o chunks.json]
    parser.add_argument("file_pos", nargs="?", help="待切分的源文件路径（位置参数，兼容旧式）")
    args = parser.parse_args()

    file_arg = args.file or args.file_pos
    if not file_arg:
        parser.error("需提供 --file 或位置参数指定源文件")
    src = Path(file_arg).resolve()
    if not src.is_file():
        print(f"[ERROR] 文件不存在: {src}", file=sys.stderr)
        return 1

    chunks = split_file(src, args.max_lines, args.overlap)
    if not chunks:
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True) if out.parent != Path(".") else None
    out.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 切块 {len(chunks)} 个 -> {out}")
    for c in chunks:
        note = c.get("note", "")
        print(f"     [{c['index']}] 行 {c['start_line']}-{c['end_line']} | {c['chunk_file'] or '未落盘'} | {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
