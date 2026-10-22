import asyncio
import json
import subprocess

import pytest

from bot.downloader import (
    Cancelled,
    DownloadError,
    Option,
    Progress,
    download,
    fetch_info,
    sweep_temp,
    temp_root,
)
from bot.media import AudioTags, make_cover, make_thumbnail, probe, tag_audio


async def _ffprobe(path):
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return json.loads(out)


@pytest.mark.parametrize("ext", ["mp3", "m4a"])
async def test_cover_and_tags_embedded(tmp_path, ext):
    thumb, audio = tmp_path / "thumb.jpg", tmp_path / f"src.{ext}"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720", "-frames:v", "1",
                    str(thumb)], check=True)
    codec = ["-c:a", "libmp3lame"] if ext == "mp3" else ["-c:a", "aac"]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=2", *codec, str(audio)],
                   check=True)

    cover = await make_cover(thumb, tmp_path / "cover.jpg")
    meta = await probe(cover)
    assert meta.width == meta.height == 720  # 16:9 → квадрат

    out = tmp_path / f"Исполнитель - Трек.{ext}"
    assert await tag_audio(audio, out, AudioTags("Трек", "Исполнитель", "Альбом", "2024"), cover)
    data = await _ffprobe(out)
    tags = {k.lower(): v for k, v in data["format"]["tags"].items()}
    assert tags["title"] == "Трек" and tags["artist"] == "Исполнитель" and tags["album"] == "Альбом"
    pics = [st for st in data["streams"] if st.get("disposition", {}).get("attached_pic")]
    assert len(pics) == 1 and pics[0]["width"] == 720


async def test_fetch_download_and_media(media_server, tmp_path):
    info = await fetch_info(media_server)
    assert info.options[-1].key == "mp3"

    video = info.options[0]
    progress = Progress()
    res = await download(info.url, video, 50 * 1024 * 1024, progress=progress, filename="Мой ролик")
    try:
        assert res.path.name == "Мой ролик.mp4"
        meta = await probe(res.path)
        assert (meta.width, meta.height, meta.duration) == (360, 640, 3)
        thumb = await make_thumbnail(res.path, tmp_path / "t.jpg")
        assert thumb and thumb.stat().st_size > 0
        assert progress.stage == "process"
    finally:
        res.cleanup()

    res = await download(info.url, info.options[-1], 50 * 1024 * 1024, filename="Artist - Song",
                         tags=AudioTags("Song", "Artist"))
    try:
        assert res.path.name == "Artist - Song.mp3"
        assert [p.name for p in res.path.parent.iterdir() if p.suffix == ".mp3"] == ["Artist - Song.mp3"]
        assert (await _ffprobe(res.path))["format"]["tags"]["artist"] == "Artist"
    finally:
        res.cleanup()


async def test_size_limit(media_server, tmp_path):
    info = await fetch_info(media_server)
    with pytest.raises(DownloadError):
        await download(info.url, info.options[0], 1000, temp_dir=str(tmp_path))
    assert list(temp_root(str(tmp_path)).iterdir()) == []


async def test_cancel(media_server, tmp_path):
    info = await fetch_info(media_server)
    p = Progress()
    p.cancel.set()
    with pytest.raises(Cancelled):
        await download(info.url, info.options[0], 50 * 1024 * 1024, progress=p, temp_dir=str(tmp_path))
    assert list(temp_root(str(tmp_path)).iterdir()) == []


async def test_not_enough_disk_space(media_server, tmp_path):
    huge = Option("v1", "x", "b", size=10**15)
    with pytest.raises(DownloadError, match="мало места"):
        await download(media_server, huge, 10**16, temp_dir=str(tmp_path))
    assert list(temp_root(str(tmp_path)).iterdir()) == []


def test_sweep_removes_leftovers(tmp_path):
    root = temp_root(str(tmp_path))
    (root / "job_old").mkdir()
    (root / "job_old" / "video.mp4.part").write_bytes(b"x" * 100)
    (root / "stray.tmp").write_bytes(b"x")
    assert sweep_temp(str(tmp_path)) == 2
    assert list(root.iterdir()) == []


async def test_finalize_uses_thumbnail_as_cover(tmp_path):
    from bot.downloader import Option, _finalize

    thumb, audio = tmp_path / "abc123.jpg", tmp_path / "abc123.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1",
                    str(thumb)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "libmp3lame",
                    str(audio)], check=True)
    path, cover = await _finalize(audio, thumb, Option("mp3", "", "", "mp3"), "Artist - Song",
                                  AudioTags("Song", "Artist"))
    assert path.name == "Artist - Song.mp3" and not audio.exists()
    assert cover.name == "cover.jpg"
    pics = [st for st in (await _ffprobe(path))["streams"] if st.get("disposition", {}).get("attached_pic")]
    assert pics and pics[0]["width"] == pics[0]["height"] == 360
