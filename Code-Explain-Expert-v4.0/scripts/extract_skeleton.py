#!/usr/bin/env python3
"""extract_skeleton.py — 分层摘要蒸馏第一层：提取项目骨架（不含实现代码）。

遍历源码文件，提取类名、方法签名、Import 列表、文件间引用关系，
输出结构化 JSON 作为 LLM 的"全局上下文"，供其决策注释优先级。

用法:
    python extract_skeleton.py --root <项目根目录> [--path <目标子目录>]
    # 默认自动落盘到 skill 目录下 .work/skeleton.json（不污染客户项目）；--out - 输出到 stdout；--out <路径> 自定义
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

# skill 工作目录：中间产物（skeleton.json / batch.txt / chunks.json）自动落盘到这里，
# 不污染客户项目。完成后由 LLM 统一清理。
SKILL_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = SKILL_ROOT / ".work"

# 源码扩展名 -> 语言
SOURCE_EXT = {
    ".java": "java", ".kt": "kotlin",
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".cs": "csharp", ".php": "php", ".rb": "ruby", ".swift": "swift",
}

# 跳过目录（任意层级命中即跳过）
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
    "dist", "build", "target", "out", "bin", "obj",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "coverage", ".next", ".nuxt", "vendor", ".gradle", "bower_components",
    ".terraform", "Pods", ".dart_tool",
}

# 跳过文件（构建产物 / 生成文件）
SKIP_FILES_PREFIX = ("min.", ".min.")
SKIP_FILES_SUFFIX = {".map", ".d.ts", ".pyc", ".pyo", ".class", ".jar", ".so", ".dll", ".dylib", ".exe"}

# 明确跳过的非源码扩展名（配置文件 / 文档 / 静态资源 / 数据）
NON_SOURCE_EXT = {
    # 配置文件
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf", ".properties",
    ".env", ".gradle", ".pro", ".mk", ".lock",
    # 文档
    ".md", ".markdown", ".rst", ".txt", ".adoc",
    # 静态资源 / 样式 / 模板
    ".css", ".scss", ".sass", ".less", ".html", ".htm", ".vue", ".svg", ".png", ".jpg",
    ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".webp",
    # 数据 / 其他
    ".csv", ".tsv", ".sql", ".db", ".sqlite", ".log", ".pdf", ".zip", ".tar", ".gz",
}

# 测试文件特征
TEST_PATTERNS = (
    r"(^|[/\\])(test|tests|__tests__|spec|specs)([/\\]|$)",
    r"(^|[/\\])test_[^/\\]+\.(py|js|ts)$",
    r"(^|[/\\])[^/\\]*(_test|\.test|\.spec|Test)\.(java|py|js|ts|go)$",
    r"(^|[/\\])[^/\\]*Test(s)?\.(java|kt)$",
)

# 构建标志文件 -> 主导语言（用于语言识别）
BUILD_MARKERS = {
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "kotlin",
    "settings.gradle": "java", "settings.gradle.kts": "kotlin",
    "package.json": "javascript", "tsconfig.json": "typescript",
    "go.mod": "go", "Cargo.toml": "rust",
    "setup.py": "python", "pyproject.toml": "python", "requirements.txt": "python",
    "Pipfile": "python",
}

# 控制流关键字：方法名正则需排除，避免把 if/for 等误识别为方法
CONTROL_KEYWORDS = (
    "if|for|while|switch|catch|return|new|case|else|do|try|finally|synchronized|"
    "with|match|from|import|in|assert|throw|yield|await|delete|typeof|instanceof|extends"
)
_KEYWORD_GUARD = rf"(?!{CONTROL_KEYWORDS}\b)"

# ---------------------------------------------------------------------------
# 文本清洗：去注释与字符串字面量，保留换行（行号可用）
# ---------------------------------------------------------------------------


def _strip_comments(source: str, ext: str) -> str:
    """剥离注释与字符串字面量，保留换行（行号可用）。

    注意：本函数仅用于非 Python 语言的"近似行号定位"。Python 用 ast 解析，
    无需依赖此函数的字符串/文档串处理精度。对 Java/JS/TS 的多行字符串不做
    完美处理（这些语言罕见多行字符串），但对单行字符串与转义符正确。
    """
    lines = source.split("\n")
    out: list[str] = []
    in_block = False
    for line in lines:
        i = 0
        cleaned = ""
        in_str: str | None = None
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_block:
                if ch == "*" and nxt == "/":
                    in_block = False
                    i += 2
                    continue
                i += 1
                continue
            if in_str is not None:
                # 转义：反斜杠后跟任意字符，整体跳过，原位补空格保持列对齐
                if ch == "\\":
                    cleaned += "  "
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
                    cleaned += " "
                    i += 1
                    continue
                cleaned += " "
                i += 1
                continue
            if ext == "python" and ch == "#":
                break
            if ch == "/" and nxt == "/":
                break
            if ch == "/" and nxt == "*":
                in_block = True
                i += 2
                continue
            if ch in ("'", '"', "`"):
                in_str = ch
                cleaned += " "
                i += 1
                continue
            cleaned += ch
            i += 1
        out.append(cleaned)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 解析正则
# ---------------------------------------------------------------------------

# Java/Kotlin/C#/C/C++ 类声明
_CLASS_RE = re.compile(
    r"(?:(?:@\w+(?:\([^)\n]*\))?)\s*)*"
    r"\b(?P<kw>class|interface|enum|record|struct|trait)\s+"
    r"(?P<name>[A-Za-z_$]\w*)\s*"
    r"(?:<[^>]*>)?\s*"
    r"(?P<extends>extends\s+[A-Za-z_$][\w.$]*)?\s*"
    r"(?P<implements>implements\s+[A-Za-z_$][\w.$]*(?:\s*,\s*[A-Za-z_$][\w.$]*)*)?",
    re.MULTILINE,
)

# 方法声明：modifiers [retType] name(params) { | ; | => | 换行
_METHOD_RE = re.compile(
    r"(?P<annot>(?:@\w+(?:\([^)\n]*\))?\s*)*)"
    r"(?P<mods>(?:(?:public|private|protected|static|final|abstract|synchronized|default|async|"
    r"override|open|internal|extern|pub\s+fn|export\s+default|export\s+|virtual|sealed|inline)\s+)*)"
    r"(?:(?P<ret>[A-Za-z_$][\w.$<>?\[\],\s]*?)\s+)?"
    rf"(?<![\w.$])(?P<name>{_KEYWORD_GUARD}[A-Za-z_$]\w*)"
    r"\s*\((?P<params>[^;{}]*?)\)\s*(?=\s*(?:\{|;|=>|\n))",
)

_CLASS_RE_PY = re.compile(
    r"^class\s+(?P<name>[A-Za-z_]\w*)\s*(?:\((?P<bases>[^)]*)\))?\s*:",
    re.MULTILINE,
)

_METHOD_RE_PY = re.compile(
    r"^[ \t]*(?P<deco>(?:@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
    r"(?P<mods>async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*->\s*(?P<ret>[^:]+))?\s*:",
    re.MULTILINE,
)

_FUNC_RE_TS = re.compile(
    r"(?P<mods>(?:export\s+)?(?:default\s+)?(?:async\s+)?)?"
    r"\bfunction\s+(?P<name>[A-Za-z_$]\w*)\s*\((?P<params>[^)]*)\)\s*"
    r"(?::\s*(?P<ret>[\w.<>\[\],\s]+))?\s*\{",
)

_FUNC_RE_GO = re.compile(
    r"^func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*(?P<ret>[^{]+))?\s*\{",
    re.MULTILINE,
)

_IMPORT_RE_JAVA = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.[*])?)\s*;", re.MULTILINE)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)

_IMPORT_RE_PY = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+.*|import\s+([\w.]+))", re.MULTILINE)
_IMPORT_RE_TS = re.compile(r"^\s*import\s+(?:type\s+)?(?:\{[\s\S]*?\}\s*|\*+\s+as\s+\w+\s*|\w+\s*,\s*)?from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)
_REQUIRE_RE_TS = re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
_IMPORT_RE_GO = re.compile(r'^\s*"([^"]+)"', re.MULTILINE)


def _line_of(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


def _class_annotations(lines_before: list[str]) -> list[str]:
    return [ln.strip().split("(")[0] for ln in lines_before if ln.strip().startswith("@")][:5]


def _method_annotations(raw: str) -> list[str]:
    return [a.strip().split("(")[0] for a in raw.split() if a.startswith("@")][:5]


# ---------------------------------------------------------------------------
# 语言解析器
# ---------------------------------------------------------------------------


def _parse_java_like(source: str, lines: list[str]) -> tuple[list[dict], list[str], str, list[str]]:
    """Java / Kotlin / C# / C / C++：类 + 方法 + imports + package。"""
    classes: list[dict] = []
    imports: list[str] = []
    package = ""
    for m in _IMPORT_RE_JAVA.finditer(source):
        imports.append(m.group(1))
    pm = _PACKAGE_RE.search(source)
    if pm:
        package = pm.group(1)

    for cm in _CLASS_RE.finditer(source):
        name, kw = cm.group("name"), cm.group("kw")
        cls_line = _line_of(source, cm.start())
        body_start = source.find("{", cm.end())
        if body_start == -1:
            methods: list[dict] = []
        else:
            depth, body_end = 0, len(source)
            for i in range(body_start, len(source)):
                if source[i] == "{":
                    depth += 1
                elif source[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body_end = i
                        break
            body = source[body_start:body_end]
            methods = []
            for mm in _METHOD_RE.finditer(body):
                methods.append({
                    "name": mm.group("name"),
                    "signature": mm.group(0).replace("\n", " ").strip()[:200],
                    "annotations": _method_annotations(mm.group("annot")),
                    "line": _line_of(source, body_start + mm.start()),
                })
        classes.append({
            "name": name,
            "type": kw,
            "annotations": _class_annotations(lines[max(0, cls_line - 7): cls_line - 1]),
            "extends": (cm.group("extends") or "").replace("extends", "").strip(),
            "implements": [s.strip() for s in (cm.group("implements") or "").replace("implements", "").split(",") if s.strip()],
            "methods": methods,
            "line": cls_line,
        })
    # 类体引用类型：imports 的短名 + 类声明里的 extends/implements/字段类型难以精确提取，交由第二遍扫描补全
    referenced = [imp.split(".")[-1].replace("*", "") for imp in imports]
    return classes, imports, package, referenced


def _parse_python(raw: str) -> tuple[list[dict], list[str], str, list[dict]]:
    """Python 用 ast 解析，精度远胜正则。

    返回 (classes, imports, package, top_level_functions)。
    package 字段对 Python 无意义，固定返回 ""。
    referenced_types 由 extract_file 第二遍扫描统一补全，此处不返回。
    """
    classes: list[dict] = []
    imports: list[str] = []
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return [], [], "", []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.names and node.names[0].name == "*":
                imports.append(base + ".*" if base else "*")
            else:
                imports.append(base)

    def _deco(d) -> str:
        try:
            return ast.unparse(d)
        except Exception:
            return ""

    def _sig(fn) -> str:
        args = [a.arg for a in fn.args.args]
        return f"{fn.name}({', '.join(args)})"

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods: list[dict] = []
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": b.name,
                        "signature": _sig(b),
                        "annotations": [_deco(d) for d in b.decorator_list][:5],
                        "line": b.lineno,
                    })
            classes.append({
                "name": node.name,
                "type": "class",
                "bases": [_deco(b) for b in node.bases],
                "annotations": [_deco(d) for d in node.decorator_list][:5],
                "methods": methods,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
            })

    top_funcs: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_funcs.append({
                "name": node.name,
                "signature": _sig(node),
                "line": node.lineno,
            })

    return classes, imports, "", top_funcs


