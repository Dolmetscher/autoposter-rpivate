"""ВКонтакте: публикация в сообщество по ключу доступа сообщества (community token) —
посты на стене, фото, видео/клипы, истории, пин, автоудаление, источники кросспостинга."""
from __future__ import annotations

import re

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, SourceItem, read_media_bytes

API = "https://api.vk.com/method"
V = "5.199"


class VKAdapter(SocialAdapter):
    source_id = 1
    name = "ВКонтакте"
    capabilities = ("post", "photo", "video", "story", "clip", "channel")

    # ---------- служебные ----------
    async def call(self, method: str, token: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API}/{method}",
                                  data={**params, "access_token": token, "v": V})
        data = r.json()
        if "error" in data:
            raise AdapterError(f"VK {method}: {data['error'].get('error_msg', 'ошибка')}")
        return data["response"]

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
            # у токена сообщества groups.getById без group_id возвращает само сообщество
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
    async def _upload_photo(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                            media) -> str:  # noqa: ANN001
        params = {"access_token": token, "v": V}
        if group_id:
            params["group_id"] = group_id
        r = await client.post(f"{API}/photos.getWallUploadServer", data=params)
        url = self._unwrap("photos.getWallUploadServer", r)
        up = await client.post(url, files={"photo": (media.name, await read_media_bytes(media))})
        saved = await client.post(f"{API}/photos.saveWallPhoto", data={
            **params, **{k: str(v) for k, v in up.json().items()
                         if k in ("server", "photo", "hash")}})
        items = self._unwrap("photos.saveWallPhoto", saved)
        it = items[0]
        return f"photo{it['owner_id']}_{it['id']}"

    async def _upload_video(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                            media) -> str:  # noqa: ANN001
        params = {"access_token": token, "v": V, "name": (media.title or media.name)[:100],
                  "description": media.description or "", "wallpost": 0}
        if group_id:
            params["group_id"] = group_id
        r = await client.post(f"{API}/video.save", data=params)
        info = self._unwrap("video.save", r)
        await client.post(info["upload_url"],
                          files={"video_file": (media.name, await read_media_bytes(media))})
        return f"video{info['owner_id']}_{info['video_id']}"

    async def _upload_doc(self, client: httpx.AsyncClient, token: str, group_id: int | None,
                          media) -> str:  # noqa: ANN001
        params = {"access_token": token, "v": V, "type": "doc", "title": media.name}
        if group_id:
            params["group_id"] = group_id
        r = await client.post(f"{API}/docs.getWallUploadServer", data=params)
        url = self._unwrap("docs.getWallUploadServer", r)
        up = await client.post(url, files={"file": (media.name, await read_media_bytes(media))})
        saved = await client.post(f"{API}/docs.save", data={
            **params, **{k: str(v) for k, v in up.json().items()
                         if k in ("file", "title", "tags")}})
        items = self._unwrap("docs.save", saved)
        if isinstance(items, dict):
            items = items.get("doc") or items.get("items") or [items]
        it = items[0] if isinstance(items, list) else items
        return f"doc{it['owner_id']}_{it['id']}"

    async def _post_story(self, client: httpx.AsyncClient, token: str, page,
                          content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        group_id = None if getattr(page, "page_kind", "group") == "user" \
            else int(re.sub(r"\D", "", str(page.social_page_id)))
        params = {"access_token": token, "v": V, "add_news": 1}
        if group_id:
            params["group_id"] = group_id
        if not content.media:
            raise AdapterError("VK истории: нужен файл фото или видео")
        media = content.media[0]
        method = "stories.getPhotoUploadServer" if media.type == "photo" else "stories.getVideoUploadServer"
        r = await client.post(f"{API}/{method}", data=params)
        url = self._unwrap(method, r)
        field = "photo" if media.type == "photo" else "video_file"
        up = await client.post(url, files={field: (media.name, await read_media_bytes(media))})
        data = up.json()
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

    async def _add_link(self, client: httpx.AsyncClient, token: str, owner_id: int, url: str) -> str:
        """Add link via VK links.add and return attachment string."""
        r = await client.post(f"{API}/links.add", data={
            "access_token": token, "v": V, "owner_id": owner_id, "url": url
        })
        data = self._unwrap("links.add", r)
        return f"link{data['owner_id']}_{data['post_id']}"

    @staticmethod
    def _unwrap(method: str, r: httpx.Response):
        data = r.json()
        if "error" in data:
            raise AdapterError(f"VK {method}: {data['error'].get('error_msg', 'ошибка')}")
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

            link = st.get("link", "")
            if link:
                try:
                    attachments.append(await self._add_link(client, token, owner, link))
                except AdapterError as e:
                    raise AdapterError(f"VK: ссылка не была добавлена: {e}")

            if not content.text and not attachments:
                raise AdapterError("VK: пустой пост (нет текста и вложений)")

            r = await client.post(f"{API}/wall.post", data={
                "access_token": token, "v": V, "owner_id": owner,
                "from_group": 1 if kind != "user" else 0,
                "message": content.text or "",
                "attachments": ",".join(attachments),
            })
            resp = self._unwrap("wall.post", r)
            post_id = resp["post_id"]
            if st.get("pin_post"):
                await self.call("wall.pin", token, owner_id=owner, post_id=post_id)
            return PublishOutcome(True, external_id=f"{owner}_{post_id}",
                                  external_url=f"https://vk.com/wall{owner}_{post_id}")

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
                               filter="owner" if owner < 0 else "owner")
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
