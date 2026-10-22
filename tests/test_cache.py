from bot.cache import FileCache


def test_cache_roundtrip(tmp_path):
    c = FileCache(tmp_path / "sub" / "c.db")
    assert c.get("k") is None
    c.put("k", "fid", "video")
    c.put("k", "fid2", "audio")
    assert c.get("k") == ("fid2", "audio")
    c.delete("k")
    assert c.get("k") is None
    c.close()
