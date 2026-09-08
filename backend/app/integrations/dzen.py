"""Дзен — ЭКСПЕРИМЕНТ: у платформы нет публичного API публикации.
Адаптер подключается по сессионным cookie и использует внутренние эндпоинты
редактора; ломается при их изменении. По умолчанию публикация возвращит
понятную ошибку, пока не будут проверены актуальные эндпоинты."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter


class DzenAdapter(SocialAdapter):
    source_id = 20
    name = "Дзен"
    capabilities = ("post",)

    def connect_requires(self) -> list[dict]:
        return [{"name": "session_cookie", "label": "Cookie сессии дзен (экспериментально)",
                 "required": False}]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        cookie = account.credentials.get("session_cookie")
        if not cookie:
            raise AdapterError("Дзен: экспериментальная интеграция — укажите session_cookie")
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
        except ValueError:
            raise AdapterError("Дзен: неожиданный ответ (экспериментальная интеграция)")
        if not pages:
            raise AdapterError("Дзен: каналы не найдены")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        raise AdapterError(
            "Дзен: публикация через API недоступна — у платформы нет публичного API. "
            "Экспериментальная поддержка появится после стабилизации внутренних эндпоинтов.")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError("Дзен: удаление недоступно (экспериментальная интеграция)")
