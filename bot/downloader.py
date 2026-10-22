import asyncio
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadCancelled

URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com|tiktok\.com|instagram\.com)"
    r"(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)

MAX_HEIGHT = 1080
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".opus", ".ogg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


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
    id: str
    extractor: str
    title: str
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    options: list[Option]

    def cache_key(self, option: Option) -> str:
        return f"{self.extractor}:{self.id}:{option.key}"


@dataclass
class Progress:
    """Состояние загрузки; пишется из потока yt-dlp, читается из asyncio."""

    stage: str = "start"  # start | download | process
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: int | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def hook(self, d: dict) -> None:
        if self.cancel.is_set():
            raise DownloadCancelled()
        if d["status"] == "downloading":
            self.stage = "download"
            self.downloaded = d.get("downloaded_bytes") or 0
            self.total = d.get("total_bytes") or d.get("total_bytes_estimate")
            self.speed = d.get("speed")
            self.eta = d.get("eta")
        elif d["status"] == "finished":
            self.stage = "process"

    def pp_hook(self, d: dict) -> None:
        if self.cancel.is_set():
            raise DownloadCancelled()
        if d["status"] == "started":
            self.stage = "process"


@dataclass
class DownloadResult:
    path: Path
    thumbnail: Path | None
    tmp: tempfile.TemporaryDirectory

    def cleanup(self) -> None:
        self.tmp.cleanup()


def find_url(text: str | None) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,!?)") if m else None


def human_size(size: float | None) -> str:
    if not size:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit in ("Б", "КБ") else f"{size:.1f} {unit}"
        size /= 1024
    return "?"


def _base_opts(cookies: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30}
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _clean(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    low = msg.lower()
    if "login" in low or "cookies" in low or "sign in" in low:
        return "Сайт требует авторизацию (приватное видео или нужен cookies.txt)."
    if "private" in low:
        return "Видео приватное."
    if "unsupported url" in low:
        return "Эта ссылка не поддерживается."
    if "not available" in low or "unavailable" in low:
        return "Видео недоступно (удалено или ограничено по региону)."
    return msg[:300]


def _fmt_size(f: dict, duration: float | None) -> int | None:
    size = f.get("filesize") or f.get("filesize_approx")
    if not size and f.get("tbr") and duration:
        size = f["tbr"] * 125 * duration  # кбит/с → байты
    return int(size) if size else None


def _is_h264(f: dict) -> bool:
    return (f.get("vcodec") or "").startswith(("avc1", "h264"))


def _video_selector(height: int) -> str:
    # предпочитаем H.264+AAC: они проигрываются в Telegram на всех клиентах
    h = f"[height<={height}]"
    return f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/b{h}[vcodec^=avc1]/b{h}[ext=mp4]/bv*{h}+ba/b{h}/b"


BEST_SELECTOR = "b[vcodec^=avc1]/b[vcodec^=h264]/bv*[vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"


def build_options(info: dict, max_bytes: int | None = None) -> list[Option]:
    formats = info.get("formats") or []
    duration = info.get("duration")

    audio = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    m4a = [f for f in audio if f.get("ext") == "m4a"] or audio
    audio_size = max((_fmt_size(f, duration) or 0 for f in m4a), default=0)

    # height -> (is_h264, size); при равной высоте берём H.264, т.к. его и выберет селектор
    by_height: dict[int, tuple[bool, int | None]] = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none") or h > MAX_HEIGHT:
            continue
        size = _fmt_size(f, duration)
        if size and f.get("acodec") in (None, "none"):
            size += audio_size
        cand = (_is_h264(f), size)
        cur = by_height.get(h)
        if cur is None or cand[0] > cur[0] or (cand[0] == cur[0] and size and not cur[1]):
            by_height[h] = cand

    def label(icon: str, name: str, size: int | None) -> str:
        if not size:
            return f"{icon} {name}"
        warn = "⚠️ " if max_bytes and size > max_bytes else ""
        return f"{warn}{icon} {name} · ~{human_size(size)}"

    options: list[Option] = []
    for h in sorted(by_height, reverse=True)[:5]:
        size = by_height[h][1]
        options.append(Option(f"v{h}", label("🎬", f"{h}p", size), _video_selector(h), size=size))
    if not options:  # TikTok/Instagram часто без height
        options.append(Option("vbest", "🎬 Видео (лучшее качество)", BEST_SELECTOR))

    a_size = audio_size or (int(duration * 24000) if duration else None)  # ~192 кбит/с mp3
    options.append(Option("audio", label("🎵", "Аудио MP3", a_size), "ba/b", True, a_size))
    return options


def _pick_thumbnail(info: dict) -> str | None:
    # Telegram надёжнее принимает jpg, чем webp
    thumbs = [t.get("url") for t in info.get("thumbnails") or [] if t.get("url")]
    jpgs = [u for u in thumbs if ".jpg" in u.split("?")[0]]
    return (jpgs[-1] if jpgs else None) or info.get("thumbnail")


async def fetch_info(url: str, cookies: str | None = None, max_bytes: int | None = None) -> VideoInfo:
    def run():
        with yt_dlp.YoutubeDL(_base_opts(cookies)) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(run)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean(e)) from e
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise DownloadError("В посте нет видео.")
        info = entries[0]
    if info.get("is_live"):
        raise DownloadError("Прямые трансляции не поддерживаются.")
    return VideoInfo(
        url=info.get("webpage_url") or url,
        id=str(info.get("id") or url),
        extractor=info.get("extractor_key") or "generic",
        title=info.get("title") or info.get("description") or "Видео",
        uploader=info.get("uploader") or info.get("channel"),
        duration=int(info["duration"]) if info.get("duration") else None,
        thumbnail=_pick_thumbnail(info),
        options=build_options(info, max_bytes),
    )


