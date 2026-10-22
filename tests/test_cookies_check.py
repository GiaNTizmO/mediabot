import time

from bot.cookies_check import inspect_cookies
from bot.downloader import _base_opts, configure_ytdlp

FUTURE = int(time.time()) + 200 * 86400
PAST = int(time.time()) - 86400
HEADER = "# Netscape HTTP Cookie File\n"


def _line(domain, name, expires=FUTURE, httponly=False):
    prefix = "#HttpOnly_" if httponly else ""
    return f"{prefix}{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\tvalue\n"


def _write(tmp_path, body, name="cookies.txt"):
    p = tmp_path / name
    p.write_text(body)
    return p


def test_logged_in_youtube_and_instagram(tmp_path):
    p = _write(tmp_path, HEADER + _line(".youtube.com", "PREF") + _line(".youtube.com", "__Secure-3PAPISID", httponly=True)
               + _line(".youtube.com", "LOGIN_INFO") + _line(".instagram.com", "sessionid"))
    r = inspect_cookies(p)
    assert r.ok
    assert any(line.startswith("YouTube: вход найден (3 cookie, истекают через") for line in r.lines)
    assert any(line.startswith("Instagram: вход найден") for line in r.lines)


def test_guest_youtube_cookies(tmp_path):
    p = _write(tmp_path, HEADER + _line(".youtube.com", "PREF") + _line(".youtube.com", "VISITOR_INFO1_LIVE"))
    r = inspect_cookies(p)
    assert not r.ok and "не были залогинены" in r.lines[0]


def test_youtube_needs_login_info_and_apisid(tmp_path):
    # так выглядит «почти вход»: SID есть, но yt-dlp не считает это авторизацией
    p = _write(tmp_path, HEADER + _line(".youtube.com", "SID") + _line(".youtube.com", "__Secure-3PSID")
               + _line(".youtube.com", "SAPISID"))
    r = inspect_cookies(p)
    assert not r.ok and "LOGIN_INFO" in r.lines[0] and "SAPISID" not in r.lines[0]
    p = _write(tmp_path, HEADER + _line(".youtube.com", "LOGIN_INFO") + _line(".youtube.com", "SID"), "b.txt")
    r = inspect_cookies(p)
    assert not r.ok and "SAPISID или __Secure-1PAPISID или __Secure-3PAPISID" in r.lines[0]


def test_expired_login(tmp_path):
    p = _write(tmp_path, HEADER + _line(".youtube.com", "LOGIN_INFO") + _line(".youtube.com", "SAPISID", PAST))
    r = inspect_cookies(p)
    assert not r.ok and "истекли" in r.lines[0]


def test_json_export_rejected(tmp_path):
    r = inspect_cookies(_write(tmp_path, '[{"domain": ".youtube.com"}]'))
    assert not r.ok and "JSON" in r.lines[0]


def test_windows_line_endings(tmp_path):
    p = tmp_path / "c.txt"
    body = HEADER + _line(".youtube.com", "LOGIN_INFO") + _line(".youtube.com", "SAPISID")
    p.write_bytes(body.replace("\n", "\r\n").encode())
    assert inspect_cookies(p).ok


def test_wrong_site_and_missing(tmp_path):
    assert "не тот сайт" in inspect_cookies(_write(tmp_path, HEADER + _line(".google.com", "SID"))).lines[0]
    assert not inspect_cookies(tmp_path / "nope.txt").ok


def test_pot_provider_extractor_args():
    try:
        configure_ytdlp("http://pot-provider:4416/")
        assert _base_opts(None)["extractor_args"] == {
            "youtubepot-bgutilhttp": {"base_url": ["http://pot-provider:4416"]}
        }
    finally:
        configure_ytdlp(None)
    assert "extractor_args" not in _base_opts(None)


def test_login_line_with_spaces_instead_of_tabs(tmp_path):
    # ровно случай с сервера: файл вставлен через буфер, в строке LOGIN_INFO табуляция стала пробелами
    good = _line(".youtube.com", "SAPISID") + _line(".youtube.com", "PREF")
    broken = f".youtube.com    TRUE    /       TRUE    {FUTURE}      LOGIN_INFO      AFmm:Qq==\n"
    p = _write(tmp_path, HEADER + good + broken)
    r = inspect_cookies(p)
    assert r.ok, r.lines
    assert r.lines[0].startswith("YouTube: вход найден (3 cookie")
    assert "пробелами вместо табуляции: 1 (№ 4)" in r.lines[1]


def test_bom_crlf_and_unfixable_line(tmp_path):
    body = HEADER + _line(".youtube.com", "LOGIN_INFO") + _line(".youtube.com", "SAPISID") + ".youtube.com TRUE /\n"
    p = tmp_path / "c.txt"
    p.write_bytes(("﻿" + body).replace("\n", "\r\n").encode())
    r = inspect_cookies(p)
    assert r.ok
    assert r.lines[-1] == "строка 4 пропущена: 3 полей вместо 7"


def test_bot_copy_is_normalized(tmp_path):
    from yt_dlp.cookies import YoutubeDLCookieJar

    from bot.downloader import _copy_cookies

    src = _write(tmp_path, HEADER + f".youtube.com  TRUE  /  TRUE  {FUTURE}  LOGIN_INFO  v1\n"
                 + f"#HttpOnly_.youtube.com TRUE / TRUE {FUTURE} SAPISID v2\n")
    copy = _copy_cookies(str(src), tmp_path)
    jar = YoutubeDLCookieJar(str(copy))
    jar.load()
    assert {c.name: c.value for c in jar} == {"LOGIN_INFO": "v1", "SAPISID": "v2"}
    assert "  " in src.read_text()  # оригинал не трогаем
