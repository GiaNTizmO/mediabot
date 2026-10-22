import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    token: str
    max_file_mb: int
    local_api_url: str | None
    allowed_users: frozenset[int]
    cookies_file: str | None
    download_dir: str = "downloads"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    allowed = frozenset(
        int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x
    )
    return Config(
        token=token,
        max_file_mb=int(os.getenv("MAX_FILE_MB", "50")),
        local_api_url=os.getenv("LOCAL_API_URL") or None,
        allowed_users=allowed,
        cookies_file=os.getenv("COOKIES_FILE") or None,
    )
