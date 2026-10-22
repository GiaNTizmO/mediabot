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
