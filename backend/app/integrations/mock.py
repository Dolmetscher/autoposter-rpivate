"""Песочница: имитирует публикацию без обращения к внешним сетям.
Позволяет протестировать весь конвейер (файлы, посты, расписание, API, кросспостинг)
без ключей соцсетей."""
import asyncio
import uuid

from ..constants import ALL_CAPS
from .base import PageInfo, PublishContent, PublishOutcome, SocialAdapter, SourceItem


class MockAdapter(SocialAdapter):
    source_id = 99
    name = "Песочница"
    capabilities = ALL_CAPS

    async def fetch_pages(self, account, extra: dict | None = None) -> list[PageInfo]:
        prefix = f"m{account.id}"
        return [
            PageInfo(f"{prefix}_blog", "Мой блог (песочница)", kind="group"),
            PageInfo(f"{prefix}_news", "Новости проекта (песочница)", kind="channel"),
            PageInfo(f"{prefix}_me", "Личная страница (песочница)", kind="user"),
        ]

    def connect_requires(self) -> list[dict]:
        return [{"name": "nickname", "label": "Никнейм (любой)", "required": False}]

    async def publish(self, page, content: PublishContent) -> PublishOutcome:
        await asyncio.sleep(0.05)
        ext_id = f"mock_{uuid.uuid4().hex[:12]}"
        return PublishOutcome(True, external_id=ext_id,
                              external_url=f"https://mock.local/{page.social_page_id}/{ext_id}")

    async def delete(self, page, external_id: str) -> bool:
        return True

    async def fetch_stats(self, page, external_id: str) -> dict | None:  # noqa: ANN001
        import hashlib
        h = int(hashlib.md5(external_id.encode()).hexdigest(), 16)
        return {"views": h % 5000, "likes": h % 300, "reposts": h % 50, "comments": h % 30}

    async def fetch_source_items(self, source_ref: str, limit: int = 10,
                                 account=None) -> list[SourceItem]:
        return []
