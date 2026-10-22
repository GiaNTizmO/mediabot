"""Проверка cookies.txt при старте: чтобы по логу сразу было видно, есть ли в файле вход в аккаунт."""
import time
from dataclasses import dataclass, field
from pathlib import Path

from yt_dlp.cookies import YoutubeDLCookieJar

# cookie, без которых сайт считает вас гостем
AUTH_COOKIES = {
    "YouTube": ("youtube.com", {"SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}),
    "Instagram": ("instagram.com", {"sessionid"}),
    "TikTok": ("tiktok.com", {"sessionid", "sessionid_ss"}),
}


@dataclass
class CookieReport:
    ok: bool
    lines: list[str] = field(default_factory=list)


def _fmt_days(seconds: float) -> str:
    days = int(seconds // 86400)
    return f"{days} дн." if days else "меньше суток"


def inspect_cookies(path: str | Path, now: float | None = None) -> CookieReport:
    now = now or time.time()
    path = Path(path)
    if not path.is_file():
        return CookieReport(False, [f"файл {path} не найден"])
    head = path.read_text(encoding="utf-8", errors="replace").lstrip()[:1]
    if head in ("[", "{"):
        return CookieReport(False, ["файл в формате JSON — экспортируйте в формате Netscape (cookies.txt)"])

    jar = YoutubeDLCookieJar(str(path))
    try:
        jar.load()
    except Exception as e:  # noqa: BLE001 — любой сбой разбора показываем как есть
        return CookieReport(False, [f"не удалось прочитать файл: {e}"])
    cookies = list(jar)
    if not cookies:
        return CookieReport(False, ["в файле нет ни одной cookie"])

    report = CookieReport(True)
    for site, (domain, names) in AUTH_COOKIES.items():
        site_cookies = [c for c in cookies if c.domain.lstrip(".").endswith(domain)]
        if not site_cookies:
            continue
        auth = [c for c in site_cookies if c.name in names]
        live = [c for c in auth if not c.expires or c.expires > now]
        if not auth:
            report.ok = False
            report.lines.append(
                f"{site}: {len(site_cookies)} cookie, но нет cookie входа ({', '.join(sorted(names))}) — "
                "вы не были залогинены при экспорте"
            )
        elif not live:
            report.ok = False
            report.lines.append(f"{site}: cookie входа истекли — экспортируйте заново")
        else:
            expires = [c.expires for c in live if c.expires]
            left = f", истекают через {_fmt_days(min(expires) - now)}" if expires else ""
            report.lines.append(f"{site}: вход найден ({len(site_cookies)} cookie{left})")
    if not report.lines:
        report.ok = False
        report.lines.append("нет cookies YouTube, Instagram или TikTok — экспортирован не тот сайт")
    return report


if __name__ == "__main__":
    # docker compose exec bot python -m bot.cookies_check /app/cookies.txt
    import os
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else os.getenv("COOKIES_FILE", "cookies.txt")
    result = inspect_cookies(target)
    print(("✅ " if result.ok else "❌ ") + target)
    for line in result.lines:
        print("  •", line)
    sys.exit(0 if result.ok else 1)
