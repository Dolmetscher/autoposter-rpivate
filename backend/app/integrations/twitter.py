"""Твиттер/X: посты в профиль через API v2 (текст) + v1.1 media/upload (фото/видео).
Ключи OAuth 1.0a берутся из Developer Portal (Project → Keys and tokens):
consumer key/secret + access token/secret пользователя с правом tweet.write."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from ..config import settings
from ._oauth1 import OAuth1Client
from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   media_filename, read_media_bytes, read_media_bytes_to_file)

API2 = "https://api.twitter.com/2"
UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"


class TwitterAdapter(SocialAdapter):
    source_id = 4
    name = "Твиттер/X"
    capabilities = ("post", "photo", "video")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "api_key", "label": "API Key (consumer key)", "required": True},
            {"name": "api_secret", "label": "API Key Secret", "required": True},
            {"name": "access_token", "label": "Access Token", "required": True},
            {"name": "access_token_secret", "label": "Access Token Secret", "required": True},
        ]

    def _creds(self, account) -> dict:  # noqa: ANN001
        creds = {**{"api_key": settings.twitter_api_key,
                    "api_secret": settings.twitter_api_secret},
                 **account.credentials}
        if not (creds.get("api_key") and creds.get("api_secret")
                and creds.get("access_token") and creds.get("access_token_secret")):
            raise AdapterError("Twitter: нужны api_key, api_secret, access_token "
                               "и access_token_secret (Developer Portal)")
        return creds

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        creds = self._creds(account)
        client = OAuth1Client(creds)
        r = await client.request("GET", f"{API2}/users/me",
                                 params={"user.fields": "name,username,profile_image_url"})
        data = r.json()
        if "data" not in data:
            raise AdapterError(f"Twitter users/me: {data.get('detail') or data.get('title') or r.text[:200]}")
        u = data["data"]
        return [PageInfo(str(u["id"]), f"{u.get('name', '')} (@{u.get('username', '')})",
                         u.get("profile_image_url", ""), kind="user")]

    async def _upload_media(self, client: OAuth1Client, media) -> str:  # noqa: ANN001
        if media.type == "video":
            return await self._upload_video(client, media)
        data = await read_media_bytes(media)
        r = await client.request("POST", UPLOAD,
                                 files={"media": (media_filename(media, "media"), data)})
        out = r.json()
        if "media_id_string" not in out:
            raise AdapterError(f"Twitter media/upload: {out.get('errors', out)}")
        return out["media_id_string"]

    async def _upload_video(self, client: OAuth1Client, media) -> str:  # noqa: ANN001
        # INIT → APPEND (чанками по 4 МБ) → FINALIZE
        fd, tmp = tempfile.mkstemp(prefix="ap_tw_")
        os.close(fd)
        try:
            total = await read_media_bytes_to_file(media, tmp)
            r = await client.request("POST", UPLOAD, params={
                "command": "INIT", "media_type": "video/mp4", "total_bytes": total,
                "media_category": "tweet_video"})
            out = r.json()
            media_id = out.get("media_id_string")
            if not media_id:
                raise AdapterError(f"Twitter INIT: {out.get('errors', out)}")
            with open(tmp, "rb") as f:
                index = 0
                while chunk := f.read(4 * 1024 * 1024):
                    r = await client.request(
                        "POST", UPLOAD,
                        params={"command": "APPEND", "media_id": media_id,
                                "segment_index": index},
                        files={"media": ("chunk.bin", chunk)})
                    if r.status_code >= 300:
                        raise AdapterError(f"Twitter APPEND: {r.text[:200]}")
                    index += 1
            r = await client.request("POST", UPLOAD,
                                     params={"command": "FINALIZE", "media_id": media_id})
            out = r.json()
            if "media_id_string" not in out:
                raise AdapterError(f"Twitter FINALIZE: {out.get('errors', out)}")
            # ждём окончания обработки (processing_info), до ~2 минут
            info = out.get("processing_info") or {}
            for _ in range(24):
                if not info or info.get("state") not in ("pending", "in_progress"):
                    break
                await asyncio.sleep(max(int(info.get("check_after_secs", 5)), 1))
                r = await client.request(
                    "POST", UPLOAD,
                    params={"command": "STATUS", "media_id": media_id})
                out = r.json()
                if "processing_info" not in out:
                    break
                info = out["processing_info"]
            if info.get("state") in ("failed", "errored"):
                raise AdapterError(f"Twitter: видео не обработано: "
                                   f"{info.get('error', {}).get('message', 'ошибка обработки')}")
            return media_id
        finally:
            Path(tmp).unlink(missing_ok=True)

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = self._creds(page.account)
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("Twitter: историй (stories) нет — это пост в ленту")
        if not content.text and not content.media:
            raise AdapterError("Twitter: пустой пост")
        # Проверяем до загрузки файлов: X принимает до 4 фото либо 1 видео.
        videos = [m for m in content.media if m.type == "video"]
        if len(content.media) > 4:
            raise AdapterError("Twitter: не более 4 медиа на пост")
        if videos and len(content.media) > 1:
            raise AdapterError("Twitter: видео должно быть единственным медиа в посте")

        client = OAuth1Client(creds, timeout=300)
        media_ids: list[str] = []
        for m in content.media:
            media_ids.append(await self._upload_media(client, m))

        body = {"text": content.text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        r = await client.request_json("POST", f"{API2}/tweets", body=body)
        data = r.json()
        tweet = (data.get("data") or {})
        if "id" not in tweet:
            raise AdapterError(f"Twitter tweets: {data.get('detail') or data.get('title') or r.text[:200]}")
        username = page.social_page_name.partition("(")[2].rstrip(")").lstrip("@")
        return PublishOutcome(True, external_id=tweet["id"],
                              external_url=f"https://x.com/{username}/status/{tweet['id']}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        creds = self._creds(page.account)
        client = OAuth1Client(creds)
        r = await client.request("DELETE", f"{API2}/tweets/{external_id}")
        data = r.json()
        return bool((data.get("data") or {}).get("deleted"))
