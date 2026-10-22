import pytest

from bot.downloader import DownloadError, _clean, download, fetch_info, temp_root

COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tabc\n"


async def test_original_cookies_untouched_and_no_copies_left(media_server, tmp_path):
    src = tmp_path / "cookies.txt"
    src.write_text(COOKIES)
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    work = str(tmp_path / "work")

    info = await fetch_info(media_server, str(src), temp_dir=work)
    res = await download(info.url, info.options[0], 50 * 1024 * 1024, str(src), temp_dir=work)
    res.cleanup()

    assert (src.read_bytes(), src.stat().st_mtime_ns) == before
    assert list(temp_root(work).iterdir()) == []


async def test_missing_cookies_file(media_server, tmp_path):
    with pytest.raises(DownloadError, match="cookies не найден"):
        await fetch_info(media_server, str(tmp_path / "nope.txt"), temp_dir=str(tmp_path))


@pytest.mark.parametrize("raw, cookies, expected", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser",
     False, "YouTube требует подтвердить, что вы не бот"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot.", True, "устарели"),
    ("ERROR: [Instagram] C1: Requested content is not available, rate-limit reached or login required",
     False, "Instagram требует вход"),
    ("ERROR: [TikTok] 1: This post may not be comfortable for some audiences. Log in for access.",
     False, "TikTok требует вход"),
])
def test_auth_error_messages(raw, cookies, expected):
    assert expected in _clean(Exception(raw), cookies)
