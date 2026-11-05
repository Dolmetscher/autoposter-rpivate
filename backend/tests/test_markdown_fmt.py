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


# ---------- Telegram MarkdownV2 ----------


def test_mdv2_basic_entities():
    from app.integrations._mdfmt import md_to_telegram_markdownv2 as mdv2
    assert mdv2("**жирный**") == "*жирный*"
    assert mdv2("*курсив*") == "_курсив_"
    assert mdv2("__подчёркнутый__") == "__подчёркнутый__"
    assert mdv2("~~зачёркнутый~~") == "~зачёркнутый~"
    assert mdv2("`код`") == "`код`"
    assert mdv2("# Заголовок") == "*Заголовок*"
    assert mdv2("[сайт](https://ex.ru)") == "[сайт](https://ex.ru)"


def test_mdv2_escapes_specials():
    from app.integrations._mdfmt import md_to_telegram_markdownv2 as mdv2
    # вне сущностей экранируются все 18 спецсимволов MarkdownV2
    assert mdv2("2+2=4. Привет!") == "2\\+2\\=4\\. Привет\\!"
    assert mdv2("a_b * c | d") == "a\\_b \\* c \\| d"
    assert mdv2("привет, мир 2026") == "привет, мир 2026"
    # внутри кода экранируются только ` и \
    assert mdv2("`a_b.c`") == "`a_b.c`"
    assert mdv2("```\nx = 1 < 2\n```") == "```x = 1 < 2\n```"
    # в URL ссылки экранируются ) и \
    assert mdv2("[t](https://e.ru/x)") == "[t](https://e.ru/x)"


def test_mdv2_unbalanced_markers_stay_escaped_text():
    from app.integrations._mdfmt import md_to_telegram_markdownv2 as mdv2
    assert mdv2("**незакрытый") == "\\*\\*незакрытый"


# ---------- rich HTML (WordPress, Tumblr, RSS-фид) ----------


def test_rich_html_paragraphs_and_heads():
    from app.integrations._mdfmt import md_to_rich_html as rich
    assert rich("первый\n\nвторой") == "<p>первый</p><p>второй</p>"
    assert rich("# Заголовок\nтекст") == "<h2>Заголовок</h2><p>текст</p>"
    assert rich("## Раздел") == "<h3>Раздел</h3>"
    assert rich("a\nb") == "<p>a<br/>b</p>"


def test_rich_html_inline_and_fence():
    from app.integrations._mdfmt import md_to_rich_html as rich
    assert rich("**жирный** и [сайт](https://e.ru)") == \
        '<p><b>жирный</b> и <a href="https://e.ru">сайт</a></p>'
    assert rich("__подч__ ~~зач~~ `код`") == "<p><u>подч</u> <s>зач</s> <code>код</code></p>"
    assert rich("`<div>`") == "<p><code>&lt;div&gt;</code></p>"
    assert rich("до\n```py\nx < 2\n```\nпосле") == \
        "<p>до</p><pre>x &lt; 2\n</pre><p>после</p>"


def test_rich_html_escapes_and_unbalanced():
    from app.integrations._mdfmt import md_to_rich_html as rich
    assert rich("a < b & c") == "<p>a &lt; b &amp; c</p>"
    assert rich("**незакрытый") == "<p>**незакрытый</p>"


# ---------- MAX Markdown ----------


def test_max_markdown_dialect():
    from app.integrations._mdfmt import md_to_max_markdown as mdmax
    assert mdmax("**жирный**") == "**жирный**"
    assert mdmax("__подчёркнутый__") == "++подчёркнутый++"  # в MAX __ — это жирный
    assert mdmax("_курсив_") == "*курсив*"
    assert mdmax("~~зач~~ и `код`") == "~~зач~~ и `код`"
    assert mdmax("# Заголовок") == "# Заголовок"
    assert mdmax("[текст](https://e.ru)") == "[текст](https://e.ru)"
    assert mdmax("```\nx=1\n```") == "x=1\n"  # блочного кода в MAX нет
    assert mdmax("some_var_name = 1") == "some_var_name = 1"


