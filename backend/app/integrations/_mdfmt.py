"""Markdown → форматы соцсетей.

Пользователь пишет обычный Markdown в тексте поста; конвейер приводит его
к возможностям каждой сети:

- **Telegram** — рендер в HTML (parse_mode=HTML): жирный, курсив,
  подчёркнутый, зачёркнутый, код, блок кода, ссылки, заголовки строк.
  Альтернативно parse_mode=MarkdownV2 (настройка TELEGRAM_PARSE_MODE).
- **MAX (Bot API)** — рендер в родной диалект MAX Markdown (format=markdown):
  **жирный**, *курсив*, ++подчёркнутый++ (в MAX __так__ — жирный!),
  ~~зачёркнутый~~, `код`, ссылки, # заголовки.
- **WordPress (REST API) и Tumblr (body/caption)** — сети с полноценным
  HTML: заголовки #/## → h2/h3, абзацы <p>, переносы <br/>, инлайн-теги
  <b>/<i>/<u>/<s>/<code>/<pre>/<a>.
- **VK (wall.post), OK (mediatopic.post), Facebook, Instagram, Threads,
  LinkedIn, Twitter/X, Pinterest, YouTube, TikTok, Rutube** — форматирование
  текста API этих сетей не поддерживает: разметка аккуратно снимается,
  остаются чистый текст и ссылки (а не «звёздочки»).

Поддерживаемый синтаксис:
  **жирный**   __подчёркнутый__   *курсив* / _курсив_   ~~зачёркнутый~~
  `код`        ```блок кода```    [текст](https://url)   # Заголовок

Незакрытые/битые маркеры остаются как есть (просто текст) — публикация
никогда не ломается. Экранирование: в Telegram HTML спецсимволы
(< > &) заменяются сущностями, в том числе внутри кода.
"""
from __future__ import annotations

import re

# Инлайн-разметка (в порядке приоритета). Содержимое маркеров не должно
# начинаться/заканчиваться пробелом («a * b * c» — не курсив), а курсив на
# `_` требует границ слова, чтобы не ломать snake_case: some_var_name.
_INLINE_RE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<link_text>[^\]\n]+)\]\((?P<link_url>[^)\s]+)\)"
    r"|\*\*(?P<bold>[^*\n\s][^*]*?)\*\*"
    r"|__(?P<under>[^_\n\s][^_]*?)__"
    r"|~~(?P<strike>[^~\n]+)~~"
    r"|(?<![\w*])\*(?P<ital_a>\S[^*\n]*?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<ital_b>\S[^_\n]*?)_(?!\w)"
)
_FENCE_RE = re.compile(r"^([^\n`]*)\n")  # язык/пустая строка после ```

# Спецсимволы MarkdownV2 (Telegram): вне сущностей экранируются все 18,
# внутри code/pre — только ` и \.
_MDV2_SPECIAL = set("_*[]()~`>#+-=|{}.!")


