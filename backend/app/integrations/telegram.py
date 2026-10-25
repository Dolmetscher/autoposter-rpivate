"""Telegram: боты (каналы/группы) и аккаунты через Telethon (истории, диалоги).
Публичные каналы также поддерживаются как источник кросспостинга (t.me/s/...)."""
from __future__ import annotations

import asyncio
import io
import re

import httpx

from .base import (AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter,
                   SourceItem, media_filename, read_media_bytes)


def _named(data: bytes, name: str) -> io.BytesIO:
    bio = io.BytesIO(data)
    bio.name = name
    return bio


class TelegramAdapter(SocialAdapter):
    source_id = 9
    name = "Telegram"
    capabilities = ("post", "photo", "video", "document", "story", "channel", "chat")
    handles_link = True  # ссылка идёт в подпись, превью отключается флагом

    # ---------- подключение ----------
    def connect_requires(self) -> list[dict]:
        return [{"name": "mode", "label": "Режим: bot или account", "required": True},
                {"name": "bot_token", "label": "Токен бота (для режима bot)", "required": False},
                {"name": "chat_refs", "label": "Каналы через запятую (@name или -100...)",
                 "required": False}]

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        creds = account.credentials
        mode = creds.get("mode") or ("account" if creds.get("api_id") else "bot")
        if mode == "bot":
            return await self._pages_bot(creds, extra or {})
        return await self._pages_account(creds)

    async def _pages_bot(self, creds: dict, extra: dict) -> list[PageInfo]:
        token = creds.get("bot_token")
        if not token:
            raise AdapterError("Telegram: не указан bot_token")
        refs = (extra.get("chat_refs") or creds.get("chat_refs") or "")
        if isinstance(refs, str):
            refs = [r for r in re.split(r"[,\s]+", refs) if r]
        if not refs:
            raise AdapterError("Telegram: укажите каналы/группы (бот должен быть в них админом)")
        pages: list[PageInfo] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for ref in refs:
                r = await client.get(f"https://api.telegram.org/bot{token}/getChat",
                                     params={"chat_id": ref})
                data = r.json()
                if not data.get("ok"):
                    raise AdapterError(f"Telegram getChat {ref}: {data.get('description')}")
                chat = data["result"]
                cid = chat["id"]
                kind = "channel" if chat.get("type") == "channel" else "group"
                pages.append(PageInfo(str(cid), chat.get("title") or chat.get("username") or ref,
                                      "", kind=kind, can_post=True))
        return pages

    async def _pages_account(self, creds: dict) -> list[PageInfo]:
        api_id = int(creds.get("api_id") or 0)
        api_hash = creds.get("api_hash")
        session = creds.get("session")
        if not (api_id and api_hash and session):
            raise AdapterError("Telegram: нужны api_id, api_hash и session")

        def work() -> list[PageInfo]:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            out = []
            with TelegramClient(StringSession(session), api_id, api_hash) as c:
                for d in c.iter_dialogs():
                    if not (d.is_channel or d.is_group):
                        continue
                    ent = d.entity
                    can_post = bool(getattr(ent, "creator", False) or getattr(ent, "admin_rights", None)
                                    or (d.is_group and not getattr(ent, "broadcast", False)))
                    out.append(PageInfo(str(d.id), d.name, "", kind="channel" if d.is_channel else "group",
                                        can_post=can_post))
            return out

        return await asyncio.to_thread(work)

    # ---------- публикация ----------
    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        mode = creds.get("mode") or ("account" if creds.get("api_id") else "bot")
        if mode == "bot":
            return await self._publish_bot(page, content, creds)
        return await self._publish_account(page, content, creds)

    async def _publish_bot(self, page, content: PublishContent, creds: dict) -> PublishOutcome:
        import json as _json

        token = creds.get("bot_token")
        if not token:
            raise AdapterError("Telegram: не указан bot_token")
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("Telegram: истории доступны только для аккаунтов (подключите через Telethon)")
        chat = page.social_page_id
        base = f"https://api.telegram.org/bot{token}"
        link = (st.get('link') or '').strip()
        link_preview = st.get('link_preview', True)
        text = (content.text or "").strip()
        if link:
            head = text[:max(1024 - len(link) - 1, 0)].rstrip()
            full_text = f"{head}\n{link}" if head else link
        else:
            full_text = text

        # Подпись к медиа в Telegram ограничена 1024 символами. Если текст длиннее,
        # первая часть идёт подписью, а остаток публикуется отдельными сообщениями.
        caption = full_text[:1024]
        caption_rest = full_text[1024:]

        gallery = [(_named(await read_media_bytes(m), media_filename(m, "media")), m.type)
                   for m in content.media]
        docs = [(_named(await read_media_bytes(d), media_filename(d, "document")), "document")
                for d in content.documents]

        async with httpx.AsyncClient(timeout=300) as client:
            message_id = None
            if gallery:
                if len(gallery) == 1:
                    bio, mtype = gallery[0]
                    method = {"photo": "sendPhoto", "video": "sendVideo"}.get(mtype, "sendDocument")
                    field = {"photo": "photo", "video": "video"}.get(mtype, "document")
                    r = await client.post(f"{base}/{method}",
                                          data={"chat_id": chat, "caption": caption,
                                                "disable_web_page_preview": not link_preview},
                                          files={field: (bio.name, bio)})
                    message_id = self._tg_ok(method, r)["message_id"]
                else:
                    # Альбом Telegram поддерживает только фото и видео.
                    media_group = []
                    for i, (bio, mtype) in enumerate(gallery):
                        entry = {"type": mtype, "media": f"attach://file{i}"}
                        if i == 0 and caption:
                            entry["caption"] = caption
                        media_group.append(entry)
                    r = await client.post(f"{base}/sendMediaGroup",
                                          data={"chat_id": chat,
                                                "media": _json.dumps(media_group, ensure_ascii=False)},
                                          files={f"file{i}": (bio.name, bio)
                                                 for i, (bio, _) in enumerate(gallery)})
                    res = self._tg_ok("sendMediaGroup", r)
                    message_id = res[0]["message_id"]
                # Документы в альбом Telegram не пускает — шлём их отдельными сообщениями.
                for bio, _ in docs:
                    r = await client.post(f"{base}/sendDocument",
                                          data={"chat_id": chat},
                                          files={"document": (bio.name, bio)})
                    self._tg_ok("sendDocument", r)
            elif docs:
                for i, (bio, _) in enumerate(docs):
                    data = {"chat_id": chat}
                    if i == 0:
                        data["caption"] = caption
                        data["disable_web_page_preview"] = not link_preview
                    r = await client.post(f"{base}/sendDocument", data=data,
                                          files={"document": (bio.name, bio)})
                    res = self._tg_ok("sendDocument", r)
                    if i == 0:
                        message_id = res["message_id"]
            else:
                # Нет медиа — отправляем текст целиком, разбивая на 4096.
                if not full_text:
                    raise AdapterError("Telegram: пустой пост")
                message_id = await self._send_text_chunks(
                    client, f"https://api.telegram.org/bot{token}", chat, full_text,
                    link_preview=link_preview)
            # Остаток текста, не поместившийся в подпись — отдельными сообщениями.
            if caption_rest:
                rest_id = await self._send_text_chunks(
                    client, f"https://api.telegram.org/bot{token}", chat, caption_rest,
                    link_preview=False, reply_to=message_id)
                if message_id is None:
                    message_id = rest_id
            if st.get("pin_post") and message_id:
                await client.post(f"{base}/pinChatMessage",
                                  data={"chat_id": chat, "message_id": message_id})
        ext = f"{chat}_{message_id}"
        name = page.social_page_name or ""
        url = (f"https://t.me/{name.lstrip('@')}/{message_id}" if name.startswith("@")
               else f"https://t.me/c/{str(chat).replace('-100', '')}/{message_id}")
        return PublishOutcome(True, external_id=ext, external_url=url)

    async def _send_text_chunks(self, client: httpx.AsyncClient, base_url: str,
                                chat: int | str, text: str, link_preview: bool = True,
                                reply_to: int | None = None) -> int:
        """Отправляет длинный текст частями по 4096 символов (лимит Telegram на sendMessage).

        Возвращает ID последнего сообщения."""
        last_id = None
        chunk_size = 4096
        start = 0
        while start < len(text):
            chunk = text[start:start + chunk_size]
            data = {"chat_id": chat, "text": chunk,
                    "disable_web_page_preview": not link_preview}
            if reply_to is not None:
                data["reply_to_message_id"] = reply_to
            r = await client.post(f"{base_url}/sendMessage", json=data)
            res = self._tg_ok("sendMessage", r)
            last_id = res["message_id"]
            reply_to = last_id
            start += chunk_size
        return last_id

    async def _publish_account(self, page, content: PublishContent, creds: dict) -> PublishOutcome:
        api_id = int(creds.get("api_id") or 0)
        api_hash = creds.get("api_hash")
        session = creds.get("session")
        if not (api_id and api_hash and session):
            raise AdapterError("Telegram: аккаунт не настроен (api_id/api_hash/session)")

        files = [(_named(await read_media_bytes(m), media_filename(m, "media")), m.type)
                 for m in content.media]
        files += [(_named(await read_media_bytes(d), media_filename(d, "document")), "document")
                  for d in content.documents]

        def work():
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            st = content.settings or {}
            link = (st.get('link') or '').strip()
            link_preview = st.get('link_preview', True)
            text = (content.text or "").strip()
            if link:
                head = text[:max(1024 - len(link) - 1, 0)].rstrip()
                full_text = f"{head}\n{link}" if head else link
            else:
                full_text = text

            # Подпись к медиа в Telegram ограничена 1024 символами. Если текст длинше,
            # остаток публикуется отдельным сообщением (в reply к первому).
            caption = full_text[:1024]
            caption_rest = full_text[1024:]

            with TelegramClient(StringSession(session), api_id, api_hash) as c:
                entity = c.get_entity(int(page.social_page_id))
                if st.get("publish_as_story"):
                    if not files:
                        raise AdapterError("Telegram истории: нужен файл")
                    bio, ftype = files[0]
                    return _send_story(c, entity, bio, ftype, text), None

                msg_id = None

                if files:
                    # Есть медиа — отправляем с подписью (первые 1024)
                    res = c.send_file(entity, [bio for bio, _ in files],
                                      caption=caption or None)
                    if isinstance(res, list):
                        msg_id = res[0].id if res else None
                    else:
                        msg_id = getattr(res, "id", None)

                    # Остаток подписи — отдельным сообщением в reply
                    if caption_rest and msg_id:
                        c.send_message(entity, caption_rest,
                                       link_preview=False,
                                       reply_to=msg_id)
                else:
                    # Нет медиа — отправляем текст целиком, разбивая на 4096
                    if not full_text:
                        raise AdapterError("Telegram: пустой пост")
                    msg_id = None
                    start = 0
                    chunk_size = 4096
                    while start < len(full_text):
                        chunk = full_text[start:start + chunk_size]
                        msg = c.send_message(entity, chunk,
                                             link_preview=link_preview,
                                             reply_to=msg_id)
                        if msg_id is None:
                            msg_id = msg.id
                        start += chunk_size

                if st.get("pin_post") and msg_id:
                    try:
                        c.pin_message(entity, msg_id)
                    except Exception:
                        pass
                return msg_id, content.text

        msg_id, _ = await asyncio.to_thread(work)
        if msg_id is None:
            return PublishOutcome(True, external_id=f"{page.social_page_id}_story",
                                  external_url="")
        return PublishOutcome(True, external_id=f"{page.social_page_id}_{msg_id}",
                              external_url=f"https://t.me/c/{str(page.social_page_id).replace('-100', '')}/{msg_id}")

    @staticmethod
    def _tg_ok(method: str, r: httpx.Response):
        data = r.json()
        if not data.get("ok"):
            raise AdapterError(f"Telegram {method}: {data.get('description', 'ошибка')}")
        return data["result"]

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        creds = page.account.credentials
        mode = creds.get("mode") or ("account" if creds.get("api_id") else "bot")
        chat, _, mid = external_id.rpartition("_")
        try:
            mid = int(mid)
        except ValueError:
            return False
        if mode == "bot" and creds.get("bot_token"):
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(f"https://api.telegram.org/bot{creds['bot_token']}/deleteMessages",
                                      json={"chat_id": chat, "message_ids": [mid]})
                return bool(r.json().get("ok"))
        if mode == "account":
            api_id = creds.get("api_id")
            api_hash = creds.get("api_hash")
            session = creds.get("session")
            if not (api_id and api_hash and session):
                raise AdapterError("Telegram: аккаунт не настроен (api_id/api_hash/session)")

            def work():
                from telethon import TelegramClient
                from telethon.sessions import StringSession

                with TelegramClient(StringSession(session), int(api_id), api_hash) as c:
                    c.delete_messages(int(chat), [mid])
                return True
            return await asyncio.to_thread(work)
        return False

    # ---------- кросспостинг: публичные каналы через t.me/s/ ----------
    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:
        ref = source_ref.strip().lstrip("@")
        ref = ref.replace("https://t.me/", "").rstrip("/")
        url = f"https://t.me/s/{ref}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            html = r.text
        posts_html = re.split(r'<div class="tgme_widget_message_wrap', html)[1:]
        items: list[SourceItem] = []
        for block in posts_html[:limit]:
            m = re.search(r'data-post="([^"]+)"', block)
            if not m:
                continue
            ext_id = m.group(1)
            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
            text = re.sub(r"<[^>]+>", "", text_match.group(1)).strip() if text_match else ""
            text = text.replace("<br/>", "\n").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            media: list[dict] = []
            for pic in re.findall(r"background-image:url\('([^']+)'", block):
                media.append({"url": pic.replace("&amp;", "&"), "kind": "photo"})
            for vid in re.findall(r"<video[^>]*>.*?<source[^>]*src=\"([^\"]+)\"", block, re.S):
                media.append({"url": vid.replace("&amp;", "&"), "kind": "video"})
            items.append(SourceItem(external_id=ext_id, text=text, media=media,
                                    url=f"https://t.me/{ext_id}"))
        return items


def _send_story(client, entity, bio: io.BytesIO, ftype: str, caption: str):  # noqa: ANN001
    """История в Telegram через Telethon (raw API)."""
    import os

    from telethon.tl import types
    from telethon.tl.functions.stories import SendStoryRequest

    uploaded = client.upload_file(bio, file_name=bio.name)
    if ftype == "photo":
        media = types.InputMediaUploadedPhoto(file=uploaded)
    else:
        media = types.InputMediaUploadedDocument(
            file=uploaded, mime_type="video/mp4",
            attributes=[types.DocumentAttributeVideo(duration=0, w=720, h=1280)])
    result = client(SendStoryRequest(
        peer=entity, media=media, random_id=int.from_bytes(os.urandom(8), "big"),
        privacy=[types.InputPrivacyValueAllowAllRequest()], message=caption or None))
    # id=0 означает, что история не создана — возвращаем None, чтобы вызывающий
    # не построил несуществующую ссылку.
    story_id = getattr(result, "id", 0)
    return story_id or None
