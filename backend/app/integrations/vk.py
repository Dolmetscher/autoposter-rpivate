"""ВКонтакте: публикация в сообщество по ключу доступа (User Token или Community Token) —
посты на стене, фото, видео/клипы, истории, пин, автоудаление, источники кросспостинга."""
from __future__ import annotations

import json
import mimetypes
import re

import httpx

from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   SourceItem, media_filename, read_media_bytes, upload_filename)

API = "https://api.vk.com/method"
V = "5.199"


class VKAdapter(SocialAdapter):
    source_id = 1
    name = "ВКонтакте"
    capabilities = ("post", "photo", "video", "story", "clip", "channel")
    handles_link = True

    # ---------- служебные ----------
    async def call(self, method: str, token: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{API}/{method}",
                                      data={**params, "access_token": token, "v": V})
        except httpx.HTTPError as e:
            raise AdapterError(f"VK {method}: сетевая ошибка {e.__class__.__name__}") from e
        return self._unwrap(method, r)

    @staticmethod
    def owner_id(page) -> int:  # noqa: ANN001
        pid = str(page.social_page_id).lstrip("+-")
        if getattr(page, "page_kind", "group") == "user":
            return int(pid)
        return -int(re.sub(r"\D", "", pid) or 0)

    # ---------- подключение ----------
    def connect_requires(self) -> list[dict]:
        return [
            {"name": "access_token", "label": "Ключ доступа (токен пользователя или группы)",
             "required": True},
            {"name": "group_ref",
             "label": "Ссылка на группу или её ID (необязательно при использовании токена пользователя)",
             "required": False},
            {"name": "name", "label": "Название аккаунта в списке (необязательно)",
             "required": False},
        ]

    @staticmethod
    def _normalize_group_ref(ref: str) -> str:
        """'https://vk.com/club123' / 'public77' / '@name' / '-123' → '123' или 'name'."""
        ref = (ref or "").strip()
        if not ref:
            return ""
        m = re.search(r"(?:club|public)(\d+)", ref, re.I)
        if m:
            return m.group(1)
        ref = ref.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        return ref.lstrip("@").lstrip("-").strip()

    @staticmethod
    def _first_group(resp) -> dict:  # noqa: ANN001
        if isinstance(resp, dict):
            items = resp.get("groups") or [resp]
        else:
            items = resp
        if not items:
            raise AdapterError("VK groups.getById: пустой ответ")
        return items[0]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        creds = account.credentials
        token = creds.get("access_token")
        if not token:
            raise AdapterError("VK: не указан токен доступа")

        ref = self._normalize_group_ref(
            (extra or {}).get("group_ref") or creds.get("group_ref") or "")

        # 1. Если явно указали ссылку/ID группы
        if ref:
            group = self._first_group(await self.call(
                "groups.getById", token, group_id=ref, fields="photo_100"))
            name_override = creds.get("name") or (extra or {}).get("name")
            return [PageInfo(str(group["id"]),
                             (name_override or group.get("name") or "Сообщество ВК").strip(),
                             group.get("photo_100", ""), kind="group")]

        # 2. Если токен пользователя — подтягиваем пользователя и группы
        try:
            user_data = await self.call("users.get", token, fields="photo_100")
            if user_data:
                u = user_data[0]
                user_page = PageInfo(str(u["id"]), f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(),
                                     u.get("photo_100", ""), kind="user")
                admin_groups = await self.call("groups.get", token, filter="admin,editor", extended=1, fields="photo_100")
                items = admin_groups.get("items", []) if isinstance(admin_groups, dict) else []
                pages = [user_page]
                for g in items:
                    pages.append(PageInfo(str(g["id"]), g.get("name", "Группа").strip(), g.get("photo_100", ""), kind="group"))
                return pages
        except Exception:
            pass

        # 3. Резервный вариант для токена сообщества
        try:
            group = self._first_group(await self.call("groups.getById", token, fields="photo_100"))
            return [PageInfo(str(group["id"]), group.get("name", "Сообщество ВК").strip(),
                             group.get("photo_100", ""), kind="group")]
        except AdapterError as e:
            raise AdapterError("VK: не удалось определить сообщество — укажите ID группы или используйте токен пользователя") from e

    # ---------- публикация ----------
    @staticmethod
    def _upload_payload(kind: str, up, keys: tuple[str, ...] = ("server", "photo", "hash"),
                        required: str = "photo") -> dict:
        try:
            data = up.json()
        except Exception as e:
            raise AdapterError(f"VK: сервер загрузки {kind} вернул не-JSON ответ: {e}")
        if not isinstance(data, dict):
            raise AdapterError(f"VK: неожидаемый ответ сервера загрузки {kind}: {data!r}")
        err = data.get("error")
        if err:
            msg = err.get("error_msg", "ошибка") if isinstance(err, dict) else str(err)
            raise AdapterError(f"VK: загрузка {kind} не удалась — {msg}")
        payload = {k: str(v) for k, v in data.items() if k in keys}
        if not payload.get(required):
            raise AdapterError(
                f"VK: сервер загрузки не принял файл ({kind}) — ответ: {data!r}")
        return payload

    @staticmethod
    def _photo_variants(photo: str) -> list[str]:
        out: list[str] = []
        if not photo:
            return out
        try:
            items = json.loads(photo)
        except (TypeError, ValueError):
            return [photo]
        if not isinstance(items, list):
            return []
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("photo"):
                full = str(it["photo"])
                out.append(full)
                if ":" in full:
                    out.append(full.split(":")[0])
            if it.get("kid"):
                out.append(str(it["kid"]))
        return out

    @staticmethod
    def _first_item(items) -> dict:  # noqa: ANN001
        if isinstance(items, dict):
            items = items.get("items") or items.get("photo") or items.get("doc") or [items]
        if isinstance(items, list):
            if not items:
                raise AdapterError("VK: пустой ответ при сохранении файла")
            items = items[0]
        if not isinstance(items, dict):
            raise AdapterError(f"VK: неожиданный ответ при сохранении файла: {items!r}")
        return items

    async def _upload_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                            media) -> str:  # noqa: ANN001
        params = {"access_token": token, "v": V}
        if group_id:
            params["group_id"] = abs(group_id)
        filename = upload_filename(media, "photo.jpg")
        ctype = mimetypes.guess_type(filename)[0] or "image/jpeg"
        content = await read_media_bytes(media)

        payload = None
        upload_error: AdapterError | None = None
        for attempt in range(3):
            try:
                r = await client.post(f"{API}/photos.getWallUploadServer", data=params)
                info = self._unwrap("photos.getWallUploadServer", r)
                upload_url = info.get("upload_url") if isinstance(info, dict) else info
                if not isinstance(upload_url, str) or not upload_url:
                    raise AdapterError("VK: не вернулась ссылка на сервер загрузки фото")
                up = await client.post(upload_url, files={
                    "photo": (filename, content, ctype)})
                payload = self._upload_payload("фото", up)
                break
            except AdapterError as e:
                upload_error = e
            except httpx.HTTPError as e:
                upload_error = AdapterError(
                    f"VK: сетевая ошибка при загрузке фото ({e.__class__.__name__})")
        if payload is None:
            raise upload_error or AdapterError("VK: не удалось загрузить фото")

        variants: list[str] = []
        for v in [payload["photo"]] + self._photo_variants(payload.get("photo", "")):
            if v and v not in variants:
                variants.append(v)
        items = None
        save_error: AdapterError | None = None
        for photo_value in variants:
            try:
                saved = await client.post(f"{API}/photos.saveWallPhoto", data={
                    **params, **payload, "photo": photo_value})
            except httpx.HTTPError as e:
                save_error = AdapterError(
                    f"VK: сетевая ошибка при сохранении фото ({e.__class__.__name__})")
                continue
            try:
                items = self._unwrap("photos.saveWallPhoto", saved)
                break
            except AdapterError as e:
                save_error = e
        if items is None:
            raise save_error or AdapterError("VK photos.saveWallPhoto: не удалось сохранить фото")

        it = self._first_item(items)
        photo_id = it.get("id")
        owner = it.get("owner_id")
        if not photo_id:
            ids = self._photo_variants(payload.get("photo", ""))
            photo_id = ids[0] if ids else ""
            owner = owner if owner is not None else (-group_id if group_id else 0)
        if not photo_id:
            raise AdapterError(f"VK: не удалось получить идентификатор фото: {it!r}")
        return f"photo{owner if owner is not None else 0}_{photo_id}"

    async def _upload_video(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                            media) -> str:  # noqa: ANN001
        params = {"access_token": token, "v": V,
                  "name": (media.title or media_filename(media, "video.mp4"))[:100],
                  "description": media.description or "", "wallpost": 0}
        if group_id:
            params["group_id"] = abs(group_id)
        r = await client.post(f"{API}/video.save", data=params)
        info = self._unwrap("video.save", r)
        upload_url = info.get("upload_url") if isinstance(info, dict) else info
        if not isinstance(upload_url, str) or not upload_url:
            raise AdapterError("VK: не вернулась ссылка на сервер загрузки видео")
        try:
            await client.post(upload_url,
                             files={"video_file": (upload_filename(media, "video.mp4"),
                                                   await read_media_bytes(media))})
        except httpx.HTTPError as e:
            raise AdapterError(
                f"VK: сетевая ошибка при загрузке видео ({e.__class__.__name__})") from e
        return f"video{info['owner_id']}_{info['video_id']}"

    async def _upload_doc(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                          media) -> str:  # noqa: ANN001
        title = media_filename(media, "document")
        params = {"access_token": token, "v": V, "type": "doc", "title": title}
        if group_id:
            params["group_id"] = abs(group_id)
        r = await client.post(f"{API}/docs.getWallUploadServer", data=params)
        info = self._unwrap("docs.getWallUploadServer", r)
        upload_url = info.get("upload_url") if isinstance(info, dict) else info
        if not isinstance(upload_url, str) or not upload_url:
            raise AdapterError("VK: не вернулась ссылка на сервер загрузки документа")
        try:
            up = await client.post(upload_url,
                                   files={"file": (upload_filename(media, "document"),
                                                   await read_media_bytes(media))})
        except httpx.HTTPError as e:
            raise AdapterError(
                f"VK: сетевая ошибка при загрузке документа ({e.__class__.__name__})") from e
        payload = self._upload_payload("документа", up, keys=("file", "title", "tags"),
                                       required="file")
        saved = await client.post(f"{API}/docs.save", data={**params, **payload})
        items = self._unwrap("docs.save", saved)
        it = self._first_item(items)
        return f"doc{it.get('owner_id', 0)}_{it.get('id', '')}"

    async def _post_story(self, client: httpx.AsyncClient, token: str, page,
                          content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        group_id = None if getattr(page, "page_kind", "group") == "user" \
            else int(re.sub(r"\D", "", str(page.social_page_id)))
        params = {"access_token": token, "v": V, "add_news": 1}
        if group_id:
            params["group_id"] = abs(group_id)
        if not content.media:
            raise AdapterError("VK истории: нужен файл фото или видео")
        media = content.media[0]
        method = "stories.getPhotoUploadServer" if media.type == "photo" else "stories.getVideoUploadServer"
        r = await client.post(f"{API}/{method}", data=params)
        info = self._unwrap(method, r)
        upload_url = info.get("upload_url") if isinstance(info, dict) else info
        if not isinstance(upload_url, str) or not upload_url:
            raise AdapterError("VK: не вернулась ссылка на сервер загрузки истории")
        field = "photo" if media.type == "photo" else "video_file"
        try:
            up = await client.post(upload_url,
                                   files={field: (upload_filename(media, "media.jpg"),
                                                  await read_media_bytes(media))})
        except httpx.HTTPError as e:
            raise AdapterError(
                f"VK истории: сетевая ошибка при загрузке ({e.__class__.__name__})") from e
        try:
            data = up.json()
        except Exception as e:
            raise AdapterError(f"VK истории: сервер загрузки вернул не-JSON ответ: {e}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("error_msg", "ошибка") if isinstance(err, dict) else str(err)
            raise AdapterError(f"VK истории: загрузка не удалась — {msg}")
        items = (data.get("response") or {}).get("items") or []
        if not items:
            raise AdapterError(f"VK истории: загрузка не удалась: {data}")
        res = await client.post(f"{API}/stories.post", data={
            **params, "upload_results": items[0].get("upload_result", "")})
        out = self._unwrap("stories.post", res)
        story = (out.get("items") or [{}])[0]
        sid = story.get("id", "")
        return PublishOutcome(True, external_id=f"story{sid}",
                              external_url=f"https://vk.com/story{group_id or ''}_{sid}")

    @staticmethod
    def _unwrap(method: str, r: httpx.Response):
        try:
            data = r.json()
        except Exception as e:
            print(f"!!! VK {method} RAW NON-JSON RESPONSE: {r.text}", flush=True)
            raise AdapterError(f"VK {method}: сервер вернул не-JSON ответ: {e}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("error_msg", "ошибка") if isinstance(err, dict) else str(err)
            print(f"!!! VK ERROR in {method}: code={err.get('error_code')} msg={msg}", flush=True)
            raise AdapterError(f"VK {method}: [{err.get('error_code')}] {msg}")
        if not isinstance(data, dict) or "response" not in data:
            print(f"!!! VK {method} UNEXPECTED RESPONSE: {data!r}", flush=True)
            raise AdapterError(f"VK {method}: неожиданный ответ {data!r}")
        return data["response"]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        token = creds.get("access_token")
        if not token:
            raise AdapterError("VK: у аккаунта нет access_token")
        st = content.settings or {}
        owner = self.owner_id(page)
        group_id = None if owner > 0 else -owner
        kind = getattr(page, "page_kind", "group")

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                if st.get("publish_as_story"):
                    return await self._post_story(client, token, page, content)

                attachments: list[str] = []
                for media in content.media:
                    if st.get("publish_as_clips") and media.type == "video":
                        att = await self._upload_video(client, token, group_id, media)
                        if not st.get("share_clips_to_feed"):
                            info = att.replace("video", "", 1)
                            return PublishOutcome(True, external_id=info,
                                                  external_url=f"https://vk.com/clips{info}")
                        attachments.append(att)
                    elif media.type == "photo":
                        attachments.append(await self._upload_photo(client, token, group_id, media))
                    elif media.type == "video":
                        attachments.append(await self._upload_video(client, token, group_id, media))
                for doc in content.documents:
                    attachments.append(await self._upload_doc(client, token, group_id, doc))

                message = (content.text or "").strip()
                link = (st.get("link") or "").strip()

                # ИСПРАВЛЕНИЕ ВЗАИМОДЕЙСТВИЯ ССЫЛКИ И ФОТО В ВК:
                # В attachments может быть ссылка ТОЛЬКО если нет медиафайлов!
                if link:
                    if attachments:
                        # Если уже есть фото/видео — добавляем ссылку текстом в конец поста
                        message = f"{message}\n\n{link}".strip()
                    else:
                        # Если медиафайлов нет — прикрепляем как ссылочный сниппет
                        attachments.append(link)

                if not message and not attachments:
                    raise AdapterError("VK: пустой пост (нет текста и вложений)")

                r = await client.post(f"{API}/wall.post", data={
                    "access_token": token, "v": V, "owner_id": owner,
                    "from_group": 1 if kind != "user" else 0,
                    "message": message[:4096],
                    "attachments": ",".join(attachments),
                })
                print(f"!!! VK wall.post raw response: {r.text}", flush=True)
                resp = self._unwrap("wall.post", r)
                post_id = resp["post_id"]
                if st.get("pin_post"):
                    await self.call("wall.pin", token, owner_id=owner, post_id=post_id)
                
                ext_url = f"https://vk.com/wall{owner}_{post_id}"
                print(f"!!! VK POST PUBLISHED SUCCESSFULLY: {ext_url}", flush=True)
                return PublishOutcome(True, external_id=f"{owner}_{post_id}", external_url=ext_url)
        except httpx.HTTPError as e:
            raise AdapterError(
                f"VK: сетевая ошибка при публикации ({e.__class__.__name__})") from e

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if not token or "_" not in external_id:
            return False
        owner, post_id = external_id.split("_", 1)
        if post_id.startswith("story"):
            await self.call("stories.delete", token, owner_id=int(owner), story_id=post_id[5:])
        else:
            await self.call("wall.delete", token, owner_id=int(owner), post_id=int(post_id))
        return True

    # ---------- аналитика ----------
    async def fetch_stats(self, page, external_id: str) -> dict | None:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if not token or "_" not in external_id:
            return None
        resp = await self.call("wall.getById", token, posts=external_id)
        items = resp.get("items") if isinstance(resp, dict) else resp
        if not items:
            return None
        p = items[0]
        return {
            "views": (p.get("views") or {}).get("count", 0),
            "likes": (p.get("likes") or {}).get("count", 0),
            "reposts": (p.get("reposts") or {}).get("count", 0),
            "comments": (p.get("comments") or {}).get("count", 0),
        }

    # ---------- кросспостинг ----------
    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:  # noqa: ANN001
        token = (account.credentials.get("access_token") if account else None)
        if not token:
            raise AdapterError("VK-кросспостинг: подключите аккаунт ВКонтакте (нужен токен)")
        digits = re.sub(r"\D", "", source_ref)
        if not digits:
            raise AdapterError("VK: укажите id группы/человека (напр. -12345)")
        owner = int(digits)
        if "club" in source_ref or source_ref.strip().startswith("-"):
            owner = -owner
        resp = await self.call("wall.get", token, owner_id=owner, count=limit,
                               filter="owner")
        items: list[SourceItem] = []
        for p in resp.get("items", []):
            media: list[dict] = []
            for att in p.get("attachments", []):
                if att.get("type") == "photo":
                    sizes = att["photo"].get("sizes", [])
                    if sizes:
                        media.append({"url": sizes[-1]["url"], "kind": "photo"})
            text = p.get("text", "")
            if text or media:
                items.append(SourceItem(
                    external_id=f"{p.get('owner_id', owner)}_{p['id']}",
                    text=text, media=media,
                    url=f"https://vk.com/wall{p.get('owner_id', owner)}_{p['id']}"))
        return items
