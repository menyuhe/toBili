"""Build clean, checksummed preview archives without reading local account data."""
from pathlib import Path
import argparse, hashlib, shutil, tempfile, zipfile, stat
from package_macos_source import source_files

ROOT = Path(__file__).resolve().parents[1]
TAG = "toBili-20261103"

def frontend_files(folder):
    pending = [folder / "index.html"]
    assets = list((folder / "assets").glob("*"))
    found = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        if not path.is_file():
            raise RuntimeError(f"Missing frontend resource: {path}")
        found.add(path)
        if path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            pending.extend(p for p in assets if p.suffix != ".map" and p.name in text)
    return found

def validate(paths):
    for p in paths:
        if p.name in {"cookies.txt", "llm.env", ".env"} or p.suffix in {".db", ".log", ".sqlite", ".sqlite3"}:
            raise RuntimeError(f"Runtime file in release: {p}")
        if p.is_symlink():
            raise RuntimeError(f"Unexpected symlink: {p}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["source", "windows"])
    args = parser.parse_args()
    output = ROOT / "发行包"
    output.mkdir(exist_ok=True)
    if args.kind == "source":
        paths = set(source_files(ROOT))
        paths.update(ROOT / p for p in [".gitignore", "pytest.ini", "scripts/desktop.spec"])
        front = ROOT / "upstream/web/static/dist"
        paths = {p for p in paths if not p.is_relative_to(front)} | frontend_files(front)
        base, label = ROOT, TAG + "-Source"
    else:
        base = ROOT / "build-output/BinggoDesktop"
        if not (base / "toBili.exe").is_file():
            raise RuntimeError("Build scripts/desktop.spec first")
        shutil.copy2(ROOT / "README.md", base / "README.md")
        shutil.copytree(ROOT / "docs", base / "docs", dirs_exist_ok=True)
        paths = {p for p in base.rglob("*") if p.is_file()}
        front = base / "_internal/web/static/dist"
        paths = {p for p in paths if not p.is_relative_to(front)} | frontend_files(front)
        label = TAG + "-Windows-x64"
    validate(paths)
    archive = output / (label + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for p in sorted(paths):
            info = zipfile.ZipInfo(label + "/" + p.relative_to(base).as_posix())
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if p.suffix in {".command", ".exe"} else 0o644)) << 16
            out.writestr(info, p.read_bytes())
    with zipfile.ZipFile(archive) as check:
        if check.testzip():
            raise RuntimeError("Archive verification failed")
    lines = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in sorted(output.glob("*.zip"))]
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(archive, archive.stat().st_size, "bytes")

if __name__ == "__main__":
    main()
