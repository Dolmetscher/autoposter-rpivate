"""Дзен — публикация постов через editor-api (экспериментально) + RSS-фид.

Два пути доставки контента:

1. **Экспериментальный путь «пост»** (мгновенно): короткий текст (≤4096
   символов) с фотографиями (без видео) публикуется как «пост» (короткая
   запись в ленте подписчиков) через неофициальный editor-api — эмуляция
   запросов веб-редактора Дзена. Нужны cookie сессии и ссылка на канал.
   API у Дзена нет и может измениться без предупреждения; при ошибке
   сессии публикация автоматически уходит в RSS (пунктом 2).
2. **RSS-фид проекта** (официальная «трансляция»): длинные тексты и
   посты с картинками. Проект отдаёт публичный фид
   (/api/public/rss-out/...), канал Дзена забирает материалы сам —
   появляются статьёй, обычно в пределах часа.

Цепочка editor-api (снята с реального редактора, проверена живыми тестами):
  GET  /profile/editor/{slug}                     → csrfToken + publisherId (из HTML)
  POST /editor-api/v2/add-publication             → id черновика (publicationType=brief)
  POST /editor-api/v2/add-image                   → id картинки (multipart, поле image)
  POST /editor-api/v2/update-publication-content-and-publish → публикация
Антибот-поля fp (f/pgrdt/pgrd) сервером не проверяются — не отправляем.
Картинка в contentState — элемент галереи {"imageId": {"id": "<id>"}}
(imageId — объект, строка даёт illegal-argument). Файл грузится multipart
(add-image, поле image); если сервер отказал (антиабуз/сбой) —
add-image-from-url: сервер сам скачивает картинку по публичному URL.
"""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
import string

import httpx

from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome,
                   SocialAdapter, read_media_bytes, upload_filename)

logger = logging.getLogger(__name__)

EDITOR_BASE = "https://dzen.ru/editor-api/v2"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) "
      "Gecko/20100101 Firefox/159.0")
# Максимальная длина «поста» (короткой записи); длиннее — статья через RSS.
BRIEF_MAX = 4096
# Пути на dzen.ru, которые не могут быть slug канала.
_RESERVED = {"id", "a", "post", "video", "shorts", "media", "profile",
             "editor", "my", "story", "stories"}
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
_CSRF_RE = re.compile(r'"csrfToken":"([0-9a-f]{40}:\d{13})"')
_PUBID_RE = re.compile(r'"publisherId":"([0-9a-f]{24})"')
_NAME_RE = re.compile(r'"id":"[0-9a-f]{24}","name":"([^"]{1,80})"')


def _client_rid() -> str:
    """Идентификатор запроса, как его генерирует веб-редактор (14 hex)."""
    return secrets.token_hex(7)


def _block_key() -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))


