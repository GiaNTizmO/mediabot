import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
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
