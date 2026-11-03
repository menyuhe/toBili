"""Export a standalone relay kit using a source allowlist (never local data)."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {".py", ".js", ".css", ".html", ".svg", ".png", ".webmanifest"}


def package(destination: Path, root: Path = ROOT):
    files = [p for p in (root / "cloud").rglob("*")
             if p.is_file() and p.suffix in ALLOWED and "__pycache__" not in p.parts]
    files += [root / "cloud" / name for name in ("Dockerfile", "Caddyfile", "compose.yaml", "requirements.txt", ".env.example")]
    files += [root / "src" / name for name in ("__init__.py", "cloud_protocol.py", "catalog_protocol.py")]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(set(files)):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("不允许导出链接或目录外文件")
            out.write(path, "toBili-Relay-v2/" + path.relative_to(root).as_posix())
        out.writestr("toBili-Relay-v2/README.txt", "toBili 活动资料服务 v2\n\n在 cloud 目录中复制 .env.example 为 .env，填写域名、随机十六进制数据库密码与邀请码。\n确保域名已指向服务器，开放 80/443。运行 docker compose up -d --build。\n\n手机打开 https://你的域名；桌面端设置 → 多端同步中填写同一地址。\n本包没有 B 站执行器，不包含 Cookie、设备会话、本地数据库或用户配置。\n完整说明见仓库 docs/multi-device-v1.md。\n")
    with zipfile.ZipFile(destination) as check:
        if check.testzip():
            raise RuntimeError("部署包校验失败")
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".sha256").write_text(checksum + "  " + destination.name + "\n", encoding="utf-8")
    return len(files)


if __name__ == "__main__":
    output = ROOT.parent / "dist" / "toBili-Relay-v2.zip"
    print(f"Relay kit: {output}; files={package(output)}")
