"""Реестр адаптеров. Каждая поддерживаемая соцсеть реализует SocialAdapter."""
from functools import lru_cache

from ..constants import SOURCES
from .base import AdapterError, SocialAdapter
from .dzen import DzenAdapter
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .linkedin import LinkedInAdapter
from .max import MaxAdapter
from .mock import MockAdapter
from .ok import OKAdapter
from .pinterest import PinterestAdapter
from .rutube import RutubeAdapter
from .telegram import TelegramAdapter
from .tiktok import TikTokAdapter
from .vk import VKAdapter
from .wordpress import WordPressAdapter
from .youtube import YouTubeAdapter

ADAPTER_BY_SLUG = {
    "mock": MockAdapter,
    "vk": VKAdapter,
    "telegram": TelegramAdapter,
    "ok": OKAdapter,
    "dzen": DzenAdapter,
    "youtube": YouTubeAdapter,
    "instagram": InstagramAdapter,
    "tiktok": TikTokAdapter,
    "wordpress": WordPressAdapter,
    "facebook": FacebookAdapter,
    "linkedin": LinkedInAdapter,
    "pinterest": PinterestAdapter,
    "max": MaxAdapter,
    "rutube": RutubeAdapter,
}

_cache: dict[str, SocialAdapter] = {}


def get_adapter(source_id: int) -> SocialAdapter:
    info = SOURCES.get(source_id)
    if info is None or info.adapter is None:
        raise AdapterError("Эта соцсеть пока не поддерживается")
    if info.adapter not in _cache:
        _cache[info.adapter] = ADAPTER_BY_SLUG[info.adapter]()
    return _cache[info.adapter]
