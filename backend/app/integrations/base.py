from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("adapters")


class AdapterError(Exception):
    """Ошибка интеграции с соцсетью (попадает в лог публикации и статус таргета)."""


@dataclass
class PageInfo:
    social_page_id: str
    name: str
    photo: str = ""
    kind: str = "group"  # group | channel | user | page | chat
    can_post: bool = True


@dataclass
class PublishOutcome:
    ok: bool
    external_id: str = ""
    external_url: str = ""
    error: str = ""


@dataclass
class SourceItem:
    """Пост из чужого источника для кросспостинга."""
    external_id: str
    text: str = ""
    media: list[dict] = field(default_factory=list)  # [{"url": ..., "kind": "photo"|"video"}]
    url: str = ""


@dataclass
class PublishContent:
    text: str = ""
    media: list = field(default_factory=list)      # orm MediaFile (photo/video)
    documents: list = field(default_factory=list)  # orm MediaFile (document)
    settings: dict = field(default_factory=dict)
    media_base_url: str = ""  # устарело: публичный URL даёт services.storage.public_url

    def public_url(self, file_path: str) -> str:
        from ..services import storage
        return storage.public_url(file_path)


async def read_media_bytes(media) -> bytes:  # noqa: ANN001
    """Байты медиафайла из хранилища (local или S3) — для загрузки в соцсеть."""
    from ..services import storage
    return await storage.read_file(media.file_path)


class SocialAdapter(ABC):
    """Контракт интеграции с соцсетью. Все методы асинхронные, без блокировок.

    publish() сам учитывает настройки поста (publish_as_story/clips/shorts, pin_post)
    в рамках возможностей сети и возвращает результат публикации.
    """

    source_id: int = 0
    name: str = ""
    capabilities: tuple[str, ...] = ()

    # --- подключение аккаунта ---
    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:  # noqa: ANN001
        raise AdapterError(f"{self.name}: получение страниц не реализовано")

    def connect_requires(self) -> list[dict]:
        """Схема полей ручного подключения для UI."""
        return []

    def oauth_authorize_url(self, state: str) -> str | None:
        return None

    async def oauth_exchange(self, code: str) -> dict:
        raise AdapterError(f"{self.name}: OAuth не настроен")

    # --- публикация ---
    @abstractmethod
    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        ...

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError(f"{self.name}: удаление не поддерживается")

    # --- кросспостинг ---
    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:  # noqa: ANN001
        raise AdapterError(f"{self.name}: источники не поддерживаются")

    # --- аналитика: метрики опубликованного поста ---
    async def fetch_stats(self, page, external_id: str) -> dict | None:  # noqa: ANN001
        """{'views': int, 'likes': int, 'reposts': int, 'comments': int} или None."""
        return None

    # --- утилиты ---
    @property
    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=60, follow_redirects=True)

    async def close(self, client: httpx.AsyncClient) -> None:
        await client.aclose()


async def run_blocking(fn, *args):
    """Выполнить блокирующую функцию (напр. Telethon) вне event loop."""
    return await asyncio.to_thread(fn, *args)
