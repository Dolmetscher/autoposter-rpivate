import string
import uuid
from datetime import datetime, timedelta, timezone

ALPHABET = string.ascii_lowercase + string.digits


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_file_id() -> str:
    """Идентификатор файла в стиле hooppy: qb0ips21yr_6f1sy4sflx"""
    import random

    rng = random.SystemRandom()
    a = "".join(rng.choice(ALPHABET) for _ in range(10))
    b = "".join(rng.choice(ALPHABET) for _ in range(10))
    return f"{a}_{b}"


def today_folder() -> str:
    return utcnow().strftime("%Y-%m-%d")


def add_duration(dt: datetime, days: int = 0, hours: int = 0) -> datetime:
    return dt + timedelta(days=days, hours=hours)
