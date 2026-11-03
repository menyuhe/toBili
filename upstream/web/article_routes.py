"""Local editorial drafts assembled from selected lottery facts."""
from __future__ import annotations

import json
import threading
from html import escape
from typing import Literal
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from src.activity_store import load_activities
from src.app_paths import data_dir
from src.lottery_classifier import PARTICIPATABLE_TYPES
from web.activity_service import _normalize_activity
from web.pool_routes import account_uid as _legacy_account_uid

def account_uid():
    from src.accounts import read_state
    return read_state("__global__", "editorial_uid") or _legacy_account_uid()
from web.article_rich import COLORS, TYPE_COLORS, ASSET_RE, EMOTES, EMOTE_DIR, asset_path, clean_html, export_bundle, image_html, save_image, text_html

router = APIRouter(prefix='/api/article')
_lock = threading.RLock()
DEFAULT_TEMPLATE = {
    'title': '{date} 抽奖整理｜精选 {count} 个活动',
    'intro': '本期整理了 {count} 个抽奖活动，按参与类型分组。挑感兴趣的参与，具体规则和开奖安排以原动态为准。',
    'outro': '祝大家好运！有新增活动或规则变动，会继续更新整理。',
}


class Template(BaseModel):
    title: str = Field(default=DEFAULT_TEMPLATE['title'], min_length=1, max_length=200)
    intro: str = Field(default=DEFAULT_TEMPLATE['intro'], max_length=3000)
    outro: str = Field(default=DEFAULT_TEMPLATE['outro'], max_length=3000)
    intro_html: str = Field(default='', max_length=50000)
    outro_html: str = Field(default='', max_length=50000)
    theme: Literal['blue', 'pink', 'orange'] = 'blue'
    layout: Literal['compact', 'detailed'] = 'compact'
    group_by_date: bool = True
    hero_image: str = Field(default='', max_length=160)
    separator_image: str = Field(default='', max_length=160)

    @field_validator('hero_image', 'separator_image')
    @classmethod
    def valid_image(cls, value):
        if value and not ASSET_RE.fullmatch(value):
            raise ValueError('请选择本机素材库中的图片')
        return value


class DrawRange(BaseModel):
    draw_start: str = ''
    draw_end: str = ''

    @field_validator('draw_start', 'draw_end')
    @classmethod
    def valid_date(cls, value):
        if value and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
            raise ValueError('开奖日期格式无效')
        return value

    @model_validator(mode='after')
    def valid_range(self):
        if self.draw_start and self.draw_end and self.draw_start > self.draw_end:
            raise ValueError('开始日期不能晚于结束日期')
        return self


class Generate(DrawRange):
    uid: str = ''
    ids: list[str] = Field(min_length=1, max_length=100)
    template: Template = Field(default_factory=Template)

    @field_validator('ids')
    @classmethod
    def ids_valid(cls, ids):
        if len(set(ids)) != len(ids) or any(not v.isdigit() or len(v) > 25 for v in ids):
            raise ValueError('活动 ID 无效或重复')
        return ids


class Draft(DrawRange):
    uid: str = ''
    ids: list[str] = Field(default_factory=list, max_length=100)
    template: Template = Field(default_factory=Template)
    title: str = Field(default='', max_length=300)
    body: str = Field(default='', max_length=150000)
    body_html: str = Field(default='', max_length=600000)


def article_rows():
    from src.account_context import account_scope
    with account_scope(""):
        return {str(r["dynamic_id"]): r for r in load_activities()
                if r.get("lottery_type") in PARTICIPATABLE_TYPES}


@router.get('/candidates')
def candidates():
    account_uid()
    items = [_normalize_activity(row, {}, None) for row in article_rows().values()]
    items.sort(key=lambda r: (r['lottery_timestamp'] is None, r['lottery_timestamp'] or 0, r['dynamic_id']))
    return {'items': items}


