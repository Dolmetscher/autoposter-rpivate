"""TikTok: Content Posting API (видео и фото, PULL_FROM_URL).
Требует одобренного developer-приложения и публичных URL медиа."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

API = "https://open.tiktokapis.com/v2"


class TikTokAdapter(SocialAdapter):
    source_id = 14
    name = "TikTok"
    capabilities = ("video", "photo")

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token", "label": "Access token TikTok (user.access_token)",
                 "required": True}]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("TikTok: не указан access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/user/info/",
                                 params={"fields": "open_id,display_name,avatar_url"},
                                 headers={"Authorization": f"Bearer {token}"})
        data = r.json().get("data") or {}
        user = data.get("user") or {}
        if not user.get("open_id"):
            raise AdapterError("TikTok: не удалось получить профиль")
        return [PageInfo(user["open_id"], user.get("display_name") or "TikTok",
                         user.get("avatar_url", ""), kind="user")]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if not token:
            raise AdapterError("TikTok: не указан access_token")
        st = content.settings or {}
        privacy = st.get("tiktok_privacy", "PUBLIC_TO_EVERYONE")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

        async with httpx.AsyncClient(timeout=120) as client:
            videos = [m for m in content.media if m.type == "video"]
            photos = [m for m in content.media if m.type == "photo"]
            if videos:
                body = {
                    "post_info": {
                        "title": (content.text or "")[:2200],
                        "privacy_level": privacy,
                        "disable_duet": bool(st.get("disable_duet")),
                        "disable_comment": bool(st.get("disable_comments")),
                        "disable_stitch": bool(st.get("disable_stitch")),
                    },
                    "source_info": {"source": "PULL_FROM_URL",
                                    "video_url": content.public_url(videos[0].file_path)},
                }
                r = await client.post(f"{API}/post/publish/video/init/",
                                      headers=headers, json=body)
            elif photos:
                body = {
                    "post_info": {
                        "title": (content.text or "")[:2200],
                        "privacy_level": privacy,
                        "disable_comment": bool(st.get("disable_comments")),
                    },
                    "post_mode": "DIRECT_POST",
                    "media_type": "PHOTO",
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "photo_cover_from_url": {
                            "url": content.public_url(photos[0].file_path),
                            "photo_cover_index": 0},
                        "photo_images": [content.public_url(p.file_path) for p in photos[:35]],
                    },
                }
                r = await client.post(f"{API}/post/publish/content/init/",
                                      headers=headers, json=body)
            else:
                raise AdapterError("TikTok: нужны фото или видео")
        data = r.json()
        err = (data.get("error") or {})
        if err.get("code") and err["code"] != "ok":
            raise AdapterError(f"TikTok: {err.get('message', 'ошибка публикации')}")
        publish_id = (data.get("data") or {}).get("publish_id", "")
        return PublishOutcome(True, external_id=publish_id,
                              external_url=f"https://www.tiktok.com/@{page.social_page_id}"
                                           if not publish_id else "")
