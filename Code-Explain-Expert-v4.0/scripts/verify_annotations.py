#!/usr/bin/env python3
"""verify_annotations.py — 注释自检脚本：验证"只加注释，未改逻辑"红线。

算法
----
对原文件与注释后文件分别剥离【注释 + 字符串字面量】（保留换行、保留列对齐），
再各自提取【纯代码行序列】（去首尾空白、丢弃空行）。用 difflib 比对两个序列：

- 序列一致 → 只加了注释（PASS）
- 序列不一致 → 改动了逻辑行（FAIL，逐行报告差异）

为什么剥离字符串：字符串字面量里的 `//` `#` `/*` 不是注释，必须跳过；同时
docstring/字符串内容变化也属于"改了文件"，但本脚本聚焦【代码逻辑行】——字符串
内容改动若发生在业务逻辑里（如改了报错文案、改了 SQL 拼接），会被 difflib 在
"该行消失/新增"层面捕捉到；纯 docstring 文案改动不在本脚本检测范围（由红线
"已有注释一律保留"约束，自检脚本不重复管这件事）。

两种用法
--------
1) 单文件比对（手工指定原版与注释后版）：
    python verify_annotations.py --original <原文件> --annotated <注释后文件>

2) git 批量比对（自动用 git show HEAD:<file> 取原版，比对工作区当前版）：
    python verify_annotations.py --git-root <项目根>
   只调用 git 只读命令（show / diff --name-only），不修改 git 状态，符合 v4.0 红线。
   非 git 仓库或无改动时退出码 0，提示"无可比对改动"。

退出码
------
- 0：所有文件通过（只加了注释）
- 1：存在逻辑改动（FAIL）
- 2：参数错误 / 文件不存在 / git 不可用

跨语言支持
----------
Java / Kotlin / Python / JS / TS / Go / C / C++ / C# / PHP / Ruby / Swift 等。
从扩展名推断语言；可用 --lang 覆盖。
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 语言 -> 扩展名映射（与 extract_skeleton.py 的 SOURCE_EXT 对齐）
# ---------------------------------------------------------------------------

EXT_TO_LANG = {
    ".java": "java", ".kt": "kotlin",
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".cs": "csharp", ".php": "php", ".rb": "ruby", ".swift": "swift",
}

# 行注释起始符（按语言分组）
LINE_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "python": ("#",),
    "java": ("//",),
    "kotlin": ("//",),
    "javascript": ("//",),
    "typescript": ("//",),
    "go": ("//",),
    "rust": ("//",),
    "c": ("//",),
    "cpp": ("//",),
    "csharp": ("//",),
    "php": ("//", "#"),
    "ruby": ("#",),
    "swift": ("//",),
}


# ---------------------------------------------------------------------------
# 注释与字符串剥离（与 extract_skeleton.py._strip_Explains 算法保持一致）
# ---------------------------------------------------------------------------


def strip_comments(source: str, lang: str) -> str:
    """剥离注释与字符串字面量，保留换行与列对齐。

    全局状态机（状态跨行保持），正确处理：
    - `#` 行注释（Python/Ruby/PHP）
    - `//` 行注释（C 系/Java 系/Go/Rust/Swift/JS/TS）
    - `/* ... */` 块注释（跨行）
    - 单/双/反引号字符串 `'...'` `"..."` `` `...` ``（含跨行模板串）
    - Python 三引号字符串 `\"\"\"...\"\"\"` `'''...'''`（跨行 docstring）

    字符串/注释内容替换为等长空格，换行符保留，使输出与原文行号一一对应。
    """
    out: list[str] = []
    i = 0
    n = len(source)
    in_block = False
    in_str: str | None = None  # None | "'" | '"' | "`" | '"""' | "'''"
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        # 块注释状态：内容替换成空格，换行保留
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        # 字符串状态
        if in_str is not None:
            # 三引号字符串的结束判定
            if in_str in ('"""', "'''"):
                if source[i:i + 3] == in_str:
                    out.append("   ")
                    i += 3
                    in_str = None
                    continue
                out.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            # 普通字符串：转义符整体跳过
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == in_str:
                in_str = None
                out.append(" ")
                i += 1
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        # 非字符串非注释状态
        if lang in ("python", "ruby", "php") and ch == "#":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            out.append("  ")
            i += 2
            continue
        # Python 三引号字符串（须在单引号判定之前）
        if lang == "python" and source[i:i + 3] in ('"""', "'''"):
            in_str = source[i:i + 3]
            out.append("   ")
            i += 3
            continue
        # 单引号/双引号/反引号字符串
        if ch in ("'", '"', "`"):
            in_str = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def code_lines_only(stripped: str) -> list[str]:
    """从剥离注释后的文本里提取【纯代码行序列】。

    - 去掉每行首尾空白
    - 丢弃空白行（剥离注释后变空的行不算逻辑行）
    返回非空代码行列表，用于 difflib 比对。
    """
    return [ln.strip() for ln in stripped.split("\n") if ln.strip()]


# ---------------------------------------------------------------------------
# 比对与报告
# ---------------------------------------------------------------------------


