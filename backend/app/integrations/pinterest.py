"""Пинтерест: пины на доски через API v5. Доски выступают «страницами».
Пин требует медиа — фото или видео передаются публичной ссылкой."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

API = "https://api.pinterest.com/v5"


class PinterestAdapter(SocialAdapter):
    source_id = 8
    name = "Пинтерест"
    capabilities = ("post", "photo", "video")

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token", "label": "Access token Pinterest (pins:write)",
                 "required": True}]

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("Pinterest: не указан access_token")
        pages: list[PageInfo] = []
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/boards",
                                 params={"page_size": 50}, headers=self._headers(token))
            if r.status_code == 401:
                raise AdapterError("Pinterest: токен недействителен")
            r.raise_for_status()
            for b in r.json().get("items", []):
                pages.append(PageInfo(str(b["id"]), b.get("name", ""), kind="board"))
            if not pages:
                raise AdapterError("Pinterest: не найдено ни одной доски — создайте доску в Pinterest")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        photos = [m for m in content.media if m.type == "photo"]
        videos = [m for m in content.media if m.type == "video"]
        if not photos and not videos:
            raise AdapterError("Pinterest: пин требует фото или видео")
        if videos:
            # Для видео Pinterest обязательно требует обложку (cover_image_url).
            cover = videos[0].file_thumbnail_path or videos[0].file_path
            media_source = {"source_type": "video_url",
                            "cover_image_url": content.public_url(cover),
                            "media_url": content.public_url(videos[0].file_path)}
        else:
            media_source = {"source_type": "image_url",
                            "url": content.public_url(photos[0].file_path)}
        text = content.text or ""
        title = text.splitlines()[0][:100] if text else "Пин"
        body = {
            "board_id": page.social_page_id,
            "title": title,
            "description": text[:500],
            "media_source": media_source,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{API}/pins", headers=self._headers(token), json=body)
        if r.status_code >= 300:
            raise AdapterError(f"Pinterest: {r.status_code} {r.text[:200]}")
        pin = r.json()
        pin_id = str(pin.get("id", ""))
        return PublishOutcome(True, external_id=pin_id,
                              external_url=f"https://www.pinterest.com/pin/{pin_id}/")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{API}/pins/{external_id}",
                                    headers=self._headers(token))
        return r.status_code in (200, 204, 404)
