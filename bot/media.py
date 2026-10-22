import asyncio
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaMeta:
    width: int | None = None
    height: int | None = None
    duration: int | None = None


async def _run(*args: str) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return out if proc.returncode == 0 else None


async def probe(path: Path) -> MediaMeta:
    out = await _run(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
    )
    if not out:
        return MediaMeta()
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    duration = data.get("format", {}).get("duration")
    return MediaMeta(
        width=stream.get("width"),
        height=stream.get("height"),
        duration=round(float(duration)) if duration else None,
    )


async def make_thumbnail(src: Path, dst: Path, seek: float = 1.0) -> Path | None:
    """JPEG-превью ≤320px, как требует Telegram. src может быть видео или картинкой."""
    scale = "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease"
    for ss in ((seek, 0.0) if seek else (0.0,)):
        args = ["ffmpeg", "-y", "-v", "error"]
        if ss:
            args += ["-ss", str(ss)]
        args += ["-i", str(src), "-frames:v", "1", "-vf", scale, "-q:v", "5", str(dst)]
        if await _run(*args) is not None and dst.exists() and dst.stat().st_size:
            return dst
    return None


@dataclass
class AudioTags:
    title: str
    artist: str | None = None
    album: str | None = None
    year: str | None = None


async def make_cover(src: Path, dst: Path, size: int = 800) -> Path | None:
    """Квадратная обложка из превью: обрезаем по центру (16:9 → 1:1), как делает YouTube Music."""
    vf = f"crop='min(iw,ih)':'min(iw,ih)',scale='min({size},iw)':'min({size},ih)'"
    ok = await _run("ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", "2", str(dst))
    return dst if ok is not None and dst.exists() and dst.stat().st_size else None


async def tag_audio(src: Path, dst: Path, tags: AudioTags, cover: Path | None) -> bool:
    """Вшивает в mp3/m4a теги и обложку без перекодирования звука."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if cover:
        args += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-disposition:v:0", "attached_pic"]
    else:
        args += ["-map", "0:a"]
    args += ["-c", "copy", "-map_metadata", "-1"]
    for k, v in (("title", tags.title), ("artist", tags.artist), ("album", tags.album), ("date", tags.year)):
        if v:
            args += ["-metadata", f"{k}={v}"]
    if dst.suffix.lower() == ".mp3":
        args += ["-id3v2_version", "3"]
        if cover:
            args += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    args.append(str(dst))
    return await _run(*args) is not None and dst.exists() and dst.stat().st_size > 0
