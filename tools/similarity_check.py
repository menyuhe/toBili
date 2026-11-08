"""Check layer dependencies; optionally report similarity against a supplied source tree.

Metrics are diagnostic indicators, not an originality or licensing determination.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

TEXT_SUFFIXES = {".py", ".ts", ".js", ".vue", ".css", ".html", ".json", ".md"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "data",
    ".pytest_cache",
    ".ruff_cache",
}

# 少于这些有效行的文件不参与相似度比较：导出文件与短脚本的 Jaccard 不稳定。
MIN_CODE_LINES = 20

THRESHOLDS = {
    "jaccard": 0.20,
    "common_run": 10,
    "css_overlap": 0.05,
    "api_overlap": 0,
    "zh_overlap": 0.10,
}

# 旧版本用过的包名。这里只用于"确保自己没有引用它"，不是依赖。
LEGACY_NAMES = ("from src.", "import src.", "from web.", "import web.")

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fff]")

# 语言关键字、内置类型、标准库通用名。这些词人人都会写，留着只会让
# "两个都用 httpx 的文件"看起来像互相抄。要比的是作者特有的命名与结构。
STOPWORDS = frozenset(
    [
        "if",
        "else",
        "elif",
        "for",
        "while",
        "def",
        "class",
        "return",
        "import",
        "from",
        "as",
        "with",
        "try",
        "except",
        "finally",
        "raise",
        "pass",
        "break",
        "continue",
        "and",
        "or",
        "not",
        "in",
        "is",
        "None",
        "True",
        "False",
        "self",
        "cls",
        "lambda",
        "yield",
        "global",
        "nonlocal",
        "assert",
        "del",
        "async",
        "await",
        "match",
        "case",
        "type",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "len",
        "range",
        "print",
        "open",
        "Path",
        "os",
        "sys",
        "re",
        "json",
        "time",
        "typing",
        "Any",
        "Optional",
        "Iterable",
        "Iterator",
        "Mapping",
        "Sequence",
        "Exception",
        "ValueError",
        "RuntimeError",
        "TypeError",
        "KeyError",
        "OSError",
        "file",
        "files",
        "name",
        "path",
        "text",
        "data",
        "value",
        "key",
        "item",
        "result",
        "line",
        "lines",
        "read",
        "write",
        "open",
        "close",
        "get",
        "set",
        "add",
    ]
)
_CSS_CLASS = re.compile(r"\.([a-zA-Z][a-zA-Z0-9_-]{2,})")
_API_PATH = re.compile(r"""["'](/(?:api|v1)/[a-zA-Z0-9_{}/\-]*)["']""")
_ZH = re.compile(r"[\u4e00-\u9fff]{4,}")


def walk(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# 操作系统 API 的名字。任何调用 DPAPI 的代码都会出现这些标识符，
# 它们属于平台事实，不属于任何人的表达。
SYSTEM_SYMBOLS = frozenset(
    [
        "ctypes",
        "wintypes",
        "crypt32",
        "kernel32",
        "WinDLL",
        "windll",
        "DWORD",
        "POINTER",
        "byref",
        "Structure",
        "create_string_buffer",
        "string_at",
        "c_char",
        "pbData",
        "cbData",
        "use_last_error",
        "get_last_error",
        "CryptProtectData",
        "CryptUnprotectData",
        "LocalFree",
        "CSIDL",
        "DPAPI",
    ]
)


def tokens(text: str) -> Counter[str]:
    """只统计标识符与数字。

    中文单字不参与：任何中文注释都会产生"的/不/是/密/文"这类重合，纯噪声。
    中文相似度由 `zh_grams` 用 4-gram 单独衡量，两项各管一段。
    """
    counts: Counter[str] = Counter()
    for word in _TOKEN.findall(text):
        if len(word) == 1 and "\u4e00" <= word <= "\u9fff":
            continue
        if word in STOPWORDS or word in SYSTEM_SYMBOLS or word.isdigit():
            continue
        counts[word] += 1
    return counts


def jaccard(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    keys = set(left) | set(right)
    inter = sum(min(left[k], right[k]) for k in keys)
    union = sum(max(left[k], right[k]) for k in keys)
    return inter / union if union else 0.0


def code_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
            continue
        lines.append(stripped)
    return lines


def longest_run(left: list[str], right: list[str]) -> int:
    """最长公共连续行。O(n*m) 足够，文件不大。"""
    if not left or not right:
        return 0
    best = 0
    previous = [0] * (len(right) + 1)
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] == right[j - 1] and len(left[i - 1]) > 8:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def css_classes(root: Path) -> set[str]:
    found: set[str] = set()
    for path in walk(root):
        if path.suffix == ".css":
            found.update(_CSS_CLASS.findall(read(path)))
    return found


def api_paths(root: Path) -> set[str]:
    found: set[str] = set()
    for path in walk(root):
        if path.suffix == ".py":
            found.update(_API_PATH.findall(read(path)))
    found.discard("/api/health")
    return found


def zh_grams(root: Path, size: int = 4) -> set[str]:
    grams: set[str] = set()
    for path in walk(root):
        for chunk in _ZH.findall(read(path)):
            for i in range(len(chunk) - size + 1):
                grams.add(chunk[i : i + size])
    return grams


def compare(current: Path, baseline: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    base_tokens: dict[str, Counter[str]] = {}
    for path in walk(baseline):
        if path.suffix in {".py", ".ts", ".js", ".vue", ".css", ".html"}:
            if len(code_lines(read(path))) < MIN_CODE_LINES:
                continue
            base_tokens[str(path.relative_to(baseline))] = tokens(read(path))

    # 先算相似度并排序。连续相同行是 O(n*m)，只对最像的若干对计算，
    # 否则一次全量比对要跑十几分钟，没人会真的去跑它。
    scores: list[tuple[float, Path, str]] = []
    for path in walk(current):
        if path.suffix not in {".py", ".ts", ".js", ".vue", ".css", ".html"}:
            continue
        # 只有几行 import 的导出文件，Jaccard 天然接近 1，没有统计意义。
        if len(code_lines(read(path))) < MIN_CODE_LINES:
            continue
        mine = tokens(read(path))
        if not mine:
            continue
        for name, their in base_tokens.items():
            score = jaccard(mine, their)
            if score > 0.0:
                scores.append((score, path, name))
    scores.sort(key=lambda item: item[0], reverse=True)

    worst = (
        (scores[0][0], f"{scores[0][1].relative_to(current)} ↔ {scores[0][2]}")
        if scores
        else (0.0, "")
    )
    worst_run = (0, "")
    for _score, path, name in scores[:40]:
        other = baseline / name
        if not other.is_file():
            continue
        run = longest_run(code_lines(read(path)), code_lines(read(other)))
        if run > worst_run[0]:
            worst_run = (run, f"{path.relative_to(current)} ↔ {name}")

    results.append(
        (
            f"token 相似度（阈值 ≤{THRESHOLDS['jaccard']:.2f}）",
            worst[0] <= THRESHOLDS["jaccard"],
            f"最高 {worst[0]:.3f} — {worst[1] or '无对照'}",
        )
    )
    results.append(
        (
            f"连续相同行（阈值 ≤{THRESHOLDS['common_run']}）",
            worst_run[0] <= THRESHOLDS["common_run"],
            f"最长 {worst_run[0]} 行 — {worst_run[1] or '无对照'}",
        )
    )

    mine_css = css_classes(current)
    their_css = css_classes(baseline)
    shared_css = mine_css & their_css
    ratio = len(shared_css) / len(mine_css) if mine_css else 0.0
    results.append(
        (
            f"CSS class 交集（阈值 <{THRESHOLDS['css_overlap']:.0%}）",
            ratio < THRESHOLDS["css_overlap"],
            f"重合 {len(shared_css)}/{len(mine_css)} = {ratio:.1%}",
        )
    )

    mine_api = api_paths(current)
    their_api = api_paths(baseline)
    shared_api = mine_api & their_api
    results.append(
        (
            "API 路径交集（阈值 =0）",
            len(shared_api) <= THRESHOLDS["api_overlap"],
            f"重合 {sorted(shared_api) if shared_api else '无'}",
        )
    )

    mine_zh = zh_grams(current)
    their_zh = zh_grams(baseline)
    shared_zh = mine_zh & their_zh
    zh_ratio = len(shared_zh) / len(mine_zh) if mine_zh else 0.0
    results.append(
        (
            f"中文文案 4-gram 重合（阈值 <{THRESHOLDS['zh_overlap']:.0%}）",
            zh_ratio < THRESHOLDS["zh_overlap"],
            f"重合 {len(shared_zh)}/{len(mine_zh)} = {zh_ratio:.1%}",
        )
    )
    return results


def check_isolation(root: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    forbidden = ("httpx", "sqlite3", "os.environ", "requests", "aiohttp")
    domain_violations: list[str] = []
    for path in (root / "core" / "src" / "tobili" / "domain").rglob("*.py"):
        text = read(path)
        for name in forbidden:
            if re.search(rf"^\s*(import|from)\s+{re.escape(name)}", text, re.M):
                domain_violations.append(f"{path.name}:{name}")
    results.append(
        (
            "domain 层零 IO 依赖",
            not domain_violations,
            "、".join(domain_violations) or "干净",
        )
    )

    legacy_hits: list[str] = []
    for path in walk(root):
        # 自检脚本本身要写出这些字面量才能检查它们，不能算命中。
        if path.suffix != ".py" or path.parent.name == "tools":
            continue
        text = read(path)
        for name in LEGACY_NAMES:
            if name in text:
                legacy_hits.append(f"{path.name}:{name}")
    results.append(
        (
            "外部旧包名零引用",
            not legacy_hits,
            "、".join(legacy_hits) or "0 命中",
        )
    )

    editorial = root / "editorial"
    if editorial.exists():
        bad = []
        for path in editorial.rglob("*.py"):
            text = read(path)
            if "tobili." in text:
                bad.append(path.name)
        results.append(("私有后台不依赖主程序", not bad, "、".join(bad) or "0 依赖"))
    else:
        results.append(("私有后台不依赖主程序", True, "尚未建立（跳过）"))

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立实现自检")
    parser.add_argument("--root", default=".", help="当前仓库根目录")
    parser.add_argument("--baseline", default="", help="对照仓库根目录（可选）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    rows = check_isolation(root)

    if args.baseline:
        baseline = Path(args.baseline).resolve()
        if not baseline.is_dir():
            print(f"对照目录不存在：{baseline}")
            return 2
        rows.extend(compare(root, baseline))
    else:
        print("（未提供 --baseline，只做了依赖与结构检查）\n")

    width = max(len(name) for name, _ok, _detail in rows)
    failed = 0
    for name, ok, detail in rows:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {name.ljust(width)}  {detail}")

    print()
    if failed:
        print(f"{failed} 项未通过。")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
