"""Публичный RSS-фид проекта — канал доставки контента в Дзен.

У Дзена нет публичного API публикации, зато есть официальная «трансляция»:
канал Дзена сам забирает материалы из RSS-фида. Проект автопостера с
Дзен-страницей отдаёт фид по ссылке с секретным токеном; пост попадает в
фид, когда его Дзен-цель опубликована (адаптер Дзена считает публикацией
передачу в фид). Дзен подтягивает новые items по своему расписанию.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config import settings
from ...db import get_session
from ...integrations._mdfmt import md_to_plain, md_to_rich_html
from ...models import Post, PostAttachment, Project
from ...util import utcnow

DZEN_SOURCE_ID = 20
MAX_ITEMS = 30

router = APIRouter(prefix="/rss-out", tags=["rss-out"])

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}


def _cdata(text: str) -> str:
    return f"<![CDATA[{(text or '').replace(']]>', ']]]]><![CDATA[>')}]]>"


def _text_to_html(text: str) -> str:
    """Текст поста (Markdown) → HTML для content:encoded.

    Дзен забирает материалы из фида с форматированием: **жирный**,
    *курсив*, # заголовок, [ссылки](url) рендерятся в HTML.
    """
    return md_to_rich_html(text or "")


def _item_title(post: Post) -> str:
    """Заголовок: первая содержательная строка текста, иначе имя медиафайла."""
    for line in (post.texts[0].text if post.texts else "").splitlines():
        line = line.strip()
        if line:
            return md_to_plain(line)[:120]
    for att in post.attachments:
        name = (att.media_file.title or att.media_file.name or "").strip()
        if name:
            return name[:120]
    return f"Пост #{post.id}"


def _media_url(rel_path: str) -> str:
    from ...services import storage
    return storage.public_url(rel_path)


def _render_item(post: Post, feed_url: str) -> str:
    media = [a.media_file for a in sorted(post.attachments, key=lambda a: a.position)
             if a.media_file.type in ("photo", "video")]
    photos = [m for m in media if m.type == "photo"]
    videos = [m for m in media if m.type == "video"]

    # Ссылка item: публичный URL первого фото, иначе первого видео,
    # иначе якорь фида — главное, что адрес абсолютный и уникальный.
    link = _media_url(photos[0].file_path) if photos and photos[0].file_path else (
        _media_url(videos[0].file_path) if videos and videos[0].file_path
        else f"{feed_url}#post-{post.id}")

    # Текст + медиа в content:encoded
    parts = [_text_to_html(post.texts[0].text if post.texts else "")]
    for m in photos:
        if m.file_path:
            parts.append(f'<p><img src="{escape(_media_url(m.file_path))}" alt=""/></p>')
    for m in videos:
        if m.file_path:
            name = html.escape(m.name or "видео")
            parts.append(f'<p><a href="{escape(_media_url(m.file_path))}">Видео: {name}</a></p>')
    content_encoded = "\n".join(p for p in parts if p)

    published = post.publication_date or post.created_at or utcnow()
    if isinstance(published, datetime) and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    lines = ["    <item>",
             f"      <title>{escape(_item_title(post))}</title>",
             f"      <link>{escape(link)}</link>",
             '      <guid isPermaLink="false">autoposter-post-'
             f"{post.id}</guid>",
             f"      <pubDate>{format_datetime(published)}</pubDate>",
             f"      <description>{escape(_item_title(post))}</description>"]
    if content_encoded:
        lines.append(f"      <content:encoded>{_cdata(content_encoded)}</content:encoded>")
    if photos and photos[0].file_path:
        m = photos[0]
        ext = "." + (m.name or m.file_path).rsplit(".", 1)[-1].lower() \
            if "." in (m.name or m.file_path) else ".jpg"
        mime = IMAGE_TYPES.get(ext, "image/jpeg")
        length = m.size if m.size and m.size > 0 else 0
        lines.append(f'      <enclosure url="{escape(_media_url(m.file_path))}" '
                     f'type="{mime}" length="{length}"/>')
    lines.append("    </item>")
    return "\n".join(lines)


@router.get("/{project_id}/{token}.xml")
async def rss_out(project_id: int, token: str, s: AsyncSession = Depends(get_session)):
    project = (await s.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    # 404 на любой промах — не раскрываем существование проекта
    if project is None or not project.rss_token or token != project.rss_token:
        raise HTTPException(404, "Фид не найден")

    base = settings.public_base_url.rstrip("/")
    feed_url = f"{base}/api/public/rss-out/{project.id}/{project.rss_token}.xml"

    # Публикации проекта, уже переданные в фид (Дзен-цель = published)
    posts = (await s.execute(
        select(Post)
        .options(selectinload(Post.texts),
                 selectinload(Post.attachments).selectinload(PostAttachment.media_file),
                 selectinload(Post.targets))
        .where(Post.project_id == project.id,
               Post.status.in_(("published", "partial")))
        .order_by(Post.publication_date.desc(), Post.id.desc())
        .limit(MAX_ITEMS * 2)
    )).scalars().all()
    feed_posts = [p for p in posts if any(
        t.source_id == DZEN_SOURCE_ID and t.status == "published" for t in p.targets)]
    feed_posts = feed_posts[:MAX_ITEMS]

    items = "\n".join(_render_item(p, feed_url) for p in feed_posts)
    last = feed_posts[0].publication_date or feed_posts[0].created_at if feed_posts else utcnow()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    updated = format_datetime(last)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(f"Проект «{project.name}»")}</title>
    <link>{escape(base)}</link>
    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>
    <description>{escape(f"Публикации проекта «{project.name}» — трансляция в Дзен")}</description>
    <language>ru</language>
    <generator>Autoposter</generator>
    <lastBuildDate>{updated}</lastBuildDate>
{items}
  </channel>
</rss>"""
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8",
                    headers={"Cache-Control": "no-store"})
