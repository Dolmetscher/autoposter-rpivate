"""Одноклассники: медитопики (посты) с фото/видео, удаление — через
официальный REST API с подписью запросов (md5).

Личные страницы: публикация в ленту профиля (mediatopic.post type=USER).

Репост из своих пабликов: публичный API ОК не отдаёт ленту группы напрямую,
но владельцу/администратору доступна статистика топиков (group.getStatTopics
возвращает ID и время создания топиков), а содержимое дотягивается
mediatopic.getByIds. На этом построен fetch_source_items — репост работает
только со своих пабликов, чужие группы ОК не читает.
"""
from __future__ import annotations

import hashlib
import io
import json
import re

import httpx

from ..config import settings
from ._mdfmt import md_to_plain
from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   SourceItem, media_filename, read_media_bytes)


class OKAdapter(SocialAdapter):
    source_id = 2
    name = "Одноклассники"
    capabilities = ("post", "photo", "video")
    note = ("публикация — в группы и на личную страницу (ленту профиля); "
            "репост — из своих пабликов: лента группы доступна только "
            "владельцу/администратору (через статистику топиков), чужие группы "
            "ОК не отдаёт. Форматирование текста API ОК не поддерживает: "
            "разметка Markdown (жирный/курсив/ссылки) аккуратно снимается, "
            "остаются чистый текст и ссылки")

    # ---------- служебные ----------
    async def call(self, method: str, creds: dict, extra_params: dict | None = None) -> dict:
        token = creds.get("access_token")
        app_key = creds.get("application_key") or settings.ok_app_public_key
        secret = creds.get("session_secret_key") or settings.ok_app_secret
        if not (token and app_key and secret):
            raise AdapterError("OK: не хватает access_token / application_key / session_secret_key")
        params = {"application_key": app_key, "format": "json", "method": method,
                  **(extra_params or {})}
        sig_src = "".join(f"{k}={params[k]}" for k in sorted(params)) + secret
        params.update({"sig": hashlib.md5(sig_src.encode()).hexdigest(),
                       "access_token": token})
        
        # Исправление: отправляем через POST data=params, чтобы избежать 400 Bad Request из-за длины URL
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post("https://api.ok.ru/fb.do", data=params)
        try:
            data = r.json()
        except ValueError as e:
            raise AdapterError(f"OK {method}: некорректный ответ {r.text[:200]}") from e
        if isinstance(data, dict) and data.get("error_code"):
            raise AdapterError(f"OK {method}: {data.get('error_msg')}")
        return data

    # ---------- подключение ----------
    def connect_requires(self) -> list[dict]:
        return [
            {"name": "access_token", "label": "Access token OK", "required": True},
            {"name": "application_key", "label": "Application key (публичный)", "required": True},
            {"name": "session_secret_key", "label": "Session secret key", "required": True},
        ]

    def oauth_authorize_url(self, state: str) -> str | None:
        if not settings.ok_app_id:
            return None
        redirect = f"{settings.public_base_url}/api/web/oauth/ok/callback"
        return ("https://connect.ok.ru/oauth/authorize?client_id={}&scope=VALUABLE_ACCESS;"
                "GROUP_CONTENT;PHOTO_CONTENT;LONG_ACCESS_TOKEN&response_type=code"
                "&redirect_uri={}&layout=w&state={}").format(settings.ok_app_id, redirect, state)

    async def oauth_exchange(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(settings.ok_access_token_url, data={
                "code": code, "client_id": settings.ok_app_id,
                "client_secret": settings.ok_app_secret,
                "redirect_uri": f"{settings.public_base_url}/api/web/oauth/ok/callback",
                "grant_type": "authorization_code",
            })
        data = r.json()
        if "access_token" not in data:
            raise AdapterError(f"OK OAuth: {data.get('error_description') or data}")
        return {"access_token": data["access_token"],
                "application_key": settings.ok_app_public_key,
                "session_secret_key": data.get("session_secret_key", "")}

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        creds = account.credentials
        me = await self.call("users.getCurrentUser", creds,
                             {"fields": "uid,name,pic_1"})
        if not isinstance(me, dict) or not me.get("uid"):
            raise AdapterError("OK: не удалось получить профиль "
                               "(проверьте access_token и права приложения)")
        pages = [PageInfo(str(me["uid"]), me.get("name", ""), me.get("pic_1", ""), kind="user")]
        groups = await self.call("group.getUserGroupsV2", creds, {"count": 50})
        raw_groups = groups.get("groups", []) if isinstance(groups, dict) else []
        ids = [g["groupId"] for g in raw_groups or [] if isinstance(g, dict) and g.get("groupId")]
        if ids:
            # Запрашиваем только name и status
            infos = await self.call("group.getInfo", creds,
                                    {"uids": ",".join(ids), "fields": "name,status"})
            if isinstance(infos, list):
                groups_list = [g for g in infos if isinstance(g, dict)]
            elif isinstance(infos, dict):
                if infos and all(isinstance(v, dict) for v in infos.values()):
                    groups_list = list(infos.values())
                else:
                    groups_list = [infos]
            else:
                groups_list = []

            def gkey(g: dict) -> str:
                key = g.get("uid") or g.get("groupId") or g.get("id") or ""
                return str(key)

            by_id = {gkey(g): g for g in groups_list if gkey(g)}
            if by_id:
                infos_for = [by_id.get(str(gid), {}) for gid in ids]
            else:
                infos_for = (groups_list + [{}] * len(ids))[:len(ids)]
            for gid, info in zip(ids, infos_for):
                name = info.get("name") or info.get("NAME") or ""
                status = info.get("status") or info.get("STATUS") or ""
                pages.append(PageInfo(str(gid), name, "",
                                      kind="group", can_post=status != "BLOCKED"))
        return pages

    # ---------- публикация ----------
    async def _upload_video(self, creds: dict, gid: str | None, media) -> str:  # noqa: ANN001
        """Загрузка ролика в видеоальбом группы (gid) или профиля. Возвращает video_id."""
        vname = media_filename(media, "video.mp4")
        params = {"file_name": vname}
        if gid:
            params["gid"] = gid
        up = await self.call("video.getUploadUrl", creds, params)
        # video.getUploadUrl отдаёт строку-ссылку, photosV2 — объект.
        upload_url = up if isinstance(up, str) else (up or {}).get("upload_url")
        if not upload_url:
            raise AdapterError("OK: не удалось получить ссылку загрузки видео")
        bio = io.BytesIO(await read_media_bytes(media))
        bio.name = vname
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                r = await client.post(upload_url, files={"file": (vname, bio)})
        finally:
            bio.close()
        try:
            vid = (r.json() or {}).get("video_id") or ""
        except ValueError:  # сервер вернул не-JSON (страница ошибки)
            vid = ""
        if not vid:
            raise AdapterError("OK: загрузка видео не удалась (сервер не вернул video_id)")
        return str(vid)

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        st = content.settings or {}
        # publish_as_story: у ОК нет историй через API — настройка
        # игнорируется, пост публикуется как обычный (см. 4.8 руководства).
        gid = None if getattr(page, "page_kind", "group") == "user" else page.social_page_id

        if st.get("video_only"):
            # Режим «только видео»: ролик уходит в раздел видео группы/профиля,
            # медитопик (пост) не создаётся.
            videos = [m for m in content.media if m.type == "video"]
            if not videos:
                raise AdapterError(
                    "OK: включён режим «только видео», но видеофайлов в посте нет")
            if len(videos) > 1:
                raise AdapterError("OK: в один пост загружается только одно видео")
            vid = await self._upload_video(creds, gid, videos[0])
            return PublishOutcome(True, external_id=f"video{vid}",
                                  external_url=f"https://ok.ru/video/{vid}")

        attachments: list[dict] = []
        if content.text:
            attachments.append({"type": "text", "text": md_to_plain(content.text)})

        photos = [m for m in content.media if m.type == "photo"]
        if photos:
            params = {"count": str(len(photos))}
            if gid:
                params["gid"] = gid
            up = await self.call("photosV2.getUploadUrl", creds, params)
            upload_url = up.get("upload_url")
            files = {}
            for i, m in enumerate(photos):
                bio = io.BytesIO(await read_media_bytes(m))
                fname = media_filename(m, "photo.jpg")
                bio.name = fname
                files[f"pic{i + 1}"] = (fname, bio)
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    r = await client.post(upload_url, files=files)
                data = r.json()
            finally:
                for _, bio in files.values():
                    bio.close()
            photo_list = []
            for pid, pdata in (data.get("photos") or {}).items():
                # Значением может быть и токен-строка, и объект с полем token.
                token = pdata.get("token") if isinstance(pdata, dict) else pdata
                photo_list.append({"id": token or pid})
            if photo_list:
                attachments.append({"type": "photo", "list": photo_list})

        videos = [m for m in content.media if m.type == "video"]
        if videos:
            if len(videos) > 1:
                raise AdapterError("OK: в один пост загружается только одно видео")
            vid = await self._upload_video(creds, gid, videos[0])
            attachments.append({"type": "video", "list": [{"id": vid}]})

        if not attachments:
            raise AdapterError("OK: пустой пост")
        
        # Исправление: mediatopic.post теперь тоже летит через POST
        topic = await self.call("mediatopic.post", creds,
                                {"type": "GROUP_THEME" if gid else "USER",
                                 **({"gid": gid} if gid else {}),
                                 "attachment": json.dumps({"media": attachments}, ensure_ascii=False)})
        topic_id = str(topic)
        url = f"https://ok.ru/{('group/' + gid) if gid else 'profile/' + str(page.social_page_id)}/{topic_id}"
        return PublishOutcome(True, external_id=topic_id, external_url=url)

    # ---------- кросспостинг ----------
    @staticmethod
    def _parse_group_ref(ref: str) -> str:
        """Ссылка на паблик или голый ID → числовой ID группы.

        Короткие адреса вида ok.ru/имя не принимаются: group.getStatTopics
        работает с числовым gid, поэтому просим числовую ссылку (ok.ru/group/…).
        """
        ref = (ref or "").strip()
        if not ref:
            raise AdapterError("OK: укажите ссылку на свой паблик или его ID "
                               "(напр. https://ok.ru/group/123456789)")
        m = re.search(r"(?:ok\.ru|odnoklassniki\.ru)/group/(\d+)", ref)
        if m:
            return m.group(1)
        if ref.isdigit():
            return ref
        raise AdapterError(
            "OK: не удалось определить ID паблика. Укажите числовую ссылку "
            "(https://ok.ru/group/123456789) или числовой ID — короткие адреса "
            "вида ok.ru/имя API ОК не принимает")

    @staticmethod
    def _photo_url(photo) -> str:  # noqa: ANN001
        """Ссылка на изображение из фото-блока топика: ОК отдаёт набор
        pic_-превью разного размера — берём самое крупное из доступных."""
        if not isinstance(photo, dict):
            return ""
        for key in ("pic_max", "pic1024x768", "pic640x480", "pic320x240",
                    "pic190x190"):
            url = photo.get(key)
            if isinstance(url, str) and url.startswith("http"):
                return url
        for key, url in sorted(photo.items()):
            if key.startswith("pic") and isinstance(url, str) and url.startswith("http"):
                return url
        return ""

    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:  # noqa: ANN001
        """Лента своего паблика ОК: свежие топики для репоста.

        Лента группы читается в два шага: group.getStatTopics отдаёт ID и
        время создания топиков (метод статистики — доступен только
        владельцу/администратору группы, права GROUP_CONTENT + VALUABLE_ACCESS),
        затем mediatopic.getByIds возвращает содержимое. Поэтому репост
        возможен только со своих пабликов — чужие группы ОК не отдаёт.

        Топики без статистики в выдачу не попадают (ограничение OK): пост,
        который кроме автора никто не видел, репостнуться не сможет.
        """
        creds = (account.credentials if account is not None else None) or {}
        if not creds.get("access_token"):
            raise AdapterError("ОК-кросспостинг: подключите аккаунт "
                               "Одноклассники (нужен токен)")
        gid = self._parse_group_ref(source_ref)
        try:
            stats = await self.call("group.getStatTopics", creds,
                                    {"gid": gid, "count": limit,
                                     "fields": "topic.*"})
        except AdapterError as e:
            raise AdapterError(
                f"ОК-репост группы {gid}: {e} — лента группы доступна только "
                "владельцу/администратору. Укажите свой паблик: репост из "
                "чужих групп Одноклассники не поддерживает") from e
        rows = ([t for t in (stats.get("topics") or [])
                 if isinstance(t, dict) and t.get("id")]
                if isinstance(stats, dict) else [])
        rows.sort(key=lambda t: int(t.get("created_ms") or 0), reverse=True)
        ids = [str(t["id"]) for t in rows[:limit]]
        if not ids:
            return []

        full = await self.call("mediatopic.getByIds", creds,
                               {"topic_ids": ",".join(ids),
                                "fields": "media_topic.*"})
        full_list = full if isinstance(full, list) else (
            (full.get("topics") if isinstance(full, dict) else None) or [])
        by_id = {str(t.get("id")): t for t in full_list
                 if isinstance(t, dict) and t.get("id") is not None}

        items: list[SourceItem] = []
        for tid in ids:
            topic = by_id.get(tid)
            if not topic:
                continue
            text_parts: list[str] = []
            media: list[dict] = []
            for block in topic.get("media") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(str(block["text"]))
                elif block.get("type") == "photo":
                    for ph in block.get("list") or []:
                        url = self._photo_url(ph)
                        if url:
                            media.append({"url": url, "kind": "photo"})
                # видео (movie) и прочие блоки пропускаем: прямой ссылки для
                # загрузки нет — как и в источнике ВК, репостятся текст и фото
            text = "\n".join(text_parts).strip()
            if text or media:
                items.append(SourceItem(
                    external_id=tid, text=text, media=media,
                    url=f"https://ok.ru/group/{gid}/topic/{tid}"))
        return items

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        if external_id.startswith("video"):
            # видеозапись из режима «только видео» — удаляется как видео
            await self.call("video.delete", page.account.credentials,
                            {"video_id": external_id[5:]})
            return True
        await self.call("mediatopic.delete", page.account.credentials,
                        {"topic_id": external_id})
        return True
