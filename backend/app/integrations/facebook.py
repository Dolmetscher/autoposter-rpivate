"""Facebook: посты на страницы через Graph API (Page access token).
Фото/видео загружаются напрямую байтами (multipart), не требуя публичных ссылок."""
from __future__ import annotations

import logging

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, read_media_bytes

logger = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v21.0"


class FacebookAdapter(SocialAdapter):
    source_id = 3
    name = "Facebook"
    capabilities = ("post", "photo", "video")

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token",
                 "label": "Page Access Token или User Token (developers.facebook.com)", "required": True}]

    async def _resolve_page_token(self, pid: str, user_token: str) -> str:
        """Если у страницы нет сохраненного индивидуального токена, достаем его на лету по user-токену."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{GRAPH}/{pid}",
                                 params={"fields": "access_token", "access_token": user_token})
            data = r.json()
            if "access_token" in data:
                return data["access_token"]
        return user_token

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("FB: не указан access_token")
            
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{GRAPH}/me/accounts",
                                 params={"fields": "name,picture,access_token", "access_token": token})
            data = r.json()

            if "error" in data and data["error"].get("code") in (100, 190, 200):
                # 100 — «Unsupported get request», 190 — недействительный токен,
                # 200 — нет прав manage_pages: во всех случаях пробуем личный профиль.
                r_me = await client.get(f"{GRAPH}/me",
                                        params={"fields": "name,picture", "access_token": token})
                me_data = r_me.json()
                if "error" in me_data:
                    raise AdapterError(f"FB: {me_data['error'].get('message')}")
                return [PageInfo(me_data["id"], me_data.get("name", ""),
                                 (me_data.get("picture", {}).get("data") or {}).get("url", ""),
                                 kind="page")]

        if "error" in data:
            raise AdapterError(f"FB: {data['error'].get('message')}")
            
        pages = []
        for p in data.get("data", []):
            page_token = p.get("access_token") or token
            pages.append(PageInfo(p["id"], p.get("name", ""),
                                  (p.get("picture", {}).get("data") or {}).get("url", ""),
                                  kind="page"))
            if page_token:
                # Присваиваем новый словарь, а не мутируем на месте: колонка
                # credentials — обычный JSON, и in-place изменение SQLAlchemy
                # не отслеживает (токен не попал бы в БД).
                account.credentials = {**account.credentials, f"token_{p['id']}": page_token}

        if not pages:
            raise AdapterError("FB: не найдено страниц (нужен токен страницы или "
                               "user-токен с правом manage_pages)")
        return pages

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        pid = page.social_page_id
        user_token = page.account.credentials.get("access_token")
        if not user_token:
            raise AdapterError("FB: у аккаунта нет access_token")

        token = page.account.credentials.get(f"token_{pid}")
        if not token or token == user_token:
            token = await self._resolve_page_token(pid, user_token)

        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("FB: истории через API не поддерживаются в этой версии")
            
        photos = [m for m in content.media if m.type == "photo"]
        videos = [m for m in content.media if m.type == "video"]

        async with httpx.AsyncClient(timeout=300) as client:
            if videos:
                if len(videos) > 1:
                    raise AdapterError("FB: одно видео на пост")
                vid_bytes = await read_media_bytes(videos[0])
                vid_name = getattr(videos[0], "name", "video.mp4") or "video.mp4"
                r = await client.post(
                    f"{GRAPH}/{pid}/videos",
                    data={
                        "description": content.text or "",
                        "access_token": token,
                    },
                    files={"source": (vid_name, vid_bytes)}
                )
            elif photos:
                if len(photos) > 1:
                    raise AdapterError("FB: не более одного фото на пост в этой версии")
                # ИСПРАВЛЕНИЕ: читаем файл локально и отправляем сами байты картинки в Facebook напрямую
                img_bytes = await read_media_bytes(photos[0])
                img_name = getattr(photos[0], "name", "photo.jpg") or "photo.jpg"
                r = await client.post(
                    f"{GRAPH}/{pid}/photos",
                    data={
                        "caption": (content.text or "")[:2200],
                        "access_token": token,
                    },
                    files={"source": (img_name, img_bytes)}
                )
            else:
                if not content.text:
                    raise AdapterError("FB: пустой пост")
                r = await client.post(f"{GRAPH}/{pid}/feed", data={
                    "message": content.text,
                    "access_token": token})

        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text}}

        if "error" in data:
            err_msg = data['error'].get('message')
            logger.error(f"FB publish failed [{r.status_code}] on page {pid}: {data}")
            raise AdapterError(f"FB: {err_msg}")

        ext = data.get("post_id") or data.get("id", "")
        return PublishOutcome(True, external_id=ext,
                              external_url=f"https://www.facebook.com/{ext}" if ext else "")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        pid = page.social_page_id
        user_token = page.account.credentials.get("access_token")
        token = page.account.credentials.get(f"token_{pid}") or user_token
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{GRAPH}/{external_id}",
                                    params={"access_token": token})
        return r.status_code in (200, 204)
