"""Получение материалов из источников всех 5 типов (FR-1.1).

RSS — свой парсер (integrations.rss), TG/VK — адаптеры (как в кросспостинге),
Reddit и Bluesky — прямые HTTP-запросы к публичным JSON API.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from ..integrations.base import AdapterError, SourceItem
from ..integrations.rss import fetch_rss_items
from ..integrations.registry import get_adapter

REDDIT_TYPES = ("rss", "reddit", "bluesky", "tg", "vk")

_UA = "Autoposter-Redakciya/1.0 (news monitoring; official APIs only)"


def _dt_from_ts(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


async def fetch_items(src_type: str, identifier: str, limit: int = 20,
                      account=None) -> list[SourceItem]:
    """Единая точка получения материалов; source-специфичные ошибки — ValueError/AdapterError."""
    src_type = (src_type or "").strip().lower()
    identifier = (identifier or "").strip()
    if src_type == "rss":
        return await fetch_rss_items(identifier, limit)
    if src_type == "tg":
        return await get_adapter(9).fetch_source_items(identifier, limit, account=account)
    if src_type == "vk":
        return await get_adapter(1).fetch_source_items(identifier, limit, account=account)
    if src_type == "reddit":
        return await fetch_reddit(identifier, limit)
    if src_type == "bluesky":
        return await fetch_bluesky(identifier, limit)
    raise ValueError(f"Неизвестный тип источника: {src_type}")


# --- Reddit: официальный публичный JSON (.json-эндпоинты), без обхода лимитов (NFR-17) ---

def _subreddit(identifier: str) -> str:
    m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", identifier)
    if m:
        return m.group(1)
    sub = identifier.strip().lstrip("/").removeprefix("r/").split("?")[0].strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", sub):
        raise ValueError("Reddit: укажите сабреддит, напр. r/MachineLearning")
    return sub


async def fetch_reddit(identifier: str, limit: int = 20) -> list[SourceItem]:
    sub = _subreddit(identifier)
    url = f"https://www.reddit.com/r/{sub}/new.json?limit={min(limit, 50)}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": _UA}) as client:
        r = await client.get(url)
        if r.status_code == 404:
            raise ValueError(f"Reddit: сабреддит r/{sub} не существует")
        r.raise_for_status()
        data = r.json()
    items: list[SourceItem] = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied"):
            continue
        title = (d.get("title") or "").strip()
        body = (d.get("selftext") or "").strip()
        text = title + (f"\n\n{body}" if body else "")
        media: list[dict] = []
        preview = (d.get("preview") or {}).get("images") or []
        if preview:
            src = preview[0].get("source") or {}
            if src.get("url"):
                media.append({"url": src["url"].replace("&amp;", "&"), "kind": "photo"})
        items.append(SourceItem(
            external_id=d.get("name") or d.get("id") or "",
            text=text[:10000], media=media,
            url=f"https://www.reddit.com{d.get('permalink', '')}"))
    return items


# --- Bluesky: публичный AppView (public.api.bsky.app), без авторизации ---

_BSKY = "https://public.api.bsky.app/xrpc"


def _bsky_actor(identifier: str) -> str | None:
    """handle (посты автора) или None, если identifier — поисковый запрос."""
    raw = identifier.strip().lstrip("@")
    if re.fullmatch(r"[\w.-]+\.(bsky\.social|[\w-]+\.[\w.-]+)", raw) and " " not in raw:
        return raw
    return None


def _bsky_post_url(uri: str) -> str:
    parts = uri.replace("at://", "").split("/")
    if len(parts) >= 3:
        return f"https://bsky.app/profile/{parts[0]}/post/{parts[2]}"
    return ""


def _bsky_media(post: dict) -> list[dict]:
    media: list[dict] = []
    embed = post.get("embed") or {}
    images = embed.get("images") or (embed.get("media") or {}).get("images") or []
    for img in images:
        u = img.get("fullsize") or img.get("thumb")
        if u:
            media.append({"url": u, "kind": "photo"})
    return media


async def fetch_bluesky(identifier: str, limit: int = 20) -> list[SourceItem]:
    actor = _bsky_actor(identifier)
    params: dict = {"limit": min(limit, 50)}
    if actor:
        endpoint = f"{_BSKY}/app.bsky.feed.getAuthorPosts"
        params["actor"] = actor
    else:
        q = identifier.lstrip("#").strip()
        if not q:
            raise ValueError("Bluesky: укажите handle (@name.bsky.social) или поисковый запрос")
        endpoint = f"{_BSKY}/app.bsky.feed.searchPosts"
        params["q"] = q
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": _UA}) as client:
        r = await client.get(endpoint, params=params)
        if r.status_code == 400:
            raise ValueError("Bluesky: профиль/запрос не найден или неверный handle")
        r.raise_for_status()
        data = r.json()
    items: list[SourceItem] = []
    for feed_el in data.get("posts", []):
        record = feed_el.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            continue
        items.append(SourceItem(
            external_id=feed_el.get("uri") or "",
            text=text[:10000],
            media=_bsky_media(feed_el),
            url=_bsky_post_url(feed_el.get("uri") or "")))
    return items
