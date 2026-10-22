from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.cache import FileCache
from bot.config import Config
from bot.downloader import fetch_info, temp_root
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
    cfg = Config(token="x", temp_dir=str(tmp_path / "t"))
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
    assert list(temp_root(cfg.temp_dir).iterdir()) == []  # файл удалён сразу после отправки

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


async def test_file_removed_when_upload_fails(media_server, tmp_path):
    cfg = Config(token="x", temp_dir=str(tmp_path / "t"))
    store = BotState(1)
    info = await fetch_info(media_server)
    token = store.add(Pending(info, 1, 10, 5))
    bot = make_bot()
    bot.send_video = AsyncMock(side_effect=RuntimeError("network down"))
    cb = make_cb(token, info.options[0].key)
    await on_choice(cb, cfg, store, FileCache(tmp_path / "c.db"), bot)
    assert list(temp_root(cfg.temp_dir).iterdir()) == []
    assert "Не удалось" in cb.message.edit_text.await_args.args[0]


async def test_track_picker_flow(media_server, tmp_path, monkeypatch):
    from bot import handlers
    from bot.downloader import AudioTrack, DownloadError

    cfg = Config(token="x", temp_dir=str(tmp_path / "t"))
    store = BotState(1)
    info = await fetch_info(media_server)
    info.audio_tracks = [AudioTrack("en", "🇬🇧 Английский", True), AudioTrack("ru", "🇷🇺 Русский")]
    token = store.add(Pending(info, 1, 10, 5))
    key = info.options[0].key
    bot = make_bot()
    calls = []

    async def fake_download(*args, **kwargs):
        calls.append(kwargs)
        raise DownloadError("boom")

    monkeypatch.setattr(handlers, "download", fake_download)

    # 1. выбор качества → показываем дорожки, ничего не качаем
    cb = make_cb(token, key)
    await on_choice(cb, cfg, store, FileCache(tmp_path / "c.db"), bot)
    assert calls == []
    text, kb = cb.message.edit_text.await_args.args[0], cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert "звуковую дорожку" in text
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["🇬🇧 Английский · оригинал", "🇷🇺 Русский", "⬅️ Назад"]
    assert buttons[1].callback_data == f"dl:{token}:{key}:1"

    # 2. выбор дорожки → загрузка с языком ru
    cb = make_cb(token, f"{key}:1")
    await on_choice(cb, cfg, store, FileCache(tmp_path / "c.db"), bot)
    assert calls[0]["lang"] == "ru"

    # 3. кэш хранится отдельно для каждой дорожки
    assert info.cache_key(info.options[0], "ru") != info.cache_key(info.options[0], "en")

    # 4. неверный индекс дорожки
    cb = make_cb(token, f"{key}:9")
    await on_choice(cb, cfg, store, FileCache(tmp_path / "c.db"), bot)
    assert cb.answer.await_args.kwargs.get("show_alert")
