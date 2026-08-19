#!/usr/bin/env python3
"""test_fetch_sources.py — fetch_sources.py 单元测试。

验证：--files / --from-skeleton --ids / --from-skeleton --top / --max-bytes 截断 / 不存在文件警告。
纯标准库，python tests/test_fetch_sources.py 直接跑。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SCRIPTS = SKILL_ROOT / "scripts"
FIXTURES = SKILL_ROOT / "tests" / "fixtures"
SAMPLE_PY = FIXTURES / "sample-py"
WORK_DIR = SKILL_ROOT / ".work"

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


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(SCRIPTS / "fetch_sources.py"), *args],
        capture_output=True, text=True, cwd=str(SKILL_ROOT),
    )


def _ensure_skeleton():
    """确保 .work/skeleton.json 存在（--from-skeleton 模式依赖它）。"""
    subprocess.run(
        [PYTHON, str(SCRIPTS / "extract_skeleton.py"), "--root", str(SAMPLE_PY)],
        capture_output=True, text=True, cwd=str(SKILL_ROOT),
    )


def test_files_mode():
    print("\n[1] --files 模式")
    r = _run("--root", str(SAMPLE_PY), "--files", "app/payment.py,app/order_service.py", "--out", "-")
    check("exit 0", r.returncode == 0, r.stderr)
    check("含 FILE 分隔符", "FILE: app/payment.py" in r.stdout)
    check("含两个文件", r.stdout.count("FILE:") == 2, r.stdout[:200])
    check("含 PaymentClient 类名", "PaymentClient" in r.stdout)


def test_from_skeleton_ids():
    print("\n[2] --from-skeleton --ids 模式")
    _ensure_skeleton()
    sk = json.loads((WORK_DIR / "skeleton.json").read_text(encoding="utf-8"))
    check("skeleton.json 已生成", len(sk.get("files", [])) > 0)
    first_path = sk["files"][0]["path"]
    r = _run("--root", str(SAMPLE_PY), "--from-skeleton", "--ids", "0", "--out", "-")
    check("exit 0", r.returncode == 0, r.stderr)
    check(f"捞取到 files[0] = {first_path}", f"FILE: {first_path}" in r.stdout, r.stdout[:200])


def test_from_skeleton_top():
    print("\n[3] --from-skeleton --top N 模式")
    _ensure_skeleton()
    r = _run("--root", str(SAMPLE_PY), "--from-skeleton", "--top", "1", "--out", "-")
    check("exit 0", r.returncode == 0, r.stderr)
    check("只捞 1 个文件", r.stdout.count("FILE:") == 1, r.stdout[:200])


def test_max_bytes_truncation():
    print("\n[4] --max-bytes 截断行为")
    # 用很小的 max-bytes，两个文件应至少截掉一个
    r = _run("--root", str(SAMPLE_PY), "--files", "app/payment.py,app/order_service.py",
             "--out", "-", "--max-bytes", "50")
    # 50 字节太小，可能只装下 1 个文件或全部截断
    check("exit 0 或 有截断信息", r.returncode == 0 or "max-bytes" in r.stderr or "截断" in r.stderr,
          f"rc={r.returncode} stderr={r.stderr[:200]}")
    # 若成功捞取，应只含部分文件（截断生效）
    if r.returncode == 0:
        check("截断后只捞到 ≤1 个文件", r.stdout.count("FILE:") <= 1, r.stdout[:200])


def test_nonexistent_file():
    print("\n[5] 不存在的文件警告")
    r = _run("--root", str(SAMPLE_PY), "--files", "app/not_exist.py,app/payment.py", "--out", "-")
    check("exit 0（不存在的跳过，存在的捞取）", r.returncode == 0, r.stderr)
    check("stderr 含 WARN 或 跳过", "WARN" in r.stderr or "跳过" in r.stderr, r.stderr[:200])
    check("stdout 含存在的文件", "FILE: app/payment.py" in r.stdout)


def main() -> int:
    print("=== fetch_sources.py 单元测试 ===")
    try:
        test_files_mode()
        test_from_skeleton_ids()
        test_from_skeleton_top()
        test_max_bytes_truncation()
        test_nonexistent_file()
    finally:
        # 清理 .work/（测试产生的中间产物）
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    print(f"\n=== 结果: {_passed} passed, {_failed} failed ===")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
