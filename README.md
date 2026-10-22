# mediabot

Telegram-бот для скачивания видео с **YouTube, YouTube Shorts, TikTok и Instagram**.
Вы присылаете ссылку, бот показывает превью и варианты (качество видео или MP3), а потом присылает выбранный файл.

Стек: aiogram 3, yt-dlp, ffmpeg.

## Возможности
- Превью с обложкой, названием, автором и длительностью
- Варианты качества (до 1080p) с примерным размером; ⚠️ помечает файлы больше лимита
- MP3 с обложкой и тегами
- Прогресс загрузки (процент, скорость, ETA) и кнопка **«Отмена»**
- Правильные пропорции и превью для вертикальных видео (Shorts/TikTok/Reels)
- Предпочтение H.264/AAC, чтобы видео проигрывалось во всех клиентах Telegram
- **Кэш file_id** (SQLite): повторный запрос того же видео отправляется мгновенно
- Очередь: ограничение одновременных загрузок, одна загрузка на пользователя
- Работа в группах: бот реагирует только на ссылки, а кнопки нажимает только автор запроса
- Понятные сообщения об ошибках (приватное видео, нужна авторизация, недоступно…)
- Опционально: локальный Bot API для файлов до 2 ГБ, белый список пользователей, cookies

## Запуск через Docker (рекомендуется)
```bash
cp .env.example .env      # впишите BOT_TOKEN от @BotFather
docker compose up -d --build
```
Файлы до 2 ГБ: заполните `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`, `LOCAL_API_URL=http://telegram-bot-api:8081`,
`MAX_FILE_MB=2000` и запустите `docker compose --profile local-api up -d --build`.

## Запуск без Docker
Нужны Python 3.10+, `ffmpeg` и [deno](https://deno.com) (для YouTube требуется yt-dlp).
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m bot
```

## Настройки (.env)
| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен бота (обязательно) |
| `MAX_FILE_MB` | лимит размера файла, по умолчанию 50 |
| `LOCAL_API_URL` | адрес локального telegram-bot-api |
| `ALLOWED_USERS` | user id через запятую; пусто означает доступ для всех |
| `COOKIES_FILE` | cookies.txt для Instagram/YouTube с ограничениями |
| `MAX_CONCURRENT` | одновременных загрузок (по умолчанию 3) |
| `DATA_DIR` | папка для кэша |

## Советы
- Instagram часто требует авторизацию: экспортируйте cookies.txt расширением «Get cookies.txt LOCALLY».
- Сайты меняются, и yt-dlp периодически ломается. Обновление: `pip install -U "yt-dlp[default]"` или `docker compose build --pull`.

## Тесты
```bash
pip install -r requirements-dev.txt
pytest
```
Тесты не требуют сети: используется локальный HTTP-сервер с тестовым видео.
