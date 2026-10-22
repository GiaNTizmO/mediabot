import functools
import http.server
import subprocess
import threading

import pytest


@pytest.fixture(scope="session")
def media_server(tmp_path_factory):
    """Локальный HTTP-сервер с тестовым mp4 (H.264 + AAC, вертикальный 360x640)."""
    root = tmp_path_factory.mktemp("srv")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(root / "clip.mp4")],
        check=True,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/clip.mp4"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _no_proxy_for_localhost(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
