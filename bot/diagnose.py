"""Подробная диагностика YouTube с настройками бота.

docker compose exec bot python -m bot.diagnose "https://youtu.be/VIDEO_ID"
"""
import os
import sys
import tempfile
import urllib.request

import yt_dlp

from .cookies_check import inspect_cookies
from .downloader import _base_opts, _copy_cookies, configure_ytdlp, temp_root


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    cookies = os.getenv("COOKIES_FILE") or None
    pot = os.getenv("POT_PROVIDER_URL") or None

    print("== cookies ==")
    if cookies:
        report = inspect_cookies(cookies)
        print(("OK " if report.ok else "ПРОБЛЕМА ") + cookies)
        for line in report.lines:
            print("  •", line)
    else:
        print("  COOKIES_FILE не задан")

    print("== PO token provider ==")
    if pot:
        try:
            with urllib.request.urlopen(f"{pot.rstrip('/')}/ping", timeout=5) as r:
                print(f"  {pot}: доступен ({r.status}) {r.read(200).decode(errors='replace')}")
        except Exception as e:  # noqa: BLE001
            print(f"  {pot}: НЕДОСТУПЕН — {e}")
    else:
        print("  POT_PROVIDER_URL не задан")

    print("== yt-dlp ==", yt_dlp.version.__version__)
    configure_ytdlp(pot)
    with tempfile.TemporaryDirectory(dir=temp_root(os.getenv("TEMP_DIR"))) as tmp:
        ck = _copy_cookies(cookies, temp_root(os.getenv("TEMP_DIR"))) if cookies else None
        opts = _base_opts(str(ck) if ck else None)
        opts.pop("logger")
        opts |= {"quiet": False, "verbose": True, "simulate": True, "paths": {"home": tmp}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            print(f"\nУСПЕХ: «{info.get('title')}», форматов: {len(info.get('formats') or [])}")
            return 0
        except yt_dlp.utils.DownloadError:
            print("\nОШИБКА — пришлите вывод выше (cookie-значения в нём не печатаются).")
            return 1
        finally:
            if ck:
                ck.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
