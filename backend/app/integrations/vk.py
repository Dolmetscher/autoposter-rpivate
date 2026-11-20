"""ВКонтакте: публикация в сообщество по ключу доступа сообщества (community token) —
посты на стене, фото, видео/клипы, истории, пин, автоудаление, источники кросспостинга.

Два ключа. ВК не даёт загружать файлы ключом сообщества: методы
photos.getWallUploadServer / photos.saveWallPhoto / video.save доступны только
с ключом пользователя (для сообщества они отвечают «Group authorization failed:
method is unavailable with group auth»). Поэтому:
  * access_token — ключ сообщества: wall.post, wall.pin, удаление, статистика;
  * user_token   — ключ пользователя: загрузка фото/видео/документов.
Фото при этом сохраняется в альбом сообщества (group_id), и вложение
photo-<group>_<id> публикуется ключом сообщества.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from io import BytesIO

import httpx

from ._mdfmt import md_to_plain
from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   SourceItem, is_gif_media, media_filename, read_media_bytes, upload_filename)

API = "https://api.vk.com/method"
V = "5.199"

log = logging.getLogger(__name__)

# Технические требования ВК к фотографиям (vk.com/faq18320, dev.vk.com/ru/api/upload):
# форматы GIF/JPG/JPEG/PNG, файл до 50 МБ, сумма сторон до 14000 px. Всё остальное
# (CMYK-JPEG из графических редакторов, WebP/HEIC/TIFF, гигантские разрешения)
# серверы загрузки pu.vk.com отвергают МОЛЧА: HTTP 200 с пустым/отсутствующим photo
# и нулём вложений — пост в итоге не выходит в ВК вообще, хотя ОК/Telegram/Facebook
# тот же файл принимают. Поэтому перед загрузкой фото приводим к требованиям ВК.
VK_MAX_PHOTO_BYTES = 50 * 1024 * 1024    # лимит ВК: размер файла
VK_PHOTO_BYTES_TARGET = 45 * 1024 * 1024 # пережать, если больше (запас до лимита)
VK_MAX_SIDE_SUM = 14000                  # лимит ВК: сумма сторон, px
VK_SIDE_SUM_TARGET = 13000               # ужмаем до этого значения (запас)

try:  # Pillow нужен только для подготовки фото к загрузке
    from PIL import Image as _PILImage, ImageOps as _PILImageOps
except ImportError:  # без Pillow файлы уходят как есть — как до этого правки
    _PILImage = None
    _PILImageOps = None


class VKAdapter(SocialAdapter):
    source_id = 1
    name = "ВКонтакте"
    capabilities = ("post", "photo", "video", "story", "clip", "channel")
    handles_link = True
    note = ("через API доступны: посты на стене, фото, видео, клипы, истории. "
            "Личная страница — ключом пользователя (пост в ленту профиля), "
            "сообщество — ключом сообщества или пользователя. "
            "Каналы ВКонтакте (vk.com/im/channels) пока не имеют API — пост в канал "
            "публикуйте вручную; подключим, как только VK добавит методы. "
            "Форматирование текста постов VK API не поддерживает — разметка "
            "Markdown аккуратно снимается, остаются чистый текст и ссылки")

    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0.0.0 Safari/537.36"}

    ERROR_HINTS = {
        5: "неверный ключ доступа — переподключите сообщество с новым токеном",
        15: "у ключа доступа нет нужного права. Для постов включите в настройках "
            "сообщества право «Публикация записей на стене», для фото — «Загрузка "
            "фотографий», и пересоздайте ключ доступа",
        27: "метод недоступен с авторизацией сообщества. Включите в настройках "
            "сообщества право «Загрузка фотографий» (Управление сообществом → "
            "Настройки → Работа с API → Права доступа) и пересоздайте ключ доступа",
        100: "неверный или отсутствующий параметр запроса",
        604: "нет доступа к фотографии — перезагрузите её в сообщество",
    }

    # ---------- служебные ----------
    async def call(self, method: str, token: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=60, headers=self.HEADERS) as client:
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
            {"name": "access_token", "label": "Ключ доступа (токен сообщества или пользователя)",
             "required": True},
            {"name": "user_token",
             "label": "Ключ доступа пользователя — только для загрузки фото и видео "
                      "(если публикуете ключом сообщества)",
             "required": False},
            {"name": "group_ref",
             "label": "Ссылка на группу или её ID (необязательно при использовании "
                      "токена пользователя)",
             "required": False},
            {"name": "name", "label": "Название аккаунта в списке (необязательно)",
             "required": False},
        ]

    @staticmethod
    def _normalize_group_ref(ref: str) -> str:
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
            raise AdapterError("VK: не указан ключ доступа (токен)")

        ref = self._normalize_group_ref(
            (extra or {}).get("group_ref") or creds.get("group_ref") or "")
        name_override = creds.get("name") or (extra or {}).get("name")

        if ref:
            group = self._first_group(await self.call(
                "groups.getById", token, group_id=ref, fields="photo_100"))
            return [PageInfo(str(group["id"]),
                             (name_override or group.get("name") or "Сообщество ВК").strip(),
                             group.get("photo_100", ""), kind="group")]

        try:
            user_data = await self.call("users.get", token, fields="photo_100")
            if user_data:
                u = user_data[0]
                pages = [PageInfo(str(u["id"]),
                                  f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(),
                                  u.get("photo_100", ""), kind="user")]
                admin_groups = await self.call("groups.get", token, filter="admin,editor",
                                               extended=1, fields="photo_100")
                items = admin_groups.get("items", []) if isinstance(admin_groups, dict) else []
                for g in items:
                    pages.append(PageInfo(str(g["id"]), g.get("name", "Группа").strip(),
                                          g.get("photo_100", ""), kind="group"))
                return pages
        except Exception:
            pass

        try:
            group = self._first_group(await self.call(
                "groups.getById", token, fields="photo_100"))
            return [PageInfo(str(group["id"]),
                             (name_override or group.get("name") or "Сообщество ВК").strip(),
                             group.get("photo_100", ""), kind="group")]
        except AdapterError as e:
            raise AdapterError(
                "VK: не удалось определить сообщество — укажите ссылку на группу "
                "или её ID, либо подключитесь ключом пользователя") from e

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

    @classmethod
    def _prepare_photo(cls, content: bytes, filename: str,
                       ctype: str) -> tuple[bytes, str, str]:
        """Приводит фото к требованиям ВК до загрузки (см. константы выше).

        Чистые JPEG/PNG в пределах лимитов не перекодируются (ноль потерь и
        лишней работы — как у постов, которые публиковались нормально).
        Перекодируются: CMYK/16-бит и прочие «неродные» режимы, WebP/HEIC/TIFF/BMP,
        слишком большие разрешения (сумма сторон > 14000 px) и файлы ближе
        к 50 МБ. Прозрачный PNG сохраняется PNG, остальное — JPEG (с постепенным
        снижением качества и даунскейлом, пока файл не влезет в лимит).
        Файл, который Pillow не разбирает, уходит как есть — пусть отвечает VK.
        """
        if (_PILImage is None or not content
                or ctype.startswith("video/") or filename.lower().endswith(".gif")):
            return content, filename, ctype
        try:
            img = _PILImage.open(BytesIO(content))
            if (img.format or "").upper() == "JPEG" and sum(img.size) > VK_MAX_SIDE_SUM:
                # draft-режим JPEG декодирует сразу уменьшенным — без гигантского RAM
                try:
                    scale = VK_SIDE_SUM_TARGET / float(img.width + img.height)
                    img.draft("RGB", (max(1, round(img.width * scale)),
                                       max(1, round(img.height * scale))))
                except Exception:
                    pass
            img.load()
        except Exception:
            return content, filename, ctype

        fmt = (img.format or "").upper()
        mode = img.mode
        has_alpha = (mode in ("RGBA", "LA", "PA")
                     or (mode == "P" and "transparency" in img.info))
        bad_mode = (mode in ("CMYK", "YCCK", "I", "F") or mode.startswith("I;16"))
        bad_format = fmt not in ("JPEG", "PNG")
        too_big = len(content) > VK_PHOTO_BYTES_TARGET
        too_long = img.width + img.height > VK_MAX_SIDE_SUM
        if not (bad_mode or bad_format or too_big or too_long):
            return content, filename, ctype

        try:
            return cls._reencode_photo(img, fmt, has_alpha, too_big, filename)
        except Exception:
            # перекодировка сорвалась (экзотический режим/битрый кадр) —
            # отдаём исходный файл: ВК может и принять, а текстовый фолбэк
            # публикации в любом случае не даст посту пропасть целиком
            log.warning("VK: не удалось подготовить фото к загрузке, файл уходит как есть",
                        exc_info=True)
            return content, filename, ctype

    @staticmethod
    def _reencode_photo(img, fmt: str, has_alpha: bool, too_big: bool,
                        filename: str) -> tuple[bytes, str, str]:
        try:
            img = _PILImageOps.exif_transpose(img) or img
        except Exception:
            pass
        if img.width + img.height > VK_MAX_SIDE_SUM:
            scale = VK_SIDE_SUM_TARGET / float(img.width + img.height)
            img = img.resize((max(1, round(img.width * scale)),
                              max(1, round(img.height * scale))), _PILImage.LANCZOS)

        stem = filename.rsplit(".", 1)[0] if "." in filename else filename

        def _png_bytes() -> bytes:
            buf = BytesIO()
            img.save(buf, "PNG", optimize=True)
            return buf.getvalue()

        def _jpeg_bytes(image, quality: int) -> bytes:  # noqa: ANN001
            buf = BytesIO()
            image.save(buf, "JPEG", quality=quality, optimize=True)
            return buf.getvalue()

        def _flatten(image):  # noqa: ANN001
            if image.mode == "RGBA":
                bg = _PILImage.new("RGB", image.size, (255, 255, 255))
                bg.paste(image, mask=image.split()[-1])
                return bg
            if image.mode not in ("RGB", "L"):
                return image.convert("RGB")
            return image

        # 1) Прозрачность (в любом исходном формате) или «родной» палитровый/
        #    серый PNG и при этом нет проблемы с размером — сохраняем PNG:
        #    прозрачность дороже, ВК её принимает. PNG не влез в лимит —
        #    уйдём ниже в JPEG (прозрачность ляжет на белый фон).
        keep_png = (has_alpha or (fmt == "PNG" and img.mode in ("L", "P", "1")))
        if keep_png and not too_big:
            data = _png_bytes()
            if len(data) <= VK_PHOTO_BYTES_TARGET:
                return data, f"{stem}.png", "image/png"
        # 2) JPEG: без альфы (или с альфой, расплющенной на белый), лесенка качества
        img = _flatten(img)
        data = b""
        for _ in range(8):
            for quality in (88, 72, 56):
                data = _jpeg_bytes(img, quality)
                if len(data) <= VK_PHOTO_BYTES_TARGET:
                    return data, f"{stem}.jpg", "image/jpeg"
            if img.width <= 64 and img.height <= 64:
                break  # меньше некуда — отдаём что получилось
            img = img.resize((max(64, round(img.width * 0.75)),
                              max(64, round(img.height * 0.75))), _PILImage.LANCZOS)
        return data, f"{stem}.jpg", "image/jpeg"

    async def _upload_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                            media) -> str:  # noqa: ANN001
        filename = upload_filename(media, "photo.jpg")
        ctype = mimetypes.guess_type(filename)[0] or "image/jpeg"
        content = await read_media_bytes(media)
        # ВК привередлив к форматам (см. _prepare_photo) — готовим файл до загрузки
        content, filename, ctype = self._prepare_photo(content, filename, ctype)

        errors: list[str] = []
        for attempt in range(3):
            try:
                return await self._wall_photo(client, token, group_id, content,
                                              filename, ctype)
            except AdapterError as e:
                errors.append(str(e))
                if "unavailable with group auth" in str(e) or "ошибка 27" in str(e):
                    break
            except httpx.HTTPError as e:
                errors.append(f"сетевая ошибка при загрузке фото ({e.__class__.__name__})")
        if group_id:
            # Фолбэк через альбом сообщества: берём существующий альбом
            # (photos.getAlbums), а если нет — создаём «Автопостер». Без album_id
            # photos.getUploadServer в альбом сообщества не работает — вызов с
            # пустым album_id всегда падает с ошибкой 100 ещё до загрузки файла.
            try:
                album_id = await self._group_album_id(client, token, group_id)
                return await self._album_photo(client, token, group_id, album_id,
                                               content, filename, ctype)
            except (AdapterError, httpx.HTTPError) as e:
                errors.append(str(e))
        try:
            return await self._messages_photo(client, token, group_id, content,
                                              filename, ctype)
        except (AdapterError, httpx.HTTPError) as e:
            errors.append(str(e))
        raise AdapterError("VK: не удалось загрузить фото — " + " | ".join(errors))

    async def _wall_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                           content: bytes, filename: str, ctype: str) -> str:
        params = {"access_token": token, "v": V}
        if group_id:
            params["group_id"] = int(abs(group_id))
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
                    "photo": (filename, content, ctype)}, headers=self.HEADERS)
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
        return self._photo_attachment(items, group_id, payload)

    async def _album_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                           album_id, content: bytes, filename: str, ctype: str) -> str:
        gid = int(abs(group_id or 0))
        params = {"access_token": token, "v": V, "group_id": gid}
        if album_id is not None:
            params["album_id"] = int(album_id)
        r = await client.post(f"{API}/photos.getUploadServer", data=params)
        info = self._unwrap("photos.getUploadServer", r)
        if not isinstance(info, dict):
            raise AdapterError(f"VK photos.getUploadServer: неожиданный ответ {info!r}")
        upload_url = info.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise AdapterError("VK: не вернулась ссылка на сервер загрузки фото")
        up = await client.post(upload_url, files={"photo": (filename, content, ctype)},
                               headers=self.HEADERS)
        payload = self._upload_payload("фото", up)
        photos_list = payload.get("photo") or payload.get("photos_list")
        if not photos_list:
            raise AdapterError(f"VK: сервер загрузки не принял фото — ответ: {payload!r}")
        target_album = info.get("album_id") or album_id or -14
        saved = await client.post(f"{API}/photos.save", data={
            "access_token": token, "v": V, "group_id": gid,
            "album_id": int(target_album), "server": payload.get("server", ""),
            "photos_list": photos_list, "hash": payload.get("hash", "")})
        items = self._unwrap("photos.save", saved)
        return self._photo_attachment(items, group_id, payload)

    async def _group_album_id(self, client: httpx.AsyncClient, token: str,
                              group_id: int | None) -> int:
        """ID альбома сообщества для фолбэка загрузки фото: существующий альбом
        (photos.getAlbums — и повторные посты не плодят новые альбомы), иначе
        новый «Автопостер» (photos.createAlbum — нужны права администратора)."""
        gid = int(abs(group_id or 0))
        try:
            r = await client.post(f"{API}/photos.getAlbums", data={
                "access_token": token, "v": V, "group_id": gid, "need_covers": 0})
            info = self._unwrap("photos.getAlbums", r)
            items = info.get("items") if isinstance(info, dict) else None
            if isinstance(items, list):
                for alb in items:
                    if isinstance(alb, dict) and alb.get("id"):
                        return int(alb["id"])
        except (AdapterError, httpx.HTTPError):
            pass  # альбомы не читаются — попробуем создать свой
        return await self._create_album(client, token, group_id)

    async def _create_album(self, client: httpx.AsyncClient, token: str,
                            group_id: int | None) -> int:
        gid = int(abs(group_id or 0))
        r = await client.post(f"{API}/photos.createAlbum", data={
            "access_token": token, "v": V, "group_id": gid,
            "title": "Автопостер", "description": "Альбом для фото автопостинга",
            "upload_by_admins_only": 1})
        info = self._unwrap("photos.createAlbum", r)
        album_id = info.get("album_id") if isinstance(info, dict) else info
        if not album_id:
            raise AdapterError(f"VK photos.createAlbum: не вернулся идентификатор альбома: {info!r}")
        return int(album_id)

    async def _messages_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                               content: bytes, filename: str, ctype: str) -> str:
        params = {"access_token": token, "v": V}
        if group_id:
            params["group_id"] = int(abs(group_id))
        r = await client.post(f"{API}/photos.getMessagesUploadServer", data=params)
        info = self._unwrap("photos.getMessagesUploadServer", r)
        if not isinstance(info, dict):
            raise AdapterError(f"VK photos.getMessagesUploadServer: неожиданный ответ {info!r}")
        upload_url = info.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise AdapterError("VK: не вернулась ссылка на сервер загрузки фото")
        up = await client.post(upload_url, files={"photo": (filename, content, ctype)},
                               headers=self.HEADERS)
        payload = self._upload_payload("фото", up)
        photos_list = payload.get("photo") or payload.get("photos_list")
        if not photos_list:
            raise AdapterError(f"VK: сервер загрузки не принял фото — ответ: {payload!r}")
        saved = await client.post(f"{API}/photos.saveMessagesPhoto", data={
            "access_token": token, "v": V, "server": payload.get("server", ""),
            "photos_list": photos_list, "hash": payload.get("hash", "")})
        items = self._unwrap("photos.saveMessagesPhoto", saved)
        return self._photo_attachment(items, group_id, payload)

    def _photo_attachment(self, items, group_id: int | None, payload: dict) -> str:
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
        # Параметр публикации в новости называется add_to_news (add_news VK
        # молча игнорирует), а после загрузки историю публикует stories.save
        # с upload_results — метода stories.post в API не существует.
        params = {"access_token": token, "v": V, "add_to_news": 1}
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
        # Сервер загрузки историй ждёт поле file (фото) / video_file,
        # а не «photo», как при загрузке фото на стену.
        field = "file" if media.type == "photo" else "video_file"
        try:
            fname = upload_filename(media, "media.jpg")
            raw = await read_media_bytes(media)
            if media.type == "photo":
                # у историй те же серверы загрузки и требования к файлам
                # (CMYK/экзотику они тоже молча отвергают) — готовим фото
                raw, fname, _ = self._prepare_photo(
                    raw, fname, mimetypes.guess_type(fname)[0] or "image/jpeg")
            up = await client.post(upload_url, files={field: (fname, raw)})
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
        # Ответ сервера загрузки историй: {"response": {"upload_result": "..."}}
        # — никаких items (items появляются только у stories.save).
        upload_result = ((data.get("response") or {}).get("upload_result", "")
                         if isinstance(data, dict) else "")
        if not upload_result:
            raise AdapterError(f"VK истории: загрузка не удалась: {data}")
        res = await client.post(f"{API}/stories.save", data={
            **params, "upload_results": upload_result})
        out = self._unwrap("stories.save", res)
        story = ((out.get("items") or [{}]) or [{}])[0]
        sid = story.get("id", "")
        owner = story.get("owner_id") or group_id or ""
        return PublishOutcome(True, external_id=f"story{sid}",
                              external_url=f"https://vk.com/story{owner}_{sid}")

    @staticmethod
    def _unwrap(method: str, r: httpx.Response):
        try:
            data = r.json()
        except Exception as e:
            raise AdapterError(f"VK {method}: сервер вернул не-JSON ответ: {e}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if isinstance(err, dict):
                msg = err.get("error_msg", "ошибка")
                hint = VKAdapter.ERROR_HINTS.get(err.get("error_code"))
                if hint:
                    msg = f"{msg} — {hint}"
            else:
                msg = str(err)
            raise AdapterError(f"VK {method}: {msg}")
        if not isinstance(data, dict) or "response" not in data:
            raise AdapterError(f"VK {method}: неожиданный ответ {data!r}")
        return data["response"]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        token = creds.get("access_token")
        if not token:
            raise AdapterError("VK: у аккаунта нет ключа доступа (токена) — переподключите "
                               "сообщество или пересоздайте ключ доступа в настройках VK")
        upload_token = creds.get("user_token") or token
        st = content.settings or {}
        owner = self.owner_id(page)
        group_id = None if owner > 0 else -owner
        kind = getattr(page, "page_kind", "group")

        try:
            async with httpx.AsyncClient(timeout=300, headers=self.HEADERS) as client:
                if st.get("publish_as_story"):
                    return await self._post_story(client, token, page, content)

                if st.get("video_only"):
                    # Режим «только видео»: ролики уходят в раздел видеозаписей
                    # сообщества (video.save с group_id), пост на стене не создаётся.
                    vids = [m for m in content.media if m.type == "video"]
                    if not vids:
                        raise AdapterError(
                            "VK: включён режим «только видео», но видеофайлов в посте нет")
                    first = ""
                    for media in vids:
                        att = await self._upload_video(client, upload_token, group_id, media)
                        info = att.replace("video", "", 1)
                        first = first or info
                    return PublishOutcome(
                        True,
                        external_id=f"{owner}_video{first.split('_', 1)[1]}",
                        external_url=f"https://vk.com/video{first}")

                media_attachments: list[str] = []
                upload_warnings: list[str] = []
                photo_error: Exception | None = None
                for media in content.media:
                    if st.get("publish_as_clips") and media.type == "video":
                        att = await self._upload_video(client, upload_token, group_id, media)
                        if not st.get("share_clips_to_feed"):
                            info = att.replace("video", "", 1)
                            return PublishOutcome(True, external_id=info,
                                                  external_url=f"https://vk.com/clips{info}")
                        media_attachments.append(att)
                    elif media.type == "photo":
                        if is_gif_media(media):
                            # GIF-анимация: загрузка через photos.* превращает её
                            # в статичный кадр, а документ с расширением .gif ВК
                            # сам показывает на стене анимированным.
                            media_attachments.append(
                                await self._upload_doc(client, upload_token, group_id, media))
                        else:
                            try:
                                media_attachments.append(
                                    await self._upload_photo(client, upload_token, group_id, media))
                            except Exception as e:  # noqa: BLE001
                                # Фото не прошло ни одним путём (серверы загрузки ВК
                                # молча отвергают CMYK/экзотику/перегабарит, файл мог
                                # быть стёрт автоочисткой) — не роняем весь пост:
                                # публикуем текст и остальные вложения, в журнал
                                # уходит предупреждение. Если кроме фото в посте
                                # ничего нет — ниже честно падаем с исходной ошибкой.
                                photo_error = e
                                upload_warnings.append(
                                    f"фото «{media_filename(media, 'без имени')}»: {e}")
                    elif media.type == "video":
                        media_attachments.append(
                            await self._upload_video(client, upload_token, group_id, media))
                for doc in content.documents:
                    media_attachments.append(
                        await self._upload_doc(client, upload_token, group_id, doc))

                attachments = list(media_attachments)
                # VK не рендерит Markdown в постах — снимаем разметку,
                # оставляя чистый текст (ссылки [текст](url) → «текст (url)»)
                message = md_to_plain((content.text or "").strip())
                link = (st.get("link") or "").strip()

                # Проверяем галочку превью (поддерживаем любые возможные имена переменной из формы)
                want_preview = True
                for k in ("attach_link_preview", "link_preview", "preview_link", "attach_preview"):
                    if k in st:
                        want_preview = bool(st[k])
                        break

                link_in_attachments = False
                if link:
                    # Если есть реальные фото/видео или пользователь снял галочку превью —
                    # добавляем ссылку текстом в сообщение (ВК запрещает ссылку в attachments вместе с файлами).
                    if media_attachments or not want_preview:
                        if link not in message:
                            message = f"{message}\n\n{link}".strip() if message else link
                    else:
                        # Если файлов нет и превью нужно — пробуем сделать нативную карточку-сниппет через attachments
                        attachments.append(link)
                        link_in_attachments = True

                if upload_warnings and not message and not attachments:
                    # кроме неотправившегося фото в посте ничего нет — публиковать
                    # нечего, честно роняем публикацию с исходной ошибкой загрузки
                    # (а не невнятным «пустой пост»)
                    raise photo_error
                if not message and not attachments:
                    raise AdapterError("VK: пустой пост (нет текста и вложений)")

                post_data = {
                    "access_token": token, "v": V, "owner_id": owner,
                    "from_group": 1 if kind != "user" else 0,
                    "message": message[:4096],
                    "attachments": ",".join(attachments),
                }

                try:
                    r = await client.post(f"{API}/wall.post", data=post_data)
                    resp = self._unwrap("wall.post", r)
                except AdapterError as e:
                    # Умный фоллбэк: если ВК забраковал карточку сайта (link_photo_sizing_rule)
                    # или ругнулся на формат ссылки — переносим ссылку в текст и публикуем гарантированно!
                    err_msg = str(e).lower()
                    if link_in_attachments and ("link_photo_sizing_rule" in err_msg or "attachments is invalid" in err_msg or "code=100" in err_msg):
                        fallback_msg = f"{message}\n\n{link}".strip() if (link not in message) else message
                        fallback_data = {
                            "access_token": token, "v": V, "owner_id": owner,
                            "from_group": 1 if kind != "user" else 0,
                            "message": fallback_msg[:4096],
                            "attachments": ",".join(media_attachments),
                        }
                        r = await client.post(f"{API}/wall.post", data=fallback_data)
                        resp = self._unwrap("wall.post (текстовый фоллбэк)", r)
                    else:
                        raise

                post_id = resp["post_id"]
                if st.get("pin_post"):
                    await self.call("wall.pin", token, owner_id=owner, post_id=post_id)
                outcome = PublishOutcome(True, external_id=f"{owner}_{post_id}",
                                         external_url=f"https://vk.com/wall{owner}_{post_id}")
                if upload_warnings:
                    outcome.warning = ("Пост опубликован без фото — "
                                       + "; ".join(upload_warnings))
                return outcome
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
        elif post_id.startswith("video"):
            # видеозапись из режима «только видео» — удаляется через video.delete
            await self.call("video.delete", token, owner_id=int(owner),
                            video_id=int(post_id[5:]))
        else:
            await self.call("wall.delete", token, owner_id=int(owner), post_id=int(post_id))
        return True

    # ---------- аналитика ----------
    async def fetch_stats(self, page, external_id: str) -> dict | None:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if not token or "_" not in external_id:
            return None
        # статистика берётся только у постов на стене (у видео/историй — другие API)
        if not external_id.split("_", 1)[1].isdigit():
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
