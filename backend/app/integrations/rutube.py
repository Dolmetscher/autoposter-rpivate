"""Рутуб — ЭКСПЕРИМЕНТ: публичного API загрузки нет, доступ выдаёт поддержка
Rutube индивидуально. Адаптер использует легаси-эндпоинты api.rutube.ru
(Authorization: Token): публикация видео по публичной ссылке. Может ломаться."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, SourceItem, read_media_bytes

API = "https://api.rutube.ru"


class RutubeAdapter(SocialAdapter):
    source_id = 15
    name = "Рутуб"
    capabilities = ("video",)

    def connect_requires(self) -> list[dict]:
        return [{"name": "token",
                 "label": "API-токен Рутуб (выдаётся поддержкой rutube.ru/feedback)",
                 "required": False}]

    def _headers(self, creds: dict, extra: dict | None = None) -> dict:
        token = creds.get("token") or (extra or {}).get("token")
        if not token:
            raise AdapterError("Рутуб: нужен API-токен (обратитесь в поддержку Rutube)")
        return {"Authorization": f"Token {token}"}

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        headers = self._headers(account.credentials, extra)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/api/person/", headers=headers)
        if r.status_code >= 300:
            raise AdapterError("Рутуб: токен недействителен или доступ не выдан "
                               "(экспериментальная интеграция)")
        me = r.json()
        return [PageInfo(str(me.get("id", "channel")), me.get("name", "Канал Рутуб"),
                         kind="channel")]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        videos = [m for m in content.media if m.type == "video"]
        if not videos:
            raise AdapterError("Рутуб: публикация требует видеофайл")
        if len(videos) > 1:
            raise AdapterError("Рутуб: одно видео на публикацию")
        text = content.text or ""
        title = (videos[0].title or text.splitlines()[0] if text else videos[0].name)[:100]

        # грузим видео в хранилище-URL (Рутуб скачивает файл сам)
        video_url = content.public_url(videos[0].file_path)
        headers = self._headers(page.account.credentials)
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{API}/api/video/", headers=headers, json={
                "url": video_url,
                "title": title,
                "description": text,
                "is_hidden": False,
            })
        if r.status_code >= 300:
            raise AdapterError(f"Рутуб: {r.status_code} {r.text[:200]} "
                               "(экспериментальная интеграция)")
        vid = r.json().get("id", "")
        return PublishOutcome(True, external_id=str(vid),
                              external_url=f"https://rutube.ru/video/{vid}/")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError("Рутуб: удаление через API недоступно "
                           "(экспериментальная интеграция)")

    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:  # noqa: ANN001
        raise AdapterError("Рутуб: источники кросспостинга не поддерживаются")
