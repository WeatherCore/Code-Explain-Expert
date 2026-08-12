#!/usr/bin/env python3
"""bigfile_split.py — 对超大源文件做行级切块，避免单文件超出 LLM 上下文。

把文件按 max_lines 切片，相邻块保留 overlap 行重叠，便于跨块连续性。
输出每块的行区间到 JSON，供注释阶段用 Read 工具按 offset/limit 投喂。

v4.0 安全约束：
    - **不再落盘切块文件到客户项目**（v3 的 .cc_split/<base>.NNN.txt 已移除）。
    - 切块清单 JSON 默认自动落盘到 skill .work/chunks.json（不污染客户项目）。
    - LLM 用 Read 工具的 offset/limit 按行号区间读源文件。

用于场景：单文件 > 500 行（orchestration-guide.md 规定的"大文件"阈值）时，
先切块再注释，避免一次性把整个大文件塞进上下文。

用法:
    python bigfile_split.py --file <源文件> [--max-lines 800] [--overlap 40]
    # 旧式位置参数也兼容：python bigfile_split.py <源文件>
    # --out 不传时默认落盘到 skill .work/chunks.json；- = stdout；<路径> = 自定义
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# skill 工作目录：中间产物自动落盘到这里，不污染客户项目
SKILL_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = SKILL_ROOT / ".work"

DEFAULT_MAX = 800
DEFAULT_OVERLAP = 40


def split_file(path: Path, max_lines: int = DEFAULT_MAX, overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """切分大文件。仅返回切块清单（行号区间），**不落盘切块文件到客户项目**（清单 JSON 自动写到 skill `.work/chunks.json`）。

    v4.0：移除了 v3 落盘到 <file_dir>/.cc_split/ 的逻辑。
    LLM 用 Read 工具的 offset/limit 按行号区间读源文件即可，无需中间文件。
    """
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

    # 超阈值：只输出行号区间清单，不落盘切块文件
    base = path.stem
    chunks: list[dict] = []
    i, idx = 0, 0
    while i < total:
        end = min(i + max_lines, total)
        chunks.append({
            "index": idx,
            "start_line": i + 1,
            "end_line": end,
            "path": path.as_posix(),
            "chunk_file": None,  # v4.0：始终 None，不再落盘
            "note": f"切块 {idx}（行 {i+1}-{end}），用 Read 工具 offset={i+1} limit={max_lines} 读取",
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
    parser.add_argument("--out", "-o", default=None, help="输出路径（默认自动落盘到 skill .work/chunks.json；- = stdout；<路径> = 自定义）")
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

    chunks_json = json.dumps(chunks, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(chunks_json)
        print(f"[OK] 切块 {len(chunks)} 个，已输出到 stdout", file=sys.stderr)
        for c in chunks:
            note = c.get("note", "")
            print(f"     [{c['index']}] 行 {c['start_line']}-{c['end_line']} | {note}", file=sys.stderr)
    else:
        out_path = Path(args.out) if args.out else WORK_DIR / "chunks.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(chunks_json, encoding="utf-8")
        print(f"[OK] 切块 {len(chunks)} 个 -> {out_path}")
        for c in chunks:
            note = c.get("note", "")
            print(f"     [{c['index']}] 行 {c['start_line']}-{c['end_line']} | {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
