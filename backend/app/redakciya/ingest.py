"""Ingestion: опрос источников по расписанию с джиттером (FR-1.4–1.7),
нормализация (FR-1.8), двухуровневая дедупликация с кластерами (FR-1.9–1.10),
скачивание медиа (FR-1.12–1.13) и учёт результатов сборов (FR-5.3)."""
from __future__ import annotations

import hashlib
import logging
import random
import re
import html as html_mod
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..integrations.base import SourceItem
from ..models import AppState
from ..services import files as files_service
from ..util import utcnow
from . import fetchers
from .models import RedItem, RedSource, RedSourceCheck

log = logging.getLogger("red.ingest")

# Интервалы по умолчанию, сек (FR-1.5)
DEFAULT_INTERVALS = {"rss": 300, "reddit": 600, "bluesky": 600, "tg": 300, "vk": 300}

# Порог свежести для health-check, часы (FR-5.2)
STALENESS_HOURS = {"rss": 48, "reddit": 48, "bluesky": 48, "tg": 24, "vk": 24}

_UTM_PARAMS = re.compile(
    r"^(utm_[a-z]+|fbclid|gclid|yclid|msclkid|ref|icid|igshid|spm)$", re.I)


def normalize_url(url: str) -> str:
    """Срез UTM/трекинг-параметров, канонический вид (FR-1.8)."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        return url
    keep = [kv for kv in parts.query.split("&")
            if kv and not _UTM_PARAMS.match(kv.split("=")[0])]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       "&".join(keep), ""))


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def url_hash(url: str, external_id: str = "", source_id: int = 0) -> str:
    base = normalize_url(url) or f"ext:{source_id}:{external_id}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def content_hash(title: str, body: str) -> str:
    base = f"{(title or '').strip().lower()}|{(body or '')[:500].strip().lower()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=None)
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(raw.replace("+00:00", "Z"), fmt)
            return dt.replace(tzinfo=None) if fmt.endswith("%z") else \
                datetime.fromtimestamp(dt.timestamp())
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(tz=None).replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def _next_poll(src: RedSource, now: datetime) -> datetime:
    interval = max(60, src.poll_interval_sec)
    if src.status == "degraded":
        interval = settings.red_degraded_poll_sec  # FR-5.6
    jitter = random.uniform(-src.jitter_pct, src.jitter_pct) / 100 * interval
    return now + timedelta(seconds=interval + jitter)


async def poll_due(s: AsyncSession, now: datetime | None = None) -> int:
    """Опрос всех «зрелых» источников; вызывается каждый тик."""
    now = now or utcnow()
    rows = (await s.execute(
        select(RedSource)
        .where(RedSource.status.in_(("active", "degraded")),
               RedSource.next_poll_at <= now)
        .order_by(RedSource.next_poll_at)
        .limit(30)
    )).scalars().all()
    polled = 0
    for src in rows:
        src.last_polled_at = now
        src.next_poll_at = _next_poll(src, now)
        try:
            await poll_source(s, src, now)
        except Exception:
            log.exception("red poll failed for source %s", src.id)
        polled += 1
    if polled:
        await s.flush()
    return polled


async def poll_source(s: AsyncSession, src: RedSource, now: datetime | None = None) -> int:
    """Один цикл сбора: fetch → нормализация → дедуп → запись. Возвращает число новых."""
    now = now or utcnow()
    started = utcnow()
    try:
        items = await fetchers.fetch_items(src.type, src.identifier, limit=25)
        duration_ms = int((utcnow() - started).total_seconds() * 1000)
    except Exception as e:  # noqa: BLE001 — фиксируем любую ошибку сбора в source_checks
        duration_ms = int((utcnow() - started).total_seconds() * 1000)
        msg = str(e)[:500]
        s.add(RedSourceCheck(source_id=src.id, ok=False, error=msg, duration_ms=duration_ms))
        src.fail_count += 1
        src.ok_streak = 0
        src.last_error = msg
        if src.status == "active" and src.fail_count >= 3:  # FR-5.2
            src.status = "degraded"
            from . import alerting
            await alerting.notify_source_degraded(s, src)
        await s.flush()
        return 0

    s.add(RedSourceCheck(source_id=src.id, ok=True, duration_ms=duration_ms))
    src.fail_count = 0
    src.ok_streak += 1
    src.last_ok_at = now
    src.last_error = ""
    if src.status == "degraded" and src.ok_streak >= 5:  # FR-5.2: авто-восстановление
        src.status = "active"
        src.ok_streak = 0

    created = 0
    for item in items:
        if await _create_item(s, src, item, now):
            created += 1
    src.items_24h = await _count_24h(s, src.id, now)
    await s.flush()
    if created:
        await _mark_activity(s, now)
    return created


async def _count_24h(s: AsyncSession, source_id: int, now: datetime) -> int:
    return int((await s.execute(
        select(func.count(RedItem.id)).where(
            RedItem.source_id == source_id, RedItem.fetched_at >= now - timedelta(hours=24)
        ))).scalar() or 0)


async def _mark_activity(s: AsyncSession, now: datetime) -> None:
    """Отметка «пайплайн жив» для silence-watch (FR-3.3)."""
    row = await s.get(AppState, "red_silence")
    value = {"last_item_ts": now.isoformat()}
    if row is not None:
        row.value = value
        row.updated_at = now
    else:
        s.add(AppState(key="red_silence", value=value, updated_at=now))


async def _create_item(s: AsyncSession, src: RedSource, raw: SourceItem,
                       now: datetime) -> bool:
    """Нормализация + дедуп + запись материала. True, если материал новый."""
    url = normalize_url(raw.url or "")
    title, body = _split_text(raw.text or "")
    u_hash = url_hash(url, raw.external_id, src.id)
    if await s.scalar(select(RedItem.id).where(RedItem.url_hash == u_hash)):
        return False
    c_hash = content_hash(title, body)

    from .models import RedBlocklist
    if await s.scalar(select(RedBlocklist.id).where(
            RedBlocklist.user_id == src.user_id, RedBlocklist.content_hash == c_hash,
            (RedBlocklist.until.is_(None)) | (RedBlocklist.until > now))):
        status = "ignored"
        cluster = c_hash
    else:
        twin = (await s.execute(
            select(RedItem).where(RedItem.content_hash == c_hash)
            .order_by(RedItem.id).limit(1))).scalars().first()
        if twin is not None:
            cluster = twin.cluster_key or c_hash  # FR-1.9: к кластеру, без отдельного скоринга
            status = "clustered"
        else:
            cluster = c_hash
            status = "new"

    media_urls: list[str] = []
    media_ids: list[str] = []
    for m in (raw.media or [])[:settings.red_max_media_per_item]:
        if not m.get("url"):
            continue
        media_urls.append(m["url"])
        if m.get("kind", "photo") == "photo" and len(media_ids) < settings.red_max_media_per_item:
            try:
                mf = await files_service.download_from_url(src.user_id, m["url"], "photo", s)
                media_ids.append(mf.id)
            except Exception:  # noqa: BLE001 — медиа не блокирует материал (FR-8.13)
                continue

    s.add(RedItem(
        user_id=src.user_id, source_id=src.id, cluster_key=cluster,
        external_id=(raw.external_id or "")[:255], url=url, url_hash=u_hash,
        content_hash=c_hash, title=title, body=body[:settings.red_max_body_chars],
        published_at=_parse_dt(getattr(raw, "published_at", None)) or now,
        fetched_at=now, media_urls=media_urls, media_ids=media_ids, status=status,
    ))
    # TODO(S, FR-1.11): рекламный фильтр TG/VK (is_ad) — не уходит в скоринг
    return True


def _split_text(text: str) -> tuple[str, str]:
    """Первый блок — заголовок (trim, ≤300), остальное — тело (FR-1.8)."""
    text = strip_html(text or "")
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if not parts:
        return "", ""
    title = parts[0][:300].strip()
    body = "\n".join(parts[1:]).strip()
    if not body and len(text) > len(title):
        body = text[len(parts[0]):].strip()
    return title, body