def _tokens(text: str) -> list[tuple]:
    """Текст → список токенов ("kind", payload...). Payload уже без маркеров."""
    if not text:
        return []
    tokens: list[tuple] = []
    # Блоки кода ``` обрабатываем первыми (их содержимое не парсим).
    parts = text.split("```")
    for i, part in enumerate(parts):
        if i % 2:  # нечётные — содержимое блока кода
            m = _FENCE_RE.match(part)
            body = part[m.end():] if m and part != m.group(0) else part
            tokens.append(("fence", body))
            continue
        # Строчный уровень: заголовки по строкам, инлайн-маркеры внутри.
        for line_no, line in enumerate(part.split("\n")):
            if line_no:
                tokens.append(("text", "\n"))
            head = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if head:
                tokens.append(("head", head.group(2), len(head.group(1))))
                continue
            pos = 0
            for m in _INLINE_RE.finditer(line):
                if m.start() > pos:
                    tokens.append(("text", line[pos:m.start()]))
                if m.group("code") is not None:
                    tokens.append(("code", m.group("code")))
                elif m.group("link_text") is not None:
                    tokens.append(("link", m.group("link_text"), m.group("link_url")))
                elif m.group("bold") is not None:
                    tokens.append(("bold", m.group("bold")))
                elif m.group("under") is not None:
                    tokens.append(("under", m.group("under")))
                elif m.group("strike") is not None:
                    tokens.append(("strike", m.group("strike")))
                elif m.group("ital_a") is not None:
                    tokens.append(("italic", m.group("ital_a")))
                else:
                    tokens.append(("italic", m.group("ital_b")))
                pos = m.end()
            if pos < len(line):
                tokens.append(("text", line[pos:]))
    return tokens


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_to_telegram_html(text: str) -> str:
    """Markdown → HTML для Telegram Bot API / Telethon (parse_mode=HTML)."""
    out: list[str] = []
    for tok in _tokens(text):
        kind = tok[0]
        if kind == "text":
            out.append(_esc_html(tok[1]))
        elif kind in ("bold", "head"):
            out.append(f"<b>{_esc_html(tok[1])}</b>")
        elif kind == "italic":
            out.append(f"<i>{_esc_html(tok[1])}</i>")
        elif kind == "under":
            out.append(f"<u>{_esc_html(tok[1])}</u>")
        elif kind == "strike":
            out.append(f"<s>{_esc_html(tok[1])}</s>")
        elif kind == "code":
            out.append(f"<code>{_esc_html(tok[1])}</code>")
        elif kind == "fence":
            out.append(f"<pre>{_esc_html(tok[1])}</pre>")
        else:  # link
            url = tok[2].replace('"', "%22")
            out.append(f'<a href="{_esc_html(url)}">{_esc_html(tok[1])}</a>')
    return "".join(out)


def _esc_mdv2(s: str) -> str:
    return "".join("\\" + ch if ch in _MDV2_SPECIAL else ch for ch in s)


def _esc_mdv2_code(s: str) -> str:
    return s.replace("\\", "\\\\").replace("`", "\\`")


def md_to_telegram_markdownv2(text: str) -> str:
    r"""Markdown → разметка MarkdownV2 для Telegram Bot API (parse_mode=MarkdownV2).

    Экранируются все спецсимволы (иначе Telegram отклонит сообщение с
    HTTP 400 «can't parse entities»), внутри кода — только ` и \.
    Заголовки рендерятся жирным (как и в HTML-режиме).
    """
    out: list[str] = []
    for tok in _tokens(text):
        kind = tok[0]
        if kind == "text":
            out.append(_esc_mdv2(tok[1]))
        elif kind in ("bold", "head"):
            out.append(f"*{_esc_mdv2(tok[1])}*")
        elif kind == "italic":
            out.append(f"_{_esc_mdv2(tok[1])}_")
        elif kind == "under":
            out.append(f"__{_esc_mdv2(tok[1])}__")
        elif kind == "strike":
            out.append(f"~{_esc_mdv2(tok[1])}~")
        elif kind == "code":
            out.append(f"`{_esc_mdv2_code(tok[1])}`")
        elif kind == "fence":
            out.append(f"```{_esc_mdv2_code(tok[1])}```")
        else:  # link: в URL экранируются только ) и \
            url = tok[2].replace("\\", "\\\\").replace(")", "\\)")
            out.append(f"[{_esc_mdv2(tok[1])}]({url})")
    return "".join(out)


