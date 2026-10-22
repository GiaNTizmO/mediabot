import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from .media import AudioTags, make_cover, tag_audio

URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*"
    r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com|tiktok\.com|instagram\.com)"
    r"(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)

log = logging.getLogger(__name__)

MAX_HEIGHT = 1080
# во время слияния видео+аудио на диске одновременно лежат исходники и результат
DISK_HEADROOM = 2.2
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".opus", ".ogg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


@dataclass
class Option:
    key: str  # "v720", "vbest", "mp3", "m4a"
    label: str
    selector: str
    audio_codec: str | None = None  # "mp3" / "m4a" для аудио, None для видео
    size: int | None = None  # примерный размер в байтах

    @property
    def audio_only(self) -> bool:
        return self.audio_codec is not None


@dataclass
class AudioTrack:
    lang: str  # код языка как у yt-dlp: "ru", "en-US", "en-desc"
    name: str
    original: bool = False

    @property
    def label(self) -> str:
        return f"{self.name} · оригинал" if self.original else self.name


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
    artist: str | None = None
    track: str | None = None
    album: str | None = None
    year: str | None = None
    is_music: bool = False
    audio_tracks: list[AudioTrack] = field(default_factory=list)  # пусто, если дорожка одна

    def cache_key(self, option: Option, lang: str | None = None) -> str:
        key = f"{self.extractor}:{self.id}:{option.key}"
        return f"{key}:{lang}" if lang else key

    def find_track(self, lang: str | None) -> AudioTrack | None:
        return next((t for t in self.audio_tracks if t.lang == lang), None)

    @property
    def audio_title(self) -> str:
        return self.track or self.title

    @property
    def audio_artist(self) -> str | None:
        artist = self.artist or self.uploader
        return re.sub(r"\s+-\s+Topic$", "", artist) if artist else None

    def file_stem(self, option: Option) -> str:
        """Имя файла без расширения: название ролика, для музыки — «Исполнитель - Трек»."""
        if option.audio_only and self.track and self.audio_artist:
            return safe_filename(f"{self.audio_artist} - {self.track}")
        return safe_filename(self.title, "audio" if option.audio_only else "video")

    def audio_tags(self) -> AudioTags:
        return AudioTags(self.audio_title, self.audio_artist, self.album, self.year)


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


def temp_root(base: str | None = None) -> Path:
    """Папка, в которой бот держит временные файлы. Вся она принадлежит боту."""
    root = Path(base or tempfile.gettempdir()) / "mediabot"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sweep_temp(base: str | None = None) -> int:
    """Удаляет остатки прошлых запусков (после падения или перезапуска). Возвращает число удалённых папок."""
    root = temp_root(base)
    removed = 0
    for p in root.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        removed += 1
    return removed


def check_free_space(base: str | None, need: int | None) -> None:
    if not need:
        return
    free = shutil.disk_usage(temp_root(base)).free
    if free < need * DISK_HEADROOM:
        raise DownloadError(
            f"На сервере мало места: свободно {human_size(free)}, а нужно ~{human_size(need * DISK_HEADROOM)}. "
            "Выберите качество ниже или попробуйте позже."
        )


def safe_filename(name: str, fallback: str = "video", max_bytes: int = 180) -> str:
    """Название ролика → допустимое имя файла (Windows/Android/Linux)."""
    name = re.sub(r"https?://\S+", " ", name)
    no_tags = re.sub(r"(?:^|\s)#\S+", " ", name)  # хэштеги TikTok/Instagram
    if no_tags.strip():
        name = no_tags
    name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    encoded = name.encode()
    if len(encoded) > max_bytes:  # лимит ФС — 255 байт, кириллица занимает по 2
        name = encoded[:max_bytes].decode(errors="ignore").rstrip(" .") + "…"
    return name or fallback


