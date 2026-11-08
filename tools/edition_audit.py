"""Measure product source against the other desktop project, excluding dependencies and bundles."""

import argparse
import ast
import hashlib
import re
from collections import Counter
from pathlib import Path

SRC = {".py", ".ts", ".js", ".vue", ".html", ".css"}
IGNORE = {
    "node_modules",
    "__pycache__",
    "tests",
    "e2e",
    "static",
    "dist",
    "data",
    ".git",
    "build",
    "test-results",
    ".venv",
}
ASSETS = {".png", ".svg", ".jpg", ".webp", ".ico"}


def files(roots):
    for root in roots:
        group = [root] if root.is_file() else root.rglob("*")
        for p in group:
            if (
                any(
                    part in IGNORE - ({"static"} if p.suffix.lower() in ASSETS else set())
                    for part in p.parts
                )
                or not p.is_file()
                or p.name.endswith((".test.ts", ".min.js"))
            ):
                continue
            yield p


def digest(x):
    return hashlib.sha256(x).hexdigest()


def measure(paths):
    lines = Counter()
    blocks = set()
    labels = set()
    assets = set()
    whole = set()
    functions = set()
    count = 0
    for p in paths:
        if p.suffix.lower() in ASSETS:
            assets.add(digest(p.read_bytes()))
            continue
        if p.suffix not in SRC:
            continue
        s = p.read_text(encoding="utf-8-sig", errors="replace")
        if not s.strip():
            continue
        count += 1
        code = [
            x.strip()
            for x in s.splitlines()
            if len(x.strip()) >= 16 and not x.lstrip().startswith(("#", "//", "*", "<!--"))
        ]
        if code:
            whole.add(digest("\n".join(code).encode()))
            lines.update(code)
            blocks.update(tuple(code[i : i + 5]) for i in range(len(code) - 4))
        if p.suffix in {".vue", ".ts", ".html"}:
            labels.update(re.findall(r"[一-龥]{3,}", s))
        if p.suffix == ".py":
            for node in ast.walk(ast.parse(s)):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and len(list(ast.walk(node))) >= 40
                ):
                    functions.add(tuple(type(n).__name__ for n in ast.walk(node)))
    return count, whole, lines, blocks, functions, labels, assets


def report(current, other):
    a = measure(
        files(
            [
                current / "core/src",
                current / "ui/src",
                current / "ui/public",
                current / "services/catalog",
            ]
        )
    )
    upstream = other / "upstream"
    b = measure(
        files(
            [
                *other.glob("desktop*.py"),
                upstream / "src",
                upstream / "web/frontend/src",
                upstream / "web/frontend/index.html",
                *list((upstream / "web").glob("*.py")),
                upstream / "cloud",
                upstream / "assets",
                upstream / "web/frontend/public",
                upstream / "web/static",
            ]
        )
    )
    print(f"source files: {a[0]} vs {b[0]}")
    for label, left, right in zip(
        [
            "same normalized file",
            "matching line occurrences",
            "five-line windows",
            "Python AST shapes",
            "Chinese UI strings (includes comments)",
            "identical asset hashes",
        ],
        a[1:],
        b[1:],
        strict=True,
    ):
        if isinstance(left, Counter):
            hit = sum(n for k, n in left.items() if k in right)
            total = sum(left.values())
        else:
            hit = len(left & right)
            total = len(left)
        print(f"{label}: {hit}/{total} ({100 * hit / max(1, total):.2f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--current", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report(args.current.resolve(), args.baseline.resolve())
