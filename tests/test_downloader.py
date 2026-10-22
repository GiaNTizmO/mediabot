from bot.downloader import (
    Option,
    Progress,
    VideoInfo,
    build_options,
    find_url,
    human_size,
    is_music_url,
    render_progress,
    safe_filename,
)


def test_find_url():
    assert find_url("глянь https://vm.tiktok.com/ZM123/ !") == "https://vm.tiktok.com/ZM123/"
    assert find_url("https://youtube.com/shorts/abc?si=1).") == "https://youtube.com/shorts/abc?si=1"
    assert find_url("https://youtu.be/xyz") == "https://youtu.be/xyz"
    assert find_url("https://www.instagram.com/reel/C1/") == "https://www.instagram.com/reel/C1/"
    assert find_url("https://example.com/video") is None
    assert find_url(None) is None


def _yt_formats():
    fmts = [
        {"height": h, "vcodec": "vp9", "acodec": "none", "filesize": h * 40_000} for h in (360, 720, 1080, 1440)
    ] + [
        {"height": h, "vcodec": "avc1.64", "acodec": "none", "filesize": h * 30_000} for h in (360, 720, 1080)
    ]
    fmts.append({"vcodec": "none", "acodec": "mp4a", "ext": "m4a", "filesize": 3_000_000})
    return fmts


def test_build_options_prefers_h264_size_and_caps_height():
    opts = build_options({"formats": _yt_formats(), "duration": 100})
    keys = [o.key for o in opts]
    assert keys == ["v1080", "v720", "v360", "mp3", "m4a"]
    assert opts[0].size == 1080 * 30_000 + 3_000_000
    assert opts[-1].audio_only and opts[-1].audio_codec == "m4a"


def test_build_options_music_puts_audio_first():
    keys = [o.key for o in build_options({"formats": _yt_formats(), "duration": 100}, music=True)]
    assert keys[:2] == ["mp3", "m4a"] and keys[2] == "v1080"


def test_build_options_marks_oversized():
    opts = build_options({"formats": _yt_formats(), "duration": 100}, max_bytes=25_000_000)
    assert opts[0].label.startswith("⚠️")  # 1080p ≈ 35 МБ
    assert not opts[2].label.startswith("⚠️")


def test_build_options_without_heights():
    opts = build_options({"formats": [{"vcodec": "h264", "acodec": "aac"}]})
    assert [o.key for o in opts] == ["vbest", "mp3"]


def test_build_options_estimates_from_bitrate():
    opts = build_options({"formats": [{"height": 720, "vcodec": "avc1", "acodec": "mp4a", "tbr": 1000}], "duration": 60})
    assert opts[0].size == 1000 * 125 * 60


def test_human_size():
    assert human_size(None) == "?"
    assert human_size(512) == "512 Б"
    assert human_size(50 * 1024 * 1024) == "50.0 МБ"


def test_render_progress():
    p = Progress(stage="download", downloaded=5 * 1024 * 1024, total=10 * 1024 * 1024, speed=1024 * 1024, eta=5)
    text = render_progress(p, "T", 0)
    assert "50%" in text and "1.0 МБ/с" in text


def test_music_url():
    assert is_music_url("https://music.youtube.com/watch?v=abc&list=RDAMVM")
    assert not is_music_url("https://www.youtube.com/watch?v=abc")
    assert find_url("https://music.youtube.com/watch?v=abc") == "https://music.youtube.com/watch?v=abc"


def test_safe_filename():
    assert safe_filename('AC/DC: "Back in Black" | Live?') == "AC DC Back in Black Live"
    assert safe_filename("танцуем 💃 #fyp #viral #рек") == "танцуем 💃"
    assert safe_filename("#fyp #viral") == "#fyp #viral"  # только хэштеги — оставляем
    assert safe_filename("  ...  ") == "video"
    long = safe_filename("Очень длинное название " * 30)
    assert len(long.encode()) <= 183 and long.endswith("…")


def _info(**kw):
    base = dict(url="u", id="i", extractor="Youtube", title="Rick Astley - Never Gonna Give You Up (Official Video)",
                uploader="Rick Astley", duration=200, thumbnail=None, options=[])
    return VideoInfo(**(base | kw))


def test_file_stem_and_tags():
    mp3, v = Option("mp3", "", "", "mp3"), Option("v720", "", "")
    plain = _info()
    assert plain.file_stem(v) == "Rick Astley - Never Gonna Give You Up (Official Video)"
    assert plain.file_stem(mp3) == plain.file_stem(v)
    music = _info(track="Never Gonna Give You Up", artist="Rick Astley", uploader="Rick Astley - Topic",
                  album="Whenever You Need Somebody", year="1987")
    assert music.file_stem(mp3) == "Rick Astley - Never Gonna Give You Up"
    assert music.file_stem(v) == safe_filename(music.title)
    tags = music.audio_tags()
    assert (tags.title, tags.artist, tags.album, tags.year) == (
        "Never Gonna Give You Up", "Rick Astley", "Whenever You Need Somebody", "1987")
    assert _info(uploader="Some Band - Topic").audio_artist == "Some Band"
