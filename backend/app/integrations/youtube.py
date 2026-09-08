"""YouTube: загрузка видео/Shorts через Data API v3 (resumable upload),
каналы, удаление, источники кросспостинга (RSS загрузок канала)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from ..config import settings
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, SourceItem, read_media_bytes


class YouTubeAdapter(SocialAdapter):
    source_id = 17
    name = "YouTube"
    capabilities = ("video", "shorts")

    async def _token(self, creds: dict) -> str:
        if creds.get("access_token") and not creds.get("refresh_token"):
            return creds["access_token"]
        client_id = creds.get("client_id") or settings.google_client_id
        client_secret = creds.get("client_secret") or settings.google_client_secret
        refresh = creds.get("refresh_token")
        if not (client_id and client_secret and refresh):
            raise AdapterError("YouTube: нужны client_id, client_secret и refresh_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh, "grant_type": "refresh_token"})
        data = r.json()
        if "access_token" not in data:
            raise AdapterError(f"YouTube OAuth: {data.get('error_description') or data}")
        return data["access_token"]

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "refresh_token", "label": "Refresh token Google (scope youtube.upload)", "required": True},
            {"name": "client_id", "label": "Client ID (если не задан в настройках сервера)", "required": False},
            {"name": "client_secret", "label": "Client Secret", "required": False},
        ]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = await self._token(account.credentials)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://www.googleapis.com/youtube/v3/channels",
                                 params={"part": "snippet", "mine": "true"},
                                 headers={"Authorization": f"Bearer {token}"})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"YouTube channels.list: {data['error'].get('message')}")
        pages = []
        for ch in data.get("items", []):
            sn = ch["snippet"]
            pages.append(PageInfo(ch["id"], sn.get("title", ""),
                                  (sn.get("thumbnails", {}).get("default") or {}).get("url", ""),
                                  kind="channel"))
        if not pages:
            raise AdapterError("YouTube: не найден ни один канал")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        st = content.settings or {}
        videos = [m for m in content.media if m.type == "video"]
        if not videos:
            raise AdapterError("YouTube: для публикации нужен видеофайл")
        video = videos[0]
        if len(videos) > 1:
            raise AdapterError("YouTube: одно видео на пост")

        token = await self._token(page.account.credentials)
        text = content.text or ""
        title = video.title or (text.splitlines()[0] if text else video.name)
        title = title[:100]
        # YouTube сам распознаёт Shorts (вертикальное видео до 3 минут);
        # хэштег добавляем по настройке для надёжности
        if st.get("publish_as_shorts") and "#shorts" not in title.lower() and "#shorts" not in text.lower():
            title = (title + " #Shorts")[:100]

        metadata = {
            "snippet": {"title": title, "description": text, "categoryId": "22"},
            "status": {"privacyStatus": st.get("youtube_privacy", "public"),
                       "selfDeclaredMadeForKids": False},
        }
        data = await read_media_bytes(video)
        size = len(data)

        async with httpx.AsyncClient(timeout=600) as client:
            init = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={"Authorization": f"Bearer {token}",
                         "X-Upload-Content-Length": str(size),
                         "X-Upload-Content-Type": "video/mp4",
                         "Content-Type": "application/json"},
                json=metadata)
            if init.status_code >= 300:
                raise AdapterError(f"YouTube init: {init.text[:300]}")
            upload = await client.put(init.headers["Location"], content=data)
        if upload.status_code >= 300:
            raise AdapterError(f"YouTube upload: {upload.text[:300]}")
        vid = upload.json()["id"]
        return PublishOutcome(True, external_id=vid, external_url=f"https://youtu.be/{vid}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = await self._token(page.account.credentials)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete("https://www.googleapis.com/youtube/v3/videos",
                                    params={"id": external_id},
                                    headers={"Authorization": f"Bearer {token}"})
        return r.status_code in (200, 204)

    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:
        cid = source_ref.strip()
        m = re.search(r"channel/([A-Za-z0-9_-]+)", cid)
        if m:
            cid = m.group(1)
        if not cid.startswith("UC"):
            raise AdapterError("YouTube: укажите channel_id (начинается с UC)")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
            r.raise_for_status()
            xml = r.text
        entries = re.findall(r"<entry>(.*?)</entry>", xml, re.S)[:limit]
        items = []
        for e in entries:
            vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", e)
            title = re.search(r"<title>([^<]+)</title>", e)
            published = re.search(r"<published>([^<]+)</published>", e)
            if not vid:
                continue
                items.append(SourceItem(
                external_id=vid.group(1),
                text=(title.group(1) if title else "") + f"\nhttps://youtu.be/{vid.group(1)}",
                url=f"https://youtu.be/{vid.group(1)}"))
        return items
