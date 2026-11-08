"""Build an audited source archive without Git history or personal runtime data."""

import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from release_check import inspect

ROOT = Path(__file__).resolve().parents[1]


def main():
    files, issues = inspect(ROOT)
    if issues:
        raise SystemExit(chr(10).join(issues))
    out = ROOT / "dist/toBili-source.zip"
    out.parent.mkdir(exist_ok=True)
    pending = out.with_suffix(".zip.tmp")
    with ZipFile(pending, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, "toBili/" + path.relative_to(ROOT).as_posix())
    with ZipFile(pending) as archive:
        assert archive.testzip() is None
    os.replace(pending, out)
    print(out, len(files), "files")


if __name__ == "__main__":
    main()
