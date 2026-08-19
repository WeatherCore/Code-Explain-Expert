#!/usr/bin/env python3
"""test_extract_skeleton_unit.py — extract_skeleton.py 单元测试。

验证：is_test_file / detect_language / build_dependency_links / _parse_python / extract_file / 端到端依赖边。
纯标准库，python tests/test_extract_skeleton_unit.py 直接跑。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import extract_skeleton as es

PYTHON = sys.executable
FIXTURES = SKILL_ROOT / "tests" / "fixtures"
SAMPLE_JAVA = FIXTURES / "sample-java"

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


def test_is_test_file():
    print("\n[1] is_test_file — 测试文件识别（红线：测试代码不读不注）")
    cases = [
        ("test_foo.py", True), ("foo_test.py", True), ("foo.spec.ts", True),
        ("tests/app/payment.py", True), ("app/payment.py", False),
        ("PaymentServiceTest.java", True), ("PaymentService.java", False),
        ("src/test/java/FooTest.java", True), ("src/main/java/Foo.java", False),
        ("__tests__/index.ts", True), ("e2e/login.spec.js", True),
    ]
    for path, expected in cases:
        got = es.is_test_file(path)
        check(f"is_test_file({path!r}) == {expected}", got == expected, f"got {got}")


def test_detect_language():
    print("\n[2] detect_language — 构建标志识别")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pom.xml").write_text("<project/>")
        check("pom.xml -> java", es.detect_language(root) == "java")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "go.mod").write_text("module x")
        check("go.mod -> go", es.detect_language(root) == "go")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "package.json").write_text("{}")
        check("package.json -> javascript", es.detect_language(root) == "javascript")
    with tempfile.TemporaryDirectory() as td:
        check("无构建标志 -> unknown", es.detect_language(Path(td)) == "unknown")


def test_build_dependency_links():
    print("\n[3] build_dependency_links — 依赖边建立质量")
    files = [
        {"path": "a.py", "classes": [{"name": "A"}], "referenced_types": ["B"]},
        {"path": "b.py", "classes": [{"name": "B"}], "referenced_types": []},
        {"path": "c.py", "classes": [{"name": "C"}], "referenced_types": ["A", "B"]},
    ]
    links = es.build_dependency_links(files)
    pairs = {(l["from"], l["to"]) for l in links}
    check("a.py -> b.py（A 引用 B）", ("a.py", "b.py") in pairs)
    check("c.py -> a.py（C 引用 A）", ("c.py", "a.py") in pairs)
    check("c.py -> b.py（C 引用 B）", ("c.py", "b.py") in pairs)
    check("无自引用 a.py -> a.py", ("a.py", "a.py") not in pairs)
    check("无反向边 b.py -> a.py", ("b.py", "a.py") not in pairs)
    check("无重复边", len(links) == len(pairs))


def test_parse_python():
    print("\n[4] _parse_python — ast 解析精度")
    src = '''"""模块 docstring。"""
import os
from typing import List

@deco
class Foo(Base):
    def method(self, x: int) -> str:
        return str(x)

async def async_func(a, b):
    pass

def top_func():
    pass
'''
    classes, imports, package, funcs = es._parse_python(src)
    check("识别类 Foo", any(c["name"] == "Foo" for c in classes))
    check("Foo 含基类 Base", "Base" in classes[0].get("bases", []))
    check("Foo 含方法 method", any(m["name"] == "method" for m in classes[0]["methods"]))
    check("Foo 含装饰器 deco", any("deco" in d for d in classes[0].get("annotations", [])))
    check("imports 含 os", "os" in imports)
    check("imports 含 typing", "typing" in imports)
    check("顶层函数含 async_func", any(f["name"] == "async_func" for f in funcs))
    check("顶层函数含 top_func", any(f["name"] == "top_func" for f in funcs))
    check("顶层函数不含 method（类方法）", not any(f["name"] == "method" for f in funcs))


def test_extract_file_java():
    print("\n[5] extract_file — Java 文件提取")
    p = SAMPLE_JAVA / "src" / "main" / "java" / "com" / "demo" / "payment" / "PaymentController.java"
    info = es.extract_file(p, SAMPLE_JAVA,
                           project_type_names={"PaymentController", "PaymentService", "PaymentMapper"})
    check("返回非 None", info is not None)
    check("language == java", info["language"] == "java")
    check("含 PaymentController 类", any(c["name"] == "PaymentController" for c in info["classes"]))
    check("含方法", len(info["classes"][0]["methods"]) > 0)
    check("existing_Explain_ratio 字段存在", "existing_Explain_ratio" in info)
    check("line_count > 0", info["line_count"] > 0)
    check("referenced_types 含 PaymentService", "PaymentService" in info.get("referenced_types", []))


def test_end_to_end_java_dependency():
    print("\n[6] 端到端 — sample-java 依赖边（Controller→Service→Mapper）")
    r = subprocess.run(
        [PYTHON, str(SKILL_ROOT / "scripts" / "extract_skeleton.py"),
         "--root", str(SAMPLE_JAVA), "--out", "-"],
        capture_output=True, text=True,
    )
    check("exit 0", r.returncode == 0, r.stderr)
    sk = json.loads(r.stdout)
    links = sk.get("dependency_links", [])
    pairs = {(l["from"], l["to"]) for l in links}
    controller_link_exists = any(
        "PaymentController" in frm and "PaymentService" in to
        for frm, to in pairs
    )
    check("Controller -> Service 依赖边存在", controller_link_exists, f"links: {pairs}")
    check("dependency_links 非空", len(links) > 0, f"links: {links}")


def test_quick_start_files():
    print("\n[7] quick_start_files — 自动推荐快速上手 3 文件")
    MALL = SKILL_ROOT / "references" / "samples" / "Mall-Order"
    r = subprocess.run(
        [PYTHON, str(SKILL_ROOT / "scripts" / "extract_skeleton.py"),
         "--root", str(MALL), "--out", "-"],
        capture_output=True, text=True,
    )
    check("Mall-Order exit 0", r.returncode == 0, r.stderr)
    sk = json.loads(r.stdout)
    qs = sk.get("quick_start_files", [])
    check("quick_start_files 字段存在且非空", len(qs) > 0)
    check("quick_start_files ≤3 个", len(qs) <= 3, str(len(qs)))
    if qs:
        first = qs[0]
        check("每项含 path 字段", "path" in first)
        check("每项含 role 字段", "role" in first)
        check("每项含 reason 字段", "reason" in first)
    # 入口应为 OrderController（命名特征优化生效，排除 Impl 误判）
    entry = qs[0] if qs else {}
    check("入口是 OrderController（非 OrderServiceImpl）",
          "OrderController" in entry.get("path", ""),
          f"got {entry.get('path')}")
    check("入口 role == '入口'", entry.get("role") == "入口", f"got {entry.get('role')}")

    # sample-java 文件数 ≤3，应全部纳入
    r2 = subprocess.run(
        [PYTHON, str(SKILL_ROOT / "scripts" / "extract_skeleton.py"),
         "--root", str(SAMPLE_JAVA), "--out", "-"],
        capture_output=True, text=True,
    )
    sk2 = json.loads(r2.stdout)
    qs2 = sk2.get("quick_start_files", [])
    check("sample-java（≤3 文件）全部纳入", len(qs2) == sk2["total_files"],
          f"qs={len(qs2)} total={sk2['total_files']}")


def main() -> int:
    print("=== extract_skeleton.py 单元测试 ===")
    test_is_test_file()
    test_detect_language()
    test_build_dependency_links()
    test_parse_python()
    test_extract_file_java()
    test_end_to_end_java_dependency()
    test_quick_start_files()
    print(f"\n=== 结果: {_passed} passed, {_failed} failed ===")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
