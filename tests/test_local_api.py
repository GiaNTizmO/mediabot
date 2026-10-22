import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.cache import FileCache
from bot.config import Config, _file_limit
from bot.downloader import fetch_info
from bot.handlers import BotState, Pending, on_choice

from .test_handlers import make_bot, make_cb


async def test_local_api_sends_file_uri_from_readable_dir(media_server, tmp_path):
    cfg = Config(token="x", temp_dir="rel-work", local_api_url="http://telegram-bot-api:8081", max_file_mb=2000)
    store = BotState(1)
    info = await fetch_info(media_server)
    token = store.add(Pending(info, 1, 10, 5))
    bot = make_bot()
    seen = {}

    async def send_video(**kw):
        path = Path(kw["video"].removeprefix("file://"))
        seen["uri"], seen["mode"], seen["exists"] = kw["video"], stat.S_IMODE(path.parent.stat().st_mode), path.exists()
        seen["timeout"] = kw["request_timeout"]
        return make_bot().send_video.return_value

    bot.send_video = AsyncMock(side_effect=send_video)
    cwd = os.getcwd()
    os.chdir(tmp_path)  # temp_dir относительный — путь в file:// всё равно должен быть абсолютным
    try:
        await on_choice(make_cb(token, info.options[0].key), cfg, store, FileCache(tmp_path / "c.db"), bot)
    finally:
        os.chdir(cwd)

    assert seen["uri"].startswith(f"file://{tmp_path}/rel-work/mediabot/job_")
    assert seen["uri"].endswith(".mp4") and seen["exists"]
    assert seen["mode"] == 0o755  # uid 101 из контейнера telegram-bot-api должен прочитать файл
    assert seen["timeout"] == 3600
    assert list((tmp_path / "rel-work" / "mediabot").iterdir()) == []  # и всё равно удалили после отправки


def test_file_limit_clamped():
    assert _file_limit(2000, local=False) == 50
    assert _file_limit(40, local=False) == 40
    assert _file_limit(4000, local=True) == 2000
    assert _file_limit(2000, local=True) == 2000


async def test_logout_from_cloud_once(tmp_path):
    from bot import main

    cfg = Config(token="1:x", local_api_url="http://telegram-bot-api:8081", data_dir=str(tmp_path))
    calls = []

    class FakeBot:
        def __init__(self, token, **kw):
            assert "session" not in kw  # выходим именно из облачного API
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def log_out(self):
            calls.append(1)

    with patch.object(main, "Bot", FakeBot):
        await main.logout_from_cloud(cfg)
        await main.logout_from_cloud(cfg)
        await main.logout_from_cloud(Config(token="1:x", data_dir=str(tmp_path / "other")))  # облачный режим
    assert calls == [1]
    assert (tmp_path / "cloud_logged_out").exists()