LANG_NAMES = {
    "ru": ("🇷🇺", "Русский"), "en": ("🇬🇧", "Английский"), "uk": ("🇺🇦", "Украинский"),
    "be": ("🇧🇾", "Белорусский"), "kk": ("🇰🇿", "Казахский"), "uz": ("🇺🇿", "Узбекский"),
    "es": ("🇪🇸", "Испанский"), "pt": ("🇵🇹", "Португальский"), "de": ("🇩🇪", "Немецкий"),
    "fr": ("🇫🇷", "Французский"), "it": ("🇮🇹", "Итальянский"), "pl": ("🇵🇱", "Польский"),
    "nl": ("🇳🇱", "Нидерландский"), "tr": ("🇹🇷", "Турецкий"), "ar": ("🇸🇦", "Арабский"),
    "he": ("🇮🇱", "Иврит"), "hi": ("🇮🇳", "Хинди"), "bn": ("🇧🇩", "Бенгальский"),
    "ta": ("🇮🇳", "Тамильский"), "te": ("🇮🇳", "Телугу"), "ml": ("🇮🇳", "Малаялам"),
    "ja": ("🇯🇵", "Японский"), "ko": ("🇰🇷", "Корейский"), "zh": ("🇨🇳", "Китайский"),
    "id": ("🇮🇩", "Индонезийский"), "vi": ("🇻🇳", "Вьетнамский"), "th": ("🇹🇭", "Тайский"),
    "fil": ("🇵🇭", "Филиппинский"),
}


def _flag(region: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in region.upper())


def language_name(code: str) -> str:
    base, *parts = code.split("-")
    flag, name = LANG_NAMES.get(base.lower(), ("🔊", base))
    for part in parts:
        if part.lower() == "desc":
            name += " (тифлокомментарий)"
        elif part == "419":
            name += " (Лат. Америка)"
        elif len(part) == 2 and part.isalpha():
            flag = _flag(part)
    return f"{flag} {name}"


def audio_tracks(formats: list[dict]) -> list[AudioTrack]:
    """Звуковые дорожки (озвучки) ролика. Пустой список, если дорожка одна."""
    prefs: dict[str, int] = {}
    for f in formats:
        lang = f.get("language")
        if lang and f.get("acodec") not in (None, "none"):
            prefs[lang] = max(prefs.get(lang, -100), f.get("language_preference") or -1)
    if len(prefs) < 2:
        return []
    tracks = [AudioTrack(lang, language_name(lang), pref >= 10) for lang, pref in prefs.items()]
    names = [t.name for t in tracks]
    for t in tracks:  # en-US и en-GB без флага-различия — добавляем код
        if names.count(t.name) > 1:
            t.name += f" ({t.lang})"
    def order(t: AudioTrack):
        # оригинал, затем русская озвучка, затем остальные по алфавиту, тифлокомментарии в конце
        return (not t.original, "desc" in t.lang, t.lang.split("-")[0] != "ru", t.name.split(" ", 1)[-1])

    return sorted(tracks, key=order)


def with_language(selector: str, lang: str) -> str:
    """Ограничивает звук в селекторе формата выбранной дорожкой: ba → ba[language=ru], b → b[language=ru].
    bv* → bv: видео берём строго без звука, иначе yt-dlp может взять ролик со звуком другой дорожки."""
    selector = selector.replace("bv*", "bv")
    return re.sub(r"(?<![\w*])(ba|b)(?=[\[/+]|$)", rf"\1[language={lang}]", selector)


def is_music_url(url: str) -> bool:
    return "music.youtube.com" in url.lower()


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


# дополнительные extractor_args для yt-dlp, задаются один раз при старте (см. configure_ytdlp)
_EXTRACTOR_ARGS: dict[str, dict[str, list[str]]] = {}


def configure_ytdlp(pot_provider_url: str | None = None) -> None:
    """PO-токены через bgutil-ytdlp-pot-provider: помогают YouTube на серверных IP."""
    _EXTRACTOR_ARGS.clear()
    if pot_provider_url:
        _EXTRACTOR_ARGS["youtubepot-bgutilhttp"] = {"base_url": [pot_provider_url.rstrip("/")]}


