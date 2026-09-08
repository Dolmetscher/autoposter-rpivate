from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    llm_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    accounts: Mapped[list["SocialAccount"]] = relationship(back_populates="user")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="API-токен")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    credentials: Mapped[dict] = mapped_column(JSON, default=dict)  # токены/сессии сети
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # доп. параметры подключения
    social_account_id: Mapped[str] = mapped_column(String(128), default="")
    social_account_name: Mapped[str] = mapped_column(String(255), default="")
    social_account_photo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="accounts")
    pages: Mapped[list["SocialPage"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class SocialPage(Base):
    """Группа/канал/страница/профиль, куда публикуем."""
    __tablename__ = "social_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    social_page_id: Mapped[str] = mapped_column(String(128))  # id страницы внутри сети
    social_page_name: Mapped[str] = mapped_column(String(255), default="")
    social_page_photo: Mapped[str] = mapped_column(Text, default="")
    page_kind: Mapped[str] = mapped_column(String(32), default="group")  # group|channel|user|page|chat
    can_post: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["SocialAccount"] = relationship(back_populates="pages")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # пул страниц/текстов для случайного контента
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    slots: Mapped[list] = mapped_column(JSON, default=list)
    # slots: [{"days": [1..7], "time": "10:00"}, ...] в таймзоне пользователя
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # строковый id как у hooppy
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))  # photo | video | document
    name: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text, default="")  # подпись/описание фото
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    folder: Mapped[str] = mapped_column(String(32), default="")
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    file_thumbnail_path: Mapped[str] = mapped_column(String(1024), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_url: Mapped[str] = mapped_column(Text, default="")  # ссылка-источник для скопированных файлов
    updated_date: Mapped[str] = mapped_column(String(32), default="")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # draft | scheduled | publishing | published | partial | failed | canceled
    publication_when_type: Mapped[int] = mapped_column(Integer, default=1)
    # 1=сейчас, 2=по дате, 3=по расписанию, 4=проект
    publication_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    repeat_type: Mapped[int] = mapped_column(Integer, default=0)  # 0 нет, 1 ежедневно, 2 еженедельно, 3 ежемесячно
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    texts: Mapped[list["PostText"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    attachments: Mapped[list["PostAttachment"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    targets: Mapped[list["PostTarget"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostText(Base):
    __tablename__ = "post_texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 — общий текст
    text: Mapped[str] = mapped_column(Text, default="")

    post: Mapped["Post"] = relationship(back_populates="texts")


class PostAttachment(Base):
    __tablename__ = "post_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    media_file_id: Mapped[str] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)

    post: Mapped["Post"] = relationship(back_populates="attachments")
    media_file: Mapped["MediaFile"] = relationship()


class PostTarget(Base):
    """Публикация поста в одну конкретную страницу одной соцсети."""
    __tablename__ = "post_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("social_pages.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|publishing|published|failed|deleted
    external_id: Mapped[str] = mapped_column(String(255), default="")
    external_url: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delete_after_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    post: Mapped["Post"] = relationship(back_populates="targets")
    page: Mapped["SocialPage"] = relationship()


class PublicationLog(Base):
    __tablename__ = "publication_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | success | error
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CrosspostSource(Base):
    """Источник кросспостинга: канал/группа другого автора."""
    __tablename__ = "crosspost_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(Integer)  # соцсеть источника (VK, Telegram, YouTube)
    name: Mapped[str] = mapped_column(String(255), default="")
    source_ref: Mapped[str] = mapped_column(String(512))  # owner_id ВК / @канал / channel_id YT
    target_page_ids: Mapped[list] = mapped_column(JSON, default=list)  # куда копируем
    rewrite_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    only_with_media: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_filter: Mapped[str] = mapped_column(String(512), default="")
    poll_interval_min: Mapped[int] = mapped_column(Integer, default=15)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrosspostSeen(Base):
    __tablename__ = "crosspost_seen"
    __table_args__ = (UniqueConstraint("source_row_id", "external_id", name="uq_crosspost_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey("crosspost_sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Index("ix_posts_status_date", Post.status, Post.publication_date)