def compose(body: Generate, rows: dict) -> dict:
    missing = [did for did in body.ids if did not in rows]
    if missing:
        raise ValueError(f'{len(missing)} 个活动已移出或不可用，请重新选择；原草稿已保留')
    now = datetime.now(timezone(timedelta(hours=8)))
    dates = []
    for did in body.ids:
        stamp = _normalize_activity(rows[did], {}, None)['lottery_timestamp']
        day = datetime.fromtimestamp(stamp, now.tzinfo).date().isoformat() if stamp else ''
        if (body.draw_start or body.draw_end) and (not day or
                (body.draw_start and day < body.draw_start) or (body.draw_end and day > body.draw_end)):
            raise ValueError('已选活动包含开奖区间外或时间待定的活动，请移除这些活动或调整区间；原草稿已保留')
        if day:
            dates.append(day)
    start = body.draw_start or (min(dates) if dates else '')
    end = body.draw_end or (max(dates) if dates else '')
    if body.draw_start and not body.draw_end:
        draw_range = f'{body.draw_start} 起'
    elif body.draw_end and not body.draw_start:
        draw_range = f'截至 {body.draw_end}'
    else:
        draw_range = start if start == end else f'{start} 至 {end}'
        draw_range = draw_range or '开奖时间待定'
        if dates and len(dates) < len(body.ids):
            draw_range += f'（另有 {len(body.ids) - len(dates)} 个时间待定）'
    def fill(value):
        return (value.replace('{date}', now.strftime('%Y年%m月%d日')).replace('{count}', str(len(body.ids)))
                .replace('{draw_range}', draw_range).replace('{开奖区间}', draw_range))
    groups = {}
    warnings = []
    for did in body.ids:
        raw = rows[did]
        item = _normalize_activity(raw, {}, None)
        kind = item['lottery_type']
        groups.setdefault(kind, []).append((raw, item))
    paragraphs = [fill(body.template.intro)]
    template = body.template
    accent = COLORS[template.theme]
    rich = [image_html(template.hero_image, '文章头图'),
            fill(template.intro_html) if template.intro_html else text_html(fill(template.intro)),
            f'<p><strong style="color:{accent}">本期 {len(body.ids)} 个活动 · 开奖区间：{escape(draw_range)}</strong></p>']
    for kind in ('互动抽奖', '转发抽奖', '预约抽奖'):
        selected = groups.get(kind, [])
        if not selected:
            continue
        paragraphs.append(f'【{kind} · {len(selected)} 个】')
        rich.append(image_html(template.separator_image, '章节分隔图') if template.separator_image else
                    f'<p style="text-align:center;color:{accent}">── ✦ ──</p>')
        rich.append(f'<h2 style="text-align:center;color:{TYPE_COLORS[kind]}">{kind} · {len(selected)} 个</h2>')
        if template.group_by_date:
            selected = sorted(selected, key=lambda pair: pair[1]['lottery_timestamp'] or float('inf'))
        last_day = None
        for index, (raw, item) in enumerate(selected, 1):
            did = item['dynamic_id']
            prizes = '；'.join(f"{p.get('description') or '奖品待核对'}" +
                              (f" × {p['winner_count']} 名" if p.get('winner_count') else '')
                              for p in raw.get('prizes', []) if isinstance(p, dict))
            lines = [f"{index}. {item['activity_title']}", f"奖品：{prizes or '详见原动态'}",
                     f"开奖时间：{item['lottery_time']}"]
            if item['activity_status'] == '已结束':
                warnings.append(f"{item['activity_title']}：已结束")
                lines.append('活动状态：已结束，请确认是否仍要收录')
            if not item['lottery_timestamp']:
                warnings.append(f"{item['activity_title']}：开奖时间未确定")
            # Do not infer participation requirements from the category alone.
            lines.extend(['参与方式：请查看原动态中的完整要求', f'原动态：https://t.bilibili.com/{did}'])
            paragraphs.append('\n'.join(lines))
            day = datetime.fromtimestamp(item['lottery_timestamp'], now.tzinfo).strftime('%Y年%m月%d日') if item['lottery_timestamp'] else '开奖日期待定'
            if template.group_by_date and day != last_day:
                rich.append(f'<h3 style="color:{accent}">{day}</h3>')
                last_day = day
            rich.append(f'<p><strong>{index}. {escape(item["activity_title"])}</strong></p>'
                        f'<p><span style="color:#61666d">开奖：{escape(item["lottery_time"])}</span> · '
                        f'<a href="https://t.bilibili.com/{did}">查看原动态</a></p>')
            if template.layout == 'detailed':
                rich.append(text_html('\n'.join(lines[1:-1])))
            elif item['activity_status'] == '已结束':
                rich.append('<p style="color:#c44343">活动已结束，发布前请核对</p>')
    paragraphs.append(fill(body.template.outro))
    rich.append(fill(template.outro_html) if template.outro_html else text_html(fill(template.outro)))
    return {'title': fill(body.template.title), 'body': '\n\n'.join(p for p in paragraphs if p.strip()),
            'body_html': clean_html(''.join(rich), body.uid),
            'warnings': warnings, 'count': len(body.ids),
            'groups': {k: len(v) for k, v in groups.items()}}


