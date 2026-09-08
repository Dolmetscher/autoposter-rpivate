"""Одноклассники: медитопики (посты) с фото/видео, удаление, источники —
через официальный REST API с подписью запросов (md5)."""
from __future__ import annotations

import hashlib
import io
import json

import httpx

from ..config import settings
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, read_media_bytes


class OKAdapter(SocialAdapter):
    source_id = 2
    name = "Одноклассники"
    capabilities = ("post", "photo", "video")

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
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get("https://api.ok.ru/fb.do", params=params)
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
        pages = [PageInfo(str(me["uid"]), me.get("name", ""), me.get("pic_1", ""), kind="user")]
        groups = await self.call("group.getUserGroupsV2", creds, {"count": 50})
        ids = [g["groupId"] for g in groups.get("groups", []) or []]
        if ids:
            infos = await self.call("group.getInfo", creds,
                                    {"uids": ",".join(ids), "fields": "name,picAvatar,status"})
            for gid in ids:
                info = infos.get(gid, {})
                pages.append(PageInfo(gid, info.get("name", ""), info.get("picAvatar", ""),
                                      kind="group", can_post=info.get("status") != "BLOCKED"))
        return pages

    # ---------- публикация ----------
    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = page.account.credentials
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("OK: истории через API не поддерживаются")
        gid = None if getattr(page, "page_kind", "group") == "user" else page.social_page_id

        attachments: list[dict] = []
        if content.text:
            attachments.append({"type": "text", "text": content.text})

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
                bio.name = m.name
                files[f"pic{i + 1}"] = (m.name, bio)
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    r = await client.post(upload_url, files=files)
                data = r.json()
            finally:
                for _, bio in files.values():
                    bio.close()
            photo_list = []
            for pid, pdata in (data.get("photos") or {}).items():
                token = (pdata.get("token") or pid)
                photo_list.append({"id": token})
            if photo_list:
                attachments.append({"type": "photo", "list": photo_list})

        videos = [m for m in content.media if m.type == "video"]
        if videos:
            params = {"file_name": videos[0].name}
            if gid:
                params["gid"] = gid
            up = await self.call("video.getUploadUrl", creds, params)
            bio = io.BytesIO(await read_media_bytes(videos[0]))
            bio.name = videos[0].name
            async with httpx.AsyncClient(timeout=600) as client:
                r = await client.post(up["upload_url"], files={"file": (videos[0].name, bio)})
            vid = r.json().get("video_id") or ""
            if vid:
                attachments.append({"type": "video", "list": [{"id": vid}]})
            if len(videos) > 1:
                raise AdapterError("OK: в один пост загружается только одно видео")

        if not attachments:
            raise AdapterError("OK: пустой пост")
        topic = await self.call("mediatopic.post", creds,
                                {"type": "GROUP_THEME" if gid else "USER",
                                 **({"gid": gid} if gid else {}),
                                 "attachment": json.dumps(attachments, ensure_ascii=False)})
        topic_id = str(topic)
        url = f"https://ok.ru/{('group/' + gid) if gid else 'profile/' + str(page.social_page_id)}/{topic_id}"
        return PublishOutcome(True, external_id=topic_id, external_url=url)

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        await self.call("mediatopic.delete", page.account.credentials,
                        {"topic_id": external_id})
        return True
