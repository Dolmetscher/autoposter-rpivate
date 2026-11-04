"""Markdown-разметка в постах: конвертация в HTML для Telegram и снятие
разметки для сетей без форматирования (VK, OK)."""
import json

import pytest

from app.integrations._mdfmt import (md_note_for, md_to_plain,
                                     md_to_telegram_html)

# ---------- Telegram HTML ----------


def test_tg_basic_styles():
    assert md_to_telegram_html("**жирный**") == "<b>жирный</b>"
    assert md_to_telegram_html("*курсив*") == "<i>курсив</i>"
    assert md_to_telegram_html("_курсив_") == "<i>курсив</i>"
    assert md_to_telegram_html("__подчёркнутый__") == "<u>подчёркнутый</u>"
    assert md_to_telegram_html("~~зачёркнутый~~") == "<s>зачёркнутый</s>"
    assert md_to_telegram_html("`код`") == "<code>код</code>"


def test_tg_link_and_heading():
    assert md_to_telegram_html("[сайт](https://ex.ru)") == \
        '<a href="https://ex.ru">сайт</a>'
    assert md_to_telegram_html("# Заголовок\nтекст") == "<b>Заголовок</b>\nтекст"
    assert md_to_telegram_html("## Раздел") == "<b>Раздел</b>"


def test_tg_escapes_html():
    assert md_to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert md_to_telegram_html("`<div>&</div>`") == "<code>&lt;div&gt;&amp;&lt;/div&gt;</code>"
    assert md_to_telegram_html('["x"](https://e.ru/a"b)') == \
        '<a href="https://e.ru/a%22b">"x"</a>'


def test_tg_code_fence():
    src = "до\n```python\nx = 1 < 2\n```\nпосле"
    out = md_to_telegram_html(src)
    assert "<pre>x = 1 &lt; 2\n</pre>" in out
    assert "python" not in out  # язык блока снят
    assert out.startswith("до\n") and out.endswith("после")


def test_tg_unbalanced_markers_stay_literal():
    """Незакрытый маркер — просто текст, HTML не ломается (важно для кусков)."""
    assert md_to_telegram_html("**незакрытый") == "**незакрытый"
    assert md_to_telegram_html("текст с * одной звёздочкой") == \
        "текст с * одной звёздочкой"


def test_tg_plain_text_untouched():
    assert md_to_telegram_html("привет, мир 2026") == "привет, мир 2026"


# ---------- края, где разметку распознавать нельзя ----------


def test_no_false_positives():
    assert md_to_plain("some_var_name = 1") == "some_var_name = 1"
    assert md_to_plain("a * b * c") == "a * b * c"
    assert md_to_plain("2*3*4=24") == "2*3*4=24"
    assert md_to_plain("цена ** 5 руб") == "цена ** 5 руб"


# ---------- plain (VK, OK) ----------


def test_plain_strips_all_markers():
    src = "**жирный** и *курсив* и _курсив2_ и __подч__ и ~~зач~~ и `код`"
    assert md_to_plain(src) == "жирный и курсив и курсив2 и подч и зач и код"


def test_plain_links():
    assert md_to_plain("[текст](https://e.ru)") == "текст (https://e.ru)"
    # текст и ссылка совпадают — дублирование не нужно
    assert md_to_plain("[https://e.ru](https://e.ru)") == "https://e.ru"


def test_plain_keeps_structure():
    src = "строка 1\n\n# Заголовок\nконец"
    assert md_to_plain(src) == "строка 1\n\nЗаголовок\nконец"


def test_plain_multiline_bold_stays_literal():
    """Инлайн-разметка построчная: перенесённый маркер остаётся текстом."""
    assert md_to_plain("**раз\nдва**") == "**раз\nдва**"


# ---------- проводка в адаптеры ----------


async def test_ok_mediatopic_text_is_plain(monkeypatch):
    """OK: markdown в тексте поста уходит в mediatopic уже снятым."""
    from app.integrations.base import PublishContent
    from app.integrations.ok import OKAdapter

    captured = {}

    async def call(self, method, creds, extra_params=None):
        captured[method] = extra_params
        return "123456"

    monkeypatch.setattr(OKAdapter, "call", call)

    class _A:
        credentials = {"access_token": "t", "application_key": "k",
                       "session_secret_key": "s"}

    class _Page:
        account = _A()
        social_page_id = "g1"
        page_kind = "group"

    out = await OKAdapter().publish(_Page(), PublishContent(
        text="Привет **жирный** и [сайт](https://e.ru)"))
    assert out.ok
    att = json.loads(captured["mediatopic.post"]["attachment"])
    assert att["media"][0] == {"type": "text",
                               "text": "Привет жирный и сайт (https://e.ru)"}


async def test_tg_bot_sends_parse_mode_html(monkeypatch):
    """Telegram-бот: markdown рендерится и уходит с parse_mode=HTML."""
    import app.integrations.telegram as tg

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 5}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, data=None, files=None, **kw):
            captured.setdefault("calls", []).append((url, json, data))
            return FakeResp()

    monkeypatch.setattr(tg.httpx, "AsyncClient", FakeClient)

    class _A:
        credentials = {"bot_token": "t"}

    class _Page:
        account = _A()
        social_page_id = "@chan"
        social_page_name = "@chan"

    out = await tg.TelegramAdapter().publish(_Page(), tg.PublishContent(
        text="**Жирный** и [сайт](https://e.ru)"))
    assert out.ok
    url, payload, _ = captured["calls"][0]
    assert "sendMessage" in url
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == '<b>Жирный</b> и <a href="https://e.ru">сайт</a>'


def test_note_helpers():
    assert "Telegram" in md_note_for("Telegram")
