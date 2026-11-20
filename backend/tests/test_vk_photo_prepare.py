"""Подготовка фото к загрузке в ВК.

Серверы загрузки ВК (pu.vk.com) молча отвергают файлы, не проходящие
технические требования vk.com/faq18320 (форматы GIF/JPG/PNG, до 50 МБ,
сумма сторон до 14000 px): HTTP 200 с пустым/отсутствующим photo,
post_id не возвращается — пост не выходит в ВК вообще, хотя ОК/Telegram/
Facebook тот же файл принимают (инцидент 20.11.2026: 9 попыток загрузки
одного фото, ни одного photos.saveWallPhoto).

VKAdapter._prepare_photo приводит такие файлы к требованиям ВК,
а чистые JPEG/PNG в лимитах не перекодирует.
"""
import os
from io import BytesIO

from PIL import Image

from app.integrations import vk
from app.integrations.vk import VKAdapter


def _img_bytes(fmt: str, mode: str = "RGB", size=(60, 40),
               color=(120, 140, 160)) -> bytes:
    """Картинка в памяти в заданном формате/режиме (как её прислал пользователь)."""
    img = Image.new(mode, size, color)
    buf = BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def test_clean_jpeg_passthrough():
    """Обычный RGB-JPEG в лимитах не перекодируется — ноль потерь качества."""
    data = _img_bytes("JPEG")
    out, fname, ctype = VKAdapter._prepare_photo(data, "pic.jpg", "image/jpeg")
    assert out == data
    assert (fname, ctype) == ("pic.jpg", "image/jpeg")


def test_clean_transparent_png_passthrough():
    """Прозрачный PNG в лимитах уходит как есть."""
    data = _img_bytes("PNG", mode="RGBA")
    out, fname, ctype = VKAdapter._prepare_photo(data, "logo.png", "image/png")
    assert out == data
    assert (fname, ctype) == ("logo.png", "image/png")


def test_cmyk_jpeg_reencoded_to_rgb():
    """CMYK-JPEG (экспорт из графических редакторов) — классика тихого отказа
    серверов загрузки ВК. Перекодируем в RGB-JPEG."""
    data = _img_bytes("JPEG", mode="CMYK", color=(10, 20, 30, 5))
    out, fname, ctype = VKAdapter._prepare_photo(data, "print.jpg", "image/jpeg")
    img = Image.open(BytesIO(out))
    assert img.format == "JPEG"
    assert img.mode == "RGB"
    assert fname.endswith(".jpg")
    assert ctype == "image/jpeg"


def test_webp_reencoded_to_jpeg():
    """WebP ВК не принимает вовсе (только GIF/JPG/PNG) — перекодируем в JPEG."""
    data = _img_bytes("WEBP")
    out, fname, ctype = VKAdapter._prepare_photo(data, "shot.webp", "image/webp")
    img = Image.open(BytesIO(out))
    assert img.format == "JPEG"
    assert fname.endswith(".jpg")
    assert ctype == "image/jpeg"


def test_oversize_dims_downscaled():
    """Сумма сторон больше 14000 px (7500+6600=14100) — ужмаем до запаса
    до лимита, пропорции сохраняются."""
    data = _img_bytes("JPEG", size=(7500, 6600))
    out, fname, ctype = VKAdapter._prepare_photo(data, "pano.jpg", "image/jpeg")
    img = Image.open(BytesIO(out))
    assert img.width + img.height <= vk.VK_MAX_SIDE_SUM
    assert img.width + img.height >= vk.VK_SIDE_SUM_TARGET - 10
    assert fname.endswith(".jpg")


def test_oversize_transparent_png_stays_png():
    """Перегабаритный прозрачный PNG ужимается, но остаётся PNG с альфой."""
    data = _img_bytes("PNG", mode="RGBA", size=(13999, 2))
    out, fname, ctype = VKAdapter._prepare_photo(data, "wide.png", "image/png")
    img = Image.open(BytesIO(out))
    assert img.format == "PNG"
    assert "A" in img.mode or img.mode == "P"
    assert img.width + img.height <= vk.VK_MAX_SIDE_SUM
    assert fname.endswith(".png")


def test_oversize_file_compressed(monkeypatch):
    """Файл ближе к лимиту 50 МБ — лесенка качества и даунскейл доводят
    размер до целевого (лимит в тесте занижен, чтобы не гонять 45 МБ)."""
    monkeypatch.setattr(vk, "VK_PHOTO_BYTES_TARGET", 4000)
    noise = Image.frombytes("RGB", (300, 300), os.urandom(300 * 300 * 3))
    buf = BytesIO()
    noise.save(buf, "JPEG", quality=95)
    data = buf.getvalue()
    assert len(data) > 4000

    out, fname, ctype = VKAdapter._prepare_photo(data, "noise.jpg", "image/jpeg")
    assert len(out) <= 4000
    assert Image.open(BytesIO(out)).format == "JPEG"
    assert fname.endswith(".jpg")


def test_unparseable_bytes_passthrough():
    """Файл, который Pillow не разбирает, уходит как есть — пусть отвечает VK."""
    data = b"not an image at all"
    out, fname, ctype = VKAdapter._prepare_photo(data, "x.jpg", "image/jpeg")
    assert out == data
    assert (fname, ctype) == ("x.jpg", "image/jpeg")
