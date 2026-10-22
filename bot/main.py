import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.types import BotCommand, LinkPreviewOptions

from .cache import FileCache
from .config import Config, load_config
from .cookies_check import inspect_cookies
from .downloader import configure_ytdlp, sweep_temp
from .handlers import BotState, router


def build_bot(cfg: Config) -> Bot:
    session = None
    if cfg.local_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.local_api_url, is_local=True))
    return Bot(
        cfg.token,
        session=session,
        default=DefaultBotProperties(link_preview=LinkPreviewOptions(is_disabled=True)),
    )


async def logout_from_cloud(cfg: Config) -> None:
    """Перед работой через локальный Bot API бот должен один раз выйти из облачного
    (метод logOut), иначе Telegram не гарантирует доставку обновлений. Вернуться в облако
    после этого можно не раньше чем через 10 минут."""
    marker = Path(cfg.data_dir) / "cloud_logged_out"
    if not cfg.local_api_url or marker.exists():
        return
    log = logging.getLogger("mediabot")
    async with Bot(cfg.token) as cloud:
        try:
            await cloud.log_out()
            log.info("бот вышел из облачного Bot API, дальше работаем через %s", cfg.local_api_url)
        except (TelegramBadRequest, TelegramUnauthorizedError) as e:
            log.info("logOut из облачного Bot API: %s (вероятно, уже выполнен)", e.message)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    cfg = load_config()
    log = logging.getLogger("mediabot")
    configure_ytdlp(cfg.pot_provider_url)
    if cfg.pot_provider_url:
        log.info("PO token provider: %s", cfg.pot_provider_url)
    if cfg.cookies_file:
        report = inspect_cookies(cfg.cookies_file)
        for line in report.lines:
            (log.info if report.ok else log.warning)("cookies: %s", line)
    else:
        log.info("cookies: не заданы (COOKIES_FILE пуст)")
    if removed := sweep_temp(cfg.temp_dir):
        log.info("removed %d leftover temp item(s) from previous run", removed)
    cache = FileCache(Path(cfg.data_dir) / "cache.sqlite3")
    await logout_from_cloud(cfg)
    if cfg.local_api_url:
        log.info("локальный Bot API: %s, лимит файла %d МБ", cfg.local_api_url, cfg.max_file_mb)
    bot = build_bot(cfg)

    dp = Dispatcher(cfg=cfg, cache=cache, store=BotState(cfg.max_concurrent))
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cache.close()
        sweep_temp(cfg.temp_dir)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
