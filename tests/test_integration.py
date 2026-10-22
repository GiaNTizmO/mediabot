import pytest

from bot.downloader import Cancelled, DownloadError, Progress, download, fetch_info
from bot.media import make_thumbnail, probe


async def test_fetch_download_and_media(media_server, tmp_path):
    info = await fetch_info(media_server)
    assert info.options[-1].key == "audio"

    video = info.options[0]
    progress = Progress()
    res = await download(info.url, video, 50 * 1024 * 1024, progress=progress)
    try:
        assert res.path.suffix == ".mp4"
        meta = await probe(res.path)
        assert (meta.width, meta.height, meta.duration) == (360, 640, 3)
        thumb = await make_thumbnail(res.path, tmp_path / "t.jpg")
        assert thumb and thumb.stat().st_size > 0
        assert progress.stage == "process"
    finally:
        res.cleanup()

    res = await download(info.url, info.options[-1], 50 * 1024 * 1024)
    try:
        assert res.path.suffix == ".mp3"
    finally:
        res.cleanup()


async def test_size_limit(media_server):
    info = await fetch_info(media_server)
    with pytest.raises(DownloadError):
        await download(info.url, info.options[0], 1000)


async def test_cancel(media_server):
    info = await fetch_info(media_server)
    p = Progress()
    p.cancel.set()
    with pytest.raises(Cancelled):
        await download(info.url, info.options[0], 50 * 1024 * 1024, progress=p)
