import base64
import io
import zipfile

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from web import article_routes as article
from web.article_rich import clean_html, save_image, export_bundle, asset_path


def png_data():
    output = io.BytesIO()
    Image.new('RGB', (80, 20), '#c44383').save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode()


def test_html_colors_and_account_scoped_images(isolated_home):
    src = save_image('123', png_data())
    html = clean_html(f'<script>bad()</script><p style="color:#c44383;position:fixed" onclick="bad()">正文</p>'
                      f'<img src="{src}"><img src="https://example.com/x.png">'
                      '<a href="javascript:bad()">链接</a>', '123')
    assert 'color:' in html and src in html
    assert all(word not in html for word in ['script', 'onclick', 'position', 'example.com', 'javascript'])
    assert html.count('<img') == 1
    assert '<img' not in clean_html(f'<img src="{src}">', '456')
    with pytest.raises(ValueError):
        save_image('123', base64.b64encode(b'not an image').decode())
    with pytest.raises(ValueError):
        asset_path('123', '../secret.png')


def test_portable_export_deduplicates_images(isolated_home):
    src = save_image('123', png_data())
    result = export_bundle('123', '标题 <test>', f'<h2 style="color:#c44383">分类</h2><img src="{src}"><img src="{src}">', '正文')
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert len([n for n in archive.namelist() if n.endswith('.png')]) == 1
        page = archive.read('article.html').decode()
        assert page.count('src="images/01.png"') == 2
        assert '/api/' not in page and '&lt;test&gt;' in page and 'color:' in page
        assert Image.open(io.BytesIO(archive.read('images/01.png'))).size == (80, 20)


def test_rich_routes_roundtrip_and_legacy(isolated_home, monkeypatch):
    from web.app import app
    monkeypatch.setattr(article, 'account_uid', lambda: '123')
    monkeypatch.setattr('src.bilibili_auth.get_login_uid', lambda: '123')
    client = TestClient(app)
    assert client.post('/api/article/images', json={'uid':'456', 'data':png_data()}).status_code == 409
    src = client.post('/api/article/images', json={'uid':'123', 'data':png_data()}).json()['src']
    assert client.get(src).status_code == 200
    assert client.get(src.replace('/123/', '/456/')).status_code == 403
    draft = {'uid':'123', 'title':'图文草稿', 'body':'正文', 'body_html':f'<p style="color:#c44383"><strong>正文</strong></p><img src="{src}">',
             'template':{'intro_html':'<p><strong>{count} 个</strong></p>', 'hero_image':src, 'theme':'pink'}}
    assert client.put('/api/article/draft', json=draft).status_code == 200
    saved = client.get('/api/article/draft').json()
    assert '<strong>正文</strong>' in saved['body_html'] and src in saved['body_html']
    assert saved['template']['hero_image'] == src
    assert client.post('/api/article/export', json=saved).status_code == 200
    assert client.put('/api/article/draft', json={'uid':'123', 'body':'旧草稿'}).status_code == 200
    assert client.get('/api/article/draft').json()['body'] == '旧草稿'


def test_rich_compose_escapes_facts_and_expands_template():
    from datetime import datetime
    rows = {'100': dict(dynamic_id='100', lottery_type='互动抽奖', lottery_time=int(datetime.fromisoformat('2026-10-24T12:00:00+08:00').timestamp()),
                        prizes=[{'description':'<script>奖品</script>', 'winner_count':1}])}
    result = article.compose(article.Generate(uid='123', ids=['100'], template=article.Template(intro_html='<p style="color:#c44383">{count} 个 · {开奖区间}</p>')), rows)
    assert '<script>' not in result['body_html']
    assert '{count}' not in result['body_html'] and '2026年10月24日' in result['body_html']
    assert 'https://t.bilibili.com/100' in result['body_html']


def test_emotes_are_allowlisted_and_exported_offline(isolated_home):
    from web.article_rich import EMOTES
    ref = next(iter(EMOTES))
    html = clean_html(f'<p>表情<img src="{ref}" width="24" height="24"></p><img src="/api/article/emotes/unknown.webp">', '123')
    assert ref in html and 'unknown' not in html
    with zipfile.ZipFile(io.BytesIO(export_bundle('123', '表情', html, '表情'))) as z:
        page = z.read('article.html').decode()
        assert '/api/' not in page and 'emotes/' in page and 'width="24"' in page
        assert len([n for n in z.namelist() if n.endswith('.webp')]) == 1


def test_article_links_survive_text_html_and_portable_export(isolated_home):
    did = '1265663298777382912'
    rows = {did: dict(dynamic_id=did, lottery_type='转发抽奖',
                      source_url='https://www.bilibili.com/opus/'+did,
                      prizes=[{'description':'大会员'}])}
    result = article.compose(article.Generate(uid='123', ids=[did]), rows)
    expected = 'https://t.bilibili.com/'+did
    assert expected in result['body']
    assert 'href="'+expected+'"' in result['body_html']
    with zipfile.ZipFile(io.BytesIO(export_bundle('123',result['title'],result['body_html'],result['body']))) as z:
        assert expected in z.read('article.txt').decode()
        assert 'href="'+expected+'"' in z.read('article.html').decode()
    assert '/opus/' in rows[did]['source_url']  # Existing facts/drafts are not rewritten.
