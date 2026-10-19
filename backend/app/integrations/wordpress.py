"""Вордпресс: публикация постов через официальный REST API (wp-json) с
Application Passwords (Пользователи → Приложения в админке WordPress).
Фото/документы загружаются в медиатеку и прикрепляются к записи."""
from __future__ import annotations

import html

import httpx

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter, read_media_bytes


class WordPressAdapter(SocialAdapter):
    source_id = 10
    name = "Вордпресс"
    capabilities = ("post", "photo", "document")

    def connect_requires(self) -> list[dict]:
        return [
            {"name": "base_url", "label": "Адрес сайта (https://site.ru)", "required": True},
            {"name": "username", "label": "Логин", "required": True},
            {"name": "app_password", "label": "Application Password", "required": True},
        ]

    def _api(self, creds: dict) -> tuple[httpx.AsyncClient, str]:
        base = (creds.get("base_url") or "").rstrip("/")
        user = creds.get("username")
        pwd = creds.get("app_password")
        if not (base and user and pwd):
            raise AdapterError("WP: нужны base_url, username и app_password")
        if not base.startswith("http"):
            raise AdapterError("WP: base_url должен начинаться с https://")
        client = httpx.AsyncClient(timeout=120, follow_redirects=True,
                                   auth=(user, pwd))
        return client, f"{base}/wp-json/wp/v2"

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        client, api = self._api(account.credentials)
        try:
            r = await client.get(f"{api}/users/me", params={"context": "edit"})
            if r.status_code in (401, 403):
                raise AdapterError("WP: неверный логин или Application Password")
            r.raise_for_status()
            me = r.json()
        finally:
            await client.aclose()
        return [PageInfo(account.credentials["base_url"].rstrip("/"),
                         me.get("name") or "Сайт", kind="site")]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        client, api = self._api(page.account.credentials)
        try:
            media_ids: list[int] = []
            media_urls: dict[int, str] = {}
            for m in content.media + content.documents:
                data = await read_media_bytes(m)
                # Content-Disposition выставляет httpx сам для каждой части multipart —
                # ручной общий заголовок ломает разбор загрузки на стороне WordPress.
                r = await client.post(f"{api}/media",
                                      files={"file": (m.name or "file", data)})
                if r.status_code >= 300:
                    raise AdapterError(f"WP media: {r.text[:200]}")
                uploaded = r.json()
                media_ids.append(uploaded["id"])
                media_urls[uploaded["id"]] = uploaded.get("source_url", "")

            text = (content.text or "").strip()
            if not text and not media_ids:
                raise AdapterError("WP: пустой пост")
            lines = text.split("\n", 1)
            title = lines[0].strip()[:100] or "Пост"
            body = lines[1].strip() if len(lines) > 1 else ""
            # Текст поста экранируем — иначе спецсимволы ломают HTML.
            paragraphs = "".join(
                f"<p>{html.escape(p.strip()).replace(chr(10), '<br/>')}</p>"
                for p in body.split("\n\n") if p.strip())
            payload = {
                "title": title,
                "content": paragraphs,
                "status": "publish",
            }
            if media_ids:
                payload["featured_media"] = media_ids[0]
                if len(media_ids) > 1:
                    payload["content"] = paragraphs + "".join(
                        f'<p><img src="{media_urls.get(mid, "")}" alt=""/></p>'
                        for mid in media_ids[1:] if media_urls.get(mid))
            r = await client.post(f"{api}/posts", json=payload)
            if r.status_code >= 300:
                raise AdapterError(f"WP posts: {r.text[:200]}")
            post = r.json()
            base = str(page.social_page_id).rstrip("/")
            return PublishOutcome(True, external_id=str(post["id"]),
                                  external_url=post.get("link") or f"{base}/?p={post['id']}")
        finally:
            await client.aclose()

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        client, api = self._api(page.account.credentials)
        try:
            r = await client.delete(f"{api}/posts/{external_id}", params={"force": "true"})
            return r.status_code in (200, 410)  # 410 = уже удалена
        finally:
            await client.aclose()