@router.post('/generate')
def generate(body: Generate):
    uid = account_uid()
    if body.uid != uid:
        raise HTTPException(409, '账号已切换，请更新专栏页面后重试')
    try:
        return compose(body, article_rows())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def draft_path(uid):
    return data_dir() / f'article_draft_{uid}.json'


@router.get('/draft')
def get_draft():
    uid = account_uid()
    with _lock:
        path = draft_path(uid)
        draft = Draft.model_validate_json(path.read_text(encoding='utf-8')) if path.exists() else Draft()
        sanitize_draft(draft, uid)
        return {**draft.model_dump(), 'uid': uid}


def sanitize_draft(draft: Draft, uid: str):
    draft.body_html = clean_html(draft.body_html, uid)
    draft.template.intro_html = clean_html(draft.template.intro_html, uid)
    draft.template.outro_html = clean_html(draft.template.outro_html, uid)
    for key in ('hero_image', 'separator_image'):
        value = getattr(draft.template, key)
        if value:
            match = ASSET_RE.fullmatch(value)
            if not match or match[1] != uid or not asset_path(uid, match[2]).is_file():
                setattr(draft.template, key, '')


@router.put('/draft')
def save_draft(body: Draft):
    uid = account_uid()
    if body.uid != uid:
        raise HTTPException(409, '账号已切换，草稿尚未保存，请切回原账号保存')
    sanitize_draft(body, uid)
    with _lock:
        path = draft_path(uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(body.model_dump_json(), encoding='utf-8')
        temp.replace(path)
    return {'ok': True}


class ImageUpload(BaseModel):
    uid: str
    data: str = Field(max_length=12 * 1024 * 1024)


@router.get('/emotes/{name}')
def get_emote(name: str):
    if '/api/article/emotes/' + name not in EMOTES:
        raise HTTPException(404, '表情不存在')
    return FileResponse(EMOTE_DIR / name, media_type='image/webp', headers={'Cache-Control':'private, max-age=86400'})


@router.post('/images')
def upload_image(body: ImageUpload):
    uid = account_uid()
    if body.uid != uid:
        raise HTTPException(409, '账号已切换，请重新载入专栏页面')
    try:
        with _lock:
            return {'src': save_image(uid, body.data)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/assets/{uid}/{name}')
def get_image(uid: str, name: str):
    from src.bilibili_auth import get_login_uid
    if account_uid() != uid:
        raise HTTPException(403, '请切回图片所属账号')
    try:
        path = asset_path(uid, name)
    except ValueError as exc:
        raise HTTPException(404, '图片不存在') from exc
    if not path.is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/export')
def export_article(body: Draft):
    uid = account_uid()
    if body.uid != uid:
        raise HTTPException(409, '账号已切换，请重新载入专栏页面')
    try:
        return Response(export_bundle(uid, body.title, body.body_html, body.body), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="toBili-article.zip"'})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
