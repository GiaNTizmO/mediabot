"""Проверка cookies.txt при старте: чтобы по логу сразу было видно, есть ли в файле вход в аккаунт."""
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from yt_dlp.cookies import YoutubeDLCookieJar

# Набор cookie, без которых сайт считает вас гостем: нужны все группы, из каждой — хотя бы одна.
# Для YouTube повторяет логику yt-dlp (_has_auth_cookies): LOGIN_INFO + один из *APISID,
# из которого строится заголовок авторизации.
AUTH_COOKIES = {
    "YouTube": ("youtube.com", [{"LOGIN_INFO"}, {"SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"}]),
    "Instagram": ("instagram.com", [{"sessionid"}]),
    "TikTok": ("tiktok.com", [{"sessionid", "sessionid_ss"}]),
}


HTTPONLY_PREFIX = "#HttpOnly_"
FIELDS = 7  # domain, include_subdomains, path, secure, expires, name, value


@dataclass
class Normalized:
    text: str
    fixed: list[int] = field(default_factory=list)  # номера строк, где пробелы заменены на табуляцию
    bad: list[tuple[int, str]] = field(default_factory=list)  # (номер строки, причина) — строки пропущены


def normalize_cookies_text(text: str) -> Normalized:
    """Чинит типичную порчу cookies.txt при копировании через буфер обмена.

    yt-dlp принимает только строки из 7 полей, разделённых табуляцией, а остальные молча пропускает.
    При вставке в nano/терминал табуляция часто превращается в пробелы — такие строки восстанавливаем
    (в Netscape-формате значения cookie не содержат пробелов). Также убираем BOM и \r."""
    result = Normalized("")
    out = []
    for no, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.rstrip("\r")
        prefix = HTTPONLY_PREFIX if line.startswith(HTTPONLY_PREFIX) else ""
        body = line[len(prefix):]
        if not body.strip() or (not prefix and body.lstrip().startswith("#")):
            out.append(line)
            continue
        parts = body.split("\t")
        if len(parts) != FIELDS:
            words = body.split()
            if len(words) == FIELDS:
                parts = words
                result.fixed.append(no)
            else:
                result.bad.append((no, f"{len(words)} полей вместо {FIELDS}"))
                continue
        if parts[4] and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", parts[4].strip()):
            result.bad.append((no, "неверный срок действия"))
            continue
        out.append(prefix + "\t".join(p.strip() for p in parts))
    result.text = "\n".join(out) + "\n"
    return result


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
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.lstrip("\ufeff \t\r\n")[:1] in ("[", "{"):
        return CookieReport(False, ["файл в формате JSON — экспортируйте в формате Netscape (cookies.txt)"])

    norm = normalize_cookies_text(raw)
    notes = []
    if norm.fixed:
        notes.append(
            f"строк с пробелами вместо табуляции: {len(norm.fixed)} (№ {_ranges(norm.fixed)}) — "
            "бот исправляет их сам, но лучше загрузить файл на сервер через scp, а не копированием текста"
        )
    for no, reason in norm.bad:
        notes.append(f"строка {no} пропущена: {reason}")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
        tmp.write(norm.text)
    jar = YoutubeDLCookieJar(tmp.name)
    try:
        jar.load()
    except Exception as e:  # noqa: BLE001 — любой сбой разбора показываем как есть
        return CookieReport(False, [f"не удалось прочитать файл: {e}", *notes])
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    cookies = list(jar)
    if not cookies:
        return CookieReport(False, ["в файле нет ни одной cookie", *notes])

    report = CookieReport(True)
    for site, (domain, groups) in AUTH_COOKIES.items():
        site_cookies = [c for c in cookies if c.domain.lstrip(".").endswith(domain)]
        if not site_cookies:
            continue
        names = {c.name for c in site_cookies}
        live_names = {c.name for c in site_cookies if not c.expires or c.expires > now}
        auth = [c for c in site_cookies if any(c.name in g for g in groups)]
        missing = [g for g in groups if not g & names]
        expired = [g for g in groups if g & names and not g & live_names]
        if missing:
            report.ok = False
            need = "; ".join(" или ".join(sorted(g)) for g in missing)
            report.lines.append(
                f"{site}: {len(site_cookies)} cookie, но не хватает cookie входа: {need}. "
                "Вы не были залогинены при экспорте, сессия уже сброшена, "
                "или расширение экспортировало не все cookie"
            )
        elif expired:
            report.ok = False
            report.lines.append(f"{site}: cookie входа истекли — экспортируйте заново")
        else:
            expires = [c.expires for c in auth if c.expires and c.expires > now]
            left = f", истекают через {_fmt_days(min(expires) - now)}" if expires else ""
            report.lines.append(f"{site}: вход найден ({len(site_cookies)} cookie{left})")
    if not report.lines:
        report.ok = False
        report.lines.append("нет cookies YouTube, Instagram или TikTok — экспортирован не тот сайт")
    report.lines += notes
    return report


def _ranges(numbers: list[int]) -> str:
    shown = ", ".join(map(str, numbers[:10]))
    return shown + (" …" if len(numbers) > 10 else "")


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
