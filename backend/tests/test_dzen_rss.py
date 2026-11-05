"""Дзен через RSS-фид проекта: токен фида, публикация в фид, выдача XML."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import SessionFactory
from app.models import SocialAccount, SocialPage, User

DZEN_SOURCE_ID = 20
MOCK_SOURCE_ID = 99


@pytest_asyncio.fixture
async def auth_headers(client):
    await client.post("/api/web/auth/register",
                      json={"email": "user@test.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "user@test.io", "password": "secret123"})
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _make_dzen_page(email: str = "user@test.io") -> int:
    """Дзен-аккаунт и страница напрямую в БД (без обращения к dzen.ru)."""
    async with SessionFactory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        acc = SocialAccount(user_id=user.id, source_id=DZEN_SOURCE_ID, credentials={})
        s.add(acc)
        await s.flush()
        page = SocialPage(user_id=user.id, account_id=acc.id,
                          source_id=DZEN_SOURCE_ID, social_page_id="dzen",
                          social_page_name="Канал Дзен (RSS)")
        s.add(page)
        await s.commit()
        return page.id


@pytest.mark.asyncio
async def test_project_gets_rss_url(client, auth_headers):
    """Каждому проекту выдаётся rss_url (токен фида); показ блока решает фронтенд."""
    page_id = await _make_dzen_page()
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Дзен-проект", "settings": {},
                                "page_ids": [page_id]})
    assert r.status_code == 201, r.text
    proj = r.json()
    assert proj["rss_url"], "rss_url должен быть у проекта"
    assert f"/api/public/rss-out/{proj['id']}/" in proj["rss_url"]
    assert proj["rss_url"].endswith(".xml")

    # токен уникален и стабилен между запросами
    r2 = await client.get("/api/web/projects", headers=auth_headers)
    listed = [p for p in r2.json()["list"] if p["id"] == proj["id"]][0]
    assert listed["rss_url"] == proj["rss_url"]


@pytest.mark.asyncio
async def test_rss_feed_token_and_empty_feed(client, auth_headers):
    """Чужой токен → 404; правильный → валидный RSS даже без постов."""
    page_id = await _make_dzen_page()
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Дзен-проект", "settings": {},
                                "page_ids": [page_id]})
    proj = r.json()
    feed_url = proj["rss_url"].replace("http://testserver", "")

    wrong = await client.get(f"/api/public/rss-out/{proj['id']}/wrongtoken.xml")
    assert wrong.status_code == 404

    ok = await client.get(feed_url)
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"].startswith("application/rss+xml")
    assert '<rss version="2.0"' in ok.text
    assert "Дзен-проект" in ok.text
    assert "<item>" not in ok.text  # постов ещё нет


@pytest.mark.asyncio
async def test_post_publishes_into_dzen_feed(client, auth_headers):
    """Пост проекта уходит в Дзен через фид: цель published, item в XML."""
    from tests.conftest import upload_photo

    page_id = await _make_dzen_page()
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Дзен-проект", "settings": {},
                                "page_ids": [page_id]})
    proj = r.json()

    photo = await upload_photo(client, auth_headers)
    r2 = await client.post("/api/web/posts", headers=auth_headers, json={
        "publication_when_type": 1,  # сейчас — публикуется сразу
        "project_id": proj["id"],
        "page_ids": [],
        "texts": [{"text": "Первый пост в Дзен\nВторая строка", "source_id": 0}],
        "media_ids": [photo["id"]],
        "status": "scheduled",
    })
    assert r2.status_code == 200, r2.text
    dzen_targets = [t for t in r2.json()["targets"] if t["source_id"] == DZEN_SOURCE_ID]
    assert dzen_targets, "у поста должна быть Дзен-цель"
    assert dzen_targets[0]["status"] == "published", dzen_targets[0]
    assert dzen_targets[0]["external_url"].endswith(".xml")

    feed = await client.get(proj["rss_url"].replace("http://testserver", ""))
    assert feed.status_code == 200
    xml = feed.text
    assert "<item>" in xml
    assert "Первый пост в Дзен" in xml
    assert 'guid isPermaLink="false">autoposter-post-' in xml
    assert "<content:encoded>" in xml
    # Markdown-рендер фида: перенос строки → <br/> внутри абзаца
    assert "<p>Первый пост в Дзен<br/>Вторая строка</p>" in xml
    assert 'type="image/png"' in xml
    assert "/media/" in xml  # публичная ссылка на фото


@pytest.mark.asyncio
async def test_dzen_page_without_project_fails_with_hint(client, auth_headers):
    """Дзен-цель без проекта: публикация невозможна, ошибка объясняет про фид."""
    from app.integrations.base import AdapterError
    from app.integrations.dzen import DzenAdapter

    adapter = DzenAdapter()

    class FakePage:
        social_page_id = "123"

    class FakeContent:
        settings = {}
        text = "тест"

    with pytest.raises(AdapterError) as e:
        await adapter.publish(FakePage(), FakeContent())
    assert "RSS-фид" in str(e.value)