def _base_opts(cookies: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30}
    if cookies:
        opts["cookiefile"] = cookies
    if _EXTRACTOR_ARGS:
        opts["extractor_args"] = {k: dict(v) for k, v in _EXTRACTOR_ARGS.items()}
    return opts


def _copy_cookies(cookies: str | None, directory: Path) -> Path | None:
    """Личная копия cookies.txt на одну операцию.

    yt-dlp при закрытии перезаписывает cookie-файл: оригинал, смонтированный только для чтения,
    вызвал бы ошибку, а параллельные загрузки портили бы его. Копию удаляет вызывающий."""
    if not cookies:
        return None
    src = Path(cookies)
    if not src.is_file():
        raise DownloadError(f"Файл cookies не найден: {cookies}. Проверьте COOKIES_FILE.")
    fd, tmp = tempfile.mkstemp(prefix="cookies_", suffix=".txt", dir=directory)
    os.close(fd)
    shutil.copyfile(src, tmp)
    return Path(tmp)


def _site(msg: str) -> str:
    m = re.match(r"\[(\w+)", msg)
    return {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}.get(
        m.group(1).lower() if m else "", "Сайт"
    )


def _clean(e: Exception, has_cookies: bool = False) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    log.warning("yt-dlp error: %s", msg)
    low = msg.lower()
    site = _site(msg)
    if "not a bot" in low or ("sign in" in low and site == "YouTube"):
        hint = ("cookies, похоже, устарели — экспортируйте заново." if has_cookies
                else "Нужен cookies.txt (см. README, раздел «Авторизация»).")
        return f"YouTube требует подтвердить, что вы не бот (так бывает на серверных IP). {hint}"
    if any(w in low for w in ("login", "log in", "cookies", "sign in", "rate-limit", "rate limit")):
        hint = ("cookies, похоже, устарели или аккаунт не имеет доступа — экспортируйте заново." if has_cookies
                else "Нужен cookies.txt (см. README, раздел «Авторизация»).")
        return f"{site} требует вход в аккаунт. {hint}"
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


def build_options(info: dict, max_bytes: int | None = None, music: bool = False) -> list[Option]:
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

    video: list[Option] = []
    for h in sorted(by_height, reverse=True)[:5]:
        size = by_height[h][1]
        video.append(Option(f"v{h}", label("🎬", f"{h}p", size), _video_selector(h), size=size))
    if not video:  # TikTok/Instagram часто без height
        video.append(Option("vbest", "🎬 Видео (лучшее качество)", BEST_SELECTOR))

    audio_opts: list[Option] = []
    mp3_size = int(duration * 24000) if duration else audio_size or None  # 192 кбит/с
    audio_opts.append(Option("mp3", label("🎵", "MP3", mp3_size), "ba/b", "mp3", mp3_size))
    m4a_formats = [f for f in audio if f.get("ext") == "m4a"]
    if m4a_formats:
        m4a_size = max((_fmt_size(f, duration) or 0 for f in m4a_formats), default=0) or None
        audio_opts.append(
            Option("m4a", label("🎧", "M4A (оригинал)", m4a_size), "ba[ext=m4a]/ba/b", "m4a", m4a_size)
        )

    # для ссылок YouTube Music сначала музыка, видео — после
    return audio_opts + video if music else video + audio_opts


def _pick_thumbnail(info: dict) -> str | None:
    # Telegram надёжнее принимает jpg, чем webp
    thumbs = [t.get("url") for t in info.get("thumbnails") or [] if t.get("url")]
    jpgs = [u for u in thumbs if ".jpg" in u.split("?")[0]]
    return (jpgs[-1] if jpgs else None) or info.get("thumbnail")


