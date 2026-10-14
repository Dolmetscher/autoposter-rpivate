"""Сети без публичного API публикации: адаптеры-заглушки в стиле Дзена.
Подключение видно в UI, при публикации вернётся понятная ошибка —
это честнее, чем прятать сеть совсем."""
from __future__ import annotations

from .base import AdapterError, PageInfo, PublishContent, PublishOutcome, SocialAdapter


class PlannedAdapter(SocialAdapter):
    """База сетей без публичного API: publish честно говорит о статусе."""
    name = ""
    note = "у платформы нет публичного API публикации"

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        raise AdapterError(f"{self.name}: интеграция недоступна — {self.note}")

    async def publish(self, page, content: PublishContent) -> PublishOutcome:  # noqa: ANN001
        raise AdapterError(f"{self.name}: публикация через API недоступна — {self.note}")

    async def delete(self, page, external_id: str) -> bool:  # noqa: ANN001
        raise AdapterError(f"{self.name}: удаление недоступно — {self.note}")


class ViberAdapter(PlannedAdapter):
    """Viber: у Bot API нет публикации в каналы/сообщества — боты не могут
    быть админами сообществ; только рассылки по одобренным шаблонам."""
    source_id = 7
    name = "Вайбер"
    capabilities = ("post",)
    note = ("Bot API не даёт публиковать в сообщества (бот не может быть их админом); "
            "поддержка появится с открытием публичного API")


class WhatsAppAdapter(PlannedAdapter):
    """WhatsApp: Cloud API шлёт сообщения конкретным адресатам (и то по шаблонам),
    Channels API закрыт для сторонних сервисов."""
    source_id = 16
    name = "Вотсап"
    capabilities = ("post",)
    note = ("Cloud API отправляет личные сообщения, а Channels API закрыт "
            "для сторонних сервисов")


class MoimirAdapter(PlannedAdapter):
    source_id = 5
    name = "Мой Мир"
    capabilities = ("post",)
    note = "платформа закрыта для сторонних разработчиков"


class YappyAdapter(PlannedAdapter):
    source_id = 18
    name = "Яппи"
    capabilities = ("video",)
    note = "публичного API загрузки видео нет"


class VibesAdapter(PlannedAdapter):
    source_id = 21
    name = "Вайбс"
    capabilities = ("video",)
    note = "публичного API загрузки видео нет"
