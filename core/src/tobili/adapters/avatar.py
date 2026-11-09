"""Small local avatar cache. CDN requests never carry account credentials."""

import hashlib
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def image_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in {"i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com"}
        or parts.username
        or parts.password
        or parts.port
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/bfs/")
    ):
        raise ValueError("Unsupported avatar address")
    return "https://" + parts.hostname + parts.path.split("@", 1)[0] + "@96w_96h_1c.png"


def media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Avatar response is not an image")


class AvatarCache:
    def __init__(self, root: Path, *, transport=None):
        self.root = root
        self.transport = transport
        self.lock = threading.Lock()
        self.failed: dict[str, float] = {}

    def get(self, source: str) -> tuple[bytes, str]:
        url = image_url(source)
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / (key + ".img")
        with self.lock:
            # The CDN URL changes with the avatar; an existing URL is immutable.
            if path.is_file():
                data = path.read_bytes()
                if len(data) <= 512_000:
                    try:
                        return data, media_type(data)
                    except ValueError:
                        pass
            if self.failed.get(key, 0) > time.monotonic():
                raise ValueError("Avatar temporarily unavailable")
            try:
                with (
                    httpx.Client(
                        timeout=8, trust_env=False, follow_redirects=False, transport=self.transport
                    ) as client,
                    client.stream(
                        "GET", url, headers={"Referer": "https://www.bilibili.com/"}
                    ) as response,
                ):
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 512_000:
                            raise ValueError("Avatar too large")
                data = bytes(content)
                kind = media_type(data)
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
                self.failed.pop(key, None)
                return data, kind
            except (httpx.HTTPError, OSError, ValueError):
                self.failed[key] = time.monotonic() + 60
                raise ValueError("Avatar temporarily unavailable") from None
