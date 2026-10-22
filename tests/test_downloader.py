from bot.downloader import Progress, build_options, find_url, human_size, render_progress


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
    assert keys == ["v1080", "v720", "v360", "audio"]
    assert opts[0].size == 1080 * 30_000 + 3_000_000
    assert opts[-1].audio_only


def test_build_options_marks_oversized():
    opts = build_options({"formats": _yt_formats(), "duration": 100}, max_bytes=25_000_000)
    assert opts[0].label.startswith("⚠️")  # 1080p ≈ 35 МБ
    assert not opts[2].label.startswith("⚠️")


def test_build_options_without_heights():
    opts = build_options({"formats": [{"vcodec": "h264", "acodec": "aac"}]})
    assert [o.key for o in opts] == ["vbest", "audio"]


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
