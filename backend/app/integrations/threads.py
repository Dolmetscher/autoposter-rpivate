"""Threads (Meta): текст, фото, видео и карусели через Threads API
(graph.threads.com). Нужен access token приложения с правом
threads_basic/threads_content_publish и Threads User ID.

Долгоживущий токен живёт 60 дней, поэтому адаптер сам продлевает его перед
публикацией (refresh_access_token), пока токен ещё действует."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from ..util import utcnow
from ._mdfmt import md_to_plain
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

API = "https://graph.threads.com/v1.0"

log = logging.getLogger("threads")

# Продлеваем токен заранее, когда ему больше 20 дней (живёт 60, продлевать
# можно пока не истёк — успеваем многократно при регулярных публикациях).
TOKEN_REFRESH_AFTER_DAYS = 20


class ThreadsAdapter(SocialAdapter):
    source_id = 12
    name = "Тредс"
    capabilities = ("post", "photo", "video")
    note = ("лимит текста — 500 символов; форматирование API Threads не "
            "поддерживает — разметка Markdown аккуратно снимается")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "access_token",
             "label": "Access token Threads (threads_basic + threads_content_publish)",
             "required": True},
            {"name": "threads_user_id", "label": "Threads User ID", "required": True},
        ]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        creds = account.credentials
        token = creds.get("access_token")
        uid = creds.get("threads_user_id")
        if not (token and uid):
            raise AdapterError("Threads: нужны access_token и threads_user_id")
        # фиксируем момент подключения/обновления токена — от него отсчитывается
        # автопродление (см. refresh_long_lived_token)
        if not creds.get("token_updated_at"):
            account.credentials = {**creds, "token_updated_at": utcnow().isoformat()}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/{uid}",
                                 params={"fields": "username,threads_profile_picture_url",
                                         "access_token": token})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"Threads: {data['error'].get('message')}")
        return [PageInfo(str(uid), f"@{data.get('username', uid)}",
                 data.get("threads_profile_picture_url", ""), kind="user")]

    async def refresh_long_lived_token(self, account) -> dict | None:  # noqa: ANN001
        """Автопродление долгоживущего токена (60 дней), пока он ещё действует.

        GET /refresh_access_token?grant_type=th_refresh_token — официальный
        способ продления. Если токен уже истёк, продлить нельзя: возвращаем
        None, публикация покажет ошибку API и нужен новый токен."""
        creds = dict(account.credentials or {})
        token = creds.get("access_token")
        if not token:
            return None
        updated = creds.get("token_updated_at")
        if updated:
            try:
                age = utcnow() - datetime.fromisoformat(str(updated))
                if age < timedelta(days=TOKEN_REFRESH_AFTER_DAYS):
                    return None  # токен свежий — продлевать рано
            except ValueError:
                pass  # битая дата — пробуем продлить
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get("https://graph.threads.com/refresh_access_token",
                                     params={"grant_type": "th_refresh_token",
                                             "access_token": token})
                data = r.json() or {}
        except Exception:
            log.warning("Threads: не удалось продлить токен аккаунта %s",
                        getattr(account, "id", "?"), exc_info=True)
            return None
        new_token = data.get("access_token")
        if not new_token:
            # токен истёк/невалиден — продлению не подлежит
            log.warning("Threads: продление токена аккаунта %s не состоялось: %s",
                        getattr(account, "id", "?"),
                        (data.get("error") or {}).get("message") or data)
            return None
        creds["access_token"] = new_token
        creds["token_updated_at"] = utcnow().isoformat()
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            creds["token_expires_at"] = (utcnow() + timedelta(seconds=expires_in)).isoformat()
        return creds

    async def _container(self, client: httpx.AsyncClient, token: str, uid: str,
                         params: dict) -> str:
        r = await client.post(f"{API}/{uid}/threads", data={**params, "access_token": token})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"Threads container: {data['error'].get('message')}")
        return data["id"]

    async def _wait_ready(self, client: httpx.AsyncClient, token: str,
                          container_id: str) -> None:
        for _ in range(24):
            r = await client.get(f"{API}/{container_id}",
                                 params={"fields": "status", "access_token": token})
            code = (r.json() or {}).get("status")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise AdapterError("Threads: ошибка обработки медиа")
            await asyncio.sleep(3)
        raise AdapterError("Threads: медиа не обработалось вовремя")

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        token, uid = creds.get("access_token"), creds.get("threads_user_id")
        if not (token and uid):
            raise AdapterError("Threads: аккаунт не настроен")
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("Threads: историй нет — это пост в ленту")

        photos = [m for m in content.media if m.type == "photo"]
        videos = [m for m in content.media if m.type == "video"]
        if videos and (photos or len(videos) > 1):
            raise AdapterError("Threads: одно видео без других медиа на пост")
        if not content.text and not photos and not videos:
            raise AdapterError("Threads: пустой пост")
        # Лимит текста поста Threads — 500 символов; форматирование не
        # поддерживается — Markdown снимается.
        text = md_to_plain(content.text or "")[:500]

        async with httpx.AsyncClient(timeout=120) as client:
            if videos:
                container = await self._container(client, token, uid, {
                    "media_type": "VIDEO",
                    "video_url": content.public_url(videos[0].file_path),
                    "text": text})
                await self._wait_ready(client, token, container)
            elif len(photos) > 1:
                children = []
                for p in photos[:20]:
                    children.append(await self._container(client, token, uid, {
                        "media_type": "IMAGE",
                        "image_url": content.public_url(p.file_path),
                        "is_carousel_item": "true"}))
                for cid in children:
                    await self._wait_ready(client, token, cid)
                container = await self._container(client, token, uid, {
                    "media_type": "CAROUSEL", "children": ",".join(children),
                    "text": text})
                await self._wait_ready(client, token, container)
            elif photos:
                container = await self._container(client, token, uid, {
                    "media_type": "IMAGE",
                    "image_url": content.public_url(photos[0].file_path),
                    "text": text})
                await self._wait_ready(client, token, container)
            else:
                container = await self._container(client, token, uid, {
                    "media_type": "TEXT", "text": text})

            r = await client.post(f"{API}/{uid}/threads_publish",
                                  data={"creation_id": container, "access_token": token})
            data = r.json()
            if "error" in data:
                raise AdapterError(f"Threads publish: {data['error'].get('message')}")
            post_id = data.get("id", "")
        return PublishOutcome(True, external_id=post_id,
                              external_url=f"https://www.threads.net/@{page.social_page_name.lstrip('@')}/post/{post_id}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if not token:
            return False
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{API}/{external_id}",
                                    params={"access_token": token})
        return r.status_code in (200, 204, 404)