def md_to_rich_html(text: str) -> str:
    """Markdown → HTML с абзацами и заголовками (WordPress, Tumblr, RSS-фид).

    # Заголовок → <h2>…</h2> (уровень +1), ## → <h3> и т.д.; пустая строка
    разделяет абзацы <p>, одиночный перенос → <br/>, блок кода → <pre>.
    """
    blocks: list[str] = []
    para: list[str] = []
    has_content = False
    newlines = 0

    def flush() -> None:
        nonlocal para, has_content, newlines
        if has_content:
            blocks.append("<p>" + "".join(para) + "</p>")
        para, has_content, newlines = [], False, 0

    for tok in _tokens(text):
        kind = tok[0]
        if kind == "text" and tok[1] == "\n":
            if has_content:
                newlines += 1
            continue
        if kind in ("head", "fence"):
            # Заголовок и блок кода всегда самостоятельные блоки — без <br/>
            flush()
        else:
            if newlines >= 2:
                flush()
            elif newlines == 1 and has_content:
                para.append("<br/>")
        newlines = 0
        if kind == "head":
            level = min((tok[2] if len(tok) > 2 else 1) + 1, 6)
            blocks.append(f"<h{level}>{_esc_html(tok[1])}</h{level}>")
        elif kind == "fence":
            flush()
            blocks.append(f"<pre>{_esc_html(tok[1])}</pre>")
        elif kind == "text":
            para.append(_esc_html(tok[1]))
            has_content = True
        elif kind == "bold":
            para.append(f"<b>{_esc_html(tok[1])}</b>")
            has_content = True
        elif kind == "italic":
            para.append(f"<i>{_esc_html(tok[1])}</i>")
            has_content = True
        elif kind == "under":
            para.append(f"<u>{_esc_html(tok[1])}</u>")
            has_content = True
        elif kind == "strike":
            para.append(f"<s>{_esc_html(tok[1])}</s>")
            has_content = True
        elif kind == "code":
            para.append(f"<code>{_esc_html(tok[1])}</code>")
            has_content = True
        else:  # link
            url = tok[2].replace('"', "%22")
            para.append(f'<a href="{_esc_html(url)}">{_esc_html(tok[1])}</a>')
            has_content = True
    flush()
    return "".join(blocks)


def md_to_max_markdown(text: str) -> str:
    """Markdown → родной диалект MAX Bot API (POST /messages, format=markdown).

    Отличия от нашего синтаксиса: подчёркивание в MAX — ++так++, потому что
    __так__ в MAX означает жирный; блочного кода нет — содержимое ``` идёт
    обычным текстом. Заголовки и остальные маркеры совпадают.
    """
    out: list[str] = []
    for tok in _tokens(text):
        kind = tok[0]
        if kind == "text":
            out.append(tok[1])
        elif kind == "bold":
            out.append(f"**{tok[1]}**")
        elif kind == "head":
            level = tok[2] if len(tok) > 2 else 1
            out.append(f"{'#' * level} {tok[1]}")
        elif kind == "italic":
            out.append(f"*{tok[1]}*")
        elif kind == "under":
            out.append(f"++{tok[1]}++")
        elif kind == "strike":
            out.append(f"~~{tok[1]}~~")
        elif kind == "code":
            out.append(f"`{tok[1]}`")
        elif kind == "fence":
            out.append(tok[1])
        else:  # link
            out.append(f"[{tok[1]}]({tok[2]})")
    return "".join(out)


def md_tokens(text: str) -> list[tuple]:
    """Токены Markdown для построителей форматов сетей (Дзен: Draft.js).

    Виды: ("text", s), ("bold"|"italic"|"under"|"strike"|"code", s),
    ("head", s, уровень), ("link", текст, url), ("fence", s).
    """
    return _tokens(text)


def md_to_plain(text: str) -> str:
    """Markdown → чистый текст (сети без форматирования: VK, OK, FB, IG,
    Threads, LinkedIn, Twitter/X, Pinterest, YouTube, TikTok, Rutube).

    Ссылка [текст](url) → «текст (url)», а если текст и url совпадают —
    просто url. Маркеры без пары остаются как есть.
    """
    out: list[str] = []
    for tok in _tokens(text):
        kind = tok[0]
        if kind == "link":
            label, url = tok[1], tok[2]
            out.append(url if label.strip() == url else f"{label} ({url})")
        elif kind == "text":
            out.append(tok[1])
        else:
            out.append(tok[1])
    return "".join(out)


def md_note_for(name: str) -> str:
    """Короткое примечание про Markdown для заметки адаптера."""
    return (f"поддерживается разметка: **жирный**, *курсив*, __подчёркнутый__, "
            f"~~зачёркнутый~~, `код`, [текст](ссылка) — в {name} применяется, "
            f"в сетях без форматирования аккуратно убирается")
