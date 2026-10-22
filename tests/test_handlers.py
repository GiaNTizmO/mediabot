from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.cache import FileCache
from bot.config import Config
from bot.downloader import fetch_info
from bot.handlers import BotState, Pending, on_choice


def make_cb(token: str, key: str, user_id: int = 1):
    msg = MagicMock()
    msg.photo = None
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    return SimpleNamespace(data=f"dl:{token}:{key}", from_user=SimpleNamespace(id=user_id), message=msg,
                           answer=AsyncMock())


def make_bot():
    bot = MagicMock()
    bot.send_video = AsyncMock(return_value=SimpleNamespace(video=SimpleNamespace(file_id="VID")))
    bot.send_audio = AsyncMock(return_value=SimpleNamespace(audio=SimpleNamespace(file_id="AUD")))
    bot.send_chat_action = AsyncMock()
    return bot


async def test_download_send_and_cache(media_server, tmp_path):
    cfg = Config(token="x")
    store = BotState(2)
    cache = FileCache(tmp_path / "c.db")
    info = await fetch_info(media_server)
    key = info.options[0].key

    token = store.add(Pending(info, user_id=1, chat_id=10, source_message_id=5))
    bot = make_bot()
    cb = make_cb(token, key)
    await on_choice(cb, cfg, store, cache, bot)

    kw = bot.send_video.await_args.kwargs
    assert (kw["width"], kw["height"], kw["duration"]) == (360, 640, 3)
    assert kw["thumbnail"] is not None and kw["chat_id"] == 10
    cb.message.delete.assert_awaited()
    assert cache.get(info.cache_key(info.options[0])) == ("VID", "video")
    assert not store.active and not store.busy_users

    # повторный запрос — из кэша, без загрузки
    token = store.add(Pending(info, 1, 10, 6))
    bot2 = make_bot()
    await on_choice(make_cb(token, key), cfg, store, cache, bot2)
    assert bot2.send_video.await_args.kwargs["video"] == "VID"


async def test_foreign_user_rejected(media_server, tmp_path):
    store = BotState(1)
    info = await fetch_info(media_server)
    token = store.add(Pending(info, 1, 10, 5))
    cb = make_cb(token, info.options[0].key, user_id=2)
    bot = make_bot()
    await on_choice(cb, Config(token="x"), store, FileCache(tmp_path / "c.db"), bot)
    bot.send_video.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert")