def _parse_ts_like(source: str) -> tuple[list[dict], list[str], list[str], list[dict]]:
    classes: list[dict] = []
    imports: list[str] = []
    for m in _IMPORT_RE_TS.finditer(source):
        imports.append(m.group(1))
    for m in _REQUIRE_RE_TS.finditer(source):
        imports.append(m.group(1))

    for cm in _CLASS_RE.finditer(source):
        name, kw = cm.group("name"), cm.group("kw")
        if kw == "trait":
            continue
        cls_line = _line_of(source, cm.start())
        body_start = source.find("{", cm.end())
        if body_start == -1:
            methods: list[dict] = []
        else:
            depth, body_end = 0, len(source)
            for i in range(body_start, len(source)):
                if source[i] == "{":
                    depth += 1
                elif source[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body_end = i
                        break
            body = source[body_start:body_end]
            methods = []
            for mm in _METHOD_RE.finditer(body):
                methods.append({
                    "name": mm.group("name"),
                    "signature": mm.group(0).replace("\n", " ").strip()[:200],
                    "annotations": _method_annotations(mm.group("annot")),
                    "line": _line_of(source, body_start + mm.start()),
                })
        classes.append({
            "name": name, "type": kw, "annotations": [],
            "extends": (cm.group("extends") or "").replace("extends", "").strip(),
            "implements": [s.strip() for s in (cm.group("implements") or "").replace("implements", "").split(",") if s.strip()],
            "methods": methods, "line": cls_line,
        })

    funcs: list[dict] = []
    for fm in _FUNC_RE_TS.finditer(source):
        funcs.append({"name": fm.group("name"), "signature": fm.group(0).replace("\n", " ").strip()[:200], "line": _line_of(source, fm.start())})
    referenced = [imp.split("/")[-1].replace("'", "") for imp in imports]
    return classes, imports, referenced, funcs


def _parse_go(source: str) -> tuple[list[dict], list[str], list[str], list[dict]]:
    classes: list[dict] = []
    imports: list[str] = []
    for m in _IMPORT_RE_GO.finditer(source):
        imports.append(m.group(1))
    funcs: list[dict] = []
    for fm in _FUNC_RE_GO.finditer(source):
        recv = (fm.group("recv") or "").strip()
        name = fm.group("name")
        target = classes if recv else funcs
        target.append({
            "name": name,
            "signature": fm.group(0).replace("\n", " ").strip()[:200],
            "receiver": recv,
            "line": _line_of(source, fm.start()),
        })
    referenced = [imp.split("/")[-1] for imp in imports]
    return classes, imports, referenced, funcs


def _count_comment_ratio(source: str, ext: str) -> float:
    total = max(1, source.count("\n"))
    if ext == "python":
        comment_lines = sum(1 for ln in source.split("\n") if ln.strip().startswith("#"))
    else:
        comment_lines = sum(1 for ln in source.split("\n") if ln.strip().startswith(("//", "/*", "*")))
    return round(min(1.0, comment_lines / total), 3)


# ---------------------------------------------------------------------------
# 遍历与组装
# ---------------------------------------------------------------------------


def is_test_file(rel: str) -> bool:
    return any(re.search(p, rel) for p in TEST_PATTERNS)


def detect_language(root: Path) -> str:
    for marker, lang in BUILD_MARKERS.items():
        if (root / marker).exists():
            return lang
    return "unknown"


def walk_sources(root: Path, target: Path | None = None) -> list[Path]:
    base = target if target else root
    files: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith(SKIP_FILES_PREFIX) or p.suffix.lower() in SKIP_FILES_SUFFIX:
            continue
        ext = p.suffix.lower()
        if ext not in SOURCE_EXT:
            continue
        files.append(p)
    return files


def extract_file(p: Path, root: Path, project_type_names: set[str] | None = None) -> dict | None:
    ext = p.suffix.lower()
    lang = SOURCE_EXT[ext]
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not raw.strip():
        return None
    lines = raw.split("\n")
    clean = _strip_comments(raw, lang)
    rel = p.relative_to(root).as_posix()

    if lang in ("java", "kotlin", "csharp", "php", "c", "cpp"):
        classes, imports, package, referenced = _parse_java_like(clean, lines)
        funcs: list[dict] = []
    elif lang == "python":
        # Python 用 ast 解析（直接传 raw，不依赖 _strip_comments 的字符串精度）
        classes, imports, package, funcs = _parse_python(raw)
        referenced = []  # 第二遍扫描会基于项目类型名集合补全
    elif lang in ("typescript", "javascript"):
        classes, imports, referenced, funcs = _parse_ts_like(clean)
        package = ""
    elif lang == "go":
        classes, imports, referenced, funcs = _parse_go(clean)
        package = ""
    else:
        classes, imports, package, referenced = [], [], "", []
        funcs = []

    # 第二遍：项目内类型名出现在本文件文本中 → 追加为引用（覆盖 import 短名噪声）
    if project_type_names:
        local = {c["name"] for c in classes}
        in_text = {t for t in project_type_names if t not in local and re.search(rf"\b{re.escape(t)}\b", clean)}
        referenced = sorted((in_text | set(referenced)) - {""})[:80]
    else:
        referenced = list(dict.fromkeys(r for r in referenced if r))[:80]

    return {
        "path": rel,
        "language": lang,
        "package": package,
        "imports": imports[:100],
        "classes": classes,
        "top_level_functions": funcs[:50],
        "referenced_types": referenced,
        "line_count": len(lines),
        "size_bytes": p.stat().st_size,
        "existing_comment_ratio": _count_comment_ratio(raw, lang),
    }


def build_dependency_links(files: list[dict]) -> list[dict]:
    type_to_file: dict[str, str] = {}
    for f in files:
        for cls in f["classes"]:
            type_to_file.setdefault(cls["name"], f["path"])
    links: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for f in files:
        for ref in f["referenced_types"]:
            target = type_to_file.get(ref)
            if target and target != f["path"]:
                key = (f["path"], target)
                if key not in seen:
                    seen.add(key)
                    links.append({"from": f["path"], "to": target, "kind": "type_reference"})
    return links


def main() -> int:
    parser = argparse.ArgumentParser(description="提取项目骨架 JSON（类/方法/import/依赖关系，不含实现代码）")
    parser.add_argument("--root", required=True, help="项目根目录")
    parser.add_argument("--path", default=None, help="只扫描目标子目录（定向攻坚模式）")
    parser.add_argument("--out", default=None, help="输出路径（默认自动落盘到 skill .work/skeleton.json；- = stdout；<路径> = 自定义）")
    parser.add_argument("--include-tests", action="store_true", help="包含测试文件（默认跳过）")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 根目录不存在: {root}", file=sys.stderr)
        return 1
    target = Path(args.path).resolve() if args.path else None
    if target and not target.is_dir():
        print(f"[ERROR] 目标目录不存在: {target}", file=sys.stderr)
        return 1

    language = detect_language(root)
    files = walk_sources(root, target)
    file_list: list[Path] = []
    skipped_tests = 0
    for p in files:
        rel = p.relative_to(root).as_posix()
        if not args.include_tests and is_test_file(rel):
            skipped_tests += 1
            continue
        file_list.append(p)

    # 第一遍：收集全部项目类型名
    project_type_names: set[str] = set()
    for p in file_list:
        info = extract_file(p, root, project_type_names=None)
        if info:
            project_type_names.update(c["name"] for c in info["classes"])
            project_type_names.update(fn["name"] for fn in info["top_level_functions"])

    # 第二遍：带类型名集合提取引用关系
    scanned: list[dict] = []
    for p in file_list:
        info = extract_file(p, root, project_type_names=project_type_names)
        if info:
            scanned.append(info)

    scanned.sort(key=lambda f: f["path"])
    links = build_dependency_links(scanned)

    modules: dict[str, list[str]] = {}
    for f in scanned:
        parts = f["path"].split("/")
        mod = parts[0] if len(parts) > 1 else "(root)"
        modules.setdefault(mod, []).append(f["path"])

    skeleton = {
        "project_root": root.as_posix(),
        "language_hint": language,
        "scan_scope": target.as_posix() if target else root.as_posix(),
        "total_files": len(scanned),
        "skipped_test_files": skipped_tests,
        "modules": {m: sorted(fl) for m, fl in sorted(modules.items())},
        "files": scanned,
        "dependency_links": links,
    }

    skeleton_json = json.dumps(skeleton, ensure_ascii=False, indent=2)
    summary = f"语言: {language} | 文件数: {len(scanned)} | 跳过测试: {skipped_tests} | 依赖边: {len(links)}"
    if args.out == "-":
        print(skeleton_json)
        print(f"[OK] 骨架已输出到 stdout | {summary}", file=sys.stderr)
    else:
        out_path = Path(args.out) if args.out else WORK_DIR / "skeleton.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(skeleton_json, encoding="utf-8")
        print(f"[OK] 骨架已写入 {out_path} | {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