# ---------- Дзен: Draft.js contentState ----------


def test_dzen_blocks_styles_and_links():
    from app.integrations.dzen import _md_blocks
    blocks, em = _md_blocks("**Жирный** текст и [сайт](https://e.ru)")
    assert len(blocks) == 1
    b = blocks[0]
    assert b["text"] == "Жирный текст и сайт"
    assert b["inlineStyleRanges"] == [{"offset": 0, "length": 6, "style": "BOLD"}]
    assert b["entityRanges"] == [{"offset": 15, "length": 4, "key": 0}]
    assert em["0"]["type"] == "LINK"
    assert em["0"]["data"]["url"] == "https://e.ru"


def test_dzen_blocks_head_strike_under():
    from app.integrations.dzen import _md_blocks
    blocks, em = _md_blocks("# Заголовок\n~~зач~~ и __подч__")
    assert blocks[0]["text"] == "Заголовок"
    assert blocks[0]["inlineStyleRanges"] == [
        {"offset": 0, "length": 9, "style": "BOLD"}]
    assert blocks[1]["text"] == "зач и подч"
    styles = {(s["style"], s["offset"]) for s in blocks[1]["inlineStyleRanges"]}
    assert styles == {("STRIKETHROUGH", 0), ("UNDERLINE", 6)}
    assert em == {}


def test_dzen_blocks_fence_and_empty():
    from app.integrations.dzen import _md_blocks
    blocks, _ = _md_blocks("до\n```\na\nb\n```\nпосле")
    texts = [b["text"] for b in blocks]
    assert "a" in texts and "b" in texts and "```" not in "".join(texts)
    assert texts[0] == "до" and texts[-1] == "после"
    empty, em = _md_blocks("")
    assert empty[0]["text"] == "" and em == {}


def test_dzen_content_state_json():
    from app.integrations.dzen import text_to_content_state
    state = json.loads(text_to_content_state("привет", ["img1"]))
    assert state["gallery"]["items"] == [{"imageId": {"id": "img1"}}]
    inner = json.loads(state["content"])
    assert inner["blocks"][0]["text"] == "привет"
    assert inner["entityMap"] == {}


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


async def test_max_sends_format_markdown(monkeypatch):
    """MAX: текст уходит с format=markdown, __подч__ → ++подч++."""
    from app.integrations.base import PublishContent
    from app.integrations.max import MaxAdapter

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"message": {"body": {"mid": "m1"}}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            captured["url"], captured["json"] = url, kw.get("json")
            return FakeResp()

    monkeypatch.setattr("app.integrations.max.httpx.AsyncClient", FakeClient)

    class _A:
        credentials = {"access_token": "t"}

    class _Page:
        account = _A()
        social_page_id = "42"

    out = await MaxAdapter().publish(_Page(), PublishContent(
        text="__подч__ и **ж**"))
    assert out.ok
    assert captured["json"]["format"] == "markdown"
    assert captured["json"]["text"] == "++подч++ и **ж**"


async def test_facebook_message_is_plain(monkeypatch):
    """Facebook: markdown снимается — «звёздочки» не попадают в пост."""
    from app.integrations.base import PublishContent
    from app.integrations.facebook import FacebookAdapter

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"post_id": "1_2"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, files=None, **kw):
            captured["url"], captured["data"] = url, data
            return FakeResp()

        async def get(self, url, params=None, **kw):
            return FakeResp()

    monkeypatch.setattr("app.integrations.facebook.httpx.AsyncClient", FakeClient)

    class _A:
        credentials = {"access_token": "t"}

    class _Page:
        account = _A()
        social_page_id = "p1"

    out = await FacebookAdapter().publish(_Page(), PublishContent(
        text="**ж** и [сайт](https://e.ru)"))
    assert out.ok
    assert captured["data"]["message"] == "ж и сайт (https://e.ru)"
