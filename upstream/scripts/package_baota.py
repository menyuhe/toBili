"""Build the flat ToBili archive for /www/wwwroot/tobill."""
import ast
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
script = (root / "cloud/baota/setup.sh").read_text(encoding="utf-8")
ast.parse(script.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0])
target = root.parent / "dist/ToBili-BaoTa.zip"
prefix = "toBili-Relay-v1/"
with zipfile.ZipFile(root.parent / "dist/toBili-Relay-v1.zip") as source, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
    for name in source.namelist():
        if name.startswith(prefix):
            out.writestr(name[len(prefix):], source.read(name))
    for file in (root / "cloud/baota").glob("*"):
        if file.is_file():
            out.write(file, file.relative_to(root).as_posix())
    out.write(root / "docs/deployment-baota.md", "docs/deployment-baota.md")
print(target)
