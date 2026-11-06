"""Шифрование секретов в БД (Fernet). Токены соцсетей и LLM-ключи шифруются
прозрачно через TypeDecorator EncryptedJSON: в колонке хранится {"__enc": "<fernet>"}.
Старые записи в открытом виде читаются как есть (обратная совместимость)."""
from __future__ import annotations

import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import JSON, TypeDecorator

from ..config import settings

log = logging.getLogger("crypto")

_fernet: Fernet | None = None

# Сколько секретов не расшифровалось с момента последнего сохранения нового
# секрета (или старта процесса). Флаг для UI: «токены не читаются — проверьте
# SECRET_KEY/CREDENTIALS_SECRET или заново введите секреты».
_decrypt_failures = 0

# Контексты (users.llm_config, social_accounts.credentials...), по которым мы
# уже предупредили в логе: чтобы не спамить на каждый запрос, поле пользователя
# читается при каждом обращении к API.
_warned: set[str] = set()


def decrypt_failures() -> int:
    return _decrypt_failures


def _key() -> bytes:
    import base64
    import hashlib

    secret = settings.credentials_secret or settings.secret_key
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt_value(data: dict) -> dict:
    global _decrypt_failures
    # Сохранили новый секрет — считаем, что чинят: флаг нечитаемых секретов
    # сбрасываем (следующие неудачи при чтении снова его поднимут).
    _decrypt_failures = 0
    _warned.clear()
    return {"__enc": _f().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()}


def decrypt_value(stored, context: str = ""):
    if not isinstance(stored, dict):
        return {}
    raw = stored.get("__enc")
    if not raw:
        return stored  # legacy-запись в открытом виде
    try:
        return json.loads(_f().decrypt(raw.encode()).decode())
    except (InvalidToken, ValueError) as e:
        global _decrypt_failures
        _decrypt_failures += 1
        where = f" в {context}" if context else ""
        if context not in _warned:  # по каждому полю — одно предупреждение на процесс
            _warned.add(context)
            log.warning("не удалось расшифровать секреты%s (%s) — скорее всего сменился "
                        "SECRET_KEY/CREDENTIALS_SECRET, секреты придётся ввести заново. "
                        "Чтобы убрать предупреждение, заново сохраните соответствующий "
                        "раздел (например, «Настройки» для llm_config или аккаунт — для "
                        "токенов соцсети)", where, e)
        return {}


class EncryptedJSON(TypeDecorator):
    """JSON-колонка с шифрованием содержимого. Схема таблицы не меняется.

    label — имя колонки вида «users.llm_config»: попадает в лог при ошибке
    расшифровки, чтобы было видно, какой именно секрет не читается."""

    impl = JSON
    cache_ok = True

    def __init__(self, label: str = "", **kwargs):
        super().__init__(**kwargs)
        self.label = label

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value, self.label)
