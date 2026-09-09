"""Facebook: посты на страницы через Graph API (Page access token).
Фото/видео передаются публичной ссылкой (см. PUBLIC_BASE_URL / S3)."""
from __future__ import annotations

import httpx

from ..config import settings
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

GRAPH = "https://graph.facebook.com/v21.0"


class FacebookAdapter(SocialAdapter):
    source_id = 3
    name = "Facebook"
    capabilities = ("post", "photo", "video")

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token",
                 "label": "Page Access Token (developers.facebook.com)", "required": True}]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("FB: не указан access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{GRAPH}/me/accounts",
                                 params={"fields": "name,picture", "access_token": token})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"FB: {data['error'].get('message')}")
        pages = [PageInfo(p["id"], p.get("name", ""),
                          (p.get("picture", {}).get("data") or {}).get("url", ""),
                          kind="page")
                 for p in data.get("data", [])]
        if not pages:
            raise AdapterError("FB: не найдено страниц (нужен токен страницы или "
                               "user-токен с правом manage_pages)")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("FB: истории через API не поддерживаются в этой версии")
        pid = page.social_page_id
        photos = [m for m in content.media if m.type == "photo"]
        videos = [m for m in content.media if m.type == "video"]

        async with httpx.AsyncClient(timeout=300) as client:
            if videos:
                if len(videos) > 1:
                    raise AdapterError("FB: одно видео на пост")
                r = await client.post(f"{GRAPH}/{pid}/videos", data={
                    "file_url": content.public_url(videos[0].file_path),
                    "description": content.text or "",
                    "access_token": token})
            elif photos:
                if len(photos) > 1:
                    raise AdapterError("FB: не более одного фото на пост в этой версии")
                r = await client.post(f"{GRAPH}/{pid}/photos", data={
                    "url": content.public_url(photos[0].file_path),
                    "caption": (content.text or "")[:2200],
                    "access_token": token})
            else:
                if not content.text:
                    raise AdapterError("FB: пустой пост")
                r = await client.post(f"{GRAPH}/{pid}/feed", data={
                    "message": content.text,
                    "access_token": token})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"FB: {data['error'].get('message')}")
        ext = data.get("post_id") or data.get("id", "")
        return PublishOutcome(True, external_id=ext,
                              external_url=f"https://www.facebook.com/{ext}" if ext else "")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{GRAPH}/{external_id}",
                                    params={"access_token": token})
        return r.status_code in (200, 204)
