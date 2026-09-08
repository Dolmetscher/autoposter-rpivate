"""Instagram (Business): фото, карусели, Reels и истории через Graph API.
Требует публично доступных URL медиа (передаётся через media_base_url)."""
from __future__ import annotations

import asyncio

import httpx

from ..config import settings
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramAdapter(SocialAdapter):
    source_id = 6
    name = "Instagram"
    capabilities = ("post", "photo", "video", "reel", "story")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "access_token", "label": "Access token Meta (page/long-lived)", "required": True},
            {"name": "ig_user_id", "label": "Instagram Business Account ID", "required": True},
        ]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("Instagram: не указан access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{GRAPH}/me/accounts",
                                 params={"fields": "name,instagram_business_account",
                                         "access_token": token})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"Instagram: {data['error'].get('message')}")
        pages = []
        for p in data.get("data", []):
            ig = p.get("instagram_business_account")
            if ig and ig.get("id"):
                pages.append(PageInfo(ig["id"], p.get("name") or ig["id"], "", kind="page"))
        if not pages:
            raise AdapterError("Instagram: не найдено ни одного Business-аккаунта "
                               "(привяжите Instagram к странице Facebook)")
        return pages

    async def _create_container(self, client: httpx.AsyncClient, token: str, ig_id: str,
                                params: dict) -> str:
        params = {**params, "access_token": token}
        r = await client.post(f"{GRAPH}/{ig_id}/media", data=params)
        data = r.json()
        if "error" in data:
            raise AdapterError(f"Instagram media: {data['error'].get('message')}")
        return data["id"]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        token, ig_id = creds.get("access_token"), creds.get("ig_user_id")
        if not (token and ig_id):
            raise AdapterError("Instagram: аккаунт не настроен")
        if not settings.public_base_url.startswith("http"):
            raise AdapterError("Instagram: требуется публичный public_base_url")
        st = content.settings or {}
        photos = [m for m in content.media if m.type == "photo"]
        videos = [m for m in content.media if m.type == "video"]

        async with httpx.AsyncClient(timeout=120) as client:
            creation_id = None
            if st.get("publish_as_story"):
                if not photos and not videos:
                    raise AdapterError("Instagram истории: нужен файл")
                params = {"media_type": "STORIES"}
                if videos:
                    params.update({"media_type": "STORIES", "video_url":
                                   content.public_url(videos[0].file_path)})
                else:
                    params["image_url"] = content.public_url(photos[0].file_path)
                creation_id = await self._create_container(client, token, ig_id, params)
            elif len(photos) > 1 and not videos:
                children = []
                for p in photos[:10]:
                    children.append(await self._create_container(
                        client, token, ig_id,
                        {"image_url": content.public_url(p.file_path), "is_carousel_item": "true"}))
                creation_id = await self._create_container(client, token, ig_id, {
                    "media_type": "CAROUSEL", "children": ",".join(children),
                    "caption": content.text or ""})
            elif videos:
                media_type = "REELS" if st.get("publish_as_reel") else "VIDEO"
                params = {"media_type": media_type, "video_url": content.public_url(videos[0].file_path),
                          "caption": content.text or ""}
                creation_id = await self._create_container(client, token, ig_id, params)
                # контейнер видео нужно дождаться
                for _ in range(24):
                    r = await client.get(f"{GRAPH}/{creation_id}",
                                         params={"fields": "status_code",
                                                 "access_token": token})
                    code = r.json().get("status_code")
                    if code == "FINISHED":
                        break
                    if code == "ERROR":
                        raise AdapterError(f"Instagram: ошибка обработки видео")
                    await asyncio.sleep(5)
            elif photos:
                creation_id = await self._create_container(client, token, ig_id, {
                    "image_url": content.public_url(photos[0].file_path),
                    "caption": content.text or ""})
            else:
                if not content.text:
                    raise AdapterError("Instagram: пустой пост")
                # только текст невозможен — Instagram требует медиа
                raise AdapterError("Instagram: публикация требует фото или видео")

            r = await client.post(f"{GRAPH}/{ig_id}/media_publish",
                                  data={"creation_id": creation_id, "access_token": token})
            data = r.json()
            if "error" in data:
                raise AdapterError(f"Instagram media_publish: {data['error'].get('message')}")
            media_id = data["id"]
            permalink = ""
            r = await client.get(f"{GRAPH}/{media_id}", params={"fields": "permalink",
                                                                "access_token": token})
            permalink = r.json().get("permalink", "")
        return PublishOutcome(True, external_id=media_id, external_url=permalink)
