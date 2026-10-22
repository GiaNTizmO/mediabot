import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/\S+",
    re.IGNORECASE,
)

MAX_HEIGHT = 1080


@dataclass
class Option:
    key: str  # "v720", "vbest", "audio"
    label: str
    selector: str
    audio_only: bool = False
    size: int | None = None  # примерный размер в байтах


@dataclass
class VideoInfo:
    url: str
    title: str
    duration: int | None
    options: list[Option]


class DownloadError(Exception):
    pass


def find_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def _base_opts(cookies: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _fmt_size(size: int | None) -> str:
    return f" (~{size / 1024 / 1024:.0f} МБ)" if size else ""


def _video_selector(height: int) -> str:
    # предпочитаем H.264+AAC: они проигрываются в Telegram без перекодирования
    h = f"[height<={height}]"
    return (
        f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/b{h}[ext=mp4]/"
        f"bv*{h}+ba/b{h}/b"
    )


def _build_options(info: dict) -> list[Option]:
    formats = info.get("formats") or []
    duration = info.get("duration")
    audio_size = max(
        (f.get("filesize") or f.get("filesize_approx") or 0
         for f in formats if f.get("vcodec") == "none"),
        default=0,
    )

    by_height: dict[int, int | None] = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none") or h > MAX_HEIGHT:
            continue
        size = f.get("filesize") or f.get("filesize_approx")
        if size and f.get("acodec") in (None, "none"):
            size += audio_size
        if h not in by_height or (size and not by_height[h]):
            by_height[h] = size

    options: list[Option] = []
    for h in sorted(by_height, reverse=True):
        options.append(
            Option(f"v{h}", f"🎬 {h}p{_fmt_size(by_height[h])}", _video_selector(h), size=by_height[h])
        )
    if not options:  # TikTok/Instagram иногда без height
        options.append(Option("vbest", "🎬 Видео (лучшее качество)", "bv*+ba/b"))

    a_size = int(audio_size or 0) or (int(duration * 16000) if duration else None)  # ~128 кбит/с
    options.append(Option("audio", f"🎵 Аудио MP3{_fmt_size(a_size)}", "ba/b", True, a_size))
    return options


async def fetch_info(url: str, cookies: str | None = None) -> VideoInfo:
    def run():
        with yt_dlp.YoutubeDL(_base_opts(cookies)) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(run)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean(e)) from e
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    return VideoInfo(
        url=url,
        title=info.get("title") or "Видео",
        duration=info.get("duration"),
        options=_build_options(info),
    )


def _clean(e: Exception) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")[:300]


async def download(
    url: str, option: Option, max_bytes: int, cookies: str | None = None
) -> tuple[Path, tempfile.TemporaryDirectory]:
    """Скачивает файл во временную папку. Вызывающий обязан вызвать .cleanup() у второго значения."""
    tmp = tempfile.TemporaryDirectory(prefix="mediabot_")
    opts = _base_opts(cookies) | {
        "format": option.selector,
        "outtmpl": f"{tmp.name}/%(id)s.%(ext)s",
        "max_filesize": max_bytes,
        "restrictfilenames": True,
    }
    if option.audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        await asyncio.to_thread(run)
    except yt_dlp.utils.DownloadError as e:
        tmp.cleanup()
        raise DownloadError(_clean(e)) from e

    files = [p for p in Path(tmp.name).iterdir() if p.is_file()]
    if not files:
        tmp.cleanup()
        raise DownloadError("Файл не скачан (возможно, превышен лимит размера).")
    path = max(files, key=lambda p: p.stat().st_size)
    if path.stat().st_size > max_bytes:
        tmp.cleanup()
        raise DownloadError(f"Файл больше лимита {max_bytes // 1024 // 1024} МБ. Выберите качество ниже.")
    return path, tmp
