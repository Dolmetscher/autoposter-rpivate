"""Транскодинг видео через ffmpeg: подготовка вертикальных дублей под
Shorts/Reels/Клипы/TikTok, квадрат под ленту, сжатие. ffmpeg ищется в PATH;
если его нет — понятная ошибка (функция просто недоступна, всё остальное работает)."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

MODES = {
    "vertical": {  # 9:16 — Shorts/Reels/Клипы/TikTok
        "vf": "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "suffix": "9x16",
    },
    "square": {  # 1:1 — лента
        "vf": "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080",
        "suffix": "1x1",
    },
    "compress": {  # просто сжать до 720p
        "vf": "scale=-2:720",
        "suffix": "720p",
    },
}


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise ValueError(
            "ffmpeg не найден в PATH. Установите его (https://ffmpeg.org или "
            "'winget install ffmpeg') и перезапустите сервер.")
    return found


async def transcode_video(src_data: bytes, src_name: str, mode: str) -> tuple[bytes, str]:
    """Возвращает (новые байты, суффикс режима). Бросает ValueError с понятным текстом."""
    if mode not in MODES:
        raise ValueError(f"Неизвестный режим: {mode}")
    cfg = MODES[mode]
    ffmpeg = ffmpeg_path()

    with tempfile.TemporaryDirectory(prefix="ap_transcode_") as td:
        src = Path(td) / "in.mp4"
        dst = Path(td) / "out.mp4"
        src.write_bytes(src_data)
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", cfg["vf"],
            "-c:v", "libx264", "-crf", "26", "-preset", "veryfast",
            "-c:a", "aac", "-movflags", "+faststart",
            str(dst),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError(f"ffmpeg: {err.decode(errors='replace')[:300]}")
        return dst.read_bytes(), cfg["suffix"]