async def fetch_info(
    url: str, cookies: str | None = None, max_bytes: int | None = None, temp_dir: str | None = None
) -> VideoInfo:
    def run():
        ck = _copy_cookies(cookies, temp_root(temp_dir))
        try:
            with yt_dlp.YoutubeDL(_base_opts(str(ck) if ck else None)) as ydl:
                return ydl.extract_info(url, download=False)
        finally:
            if ck:
                ck.unlink(missing_ok=True)

    try:
        info = await asyncio.to_thread(run)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean(e, bool(cookies))) from e
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise DownloadError("В посте нет видео.")
        info = entries[0]
    if info.get("is_live"):
        raise DownloadError("Прямые трансляции не поддерживаются.")
    artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
    music = is_music_url(url) or is_music_url(info.get("webpage_url") or "")
    year = info.get("release_year") or (info.get("release_date") or "")[:4] or None
    return VideoInfo(
        url=info.get("webpage_url") or url,
        id=str(info.get("id") or url),
        extractor=info.get("extractor_key") or "generic",
        title=info.get("title") or info.get("description") or "Видео",
        uploader=info.get("uploader") or info.get("channel"),
        duration=int(info["duration"]) if info.get("duration") else None,
        thumbnail=_pick_thumbnail(info),
        options=build_options(info, max_bytes, music),
        artist=", ".join(artists) or None,
        track=info.get("track"),
        album=info.get("album"),
        year=str(year) if year else None,
        is_music=music,
        audio_tracks=audio_tracks(info.get("formats") or []),
    )


async def download(
    url: str,
    option: Option,
    max_bytes: int,
    cookies: str | None = None,
    progress: Progress | None = None,
    temp_dir: str | None = None,
    filename: str | None = None,
    tags: AudioTags | None = None,
    lang: str | None = None,
) -> DownloadResult:
    """Скачивает файл во временную папку. Вызывающий обязан вызвать result.cleanup()
    сразу после отправки: на диске файл живёт только между загрузкой и отправкой."""
    progress = progress or Progress()
    check_free_space(temp_dir, option.size)
    tmp = tempfile.TemporaryDirectory(prefix="job_", dir=temp_root(temp_dir))
    try:
        ck = _copy_cookies(cookies, Path(tmp.name))  # удалится вместе с папкой задачи
    except BaseException:
        tmp.cleanup()
        raise
    opts = _base_opts(str(ck) if ck else None) | {
        "format": with_language(option.selector, lang) if lang else option.selector,
        "outtmpl": f"{tmp.name}/%(id).50s.%(ext)s",
        "max_filesize": max_bytes,
        "progress_hooks": [progress.hook],
        "postprocessor_hooks": [progress.pp_hook],
        "concurrent_fragment_downloads": 4,
    }
    if option.audio_only:
        # m4a копируется без перекодирования, если исходник уже AAC
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": option.audio_codec}
        if option.audio_codec == "mp3":
            extract["preferredquality"] = "192"
        opts["writethumbnail"] = True
        opts["postprocessors"] = [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            extract,
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
        raise DownloadError(_clean(e, bool(cookies))) from e
    except BaseException:
        # в т.ч. CancelledError при остановке бота: просим поток yt-dlp прерваться и чистим папку
        progress.cancel.set()
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
    try:
        path, thumb = await _finalize(path, thumb, option, filename, tags)
    except BaseException:
        tmp.cleanup()
        raise
    return DownloadResult(path, thumb, tmp)


async def _finalize(
    path: Path, thumb: Path | None, option: Option, filename: str | None, tags: AudioTags | None
) -> tuple[Path, Path | None]:
    """Даёт файлу имя ролика; аудио дополнительно получает теги и обложку из превью."""
    target = path.with_name(f"{filename or path.stem}{path.suffix}")
    if option.audio_only:
        cover = await make_cover(thumb, path.with_name("cover.jpg")) if thumb else None
        if target == path:
            target = path.with_name(f"{path.stem}_tagged{path.suffix}")
        if await tag_audio(path, target, tags or AudioTags(path.stem), cover):
            path.unlink()
            return target, cover or thumb
        log.warning("tagging failed for %s, sending without cover", path.name)
        target.unlink(missing_ok=True)
        thumb = cover or thumb
    if target != path:
        path = path.rename(target)
    return path, thumb


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
