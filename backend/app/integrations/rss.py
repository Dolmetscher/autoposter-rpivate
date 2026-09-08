"""RSS/Atom-фиды как источник кросспостинга. Без внешних зависимостей:
парсер на регулярных выражениях, поддерживает RSS 2.0 и Atom, enclosure и media:content."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import httpx

from .base import SourceItem

_IMG_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", block, re.S | re.I)
    if not m:
        return ""
    inner = m.group(1)
    if inner.startswith("<![CDATA["):
        inner = re.sub(r"^<!\[CDATA\[|\]\]>$", "", inner.strip(), flags=re.S)
        return inner.strip()
    return html.unescape(re.sub(r"<[^>]+>", " ", inner)).strip()


def _link(block: str) -> str:
    m = re.search(r'<link(?:\s[^>]*href="([^"]+)")[^>]*/?>', block, re.I)
    if m and m.group(1):
        return html.unescape(m.group(1))
    return _tag(block, "link")


def _media(block: str) -> list[dict]:
    media: list[dict] = []
    for m in re.finditer(r'<enclosure\s[^>]*url="([^"]+)"[^>]*type="([^"]*)"', block, re.I):
        url = html.unescape(m.group(1))
        kind = "video" if "video" in m.group(2) else "photo"
        media.append({"url": url, "kind": kind})
    for m in re.finditer(r'<media:content\s[^>]*url="([^"]+)"[^>]*/?>', block, re.I):
        url = html.unescape(m.group(1))
        kind = "video" if url.lower().endswith((".mp4", ".mov", ".webm")) else "photo"
        media.append({"url": url, "kind": kind})
    return media


def _clean(text: str, limit: int = 1200) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def parse_feed(xml: str, limit: int = 10) -> list[SourceItem]:
    blocks = re.findall(r"<item(?:\s[^>]*)?>(.*?)</item>", xml, re.S | re.I)
    if not blocks:
        blocks = re.findall(r"<entry(?:\s[^>]*)?>(.*?)</entry>", xml, re.S | re.I)
    items: list[SourceItem] = []
    for block in blocks[:limit]:
        title = _tag(block, "title")
        link = _link(block) or _tag(block, "guid")
        if not link:
            continue
        desc = _tag(block, "description") or _tag(block, "summary") or _tag(block, "content")
        text_parts = [p for p in (title, _clean(desc)) if p]
        text = "\n\n".join(text_parts)
        items.append(SourceItem(external_id=link, text=text, media=_media(block), url=link))
    return items


async def fetch_rss_items(feed_url: str, limit: int = 10,
                          account=None) -> list[SourceItem]:  # noqa: ANN001
    url = feed_url.strip()
    if not url.lower().startswith("http"):
        raise ValueError("RSS: укажите URL фида (http...)")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Autoposter/1.1 (+rss)"})
        r.raise_for_status()
        return parse_feed(r.text, limit)
