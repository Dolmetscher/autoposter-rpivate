"""Facebook: посты на страницы через Graph API (Page access token).
Фото/видео загружаются напрямую байтами (multipart), не требуя публичных ссылок.

Личные профили: публикация в них запрещена Meta (право publish_actions
удалено в 2018) — доступна только ошибка с понятным объяснением.
Кросспостинг-источник: лента своей страницы (/{page-id}/posts). Чужие
публичные страницы читаются только с закрытой фичей Page Public Content
Access, обычным приложениям недоступной."""
from __future__ import annotations

import logging
import re

import httpx

from ._mdfmt import md_to_plain
from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   SourceItem, read_media_bytes)

logger = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v21.0"


class FacebookAdapter(SocialAdapter):
    source_id = 3
    name = "Facebook"
    capabilities = ("post", "photo", "video")
    note = ("публикация — на страницы (Pages); личные профили API Facebook не "
            "позволяет (Meta закрыла это в 2018). Источник кросспостинга — "
            "лента своей страницы; чужие паблики доступны только приложениям с "
            "закрытой фичей Page Public Content Access. Форматирование текста "
            "API не поддерживает — разметка Markdown аккуратно снимается")

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
                                 params={"fields": "name,picture,access_token",
                                         "limit": 200, "access_token": token})
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
                                 kind="user", can_post=False)]

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

        if getattr(page, "page_kind", "page") == "user":
            # Личная страница (профиль): Graph API запрещает публикацию в профили —
            # право publish_actions удалено Meta в 2018. Публикуйте на страницу (Page).
            raise AdapterError(
                "FB: публикация на личную страницу (профиль) запрещена API Facebook — "
                "Meta закрыла её в 2018. Доступны только страницы: подключите токен "
                "страницы или user-токен с правом manage_pages (кнопка «Обновить страницы» "
                "в настройках аккаунта)")

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
                        "description": md_to_plain(content.text or ""),
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
                        "caption": md_to_plain(content.text or "")[:2200],
                        "access_token": token,
                    },
                    files={"source": (img_name, img_bytes)}
                )
            else:
                if not content.text:
                    raise AdapterError("FB: пустой пост")
                r = await client.post(f"{GRAPH}/{pid}/feed", data={
                    "message": md_to_plain(content.text or ""),
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

    # ---------- кросспостинг ----------
    @staticmethod
    def _parse_page_ref(ref: str) -> str:
        """Ссылка на страницу или голый ID → ID/username для Graph API."""
        ref = (ref or "").strip()
        if not ref:
            raise AdapterError("FB: укажите ссылку на страницу или её ID "
                               "(напр. https://www.facebook.com/mypage или 123456789)")
        if "profile.php" in ref:
            m = re.search(r"[?&]id=(\d+)", ref)
            if m:
                return m.group(1)
        ref = ref.split("?", 1)[0].rstrip("/")
        if "://" in ref:
            ref = ref.split("://", 1)[1]
        # классические ссылки /pages/Имя/12345 — ID стоит последним сегментом
        segs = [s for s in ref.split("/") if s and s.lower() != "pages"]
        if not segs:
            raise AdapterError("FB: не удалось разобрать ссылку на страницу")
        # голый ID/username — как есть; полная ссылка — последний сегмент пути
        return segs[-1]

    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:  # noqa: ANN001
        """Лента своей страницы: новые посты для кросспостинга.

        Чужие публичные страницы Graph API не отдаёт — нужна закрытая фича
        Page Public Content Access; при попытке читаем понятную ошибку."""
        creds = (account.credentials if account else None) or {}
        token = creds.get("access_token")
        if not token:
            raise AdapterError("FB-кросспостинг: подключите аккаунт Facebook (нужен токен)")
        pid = self._parse_page_ref(source_ref)
        page_token = creds.get(f"token_{pid}")
        if not page_token or page_token == token:
            page_token = await self._resolve_page_token(pid, token)

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{GRAPH}/{pid}/posts",
                                 params={"fields": "id,message,created_time,full_picture",
                                         "limit": limit, "access_token": page_token})
        try:
            data = r.json()
        except ValueError as e:
            raise AdapterError(f"FB: некорректный ответ Graph API ({r.status_code})") from e
        if "error" in data:
            err = data["error"]
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            if err.get("code") in (300, 10) or "Page Public Content Access" in msg:
                raise AdapterError(
                    "FB: чтение чужих страниц запрещено (нужна закрытая фича "
                    "Page Public Content Access). Укажите свою страницу — ту, что "
                    "подключена в разделе «Аккаунты»")
            raise AdapterError(f"FB: {msg}")

        items: list[SourceItem] = []
        for p in data.get("data", []):
            media = []
            if p.get("full_picture"):
                media.append({"url": p["full_picture"], "kind": "photo"})
            text = p.get("message", "")
            if text or media:
                items.append(SourceItem(
                    external_id=str(p["id"]), text=text, media=media,
                    url=f"https://www.facebook.com/{p['id']}"))
        return items

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        pid = page.social_page_id
        user_token = page.account.credentials.get("access_token")
        token = page.account.credentials.get(f"token_{pid}") or user_token
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{GRAPH}/{external_id}",
                                    params={"access_token": token})
        return r.status_code in (200, 204)