def text_to_content_state(text: str, image_ids: list[str] | None = None) -> str:
    """Текст поста (+ id картинок) → contentState редактора (дважды закодированный JSON).

    Внутри — состояние Draft.js (каждый абзац это блок "unstyled") и
    галерея: элемент картинки — {"imageId": {"id": "<id>"}} (снято с
    бандла редактора и подтверждено живой публикацией).
    """
    lines = [line.rstrip() for line in (text or "").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        lines = [""]
    blocks = [{"data": {}, "depth": 0, "key": _block_key(), "type": "unstyled",
               "text": line, "entityRanges": [], "inlineStyleRanges": []}
              for line in lines]
    content = json.dumps({"blocks": blocks, "entityMap": {}},
                         ensure_ascii=False, separators=(",", ":"))
    state = {"gallery": {"ratio": "ratio-4x3", "baseRatio": "ratio-4x3",
                         "items": [{"imageId": {"id": i}} for i in (image_ids or [])]},
             "content": content}
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def image_mime(filename: str) -> str:
    """MIME-тип картинки по расширению (для multipart-загрузки add-image)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")


def slug_from_link(link: str) -> str:
    """Slug канала из ссылки (dzen.ru/{slug}, dzen.ru/profile/editor/{slug}) или слова."""
    raw = (link or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^https?://", "", raw, flags=re.I)
    raw = re.sub(r"^(www\.)?(dzen\.ru|zen\.yandex\.ru)", "", raw, flags=re.I)
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in raw.split("/") if p]
    if not parts:
        return ""
    if parts[0] == "profile" and len(parts) >= 3 and parts[1] == "editor":
        parts = parts[2:]
    elif parts[0] in _RESERVED:
        return ""
    slug = parts[0]
    return slug if _SLUG_RE.match(slug) else ""


class DzenAdapter(SocialAdapter):
    source_id = 20
    name = "Дзен"
    capabilities = ("post", "photo")
    # контент уходит через RSS-фид проекта (publish.py подставит rss_feed_url)
    rss_out = True
    note = ("короткие тексты (до 4096 символов) с фотографиями публикуются постом "
            "сразу — экспериментально, нужны cookie сессии и ссылка на канал; "
            "длинные тексты, видео и документы — через RSS-фид проекта "
            "(официальная «трансляция», появляются статьёй)")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "session_cookie", "label": "Cookie сессии Дзена (полная строка)",
             "required": False},
            {"name": "channel_link",
             "label": "Ссылка на канал (напр. https://dzen.ru/oldfag_tv) — для мгновенных постов",
             "required": False},
        ]

    # ---------- подключение ----------

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:  # noqa: ANN001
        creds = account.credentials or {}
        cookie = creds.get("session_cookie") or ""
        slug = slug_from_link(creds.get("channel_link") or "")
        if not cookie:
            # Без cookie фид всё равно работает: отдаём одну синтетическую
            # страницу — её достаточно, чтобы добавить Дзен в проект.
            return [PageInfo("dzen", "Канал Дзен (RSS)", "", kind="channel")]
        if not slug:
            raise AdapterError(
                "Дзен: укажите ссылку на канал (например https://dzen.ru/oldfag_tv) — "
                "она нужна для мгновенных постов и списка каналов")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            name, publisher_id = await self._editor_page(client, cookie, slug)
        if not publisher_id:
            raise AdapterError("Дзен: сессия недействительна (экспериментальная интеграция) "
                               "— проверьте cookie")
        return [PageInfo(slug, name or "Канал Дзен", "", kind="channel")]

    # ---------- публикация ----------

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = getattr(getattr(page, "account", None), "credentials", None) or {}
        cookie = creds.get("session_cookie") or ""
        text = (content.text or "").strip()
        media = list(getattr(content, "media", None) or [])
        photos = [m for m in media if getattr(m, "type", "") == "photo"]
        has_other_media = len(photos) != len(media)

        # «Пост»: короткий текст + фото (видео/документы — только RSS-фид)
        if cookie and not has_other_media and len(text) <= BRIEF_MAX \
                and (text or photos):
            urls: list[str] = []
            for m in photos:
                try:
                    urls.append(content.public_url(m.file_path)
                                if getattr(m, "file_path", "") else "")
                except Exception:  # noqa: BLE001 — публичный URL не обязателен
                    urls.append("")
            try:
                return await self._publish_brief(page, creds, text, photos, urls)
            except AdapterError as e:
                # Экспериментальный путь не удался (обычно протухла сессия) —
                # не теряем публикацию: отправляем текст статьёй через RSS.
                logger.warning("Дзен: пост через editor-api не удался (%s); "
                               "фоллбэк на RSS-фид", e)

        feed_url = (content.settings or {}).get("rss_feed_url", "")
        if not feed_url:
            raise AdapterError(
                "Дзен: публикация идёт через RSS-фид проекта — добавьте Дзен-страницу "
                "в проект и укажите адрес фида в настройках трансляции канала Дзена")
        # Пост передан в фид — для Дзена это и есть публикация:
        # канал заберёт материал из трансляции сам.
        return PublishOutcome(ok=True, external_id=f"rss-{page.social_page_id}",
                              external_url=feed_url)

    # ---------- экспериментальный путь: «пост» через editor-api ----------

    async def _editor_page(self, client: httpx.AsyncClient, cookie: str, slug: str):
        """Страница редактора → (название канала, publisherId); csrf парсят отдельно."""
        r = await client.get(f"https://dzen.ru/profile/editor/{slug}",
                             headers=self._browse_headers(cookie))
        if r.status_code != 200:
            return "", ""
        pub = _PUBID_RE.search(r.text)
        name = _NAME_RE.search(r.text)
        return (name.group(1) if name else ""), (pub.group(1) if pub else "")

    @staticmethod
    def _browse_headers(cookie: str) -> dict:
        return {"User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cookie": cookie}

    @staticmethod
    def _api_headers(cookie: str, csrf: str, referer: str,
                     json_body: bool = True) -> dict:
        headers = {"User-Agent": UA,
                   "Accept": "application/json",
                   "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                   "X-Csrf-Token": csrf,
                   "Origin": "https://dzen.ru",
                   "Referer": referer,
                   "Sec-Fetch-Dest": "empty",
                   "Sec-Fetch-Mode": "cors",
                   "Sec-Fetch-Site": "same-origin",
                   "Cookie": cookie}
        # для multipart (add-image) Content-Type ставит httpx сам — вместе с boundary
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _brief_slug(self, page, creds: dict) -> str:  # noqa: ANN001
        """Slug для editor-api: идентификатор страницы, иначе ссылка из аккаунта."""
        candidate = getattr(page, "social_page_id", "") or ""
        if _SLUG_RE.match(candidate) and candidate.lower() != "dzen":
            return candidate
        return slug_from_link(creds.get("channel_link") or "")

    async def _publish_brief(self, page, creds: dict, text: str,
                             photos: list | None = None,
                             public_urls: list[str] | None = None) -> PublishOutcome:  # noqa: ANN001
        cookie = creds.get("session_cookie") or ""
        slug = self._brief_slug(page, creds)
        if not slug:
            raise AdapterError(
                "Дзен: не определён адрес канала — при подключении укажите "
                "ссылку на канал (например https://dzen.ru/oldfag_tv)")
        editor_url = f"https://dzen.ru/profile/editor/{slug}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(editor_url, headers=self._browse_headers(cookie))
            if r.status_code != 200:
                raise AdapterError("Дзен: сессия недействительна (страница редактора "
                                   "не открылась) — переподключите аккаунт с новыми cookie")
            csrf = _CSRF_RE.search(r.text)
            pub = _PUBID_RE.search(r.text)
            if not csrf or not pub:
                raise AdapterError("Дзен: не удалось получить csrf-токен — сессия "
                                   "протухла, переподключите аккаунт с новыми cookie")
            csrf, publisher_id = csrf.group(1), pub.group(1)

            headers = self._api_headers(cookie, csrf, f"{editor_url}?briefEditorPublicationId=draft")
            r2 = await client.post(
                f"{EDITOR_BASE}/add-publication?publisherId={publisher_id}&clientRid={_client_rid()}",
                headers=headers,
                json={"title": "", "publisherId": publisher_id, "publicationType": "brief"})
            if r2.status_code != 200:
                raise AdapterError(f"Дзен: создание черновика не удалось "
                                   f"(HTTP {r2.status_code})")
            try:
                pub_id = (r2.json() or {}).get("id") or ""
            except ValueError as e:
                raise AdapterError("Дзен: неожиданный ответ при создании черновика") from e
            if not pub_id:
                raise AdapterError("Дзен: сервер не вернул идентификатор черновика")

            # Картинки: multipart-загрузка в черновик → id → элемент галереи.
            # Если multipart отклонён (антиабуз/сбой) — сервер сам скачивает
            # картинку по публичному URL (add-image-from-url).
            image_ids: list[str] = []
            for idx, photo in enumerate(photos or []):
                img_id = ""
                try:
                    data = await read_media_bytes(photo)
                except Exception as e:
                    raise AdapterError(f"Дзен: не удалось прочитать файл "
                                       f"картинки: {e}") from e
                fname = upload_filename(photo, default="photo.jpg")
                r_img = await client.post(
                    f"{EDITOR_BASE}/add-image"
                    f"?publicationId={pub_id}&publisherId={publisher_id}"
                    f"&clientRid={_client_rid()}",
                    headers=self._api_headers(
                        cookie, csrf,
                        f"{editor_url}?briefEditorPublicationId={pub_id}",
                        json_body=False),
                    files={"image": (fname, data, image_mime(fname))})
                if r_img.status_code == 200:
                    try:
                        img_id = (r_img.json() or {}).get("id") or ""
                    except ValueError:
                        img_id = ""
                url = (public_urls or [""] * len(photos or ()))[idx] \
                    if idx < len(public_urls or []) else ""
                if not img_id and url:
                    r_url = await client.post(
                        f"{EDITOR_BASE}/add-image-from-url"
                        f"?publicationId={pub_id}&publisherId={publisher_id}"
                        f"&url={url}&clientRid={_client_rid()}",
                        headers=self._api_headers(
                            cookie, csrf,
                            f"{editor_url}?briefEditorPublicationId={pub_id}"))
                    if r_url.status_code == 200:
                        try:
                            img_id = (r_url.json() or {}).get("id") or ""
                        except ValueError:
                            img_id = ""
                if not img_id:
                    detail = r_img.status_code if r_img.status_code != 200 \
                        else "нет id в ответе"
                    raise AdapterError(f"Дзен: загрузка картинки не удалась "
                                       f"({detail})")
                image_ids.append(img_id)

            headers = self._api_headers(
                cookie, csrf,
                f"{editor_url}/publications?state=published&briefEditorPublicationId={pub_id}")
            r3 = await client.post(
                f"{EDITOR_BASE}/update-publication-content-and-publish"
                f"?publisherId={publisher_id}&clientRid={_client_rid()}",
                headers=headers,
                json={
                    "id": pub_id,
                    "preview": {"title": "", "snippet": "",
                                "galleryPreviewImages": [], "totalSlidesCount": 0},
                    "snippetFrozen": False,
                    "hasNativeAds": False,
                    "commentsFlagState": "on",
                    "delayedPublicationFlagState": "off",
                    "visibleComments": "subscribe-visible",
                    "premiumTariffs": [],
                    "articleContent": {
                        "contentState": text_to_content_state(text, image_ids)},
                    "darkPost": False,
                    "tagsInput": {"tags": [], "detectedTagsShown": False},
                })
            if r3.status_code != 200:
                raise AdapterError(f"Дзен: публикация поста не удалась (HTTP {r3.status_code})")
            external_url = self._published_url(r3, pub_id)
        return PublishOutcome(ok=True, external_id=f"brief-{pub_id}",
                              external_url=external_url)

    @staticmethod
    def _published_url(r: httpx.Response, pub_id: str) -> str:
        """Ссылка на опубликованный пост.

        Сервер обычно не возвращает ссылку, но формат проверен живым тестом:
        https://dzen.ru/post/{id} отдаёт 200 для опубликованного поста
        и 404 для несуществующего идентификатора.
        """
        try:
            data = r.json()
        except ValueError:
            data = {}
        if isinstance(data, dict):
            for key in ("link", "url", "publicationUrl", "fullUrl"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
        return f"https://dzen.ru/post/{pub_id}"

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError("Дзен: удаление из фида недоступно — уберите пост из проекта "
                           "или отключите трансляцию в Дзене")
