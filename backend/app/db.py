from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)

# SQLite: WAL (параллельные читатели + один писатель) и терпеливые блокировки —
# без этого параллельные записи API и тика планировщика ловят "database is locked".
# foreign_keys=ON — SQLite по умолчанию НЕ проверяет внешние ключи, из-за чего
# удаление файла/страницы оставляло «висящие» post_attachments/post_targets,
# и сериализация постов падала. С ON работают те же ON DELETE CASCADE, что и в PostgreSQL.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