async def download(
    url: str,
    option: Option,
    max_bytes: int,
    cookies: str | None = None,
    progress: Progress | None = None,
) -> DownloadResult:
    """Скачивает файл во временную папку. Вызывающий обязан вызвать result.cleanup()."""
    progress = progress or Progress()
    tmp = tempfile.TemporaryDirectory(prefix="mediabot_")
    opts = _base_opts(cookies) | {
        "format": option.selector,
        "outtmpl": f"{tmp.name}/%(id).50s.%(ext)s",
        "max_filesize": max_bytes,
        "progress_hooks": [progress.hook],
        "postprocessor_hooks": [progress.pp_hook],
        "concurrent_fragment_downloads": 4,
    }
    if option.audio_only:
        opts["writethumbnail"] = True
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegMetadata"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
        ]
    else:
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        await asyncio.to_thread(run)
    except DownloadCancelled as e:
        tmp.cleanup()
        raise Cancelled("Загрузка отменена.") from e
    except yt_dlp.utils.DownloadError as e:
        tmp.cleanup()
        raise DownloadError(_clean(e)) from e
    except Exception:
        tmp.cleanup()
        raise

    files = [p for p in Path(tmp.name).iterdir() if p.is_file()]
    media = [p for p in files if p.suffix.lower() in MEDIA_EXTS]
    if not media:
        tmp.cleanup()
        raise DownloadError(
            f"Файл не скачан — вероятно, он больше лимита {human_size(max_bytes)}. Выберите качество ниже."
        )
    path = max(media, key=lambda p: p.stat().st_size)
    if path.stat().st_size > max_bytes:
        tmp.cleanup()
        raise DownloadError(
            f"Файл {human_size(path.stat().st_size)} больше лимита {human_size(max_bytes)}. Выберите качество ниже."
        )
    thumb = next((p for p in files if p.suffix.lower() in IMAGE_EXTS), None)
    return DownloadResult(path, thumb, tmp)


def render_progress(p: Progress, title: str, started: float) -> str:
    if p.stage == "process":
        return f"⚙️ Обрабатываю: {title}"
    if p.stage != "download":
        return f"⏳ Начинаю загрузку: {title}"
    lines = [f"⬇️ Скачиваю: {title}"]
    if p.total:
        pct = min(p.downloaded / p.total, 1)
        bar = "█" * int(pct * 12) + "░" * (12 - int(pct * 12))
        lines.append(f"{bar} {pct:.0%}")
        lines.append(f"{human_size(p.downloaded)} / {human_size(p.total)}")
    else:
        lines.append(human_size(p.downloaded))
    extra = []
    if p.speed:
        extra.append(f"{human_size(p.speed)}/с")
    if p.eta is not None:
        extra.append(f"осталось ~{p.eta} с")
    extra.append(f"прошло {int(time.monotonic() - started)} с")
    lines.append(" · ".join(extra))
    return "\n".join(lines)