def compare(original: str, annotated: str, lang: str) -> tuple[bool, list[str]]:
    """比对原文件与注释后文件的纯代码行序列。

    返回 (passed, report_lines)。
    - passed=True：只加了注释，逻辑零改动
    - passed=False：存在逻辑改动，report_lines 含 difflib 差异
    """
    orig_code = code_lines_only(strip_comments(original, lang))
    anno_code = code_lines_only(strip_comments(annotated, lang))

    if orig_code == anno_code:
        return True, []

    # difflib.unified_diff 给出可读差异
    diff = list(difflib.unified_diff(
        orig_code, anno_code,
        fromfile="original (logic only)",
        tofile="annotated (logic only)",
        lineterm="",
        n=2,
    ))
    return False, diff


def detect_lang(path: Path) -> str | None:
    ext = path.suffix.lower()
    return EXT_TO_LANG.get(ext)


def verify_file_pair(original_path: Path, annotated_path: Path, lang: str | None = None) -> tuple[bool, list[str]]:
    """验证一对文件。返回 (passed, report_lines)。"""
    if not original_path.is_file():
        return False, [f"[ERROR] 原文件不存在: {original_path}"]
    if not annotated_path.is_file():
        return False, [f"[ERROR] 注释后文件不存在: {annotated_path}"]

    if lang is None:
        lang = detect_lang(annotated_path) or detect_lang(original_path)
    if lang is None:
        return False, [f"[ERROR] 无法识别语言（扩展名不在支持列表）: {annotated_path.name}"]

    try:
        original = original_path.read_text(encoding="utf-8", errors="replace")
        annotated = annotated_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, [f"[ERROR] 读取失败: {e}"]

    return compare(original, annotated, lang)


# ---------------------------------------------------------------------------
# git 批量模式
# ---------------------------------------------------------------------------


def _git(git_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(git_root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def verify_git(git_root: Path) -> int:
    """git 批量模式：比对 HEAD 与工作区，找出被改动的源码文件，逐个验证。

    只调用 git 只读命令（diff --name-only / show），不修改 git 状态。
    """
    # 确认是 git 仓库
    r = _git(git_root, "rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        print(f"[SKIP] 非 git 仓库或 git 不可用: {git_root}", file=sys.stderr)
        return 0

    # 拿到工作区相对 HEAD 改动的文件（含未暂存与已暂存，不含未跟踪的新文件——
    # 未跟踪新文件没有"原版"可比对，跳过）
    r = _git(git_root, "diff", "--name-only", "HEAD")
    if r.returncode != 0:
        print(f"[ERROR] git diff 失败: {r.stderr}", file=sys.stderr)
        return 2
    changed = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not changed:
        print("[OK] 无相对 HEAD 的改动，无可比对文件")
        return 0

    # 只验证源码扩展名
    source_files = [
        Path(rel) for rel in changed
        if Path(rel).suffix.lower() in EXT_TO_LANG
    ]
    skipped = len(changed) - len(source_files)
    if not source_files:
        print(f"[OK] 改动的 {len(changed)} 个文件均非源码（跳过 {skipped} 个非源码文件）")
        return 0

    print(f"=== 注释自检（git 模式）===")
    print(f"项目根: {git_root}")
    print(f"改动源码文件: {len(source_files)} 个（跳过 {skipped} 个非源码）\n")

    fail_count = 0
    for rel in source_files:
        abs_path = git_root / rel
        lang = detect_lang(abs_path)
        if lang is None:
            continue
        # 取 HEAD 版作为原文件
        r = _git(git_root, "show", f"HEAD:{rel.replace('\\', '/')}")
        if r.returncode != 0:
            print(f"  [SKIP] {rel} — HEAD 无此文件（新增文件，无原版可比对）")
            continue
        original = r.stdout
        try:
            annotated = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  [ERROR] {rel} — 读取失败: {e}")
            fail_count += 1
            continue

        passed, diff = compare(original, annotated, lang)
        if passed:
            print(f"  [PASS] {rel} — 只加了注释，逻辑零改动")
        else:
            fail_count += 1
            print(f"  [FAIL] {rel} — 检测到逻辑改动：")
            for ln in diff:
                print(f"        {ln}")
            print()

    print(f"\n=== 结果: {len(source_files) - fail_count} PASS, {fail_count} FAIL ===")
    return 1 if fail_count else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="注释自检：验证注释写入后是否改动了业务逻辑行（只加注释 = PASS）"
    )
    parser.add_argument("--original", default=None, help="原文件路径（单文件模式）")
    parser.add_argument("--annotated", default=None, help="注释后文件路径（单文件模式）")
    parser.add_argument("--lang", default=None, help="强制指定语言（默认按扩展名推断）")
    parser.add_argument("--git-root", default=None, help="git 项目根（git 批量模式）")
    args = parser.parse_args()

    if args.git_root:
        root = Path(args.git_root).resolve()
        if not root.is_dir():
            print(f"[ERROR] 目录不存在: {root}", file=sys.stderr)
            return 2
        return verify_git(root)

    if not args.original or not args.annotated:
        parser.error("单文件模式需要 --original 与 --annotated；或用 --git-root 进入批量模式")

    original_path = Path(args.original).resolve()
    annotated_path = Path(args.annotated).resolve()
    passed, report = verify_file_pair(original_path, annotated_path, args.lang)

    if passed:
        print(f"[PASS] 只加了注释，逻辑零改动")
        print(f"  原文件: {original_path}")
        print(f"  注释后: {annotated_path}")
        return 0
    else:
        print(f"[FAIL] 检测到逻辑改动：")
        for ln in report:
            print(f"  {ln}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
