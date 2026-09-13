"""Модели подсистемы «Автоматизированная редакция» (red_*).
Стиль общий с app.models: naive-UTC DateTime, JSON для списков, user_id FK."""
from datetime import datetime

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class RedSource(Base):
    """Источник мониторинга: RSS / Reddit / Bluesky / Telegram / VK."""
    __tablename__ = "red_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))  # rss | reddit | bluesky | tg | vk
    identifier: Mapped[str] = mapped_column(String(512))  # url / r/sub / handle / @channel
    title: Mapped[str] = mapped_column(String(255), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)  # теги источника
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    jitter_pct: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active | degraded | paused | archived
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    ok_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    items_24h: Mapped[int] = mapped_column(Integer, default=0)  # денормализованный счётчик
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedItem(Base):
    """Материал из источника: статья, пост, тред."""
    __tablename__ = "red_items"
    __table_args__ = (
        Index("ix_red_items_content", "content_hash"),
        Index("ix_red_items_fetched", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("red_sources.id", ondelete="CASCADE"), index=True)
    cluster_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    external_id: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(8), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    media_urls: Mapped[list] = mapped_column(JSON, default=list)  # исходные ссылки
    media_ids: Mapped[list] = mapped_column(JSON, default=list)   # id MediaFile (скачанные)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    # new | filtered | review | scored | clustered | alerted | digest | posted | ignored
    digest_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    red_attempts: Mapped[int] = mapped_column(Integer, default=0)  # раунды неудачного скоринга
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedScore(Base):
    """Результат AI-скоринга материала (версия гайдлайнов фиксируется)."""
    __tablename__ = "red_scores"
    __table_args__ = (Index("ix_red_scores_item", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("red_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    guideline_version: Mapped[int] = mapped_column(Integer, default=0)
    significance: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_tags: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    digest_worthy: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(32), default="")
    headline_ru: Mapped[str] = mapped_column(Text, default="")
    summary_ru: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedGuideline(Base):
    """Версия редакционных гайдлайнов. Активна последняя."""
    __tablename__ = "red_guidelines"
    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_red_guideline_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, default="")  # Markdown, ≤ 5000 символов
    tags: Mapped[list] = mapped_column(JSON, default=list)  # теги для префильтра (FR-2.12)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedAlert(Base):
    """Отправленный алерт владельцу (для анти-флуда, кнопок, дедупа кластера)."""
    __tablename__ = "red_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("red_items.id", ondelete="CASCADE"), nullable=True)
    cluster_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    tg_chat_id: Mapped[str] = mapped_column(String(32), default="")
    tg_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="item")  # item | batch | system
    text: Mapped[str] = mapped_column(Text, default="")            # для сводных сообщений
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # queued | digest | ignored | wrong_score
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedDigestItem(Base):
    __tablename__ = "red_digest_items"
    __table_args__ = (UniqueConstraint("digest_id", "item_id", name="uq_red_digest_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("red_digests.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("red_items.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)


class RedDigest(Base):
    """Черновик дайджеста с approve-flow."""
    __tablename__ = "red_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dtype: Mapped[str] = mapped_column(String(16), default="daily19")  # daily19 | noon13
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # draft | approved | published | rejected | stale
    tg_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedFeedback(Base):
    """Разметка владельца: item → верное действие (для будущих правок гайдлайнов)."""
    __tablename__ = "red_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("red_items.id", ondelete="CASCADE"), nullable=True)
    action: Mapped[str] = mapped_column(String(32), default="")  # wrong_score | ...
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedSourceCheck(Base):
    """Результат каждого цикла поллинга (для health-check, без отдельных запросов)."""
    __tablename__ = "red_source_checks"
    __table_args__ = (Index("ix_red_checks_src", "source_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("red_sources.id", ondelete="CASCADE"))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedBlocklist(Base):
    """«Игнор»: контент-кластер не алертится повторно."""
    __tablename__ = "red_blocklist"
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_red_block"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RedDailyStat(Base):
    """Агрегаты дня (живут бессрочно, вне retention)."""
    __tablename__ = "red_daily_stats"
    __table_args__ = (UniqueConstraint("user_id", "day", "source_id", "tag",
                                       name="uq_red_daily"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))          # YYYY-MM-DD (UTC)
    source_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 = все источники
    tag: Mapped[str] = mapped_column(String(64), default="")    # "" = без тега
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    alerts_count: Mapped[int] = mapped_column(Integer, default=0)
    posts_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_significance: Mapped[float | None] = mapped_column(Float, nullable=True)


def red_tg_chat_id(chat_id) -> str:
    """chat_id Telegram в строковом виде (может быть отрицательным int)."""
    return str(chat_id)
