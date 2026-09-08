"""Реестр соцсетей. source_id повторяет нумерацию hooppy там, где она известна:
ВКонтакте=1, Одноклассники=2, Facebook=3, Twitter/X=4, Instagram=6, Telegram=9,
TikTok=14, YouTube=17. Свои константы: Дзен=20, Песочница=99.
Telegram объединён в один source_id (у hooppy каналы=9 и аккаунты=11 разделены)."""
from dataclasses import dataclass

C_POST = "post"
C_PHOTO = "photo"
C_VIDEO = "video"
C_STORY = "story"
C_CLIP = "clip"
C_SHORTS = "shorts"
C_REEL = "reel"
C_DOCUMENT = "document"
C_CHANNEL = "channel"
C_CHAT = "chat"
C_ALBUM = "album"

ALL_CAPS = (C_POST, C_PHOTO, C_VIDEO, C_STORY, C_CLIP, C_SHORTS, C_REEL,
            C_DOCUMENT, C_CHANNEL, C_CHAT, C_ALBUM)


@dataclass(frozen=True)
class SourceInfo:
    id: int
    slug: str
    name: str
    adapter: str | None  # None — сеть заявлена, но адаптер ещё не реализован
    capabilities: tuple[str, ...] = ()
    experimental: bool = False
    oauth: bool = False  # есть серверный OAuth-флоу (кнопка "Подключить")
    color: str = "#5181b8"


SOURCES: dict[int, SourceInfo] = {
    1: SourceInfo(1, "vk", "ВКонтакте", "vk",
                  (C_POST, C_PHOTO, C_VIDEO, C_STORY, C_CLIP, C_CHANNEL, C_ALBUM),
                  oauth=True, color="#0077ff"),
    2: SourceInfo(2, "ok", "Одноклассники", "ok", (C_POST, C_PHOTO, C_VIDEO),
                  oauth=True, color="#ff7700"),
    3: SourceInfo(3, "facebook", "Facebook", None, (C_POST, C_PHOTO, C_VIDEO, C_REEL),
                  color="#1877f2"),
    4: SourceInfo(4, "twitter", "Твиттер/X", None, (C_POST, C_PHOTO, C_VIDEO),
                  color="#1d9bf0"),
    6: SourceInfo(6, "instagram", "Instagram", "instagram",
                  (C_POST, C_PHOTO, C_VIDEO, C_STORY, C_REEL), color="#e1306c"),
    9: SourceInfo(9, "telegram", "Telegram", "telegram",
                  (C_POST, C_PHOTO, C_VIDEO, C_DOCUMENT, C_STORY, C_CHANNEL, C_CHAT),
                  color="#2aabee"),
    14: SourceInfo(14, "tiktok", "TikTok", "tiktok", (C_PHOTO, C_VIDEO),
                   color="#111111"),
    17: SourceInfo(17, "youtube", "YouTube", "youtube", (C_VIDEO, C_SHORTS),
                   color="#ff0000"),
    20: SourceInfo(20, "dzen", "Дзен", "dzen", (C_POST,), experimental=True,
                   color="#ffd54d"),
    99: SourceInfo(99, "mock", "Песочница (без сети)", "mock", ALL_CAPS,
                   color="#8e44ad"),
}

# Сети hooppy, которые появятся в будущих версиях
PLANNED_SOURCES: dict[int, SourceInfo] = {
    5: SourceInfo(5, "moimir", "Мой Мир", None, color="#168de2"),
    7: SourceInfo(7, "viber", "Вайбер", None, color="#7360f2"),
    8: SourceInfo(8, "pinterest", "Пинтерест", None, color="#e60023"),
    10: SourceInfo(10, "wordpress", "Вордпресс", None, color="#21759b"),
    12: SourceInfo(12, "threads", "Тредс", None, color="#101010"),
    13: SourceInfo(13, "linkedin", "ЛинкедИн", None, color="#0a66c2"),
    15: SourceInfo(15, "rutube", "Рутуб", None, color="#ef1f30"),
    16: SourceInfo(16, "whatsapp", "Вотсап", None, color="#25d366"),
    18: SourceInfo(18, "yappy", "Яппи", None, color="#ff4b4b"),
    19: SourceInfo(19, "tumblr", "Тамблер", None, color="#36465d"),
    21: SourceInfo(21, "vibes", "Вайбс", None, color="#7b61ff"),
    22: SourceInfo(22, "max", "Макс", None, color="#6b4eff"),
}

SOURCE_BY_SLUG = {s.slug: s for s in list(SOURCES.values()) + list(PLANNED_SOURCES.values())}

# Псевдо-источник для кросспостинга (не соцсеть — аккаунт не подключается)
SOURCE_RSS = 30


def get_source(source_id: int) -> SourceInfo | None:
    return SOURCES.get(source_id) or PLANNED_SOURCES.get(source_id)
