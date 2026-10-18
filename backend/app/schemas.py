"""Pydantic-схемы веб-API и публичного API."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from .constants import SourceInfo, SOURCES, PLANNED_SOURCES
from .util import utcnow


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    timezone: str
    llm_config: dict = {}

    class Config:
        from_attributes = True


def source_out(src: SourceInfo) -> dict:
    return {
        "id": src.id,
        "slug": src.slug,
        "name": src.name,
        "implemented": src.adapter is not None,
        "experimental": src.experimental,
        "oauth": src.oauth,
        "capabilities": list(src.capabilities),
        "color": src.color,
    }


class AccountOut(BaseModel):
    id: int
    source_id: int
    social_account_id: str
    social_account_name: str
    social_account_photo: str
    status: str

    class Config:
        from_attributes = True


class PageOut(BaseModel):
    id: int
    account_id: int
    source_id: int
    social_page_id: str
    social_page_name: str
    social_page_photo: str
    page_kind: str
    can_post: bool

    class Config:
        from_attributes = True


class ConnectIn(BaseModel):
    source_id: int
    credentials: dict[str, Any] = {}
    extra: dict[str, Any] = {}  # доп. параметры (напр. список ссылок каналов для Telegram-бота)


class MediaOut(BaseModel):
    id: str
    type: str
    name: str
    text: str = ""
    title: str = ""
    description: str = ""
    folder: str
    file_path: str
    file_thumbnail_path: str = ""
    size: int = 0
    seconds: int | None = None
    updated_date: str = ""

    class Config:
        from_attributes = True


class TextIn(BaseModel):
    text: str = ""
    source_id: int = 0


class PostIn(BaseModel):
    """Внутренний payload редактора (UI). Публичный API использует hooppy-формат."""
    texts: list[TextIn] = []
    media_ids: list[str] = []
    document_ids: list[str] = []
    page_ids: list[int] = []
    settings: dict = {}
    publication_when_type: int = 1
    publication_date: str | None = None  # ISO или дд.мм.гггг[ ЧЧ:ММ]
    schedule_id: int | None = None
    project_id: int | None = None
    status: str = "scheduled"


class PostUpdateIn(PostIn):
    pass


class PostTargetOut(BaseModel):
    id: int
    page_id: int
    source_id: int
    status: str
    external_url: str
    error: str
    attempts: int
    published_at: datetime | None
    page_name: str = ""

    class Config:
        from_attributes = True


class PostOut(BaseModel):
    id: int
    status: str
    publication_when_type: int
    publication_date: datetime | None
    project_id: int | None
    schedule_id: int | None
    settings: dict
    repeat_type: int
    created_at: datetime
    texts: list[TextIn]
    media_ids: list[str] = []
    document_ids: list[str] = []
    targets: list[PostTargetOut] = []


class ProjectIn(BaseModel):
    name: str
    settings: dict = {}
    # None — не менять привязку (PUT без поля сохраняет аккаунты проекта);
    # пустой список — явно отвязать все аккаунты.
    account_ids: list[int] | None = None


class ScheduleIn(BaseModel):
    name: str
    slots: list[dict] = []


class CrosspostSourceIn(BaseModel):
    source_id: int
    name: str = ""
    source_ref: str
    target_page_ids: list[int] = []
    rewrite_enabled: bool = False
    only_with_media: bool = False
    keyword_filter: str = ""
    poll_interval_min: int = 15
    active: bool = True


class LlmConfigIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    prompt: str = ""


class ApiTokenIn(BaseModel):
    name: str = "API-токен"


def parse_publication_date(value: str | None, tz_name: str | None = None) -> datetime | None:
    """Понимает ISO, 'дд.мм.гггг' и 'дд.мм.гггг ЧЧ:ММ'.

    Если передан tz_name, значение интерпретируется как локальное время в этой
    таймзоне и возвращается как naive-UTC (чтобы планировщик сравнивал с utcnow()).
    """
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(value, fmt)
            if tz_name:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value)
        if tz_name and dt.tzinfo is None:
            from zoneinfo import ZoneInfo
            dt = dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return dt
    except ValueError:
        raise ValueError(f"Не удалось разобрать дату: {value}")


def next_schedule_slot(slots: list[dict], tz_name: str, from_dt: datetime | None = None) -> datetime:
    """Ближайший момент слота расписания в таймзоне пользователя (возвращает naive UTC)."""
    from zoneinfo import ZoneInfo

    from_dt = from_dt or utcnow()
    tz = ZoneInfo(tz_name)
    now_local = from_dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    candidates: list[datetime] = []
    for day_shift in range(0, 8):
        day = now_local + timedelta(days=day_shift)
        for slot in slots:
            try:
                hh, mm = str(slot.get("time", "10:00")).split(":")[:2]
                minutes = int(hh) * 60 + int(mm)
            except ValueError:
                continue
            days = slot.get("days") or list(range(1, 8))
            if day.isoweekday() not in days:
                continue
            cand = day.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if cand >= now_local - timedelta(minutes=1):
                candidates.append(cand)
    if not candidates:
        return from_dt + timedelta(days=1)
    return min(candidates).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

