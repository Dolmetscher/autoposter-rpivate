"""Сети без публичного API публикации: адаптеры-заглушки в стиле Дзена.
Подключение видно в UI (с честным примечанием), при публикации вернётся
понятная ошибка — это честнее, чем прятать сеть совсем.

Статусы проверены в ноябре 2026: у всех пяти сетей по-прежнему нет
работоспособного официального способа публикации (подробности в note
каждого адаптера)."""
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
    быть админами сообществ. С фев 2024 боты создаются только на коммерческих
    условиях и умеют лишь слать сообщения подписчикам бота (broadcast)."""
    source_id = 7
    name = "Вайбер"
    capabilities = ("post",)
    note = ("публиковать в сообщества нельзя: боты не могут быть их админами; "
            "Bot API умеет только рассылать сообщения подписчикам бота, "
            "и с 2024 боты создаются на коммерческих условиях")


class WhatsAppAdapter(PlannedAdapter):
    """WhatsApp: Cloud API шлёт сообщения конкретным адресатам (и то по шаблонам).
    Groups API (2025) требует статуса Official Business Account и создаёт
    группы до 8 участников — для постинга в сообщества непригоден.
    Channels API закрыт для сторонних сервисов."""
    source_id = 16
    name = "Вотсап"
    capabilities = ("post",)
    note = ("официальный API непригоден: Cloud API шлёт лишь личные сообщения, "
            "Groups API — только для Official Business Account и группы до 8 "
            "участников, Channels API закрыт для сторонних сервисов")


class MoimirAdapter(PlannedAdapter):
    """«Мой Мир» — замороженный нишевый сервис: аудитория падает с 2014 года,
    публичного API не было и не предвидится. Оставлен в списке как заглушка."""
    source_id = 5
    name = "Мой Мир"
    capabilities = ("post",)
    note = ("сервис заморожен (нишевый проект, аудитория падает) — публичного "
            "API публикации нет; интеграция маловероятна")


class YappyAdapter(PlannedAdapter):
    """Yappy: публичного API нет, загрузка видео — только через мобильное
    приложение. Эмуляция мобильного API непрактична (ломается с каждым
    обновлением приложения)."""
    source_id = 18
    name = "Яппи"
    capabilities = ("video",)
    note = ("публичного API загрузки видео нет — загрузка только через "
            "мобильное приложение; ждём партнёрского API")


class VibesAdapter(PlannedAdapter):
    """Вайбс (ВК): публичного API нет; сервис young, следим за dev.vk.com."""
    source_id = 21
    name = "Вайбс"
    capabilities = ("video",)
    note = "публичного API нет (сервис ВК); ждём появления в dev.vk.com"
