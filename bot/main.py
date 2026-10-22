import asyncio
import logging
import secrets
from collections import OrderedDict

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import load_config
from .downloader import DownloadError, VideoInfo, download, fetch_info, find_url

log = logging.getLogger("mediabot")
cfg = load_config()
dp = Dispatcher()

# token -> VideoInfo (хранится в памяти, последние 200 запросов)
PENDING: OrderedDict[str, VideoInfo] = OrderedDict()
# ограничиваем число одновременных загрузок
SEM = asyncio.Semaphore(3)


def allowed(user_id: int) -> bool:
    return not cfg.allowed_users or user_id in cfg.allowed_users


def fmt_duration(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Пришли ссылку на видео с YouTube, YouTube Shorts, TikTok или Instagram — "
        "я предложу варианты загрузки."
    )


@dp.message(F.text)
async def on_link(message: Message):
    if not allowed(message.from_user.id):
        return await message.answer("⛔ Нет доступа.")
    url = find_url(message.text)
    if not url:
        return await message.answer("Не вижу ссылки на YouTube / TikTok / Instagram 🤔")

    status = await message.answer("🔍 Получаю информацию…")
    try:
        info = await fetch_info(url, cfg.cookies_file)
    except DownloadError as e:
        return await status.edit_text(f"❌ Не удалось получить видео:\n{e}")

    token = secrets.token_urlsafe(6)
    PENDING[token] = info
    while len(PENDING) > 200:
        PENDING.popitem(last=False)

    rows = [
        [InlineKeyboardButton(text=o.label, callback_data=f"dl:{token}:{o.key}")]
        for o in info.options
    ]
    dur = fmt_duration(info.duration)
    await status.edit_text(
        f"<b>{info.title[:200]}</b>\n{('⏱ ' + dur) if dur else ''}\n\nВыберите вариант:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("dl:"))
async def on_choice(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    _, token, key = cb.data.split(":")
    info = PENDING.get(token)
    if not info:
        return await cb.answer("Запрос устарел, пришлите ссылку заново.", show_alert=True)
    option = next((o for o in info.options if o.key == key), None)
    if not option:
        return await cb.answer("Неизвестный вариант", show_alert=True)

    max_bytes = cfg.max_file_mb * 1024 * 1024
    if option.size and option.size > max_bytes:
        return await cb.answer(
            f"Примерно {option.size // 1024 // 1024} МБ — больше лимита {cfg.max_file_mb} МБ. Выберите качество ниже.",
            show_alert=True,
        )

    await cb.answer()
    await cb.message.edit_text(f"⏳ Скачиваю: {option.label}…")
    tmp = None
    try:
        async with SEM:
            path, tmp = await download(info.url, option, max_bytes, cfg.cookies_file)
        await cb.message.edit_text("📤 Отправляю в Telegram…")
        file = FSInputFile(path)
        if option.audio_only:
            await cb.message.answer_audio(file, title=info.title[:64], duration=info.duration)
        else:
            await cb.message.answer_video(
                file, caption=info.title[:1000], duration=info.duration, supports_streaming=True
            )
        await cb.message.delete()
    except DownloadError as e:
        await cb.message.edit_text(f"❌ Ошибка загрузки:\n{e}")
    except Exception:
        log.exception("send failed")
        await cb.message.edit_text("❌ Не удалось отправить файл в Telegram.")
    finally:
        if tmp:
            tmp.cleanup()


async def run():
    logging.basicConfig(level=logging.INFO)
    session = None
    if cfg.local_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.local_api_url, is_local=True))
        session.timeout = 600
    bot = Bot(cfg.token, session=session)
    await dp.start_polling(bot)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
