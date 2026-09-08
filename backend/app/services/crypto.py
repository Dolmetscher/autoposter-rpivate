"""Шифрование секретов в БД (Fernet). Токены соцсетей и LLM-ключи шифруются
прозрачно через TypeDecorator EncryptedJSON: в колонке хранится {"enc": "<fernet>"}.
Старые записи в открытом виде читаются как есть (обратная совместимость)."""
from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import JSON, TypeDecorator

from ..config import settings

_fernet: Fernet | None = None


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
    return {"__enc": _f().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()}


def decrypt_value(stored):
    if not isinstance(stored, dict):
        return {}
    raw = stored.get("__enc")
    if not raw:
        return stored  # legacy-запись в открытом виде
    try:
        return json.loads(_f().decrypt(raw.encode()).decode())
    except (InvalidToken, ValueError):
        return {}


class EncryptedJSON(TypeDecorator):
    """JSON-колонка с шифрованием содержимого. Схема таблицы не меняется."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)
