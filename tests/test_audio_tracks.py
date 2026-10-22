import pytest
import yt_dlp

from bot.downloader import (
    BEST_SELECTOR,
    _video_selector,
    audio_tracks,
    build_options,
    language_name,
    with_language,
)


def _formats():
    fmts = [
        dict(format_id=f"a-{lang}", ext="m4a", vcodec="none", acodec="mp4a.40.2", language=lang,
             language_preference=pref, url=f"https://e.com/{lang}", protocol="https", tbr=128)
        for lang, pref in (("en", 10), ("ru", -1), ("es-419", -1), ("en-desc", -10))
    ]
    fmts.append(dict(format_id="v720", ext="mp4", vcodec="avc1.4d", acodec="none", height=720,
                     url="https://e.com/v", protocol="https", tbr=1000))
    fmts.append(dict(format_id="18", ext="mp4", vcodec="avc1.42", acodec="mp4a.40.2", height=360,
                     language="en", language_preference=10, url="https://e.com/18", protocol="https", tbr=500))
    return fmts


def _select(selector: str) -> str:
    info = {"id": "x", "title": "t", "extractor": "x", "extractor_key": "X",
            "webpage_url": "https://e.com", "formats": _formats()}
    with yt_dlp.YoutubeDL({"quiet": True, "simulate": True, "format": selector}) as ydl:
        return ydl.process_ie_result(info, download=False)["format_id"]


def test_audio_tracks_sorted_original_first():
    tracks = audio_tracks(_formats())
    assert [t.lang for t in tracks] == ["en", "ru", "es-419", "en-desc"]  # оригинал, русский, прочие, desc
    assert tracks[0].original and tracks[0].label == "🇬🇧 Английский · оригинал"
    assert tracks[1].label == "🇷🇺 Русский"


def test_single_track_means_no_choice():
    fmts = [f for f in _formats() if f.get("language") in (None, "en")]
    assert audio_tracks(fmts) == []


def test_same_language_names_disambiguated():
    fmts = [dict(acodec="aac", language=lang) for lang in ("en", "en-US")]
    names = {t.lang: t.name for t in audio_tracks(fmts)}
    assert names == {"en": "🇬🇧 Английский", "en-US": "🇺🇸 Английский"}
    fmts = [dict(acodec="aac", language=lang) for lang in ("xx", "xx-1")]
    assert sorted(t.name for t in audio_tracks(fmts)) == ["🔊 xx (xx)", "🔊 xx (xx-1)"]


def test_language_name():
    assert language_name("pt-BR") == "🇧🇷 Португальский"
    assert language_name("es-419") == "🇪🇸 Испанский (Лат. Америка)"
    assert language_name("en-desc") == "🇬🇧 Английский (тифлокомментарий)"


def test_with_language_does_not_touch_video_streams():
    sel = with_language(_video_selector(720), "ru")
    assert "bv[height<=720][vcodec^=avc1]+ba[language=ru][ext=m4a]" in sel
    assert "bv*" not in sel and "bv[language" not in sel
    assert with_language("ba/b", "en-US") == "ba[language=en-US]/b[language=en-US]"


@pytest.mark.parametrize("lang, expected", [
    ("ru", "v720+a-ru"),
    ("es-419", "v720+a-es-419"),
    ("en", "v720+a-en"),
])
def test_ytdlp_picks_chosen_track_for_video(lang, expected):
    assert _select(with_language(_video_selector(720), lang)) == expected


def test_ytdlp_picks_chosen_track_for_audio_and_best():
    assert _select(with_language("ba[ext=m4a]/ba/b", "ru")) == "a-ru"
    assert _select(with_language(BEST_SELECTOR, "ru")) == "v720+a-ru"


def test_missing_track_fails_instead_of_wrong_language():
    with pytest.raises(yt_dlp.utils.YoutubeDLError):  # в ydl.download() это DownloadError
        _select(with_language(_video_selector(720), "fr"))


def test_options_unchanged_by_tracks():
    keys = [o.key for o in build_options({"formats": _formats(), "duration": 60})]
    assert keys == ["v720", "v360", "mp3", "m4a"]
