"""Тамблер: текстовые и фото-посты в блог через API v2 (OAuth 1.0a).
Ключи: api.tumblr.com/console → consumer key/secret + OAuth token/secret."""
from __future__ import annotations

from ._oauth1 import OAuth1Client, oauth1_header
from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, read_media_bytes

API = "https://api.tumblr.com/v2"


class TumblrAdapter(SocialAdapter):
    source_id = 19
    name = "Тамблер"
    capabilities = ("post", "photo")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "api_key", "label": "Consumer Key", "required": True},
            {"name": "api_secret", "label": "Consumer Secret", "required": True},
            {"name": "access_token", "label": "OAuth Token", "required": True},
            {"name": "access_token_secret", "label": "OAuth Token Secret", "required": True},
        ]

    @staticmethod
    def _creds(account) -> dict:  # noqa: ANN001
        creds = account.credentials
        if not all(creds.get(k) for k in ("api_key", "api_secret",
                                          "access_token", "access_token_secret")):
            raise AdapterError("Tumblr: нужны consumer key/secret и OAuth token/secret")
        return creds

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        client = OAuth1Client(self._creds(account))
        r = await client.request("GET", f"{API}/user/info")
        data = r.json()
        if r.status_code >= 300 or "response" not in data:
            raise AdapterError(f"Tumblr user/info: "
                               f"{data.get('meta', {}).get('msg', r.text[:200])}")
        blogs = data["response"].get("user", {}).get("blogs", [])
        return [PageInfo(b["name"], b.get("title") or b["name"],
                         (b.get("avatar") or [{}])[0].get("url", ""), kind="user")
                for b in blogs]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        creds = self._creds(page.account)
        st = content.settings or {}
        if st.get("publish_as_story"):
            raise AdapterError("Tumblr: историй нет — это пост в блог")
        text = (content.text or "").strip()
        photos = [m for m in content.media if m.type == "photo"]
        if videos := [m for m in content.media if m.type == "video"]:
            raise AdapterError("Tumblr: видео публикуется только встроенной ссылкой "
                               "в тексте — добавьте URL видео в текст поста")
        if not text and not photos:
            raise AdapterError("Tumblr: пустой пост")

        blog = page.social_page_id
        url = f"{API}/blog/{blog}/post"
        if photos:
            # multipart: в подпись идут только oauth-параметры (RFC 5849)
            header = oauth1_header("POST", url, creds)
            files = [(f"data[{i}]", (p.name, await read_media_bytes(p)))
                     for i, p in enumerate(photos[:10])]
            import httpx
            async with httpx.AsyncClient(timeout=300) as http:
                r = await http.post(url, data={"type": "photo", "caption": text},
                                    files=files, headers={"Authorization": header})
        else:
            client = OAuth1Client(creds, timeout=60)
            r = await client.request("POST", url,
                                     data={"type": "text", "body": text})
        data = r.json()
        if r.status_code >= 300 or "response" not in data:
            raise AdapterError(f"Tumblr post: "
                               f"{data.get('meta', {}).get('msg', r.text[:200])}")
        ext = str(data["response"].get("id", ""))
        return PublishOutcome(True, external_id=ext,
                              external_url=f"https://{blog}.tumblr.com/post/{ext}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        client = OAuth1Client(self._creds(page.account))
        r = await client.request("POST", f"{API}/blog/{page.social_page_id}/post/delete",
                                 data={"id": external_id})
        return r.status_code < 300
