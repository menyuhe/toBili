"""Audit the exact public source selection; never read user runtime directories."""

import argparse
import os
import re
from pathlib import Path

ROOTS = {"core", "ui", "services", "tools", "tests", ".github", "docs/public"}
TOP = {
    "README.md",
    "PROJECT.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "uv.lock",
    "pyproject.toml",
    ".gitignore",
    ".gitattributes",
}
SKIP = {
    "node_modules",
    "data",
    "dist",
    "build",
    ".git",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".vite",
}
PRIVATE = {
    "private_pack.py",
    "coordination.py",
    "revisions.py",
    "block_sync.py",
    "sync_worker.py",
    "public_publish.py",
    "publication.py",
    "prize_sync.py",
    "PrizeChanges.vue",
    "prize_hub.py",
    "public_node.py",
    "compose_digest.py",
    "digest.py",
    "RichField.vue",
    "Digest.vue",
    "editorApi.ts",
}
PRIVATE_DIRS = {"pro_directory", "pro_backup", "emotes", "rich", "extensions"}
TEXT = {
    ".py",
    ".ts",
    ".js",
    ".mjs",
    ".vue",
    ".css",
    ".html",
    ".json",
    ".toml",
    ".lock",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".manifest",
}
LOGOS = {
    "ui/public/icon.png",
    "ui/public/favicon.png",
    "ui/public/lottery-studio.png",
    "docs/public/media/discover.png",
    "docs/public/media/queue.png",
    "docs/public/media/schedule.png",
    "docs/public/media/accounts.png",
    "docs/public/media/workflow.gif",
    "docs/public/media/account-status.gif",
    "docs/public/media/activity.png",
    "docs/public/media/overview.png",
    "docs/public/media/sources.png",
    "docs/public/media/dark.png",
    "docs/public/media/account-menu.png",
    "docs/public/media/selection.png",
}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "access-token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{32,})"
    ),
    "personal-path": re.compile(r"[A-Z]:[\\/](?:Users|Mysoft)[\\/]", re.I),
    "url-password": re.compile(r"https?://[^\s/'\"]+:[^\s/'\"]+@"),
}


def selected(root):
    root = Path(root).resolve()
    for name in sorted(TOP):
        if (root / name).is_file():
            yield root / name
    for name in sorted(ROOTS):
        for folder, dirs, files in os.walk(root / name, followlinks=False):
            for entry in dirs:
                p = Path(folder) / entry
                if entry not in SKIP and (p.is_symlink() or p.is_junction()):
                    raise ValueError(f"linked directory: {p.relative_to(root)}")
            dirs[:] = [d for d in dirs if d not in SKIP]
            for filename in sorted(files):
                yield Path(folder) / filename


def inspect(root, deny=()):
    root = Path(root).resolve()
    files, issues = [], []
    for p in selected(root):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink() or not p.resolve().is_relative_to(root):
            issues.append(f"{rel}: linked file")
            continue
        if p.name in PRIVATE or PRIVATE_DIRS.intersection(p.relative_to(root).parts):
            issues.append(f"{rel}: private implementation")
        if p.name.startswith(".env") or p.suffix.lower() in {
            ".pem",
            ".key",
            ".db",
            ".sqlite",
            ".sqlite3",
            ".bin",
            ".exe",
            ".zip",
            ".webp",
            ".pyc",
        }:
            issues.append(f"{rel}: runtime/secret/binary file")
        if p.suffix not in TEXT and rel not in TOP and rel not in LOGOS:
            issues.append(f"{rel}: unexpected file type")
        if rel in LOGOS:
            files.append(p)
            continue
        try:
            content = p.read_text(encoding="utf-8-sig")
        except UnicodeError:
            issues.append(f"{rel}: non-text content")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                issues.append(f"{rel}: {label}")
        if any(value and value.casefold() in content.casefold() for value in deny):
            issues.append(f"{rel}: personal identifier")
        files.append(p)
    return files, issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--deny", action="append", default=[], help="Additional private literal; not echoed"
    )
    args = parser.parse_args()
    files, issues = inspect(args.root, args.deny)
    for issue in issues:
        print(issue)
    print(f"Public source: {len(files)} files; findings: {len(issues)}")
    return bool(issues)


if __name__ == "__main__":
    raise SystemExit(main())
