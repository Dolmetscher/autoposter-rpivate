"""Минимальный OAuth 1.0a-клиент (HMAC-SHA1) без внешних зависимостей —
достаточно для Twitter/X API и Tumblr. Все запросы POST/GET form-encoded."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

import httpx


def _enc(s: str) -> str:
    return quote(s, safe="-._~")


def oauth1_header(method: str, url: str, creds: dict,
                  params: dict | None = None, nonce: str = "",
                  timestamp: str = "") -> str:
    """Строит заголовок Authorization: OAuth ... по consumer/token из creds:
    api_key, api_secret, access_token, access_token_secret."""
    oauth_params = {
        "oauth_consumer_key": creds["api_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds["access_token"],
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **(params or {})}
    base_str = "&".join(
        f"{_enc(str(k))}={_enc(str(all_params[k]))}" for k in sorted(all_params))
    base_url = url.split("?", 1)[0]
    signature_base = "&".join([method.upper(), _enc(base_url), _enc(base_str)])
    key = f"{_enc(creds['api_secret'])}&{_enc(creds.get('access_token_secret', ''))}"
    sig = base64.b64encode(hmac.new(key.encode(), signature_base.encode(),
                                    hashlib.sha1).digest()).decode()
    oauth_params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"'
                                for k, v in sorted(oauth_params.items()))


class OAuth1Client:
    """Асинхронный клиент с OAuth 1.0a-подписью каждого запроса."""

    def __init__(self, creds: dict, timeout: int = 60):
        self.creds = creds
        self.timeout = timeout

    async def request(self, method: str, url: str, *, params: dict | None = None,
                      data: dict | None = None, files=None) -> httpx.Response:
        signing_params = {**(data or {}), **(params or {})}
        header = oauth1_header(method, url, self.creds, signing_params)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            return await client.request(method, url, params=params, data=data,
                                        files=files, headers={"Authorization": header})

    async def request_json(self, method: str, url: str, *, body: dict,
                           params: dict | None = None) -> httpx.Response:
        """JSON-запрос (Twitter API v2): в подпись идут только oauth-параметры."""
        header = oauth1_header(method, url, self.creds, params)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            return await client.request(method, url, params=params, json=body,
                                        headers={"Authorization": header,
                                                 "Content-Type": "application/json"})


def percent_encode_bytes(name: str) -> str:
    """Имя файла для multipart — OAuth1-подпись multipart не требует, но кодировка полезна."""
    return _enc(name)
