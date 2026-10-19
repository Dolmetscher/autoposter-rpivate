"""Макс (мессенджер MAX): официальный Bot API — platform-api2.max.ru.
Бот подключается токеном; страницы — его групповые чаты и каналы (GET /chats).
Медиа загружается через POST /uploads, отправка — POST /messages."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, read_media_bytes

API = "https://platform-api2.max.ru"

UPLOAD_TYPES = {"photo": "image", "video": "video", "document": "file"}


class MaxAdapter(SocialAdapter):
    source_id = 22
    name = "Макс"
    capabilities = ("post", "photo", "video", "document", "channel", "chat")

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token", "label": "Токен бота MAX (business.max.ru)",
                 "required": True}]

    def _headers(self, token: str) -> dict:
        return {"Authorization": token}  # по документации — без "Bearer"

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("Макс: не указан токен бота")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/chats", params={"count": 50},
                                 headers=self._headers(token))
        data = r.json()
        if r.status_code >= 300:
            raise AdapterError(f"Макс: {data.get('message', 'ошибка доступа')}")
        pages: list[PageInfo] = []
        for c in (data.get("chats") or []):
            chat_id = c.get("chat_id")
            if not chat_id:
                continue
            ctype = c.get("type", "group")
            kind = "channel" if ctype == "channel" else ("chat" if ctype == "dialog" else "group")
            pages.append(PageInfo(str(chat_id), c.get("title") or "Чат",
                                  kind=kind))
        if not pages:
            raise AdapterError("Макс: бота не добавили ни в один чат/канал "
                               "(добавьте бота администратором и повторите)")
        return pages

    async def _upload(self, client: httpx.AsyncClient, token: str, media) -> dict:  # noqa: ANN001
        att_type = UPLOAD_TYPES.get(media.type, "file")
        r = await client.post(f"{API}/uploads", params={"type": att_type},
                              headers=self._headers(token))
        if r.status_code >= 300:
            raise AdapterError(f"Макс uploads: {r.text[:200]}")
        data = r.json()
        upload_url = data.get("url")
        if not upload_url:
            raise AdapterError("Макс uploads: сервер не вернул ссылку загрузки")
        await client.post(upload_url, files={"data": (media.name or "file",
                                                      await read_media_bytes(media))})
        return {"type": att_type, "payload": {"token": data.get("token")}}

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("Макс: истории не поддерживаются Bot API")
        attachments = []
        async with httpx.AsyncClient(timeout=300) as client:
            for m in content.media:
                attachments.append(await self._upload(client, token, m))
            for d in content.documents:
                attachments.append(await self._upload(client, token, d))
            if not (content.text or "").strip() and not attachments:
                raise AdapterError("Макс: пустой пост")
            r = await client.post(f"{API}/messages",
                                  params={"chat_id": page.social_page_id},
                                  headers=self._headers(token),
                                  json={"text": content.text or "",
                                        "attachments": attachments})
        if r.status_code >= 300:
            raise AdapterError(f"Макс: {r.text[:200]}")
        data = r.json()
        msg = data.get("message", {})
        body = (msg.get("body") or {})
        mid = body.get("mid", "")
        ext = f"{page.social_page_id}:{mid}" if mid else ""
        return PublishOutcome(True, external_id=ext, external_url="")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        chat, _, mid = external_id.partition(":")
        if not mid:
            return False
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{API}/messages",
                                    params={"chat_id": chat, "message_id": mid},
                                    headers=self._headers(token))
        return r.status_code == 200
