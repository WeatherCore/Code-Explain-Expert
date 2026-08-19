#!/usr/bin/env python3
"""test_bigfile_split.py — bigfile_split.py 单元测试。

验证：split_file 的边界行为（小文件不切/刚好阈值/超阈值 overlap 连续性/空文件/chunk_file 始终 None/index 连续）。
纯标准库，python tests/test_bigfile_split.py 直接跑。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import bigfile_split as bs

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name} {detail}")


def test_small_file_no_split():
    print("\n[1] 小文件（< max_lines）不切块")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write("line1\nline2\nline3\n")
        path = Path(f.name)
    try:
        chunks = bs.split_file(path, max_lines=800, overlap=40)
        check("返回 1 个切块", len(chunks) == 1, str(len(chunks)))
        check("note 含'未切块'", "未切块" in chunks[0].get("note", ""))
        check("start_line == 1", chunks[0]["start_line"] == 1)
        check("end_line == 3", chunks[0]["end_line"] == 3)
        check("chunk_file == None", chunks[0]["chunk_file"] is None)
    finally:
        path.unlink(missing_ok=True)


def test_exact_threshold():
    print("\n[2] 刚好等于 max_lines（边界）")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        for i in range(100):
            f.write(f"line{i}\n")
        path = Path(f.name)
    try:
        chunks = bs.split_file(path, max_lines=100, overlap=40)
        check("返回 1 个切块（刚好阈值不切）", len(chunks) == 1, str(len(chunks)))
        check("end_line == 100", chunks[0]["end_line"] == 100)
    finally:
        path.unlink(missing_ok=True)


def test_over_threshold_with_overlap():
    print("\n[3] 超阈值 + overlap 连续性")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        for i in range(200):
            f.write(f"line{i}\n")
        path = Path(f.name)
    try:
        chunks = bs.split_file(path, max_lines=100, overlap=40)
        check("返回 >1 个切块", len(chunks) > 1, str(len(chunks)))
        check("最后一块 end_line == 200", chunks[-1]["end_line"] == 200)
        # overlap 连续性：块 N 的 start_line == 块 N-1 的 end_line - overlap + 1
        for i in range(1, len(chunks)):
            expected_start = chunks[i - 1]["end_line"] - 40 + 1
            check(f"块 {i} start_line == 块 {i-1} end_line - overlap + 1",
                  chunks[i]["start_line"] == expected_start,
                  f"got {chunks[i]['start_line']}, expected {expected_start}")
        check("所有 chunk_file == None", all(c["chunk_file"] is None for c in chunks))
    finally:
        path.unlink(missing_ok=True)


def test_empty_file():
    print("\n[4] 空文件")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write("")
        path = Path(f.name)
    try:
        chunks = bs.split_file(path, max_lines=800, overlap=40)
        check("空文件返回 1 个切块", len(chunks) == 1, str(len(chunks)))
        check("start_line == 1", chunks[0]["start_line"] == 1)
        check("chunk_file == None", chunks[0]["chunk_file"] is None)
    finally:
        path.unlink(missing_ok=True)


def test_index_sequential():
    print("\n[5] 切块 index 连续递增")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        for i in range(500):
            f.write(f"line{i}\n")
        path = Path(f.name)
    try:
        chunks = bs.split_file(path, max_lines=100, overlap=40)
        indices = [c["index"] for c in chunks]
        check("index 从 0 开始", indices[0] == 0)
        check("index 连续递增", indices == list(range(len(chunks))), str(indices))
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    print("=== bigfile_split.py 单元测试 ===")
    test_small_file_no_split()
    test_exact_threshold()
    test_over_threshold_with_overlap()
    test_empty_file()
    test_index_sequential()
    print(f"\n=== 结果: {_passed} passed, {_failed} failed ===")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
