"""LinkedIn: текстовые посты в ленту участника через новый Posts API (rest/posts).
Токен создаётся в developer-приложении с продуктом «Share on LinkedIn» и правом
w_member_social. Изображения/видео требуют отдельной регистрации загрузки — пока
поддерживается текст (это основной формат LinkedIn)."""
from __future__ import annotations

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter

API = "https://api.linkedin.com"
VERSION = "202405"


class LinkedInAdapter(SocialAdapter):
    source_id = 13
    name = "ЛинкедИн"
    capabilities = ("post",)

    def connect_requires(self) -> list[dict]:
        return [{"name": "access_token",
                 "label": "Access token LinkedIn (w_member_social)", "required": True}]

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}",
                "LinkedIn-Version": VERSION,
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0"}

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        token = account.credentials.get("access_token")
        if not token:
            raise AdapterError("LinkedIn: не указан access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{API}/v2/userinfo",
                                 headers=self._headers(token))
        data = r.json()
        if r.status_code >= 300 or "sub" not in data:
            raise AdapterError(f"LinkedIn: {data.get('message', 'токен недействителен')}")
        name = f"{data.get('given_name', '')} {data.get('family_name', '')}".strip() or "Профиль"
        return [PageInfo(data["sub"], name, kind="user")]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        if content.media or content.documents:
            raise AdapterError("LinkedIn: пока поддерживается только текст "
                               "(медиа требует одобрения партнёрского доступа)")
        if not (content.text or "").strip():
            raise AdapterError("LinkedIn: пустой пост")
        author = f"urn:li:person:{page.social_page_id}"
        body = {
            "author": author,
            "commentary": content.text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API}/rest/posts", headers=self._headers(token),
                                  json=body)
        if r.status_code >= 300:
            raise AdapterError(f"LinkedIn: {r.status_code} {r.text[:200]}")
        post_urn = r.headers.get("x-restli-id", "") or r.headers.get("X-RestLi-Id", "")
        ext = post_urn.split(":")[-1] if post_urn else ""
        activity = post_urn or f"urn:li:activity:{ext}"
        return PublishOutcome(True, external_id=post_urn,
                              external_url=f"https://www.linkedin.com/feed/update/{activity}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        token = page.account.credentials.get("access_token")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{API}/rest/posts/{external_id}",
                                    headers=self._headers(token))
        return r.status_code in (200, 204, 404)
