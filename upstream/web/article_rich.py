"""Sanitized local article HTML and portable, account-scoped image assets."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import warnings
import zipfile
import json
from pathlib import Path
from html import escape

import nh3
from PIL import Image, ImageOps

from src.app_paths import data_dir

ASSET_RE = re.compile(r'^/api/article/assets/(\d+)/([a-f0-9]{64}\.png)$')
COLORS = {'blue': '#00699d', 'pink': '#c44383', 'orange': '#ff7f24'}
TYPE_COLORS = {'互动抽奖': '#00699d', '转发抽奖': '#089043', '预约抽奖': '#aa7d00'}
EMOTES = {e['local']: e for e in json.loads((Path(__file__).parent / 'article_emotes.json').read_text('utf-8'))}
EMOTE_DIR = Path(__file__).parent / 'static' / 'article-emotes'


def asset_path(uid: str, name: str):
    if not uid.isdigit() or not re.fullmatch(r'[a-f0-9]{64}\.png', name):
        raise ValueError('图片标识无效')
    return data_dir() / 'article_assets' / uid / name


def clean_html(value: str, uid: str) -> str:
    def attr(tag, name, text):
        if tag == 'img' and name == 'src':
            if text in EMOTES:
                return text
            match = ASSET_RE.fullmatch(text)
            return text if match and match[1] == uid and asset_path(uid, match[2]).is_file() else None
        return text
    cleaned = nh3.clean(value, tags={'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'span', 'h2', 'h3',
                                  'blockquote', 'ol', 'ul', 'li', 'a', 'img', 'hr'},
                     attributes={'*': {'style'}, 'a': {'href'}, 'img': {'src', 'alt', 'width', 'height'}},
                     attribute_filter=attr, url_schemes={'http', 'https'},
                     filter_style_properties={'color', 'background-color', 'font-size', 'font-weight',
                                              'text-align', 'text-decoration'})
    return re.sub(r'<img\b[^>]*>', lambda m: m[0] if re.search(r'\bsrc="', m[0]) else '', cleaned)


def text_html(value: str) -> str:
    return ''.join(f'<p>{escape(line) if line else "<br>"}</p>' for line in value.splitlines())


def save_image(uid: str, encoded: str) -> str:
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 8 * 1024 * 1024:
            raise ValueError('图片不能超过 8 MB')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('请选择 PNG、JPG 或 WebP 图片')
                if source.width * source.height > 25000000:
                    raise ValueError('图片像素过大，请缩小后重试')
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image.thumbnail((2400, 2400))
                output = io.BytesIO()
                image.save(output, format='PNG', optimize=True)
                content = output.getvalue()
        if len(content) > 12 * 1024 * 1024:
            raise ValueError('图片处理后仍过大，请缩小后重试')
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('图片无法读取，请选择有效的 PNG、JPG 或 WebP 文件') from exc
    name = hashlib.sha256(content).hexdigest() + '.png'
    path = asset_path(uid, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix('.tmp')
        temp.write_bytes(content)
        temp.replace(path)
    return f'/api/article/assets/{uid}/{name}'


def image_html(src: str, alt: str) -> str:
    return f'<p style="text-align:center"><img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}"></p>' if src else ''


def export_bundle(uid: str, title: str, html: str, body: str) -> bytes:
    html = clean_html(html or text_html(body), uid)
    refs = list(dict.fromkeys(re.findall(r'src="(/api/article/assets/\d+/[a-f0-9]{64}\.png)"', html)))
    if len(refs) > 100:
        raise ValueError('每篇最多导出 100 张图片')
    if sum(asset_path(uid, ASSET_RE.fullmatch(ref)[2]).stat().st_size for ref in refs) > 64 * 1024 * 1024:
        raise ValueError('图片总大小不能超过 64 MB，请压缩图片或减少数量')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for ref in dict.fromkeys(re.findall(r'src="(/api/article/emotes/[a-f0-9]{40}\.webp)"', html)):
            name = ref.rsplit('/', 1)[1]
            z.write(EMOTE_DIR / name, 'emotes/' + name)
            html = html.replace(ref, 'emotes/' + name)
        for i, ref in enumerate(refs, 1):
            match = ASSET_RE.fullmatch(ref)
            filename = f'images/{i:02d}.png'
            z.writestr(filename, asset_path(uid, match[2]).read_bytes())
            html = html.replace(ref, filename)
        page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{escape(title)}</title><style>body{{max-width:760px;margin:40px auto;padding:0 24px;font-family:"PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif;font-size:17px;color:#2f3238;line-height:1.7}}'
                'img{max-width:100%;height:auto}img[src^="emotes/"]{width:24px;height:24px;vertical-align:text-bottom}p,ul,ol,blockquote{margin:24px 0 0}h2{font-size:18px;font-weight:500;margin:36px 0 0}h3{font-size:17px;font-weight:500;margin:36px 0 0}p{overflow-wrap:anywhere}a{color:#00699d;text-decoration:none}blockquote{border-left:3px solid #dde3ec;padding-left:16px}hr{border:0;border-top:1px solid #c9ccd0;margin:24px 0}</style>'
                f'<body><h1>{escape(title)}</h1>{html}</body></html>')
        z.writestr('article.html', page)
        z.writestr('article.txt', title + '\n\n' + body)
        z.writestr('使用说明.txt', '打开 article.html 预览完整图文。复制正文到 B站编辑器后检查颜色、标题与链接；图片请从 images 文件夹按顺序上传。平台可能调整粘贴格式，请以发布前预览为准。')
    return archive.getvalue()
