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
