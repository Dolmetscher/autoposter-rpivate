"""Дзен — публикация через RSS-фид проекта («трансляция» Дзена).

У платформы нет публичного API публикации, поэтому прямая загрузка
невозможна. Рабочая схема: проект с Дзен-страницей отдаёт публичный
RSS-фид (/api/public/rss-out/...), а канал Дзена сам забирает материалы
из фида — официальная механика трансляции.

Подключение аккаунта по сессионной cookie нужно только чтобы показать
список каналов; сам фид от cookie не зависит. Публикация цели Дзена
считается успешной, когда пост передан в фид; Дзен подтягивает его по
своему расписанию (обычно в пределах часа).
"""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter


class DzenAdapter(SocialAdapter):
    source_id = 20
    name = "Дзен"
    capabilities = ("post", "photo")
    # контент уходит через RSS-фид проекта (publish.py подставит rss_feed_url)
    rss_out = True
    note = ("публикация — через RSS-фид проекта (официальная «трансляция» Дзена); "
            "прямого API публикации у платформы нет")

    def connect_requires(self) -> list[dict]:
        return [{"name": "session_cookie", "label": "Cookie сессии дзен (для списка каналов)",
                 "required": False}]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        cookie = account.credentials.get("session_cookie")
        if not cookie:
            # Без cookie фид всё равно работает: отдаём одну синтетическую
            # страницу — её достаточно, чтобы добавить Дзен в проект.
            return [PageInfo("dzen", "Канал Дзен (RSS)", "", kind="channel")]
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get("https://dzen.ru/api/v3/channel/list?limit=20",
                                 headers={"Cookie": cookie})
        if r.status_code != 200:
            raise AdapterError("Дзен: сессия недействительна (экспериментальная интеграция)")
        pages = []
        try:
            data = r.json()
            for ch in (data.get("channels") or []):
                pages.append(PageInfo(str(ch.get("id", "")), ch.get("title", "Канал Дзен"),
                                      ch.get("image", "") or "", kind="channel"))
        except ValueError as e:
            raise AdapterError("Дзен: неожиданный ответ (экспериментальная интеграция)") from e
        if not pages:
            raise AdapterError("Дзен: каналы не найдены")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        feed_url = (content.settings or {}).get("rss_feed_url", "")
        if not feed_url:
            raise AdapterError(
                "Дзен: публикация идёт через RSS-фид проекта — добавьте Дзен-страницу "
                "в проект и укажите адрес фида в настройках трансляции канала Дзена")
        # Пост передан в фид — для Дзена это и есть публикация:
        # канал заберёт материал из трансляции сам.
        return PublishOutcome(ok=True, external_id=f"rss-{page.social_page_id}",
                              external_url=feed_url)

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError("Дзен: удаление из фида недоступно — уберите пост из проекта "
                           "или отключите трансляцию в Дзене")
