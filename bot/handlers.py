import asyncio
import contextlib
import html
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)
from aiogram.utils.chat_action import ChatActionSender

from .cache import FileCache
from .config import Config
from .downloader import (
    Cancelled,
    DownloadError,
    DownloadResult,
    Option,
    Progress,
    VideoInfo,
    download,
    fetch_info,
    find_url,
    human_size,
    render_progress,
)
from .media import make_thumbnail, probe

log = logging.getLogger(__name__)
router = Router()

PENDING_LIMIT = 500
PROGRESS_INTERVAL = 3  # сек между обновлениями сообщения с прогрессом
UPLOAD_TIMEOUT = 900

HELP = (
    "Пришлите ссылку на видео с <b>YouTube</b>, <b>YouTube Shorts</b>, <b>TikTok</b> или "
    "<b>Instagram</b> — я покажу доступные варианты (качество видео или MP3), "
    "скачаю выбранный и пришлю сюда.\n\n"
    "• Уже скачанные видео присылаются мгновенно из кэша.\n"
    "• Загрузку можно отменить кнопкой «Отмена».\n"
    "• В группах бот реагирует только на сообщения со ссылками.\n\n"
    "Лимит размера файла: <b>{limit}</b>."
)


@dataclass
class Pending:
    info: VideoInfo
    user_id: int
    chat_id: int
    source_message_id: int


@dataclass
class BotState:
    max_concurrent: int
    pending: OrderedDict[str, Pending] = field(default_factory=OrderedDict)
    active: dict[str, Progress] = field(default_factory=dict)  # token -> прогресс
    busy_users: set[int] = field(default_factory=set)
    sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self):
        self.sem = asyncio.Semaphore(self.max_concurrent)

    def add(self, item: Pending) -> str:
        token = secrets.token_urlsafe(6)
        self.pending[token] = item
        while len(self.pending) > PENDING_LIMIT:
            self.pending.popitem(last=False)
        return token


# ---------- helpers ----------

def fmt_duration(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def options_text(info: VideoInfo, note: str | None = None) -> str:
    if info.track:
        parts = [f"🎵 <b>{html.escape(short(info.track, 200))}</b>"]
        author = info.audio_artist
        if info.album:
            parts.append(f"💿 {html.escape(short(info.album, 100))}" + (f" ({info.year})" if info.year else ""))
    else:
        parts = [f"<b>{html.escape(short(info.title, 200))}</b>"]
        author = info.uploader
    meta = " · ".join(
        x for x in (
            html.escape(author) if author else "",
            f"⏱ {fmt_duration(info.duration)}" if info.duration else "",
        ) if x
    )
    if meta:
        parts.insert(1, meta)
    if info.audio_tracks:
        parts.append(f"🔊 Звуковых дорожек: {len(info.audio_tracks)} — выбор после качества")
    if note:
        parts.append(f"\n{note}")
    parts.append("\nВыберите вариант:")
    return "\n".join(parts)


def options_kb(token: str, info: VideoInfo) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=o.label, callback_data=f"dl:{token}:{o.key}")] for o in info.options]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data=f"close:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tracks_kb(token: str, option: Option, info: VideoInfo) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=t.label, callback_data=f"dl:{token}:{option.key}:{i}")
        for i, t in enumerate(info.audio_tracks)
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tracks_text(info: VideoInfo, option: Option) -> str:
    return (
        f"<b>{html.escape(short(info.title, 200))}</b>\n"
        f"Выбрано: {html.escape(option.label)}\n\n🔊 Выберите звуковую дорожку:"
    )


def stop_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⛔ Отмена", callback_data=f"stop:{token}")]]
    )


def caption(info: VideoInfo, lang: str | None = None) -> str:
    title = html.escape(short(info.title, 700))
    text = f'<a href="{html.escape(info.url, quote=True)}">{title}</a>'
    if info.uploader:
        text += f"\n👤 {html.escape(info.uploader)}"
    if track := info.find_track(lang):
        text += f"\n🔊 {html.escape(track.name)}"
    return text


