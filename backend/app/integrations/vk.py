"""ВКонтакте: публикация в сообщество по ключу доступа сообщества (community token) —
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
    handles_link = True  # ссылка публикуется вложением links.add

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
            {"name": "access_token", "label": "Ключ доступа сообщества (токен группы)",
             "required": True},
            {"name": "group_ref",
             "label": "Ссылка на группу или её ID (можно не заполнять — определим сами)",
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
            raise AdapterError("VK: не указан ключ доступа сообщества (токен)")

        ref = self._normalize_group_ref(
            (extra or {}).get("group_ref") or creds.get("group_ref") or "")
        if ref:
            group = self._first_group(await self.call(
                "groups.getById", token, group_id=ref, fields="photo_100"))
        else:
            try:
                group = self._first_group(await self.call(
                    "groups.getById", token, fields="photo_100"))
            except AdapterError as e:
                raise AdapterError(
                    "VK: не удалось определить сообщество по токену — укажите "
                    "ссылку на группу или её ID при подключении") from e

        name_override = creds.get("name") or (extra or {}).get("name")
        return [PageInfo(str(group["id"]),
                         (name_override or group.get("name") or "Сообщество ВК").strip(),
                         group.get("photo_100", ""), kind="group")]

    # ---------- публикация ----------
    @staticmethod
    def _upload_payload(kind: str, up, keys: tuple[str, ...] = ("server", "photo", "hash"),
                        required: str = "photo") -> dict:
        """Параметры сохранения из ответа сервера загрузки (server/photo/hash).

        Ответ сервера загрузки бывает двух видов:
        * классический — {"server": 123, "photo": "c141990203", "hash": "..."};
        * современный — {"server": 123, "photo": "[{...json с данными фото...}]", "hash": "..."}.

        Если файл не принят, VK отвечает HTTP 200, но с пустым photo (или с
        объектом error). Раньше такой ответ молча уходил в photos.saveWallPhoto
        без параметра photo, и пользователь видел невнятное «photo is undefined»
        вместо настоящей причины.
        """
        try:
            data = up.json()
        except Exception as e:  # noqa: BLE001 — ответ мог прийти не-JSON (прокси, заглушка)
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
        """Альтернативные значения параметра photo для photos.saveWallPhoto.

        ВК принимает либо JSON-строку с данными загруженных фото (новый формат
        сервера загрузки), либо идентификатор фото из этого JSON (старый формат).
        Важно: идентификатор в современном формате содержит суффикс через
        двоеточие («6f26a6640b:w») — это часть идентификатора, отрезать её
        нельзя. Усечённое значение оставляем только последним запасным
        вариантом на случай старых ответов сервера.
        """
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
                out.append(full)                      # полный идентификатор («6f26a6640b:w»)
                if ":" in full:
                    out.append(full.split(":")[0])    # усечённый — запасной вариант
            if it.get("kid"):
                out.append(str(it["kid"]))
        return out

    @staticmethod
    def _first_item(items) -> dict:  # noqa: ANN001
        """Первый объект из ответа VK (список, либо объект с items/photo/doc)."""
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
        # Имя файла — ASCII с настоящим расширением: ВК определяет тип файла по
        # имени, а кириллица в multipart-имени ломает разбор формы на стороне ВК.
        filename = upload_filename(media, "photo.jpg")
        ctype = mimetypes.guess_type(filename)[0] or "image/jpeg"
        content = await read_media_bytes(media)

        # Загрузка идёт через отдельный сервер ВК, у которого периодически
        # рвётся соединение (502, обрывы, таймауты) — до трёх попыток, каждая
        # с новой ссылкой на загрузку (старая одноразовая). Важно ловить не
        # только ошибки VK (AdapterError), но и сетевые сбои httpx.
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

        # Сохраняем фото на стене: сначала новый формат (JSON с данными фото),
        # затем — запасные идентификаторы из него, если ВК JSON не принял.
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
            # ответ без идентификатора — берём его из загруженного фото
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
        except Exception as e:  # noqa: BLE001 — ответ мог прийти не-JSON
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
        except Exception as e:  # noqa: BLE001 — не-JSON ответ (прокси, заглушка, HTML)
            raise AdapterError(f"VK {method}: сервер вернул не-JSON ответ: {e}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("error_msg", "ошибка") if isinstance(err, dict) else str(err)
            raise AdapterError(f"VK {method}: {msg}")
        if not isinstance(data, dict) or "response" not in data:
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

                # Ссылку в ВК добавляем прямо в attachments: отдельного метода API
                # для вложения-ссылки нет, а VK сам разворачивает из URL превью.
                link = (st.get("link") or "").strip()
                if link:
                    attachments.append(link)
                message = (content.text or "").strip()

                if not message and not attachments:
                    raise AdapterError("VK: пустой пост (нет текста и вложений)")

                r = await client.post(f"{API}/wall.post", data={
                    "access_token": token, "v": V, "owner_id": owner,
                    "from_group": 1 if kind != "user" else 0,
                    "message": message[:4096],
                    "attachments": ",".join(attachments),
                })
                resp = self._unwrap("wall.post", r)
                post_id = resp["post_id"]
                if st.get("pin_post"):
                    await self.call("wall.pin", token, owner_id=owner, post_id=post_id)
                return PublishOutcome(True, external_id=f"{owner}_{post_id}",
                                      external_url=f"https://vk.com/wall{owner}_{post_id}")
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
