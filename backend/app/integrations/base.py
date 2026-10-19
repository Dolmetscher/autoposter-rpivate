from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

    def text_with_link(self) -> str:
        """Текст поста вместе со ссылкой из настроек.

        Для сетей, где нет отдельного вложения-ссылки (все, кроме VK и Telegram),
        ссылка дописывается в текст — сама сеть развернёт из неё превью.
        """
        link = (self.settings or {}).get("link", "").strip()
        text = (self.text or "").strip()
        if not link or link in text:
            return self.text or ""
        return f"{text}\n{link}" if text else link


async def read_media_bytes(media) -> bytes:  # noqa: ANN001
    """Байты медиафайла из хранилища (local или S3) — для загрузки в соцсеть."""
    from ..services import storage
    return await storage.read_file(media.file_path)


def media_filename(media, default: str = "file") -> str:
    """Имя файла для multipart — у медиафайла name может быть пустым.

    Расширение важно: VK и OK определяют тип загружаемого файла по имени.
    """
    name = (getattr(media, "name", "") or "").strip()
    if name:
        return name
    ext = {"photo": ".jpg", "video": ".mp4"}.get(getattr(media, "type", ""), "")
    stem = default.rsplit(".", 1)[0] or "file"
    return f"{stem}{ext}" if ext else default


async def read_media_bytes_to_file(media, dst_path: str) -> int:  # noqa: ANN001
    """Записывает медиафайл из хранилища в dst_path потоком (не в RAM),
    возвращает размер в байтах. Для больших видео (YouTube/Rutube)."""
    import shutil

    from ..services import storage

    if storage.is_s3():
        def down() -> int:
            obj = storage._client().get_object(Bucket=storage.settings.s3_bucket,
                                               Key=media.file_path)
            with obj["Body"] as src, open(dst_path, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            return int(obj.get("ContentLength", os.path.getsize(dst_path)))
        return await asyncio.to_thread(down)
    src = storage.local_full(media.file_path)
    await asyncio.to_thread(shutil.copyfile, str(src), dst_path)
    return src.stat().st_size


class SocialAdapter(ABC):
    """Контракт интеграции с соцсетью. Все методы асинхронные, без блокировок.

    publish() сам учитывает настройки поста (publish_as_story/clips/shorts, pin_post)
    в рамках возможностей сети и возвращает результат публикации.
    """

    source_id: int = 0
    name: str = ""
    capabilities: tuple[str, ...] = ()
    # Сеть умеет публиковать ссылку сама (вложение с превью / свой флаг).
    # Если False — ссылка из настроек дописывается в текст поста конвейером.
    handles_link: bool = False

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
