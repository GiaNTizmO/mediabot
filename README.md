# mediabot

Telegram-бот: присылаете ссылку (YouTube, Shorts, TikTok, Instagram) → бот предлагает качество/аудио → присылает файл.
Работает на aiogram 3 + yt-dlp + ffmpeg.

## Запуск
```bash
sudo apt install ffmpeg          # обязателен
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # впишите BOT_TOKEN от @BotFather
python -m bot
```

## Заметки
- Лимит Bot API на отправку — 50 МБ. Для файлов до 2 ГБ поднимите [локальный Bot API сервер](https://github.com/tdlib/telegram-bot-api) и задайте `LOCAL_API_URL` и `MAX_FILE_MB`.
- Бот сам скрывает/отклоняет варианты, не влезающие в лимит.
- Instagram и иногда YouTube требуют авторизации: экспортируйте cookies.txt (расширение «Get cookies.txt LOCALLY») и укажите `COOKIES_FILE`.
- `ALLOWED_USERS` ограничивает доступ списком user id.
- yt-dlp часто ломается из‑за изменений сайтов: `pip install -U yt-dlp`.