async def edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует сообщение, будь то текст или фото с подписью."""
    with contextlib.suppress(TelegramBadRequest):
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ---------- commands ----------

@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, cfg: Config):
    await message.answer(HELP.format(limit=human_size(cfg.max_bytes)), parse_mode="HTML")


# ---------- link ----------

@router.message(F.text | F.caption)
async def on_link(message: Message, cfg: Config, store: BotState):
    is_private = message.chat.type == ChatType.PRIVATE
    url = find_url(message.text or message.caption)
    if not url:
        if is_private:
            await message.answer("Не вижу ссылки на YouTube / TikTok / Instagram 🤔\n/help — подсказка")
        return
    if not cfg.is_allowed(message.from_user.id):
        if is_private:
            await message.answer("⛔ Нет доступа.")
        return

    status = await message.reply("🔍 Получаю информацию…")
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            info = await fetch_info(url, cfg.cookies_file, cfg.max_bytes)
    except DownloadError as e:
        await status.edit_text(f"❌ Не удалось получить видео:\n{html.escape(str(e))}", parse_mode="HTML")
        return
    except Exception:
        log.exception("fetch_info failed for %s", url)
        await status.edit_text("❌ Внутренняя ошибка при получении информации.")
        return

    token = store.add(Pending(info, message.from_user.id, message.chat.id, message.message_id))
    text, kb = options_text(info), options_kb(token, info)

    # пробуем показать превью; Telegram может не скачать картинку — тогда текстом
    if info.thumbnail:
        try:
            await message.reply_photo(info.thumbnail, caption=text, reply_markup=kb, parse_mode="HTML")
            await status.delete()
            return
        except TelegramBadRequest:
            pass
    await status.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ---------- callbacks ----------

def _parse(cb: CallbackQuery, store: BotState) -> tuple[str, list[str], Pending | None]:
    action, token, *rest = cb.data.split(":")
    return token, rest, store.pending.get(token)


@router.callback_query(F.data.startswith("close:"))
async def on_close(cb: CallbackQuery, store: BotState):
    token, _, item = _parse(cb, store)
    if item and item.user_id != cb.from_user.id:
        return await cb.answer("Это не ваш запрос.", show_alert=True)
    store.pending.pop(token, None)
    await cb.answer()
    with contextlib.suppress(TelegramBadRequest):
        await cb.message.delete()


@router.callback_query(F.data.startswith("back:"))
async def on_back(cb: CallbackQuery, store: BotState):
    token, _, item = _parse(cb, store)
    if not item:
        return await cb.answer("Запрос устарел, пришлите ссылку заново.", show_alert=True)
    if item.user_id != cb.from_user.id:
        return await cb.answer("Это не ваш запрос.", show_alert=True)
    await cb.answer()
    await edit(cb.message, options_text(item.info), options_kb(token, item.info))


@router.callback_query(F.data.startswith("stop:"))
async def on_stop(cb: CallbackQuery, store: BotState):
    token, _, item = _parse(cb, store)
    if item and item.user_id != cb.from_user.id:
        return await cb.answer("Это не ваш запрос.", show_alert=True)
    progress = store.active.get(token)
    if not progress:
        return await cb.answer("Загрузка уже завершена.")
    progress.cancel.set()
    await cb.answer("Отменяю…")


@router.callback_query(F.data.startswith("dl:"))
async def on_choice(cb: CallbackQuery, cfg: Config, store: BotState, cache: FileCache, bot: Bot):
    token, rest, item = _parse(cb, store)
    if not item:
        return await cb.answer("Запрос устарел, пришлите ссылку заново.", show_alert=True)
    if item.user_id != cb.from_user.id:
        return await cb.answer("Это не ваш запрос — пришлите свою ссылку.", show_alert=True)
    if token in store.active:
        return await cb.answer("Уже скачиваю…")
    info = item.info
    option = next((o for o in info.options if o.key == (rest[0] if rest else "")), None)
    if not option:
        return await cb.answer("Неизвестный вариант", show_alert=True)

    # несколько озвучек: сначала спрашиваем дорожку, потом качаем
    lang = None
    if info.audio_tracks:
        if len(rest) < 2:
            if option.size and option.size > cfg.max_bytes:
                return await cb.answer(
                    f"Примерно {human_size(option.size)} — больше лимита {human_size(cfg.max_bytes)}. "
                    "Выберите качество ниже.",
                    show_alert=True,
                )
            await cb.answer()
            return await edit(cb.message, tracks_text(info, option), tracks_kb(token, option, info))
        try:
            lang = info.audio_tracks[int(rest[1])].lang
        except (ValueError, IndexError):
            return await cb.answer("Неизвестная дорожка", show_alert=True)

    reply = ReplyParameters(message_id=item.source_message_id, allow_sending_without_reply=True)
    answered = False

    async def answer(text: str | None = None, alert: bool = False) -> None:
        nonlocal answered
        if not answered:
            answered = True
            with contextlib.suppress(TelegramBadRequest):
                await cb.answer(text, show_alert=alert)

    # 1. мгновенная отправка из кэша
    key = info.cache_key(option, lang)
    if cached := cache.get(key):
        await answer("Из кэша ⚡")
        try:
            await _send_cached(bot, item.chat_id, cached, info, reply, lang)
            with contextlib.suppress(TelegramBadRequest):
                await cb.message.delete()
            store.pending.pop(token, None)
            return
        except TelegramBadRequest:
            log.warning("stale file_id for %s, re-downloading", key)
            cache.delete(key)

    if option.size and option.size > cfg.max_bytes:
        return await answer(
            f"Примерно {human_size(option.size)} — больше лимита {human_size(cfg.max_bytes)}. "
            "Выберите качество ниже.",
            alert=True,
        )
    if cb.from_user.id in store.busy_users:
        return await answer("Дождитесь окончания предыдущей загрузки.", alert=True)

    await answer()
    progress = Progress()
    store.active[token] = progress
    store.busy_users.add(cb.from_user.id)
    title = html.escape(short(info.title, 80))
    msg = cb.message
    result: DownloadResult | None = None
    try:
        if store.sem.locked():
            await edit(msg, f"🕒 В очереди: {title}", stop_kb(token))
        async with store.sem:
            if progress.cancel.is_set():
                raise Cancelled("Загрузка отменена.")
            started = time.monotonic()
            await edit(msg, render_progress(progress, title, started), stop_kb(token))
            ticker = asyncio.create_task(_progress_loop(msg, progress, title, started, token))
            try:
                result = await download(
                    info.url, option, cfg.max_bytes, cfg.cookies_file, progress, cfg.temp_dir,
                    filename=info.file_stem(option),
                    tags=info.audio_tags() if option.audio_only else None,
                    lang=lang,
                )
            finally:
                ticker.cancel()

        await edit(msg, f"📤 Отправляю в Telegram: {title}")
        try:
            sent_kind, file_id = await _upload(bot, item.chat_id, result, option, info, reply, lang)
        finally:
            # файл больше не нужен — удаляем сразу, не дожидаясь остальных действий
            result.cleanup()
        cache.put(key, file_id, sent_kind)
        store.pending.pop(token, None)
        with contextlib.suppress(TelegramBadRequest):
            await msg.delete()
    except Cancelled:
        await edit(msg, options_text(info, "⛔ Загрузка отменена."), options_kb(token, info))
    except DownloadError as e:
        await edit(msg, options_text(info, f"❌ {html.escape(str(e))}"), options_kb(token, info))
    except Exception:
        log.exception("download/send failed for %s [%s]", info.url, option.key)
        await edit(msg, options_text(info, "❌ Не удалось скачать или отправить файл."), options_kb(token, info))
    finally:
        store.active.pop(token, None)
        store.busy_users.discard(cb.from_user.id)
        if result:
            result.cleanup()


async def _progress_loop(msg: Message, progress: Progress, title: str, started: float, token: str):
    last = ""
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL)
        text = render_progress(progress, title, started)
        if text != last:
            await edit(msg, text, stop_kb(token))
            last = text


async def _send_cached(
    bot: Bot, chat_id: int, cached: tuple[str, str], info: VideoInfo, reply, lang: str | None = None
):
    file_id, kind = cached
    common = dict(chat_id=chat_id, caption=caption(info, lang), parse_mode="HTML", reply_parameters=reply)
    if kind == "video":
        await bot.send_video(video=file_id, supports_streaming=True, **common)
    elif kind == "audio":
        await bot.send_audio(audio=file_id, **common)
    else:
        await bot.send_document(document=file_id, **common)


async def _upload(
    bot: Bot, chat_id: int, result: DownloadResult, option: Option, info: VideoInfo, reply,
    lang: str | None = None,
) -> tuple[str, str]:
    path = result.path
    thumb_path = Path(result.tmp.name) / "thumb_tg.jpg"
    common = dict(
        chat_id=chat_id,
        caption=caption(info, lang),
        parse_mode="HTML",
        reply_parameters=reply,
        request_timeout=UPLOAD_TIMEOUT,
    )

    if option.audio_only:
        thumb = await make_thumbnail(result.thumbnail, thumb_path, seek=0) if result.thumbnail else None
        async with ChatActionSender.upload_voice(bot=bot, chat_id=chat_id):
            sent = await bot.send_audio(
                audio=FSInputFile(path),
                title=short(info.audio_title, 64),
                performer=info.audio_artist,
                duration=info.duration,
                thumbnail=FSInputFile(thumb) if thumb else None,
                **common,
            )
        return "audio", sent.audio.file_id

    meta = await probe(path)
    thumb = await make_thumbnail(path, thumb_path, seek=min(1.0, (meta.duration or 2) / 2))
    async with ChatActionSender.upload_video(bot=bot, chat_id=chat_id):
        try:
            sent = await bot.send_video(
                video=FSInputFile(path),
                duration=meta.duration or info.duration,
                width=meta.width,
                height=meta.height,
                thumbnail=FSInputFile(thumb) if thumb else None,
                supports_streaming=True,
                **common,
            )
            return "video", sent.video.file_id
        except TelegramBadRequest as e:
            log.warning("send_video failed (%s), falling back to document", e)
            sent = await bot.send_document(document=FSInputFile(path), **common)
            return "document", sent.document.file_id
